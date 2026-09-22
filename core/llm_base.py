"""Anthropic + OpenAI client base: config, timeouts, client, wire parsing.

Split out of `core/llm.py` under the module-size rule. Nothing here may
import `llm.py`, `llm_tools.py`, or `llm_toolwire.py` — that is what keeps
the tool modules importable on their own.
"""

import os
import json
import re

import httpx


ROLES = ("writer", "judge", "review")

# Role -> default model per provider. Defaults are only used when the
# GESAKU_{ROLE}_MODEL env var is unset; any model id is a free-form string.
DEFAULT_MODELS = {
    "anthropic": {
        "writer": "claude-sonnet-4-6",
        "judge": "claude-opus-4-6",
        "review": "claude-opus-4-6",
    },
    "openai": {
        "writer": "gpt-5.2",
        "judge": "gpt-5.2",
        "review": "gpt-5.2",
    },
}

MODEL_ENV_VARS = {
    "writer": "GESAKU_WRITER_MODEL",
    "judge": "GESAKU_JUDGE_MODEL",
    "review": "GESAKU_REVIEW_MODEL",
}

PROVIDER_ENV_VARS = {
    role: f"GESAKU_{role.upper()}_PROVIDER" for role in ROLES
}

BASE_URL_ENV_VARS = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
}

KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}

# Reasoning-style models reject `temperature` and want `max_completion_tokens`.
_REASONING_MODEL_RE = re.compile(r"^(o\d|gpt-5)")

def _looks_like_reasoning_model(model: str) -> bool:
    return bool(_REASONING_MODEL_RE.match(model.strip().lower()))


# LLM per-call budgets, mirroring pipeline_infra.timeout_for naming.
# Env-tunable; callers pass a role (short/standard/long/xlong) or an
# explicit timeout. Explicit values always win.
# Transport retry policy for LLM POSTs, shared by call_llm and the tool
# loop (named constants, one owner — neither call site re-derives them).
LLM_MAX_TRANSPORT_ATTEMPTS = 5
LLM_BACKOFF_BASE_SECONDS = 2

LLM_TIMEOUTS = {
    "short": 120,
    "standard": 300,
    "long": 900,
    "xlong": 1800,
}

LLM_TIMEOUT_ENV = {
    "short": "GESAKU_LLM_TIMEOUT_SHORT",
    "standard": "GESAKU_LLM_TIMEOUT_STANDARD",
    "long": "GESAKU_LLM_TIMEOUT_LONG",
    "xlong": "GESAKU_LLM_TIMEOUT_XLONG",
}


def llm_timeout(role: str = "standard") -> int:
    """Named LLM budget in seconds."""
    default = LLM_TIMEOUTS.get(role, LLM_TIMEOUTS["standard"])
    try:
        val = int(float(os.getenv(LLM_TIMEOUT_ENV.get(role, ""), "")))
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


# Writer calls per outline-refinement block in foundation/gen_outline_part2.py,
# and the number of chapters per block. That generator works through its blocks
# one call at a time, so the phase layer derives the subprocess cap from these
# (blocks x attempts x budget) — one owner for each number, and neither side
# can drift from the other.
REFINEMENT_ATTEMPTS = 3
REFINEMENT_BLOCK_SIZE = 10


class ProviderError(Exception):
    """Raised for provider/config problems (bad headers, bad provider name).

    The message names the exact env var to set — actionable, not vague.
    """

class TruncationError(Exception):
    """Raised when the API response was truncated (stop_reason == 'max_tokens')."""
    pass


class EmptyResponseError(Exception):
    """A 200 response whose body carried no text and no stop_reason.

    Combo gateways intermittently stream thinking deltas and then never open a
    text block. Treated as retryable transport noise by call_llm, not as a
    successful empty generation.
    """


def _warn_unused_trailing(text: str, consumed_len: int) -> None:
    """Warn to stderr when a response contains content after the first JSON value."""
    import sys
    trailing = text[consumed_len:].strip()
    if trailing:
        print(
            f"  [WARN] JSON parse dropped {len(trailing)} trailing chars after the first "
            f"JSON value (possible second object or conversation tail): "
            f"{trailing[:80]!r}...",
            file=sys.stderr,
        )

def _parse_response_json(text: str) -> dict:
    """Parse (possibly damaged) JSON from an Anthropic response string."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        obj, end = decoder.raw_decode(text)
        _warn_unused_trailing(text, end)
        return obj

def _iter_sse_objects(raw: str):
    """Yield the decoded JSON payloads of an SSE body.

    Skips event/id/comment lines, the `[DONE]` sentinel, and anything that does
    not parse — a stream with one damaged chunk should still yield the rest.
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def _extract_sse_text_and_stop_reason(raw: str, dialect: str):
    """Parse an SSE stream body into (text, stop_reason) for either dialect.

    Anthropic chunks carry content_block_delta / message_start / message_delta
    events; OpenAI chunks carry choices[].delta.content and finish_reason.
    """
    text_content = ""
    stop_reason = None
    for item in _iter_sse_objects(raw):
        if dialect == "openai":
            for choice in item.get("choices", []):
                delta = choice.get("delta", {}) or {}
                text_content += delta.get("content") or ""
                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]
        else:
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "text":
                        text_content += block.get("text", "")
                if item.get("stop_reason"):
                    stop_reason = item["stop_reason"]
            elif item.get("type") == "content_block_delta":
                delta = item.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_content += delta.get("text", "")
            elif item.get("type") == "message_delta":
                if item.get("delta", {}).get("stop_reason"):
                    stop_reason = item["delta"]["stop_reason"]
    return text_content, stop_reason


def _usage_pair(usage) -> tuple:
    """(input_tokens, output_tokens) from a usage object in either dialect.

    Anthropic names the fields input_tokens/output_tokens; OpenAI names them
    prompt_tokens/completion_tokens. Gateways mix the two freely, so accept
    whichever is present.
    """
    if not isinstance(usage, dict):
        return None, None
    tin = usage.get("input_tokens")
    if tin is None:
        tin = usage.get("prompt_tokens")
    tout = usage.get("output_tokens")
    if tout is None:
        tout = usage.get("completion_tokens")
    return tin, tout


def _usage_from_sse(raw: str) -> tuple:
    """Token usage from an SSE body.

    Take the LAST non-zero block, not the first: an Anthropic-shaped stream
    opens with placeholder zeros on `message_start` and only reports the real
    totals on the final `message_delta`. Recording the first block seen is how
    every call ended up logged with no usage at all.
    """
    last_seen = (None, None)
    last_nonzero = None
    for item in _iter_sse_objects(raw):
        usage = item.get("usage")
        if usage is None and isinstance(item.get("message"), dict):
            usage = item["message"].get("usage")
        tin, tout = _usage_pair(usage)
        if tin is None and tout is None:
            continue
        last_seen = (tin, tout)
        if (tin or 0) + (tout or 0) > 0:
            last_nonzero = (tin, tout)
    return last_nonzero or last_seen


def _response_telemetry(resp, dialect: str):
    """(tokens_in, tokens_out, stop_reason) for one response.

    Gateways routinely answer with `text/event-stream` even when streaming was
    never requested. The usage and stop_reason then live *inside* the stream,
    so parsing `resp.text` as one JSON object yields an empty dict and the call
    is recorded with null tokens and a null stop_reason.
    """
    raw = resp.text
    if _is_sse_body(resp):
        _text, stop_reason = _extract_sse_text_and_stop_reason(raw, dialect)
        tin, tout = _usage_from_sse(raw)
        return tin, tout, stop_reason
    try:
        data = _parse_response_json(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return None, None, None
    tin, tout = _usage_pair(data.get("usage"))
    return tin, tout, data.get("stop_reason")


def _is_sse_body(resp) -> bool:
    raw = resp.text.strip()
    content_type = resp.headers.get("content-type", "")
    is_sse = "text/event-stream" in content_type and any(
        l.strip().startswith("data:") for l in raw.splitlines()
    )
    return is_sse or not raw.startswith("{")

def extract_text_from_response(resp, dialect: str = "anthropic"):
    if isinstance(resp, dict):
        data = resp
    else:
        if _is_sse_body(resp):
            text_content, _ = _extract_sse_text_and_stop_reason(resp.text, dialect)
            data = {"content": [{"type": "text", "text": text_content}]}
        else:
            data = _parse_response_json(resp.text)

    # OpenAI-dialect dicts keep their native shape; normalize to content blocks.
    if isinstance(data, dict) and "choices" in data:
        message = (data.get("choices") or [{}])[0].get("message", {})
        return message.get("content") or ""

    for block in data["content"]:
        if block["type"] == "text":
            return block["text"]
    return ""

# OpenAI finish_reason -> canonical Anthropic-vocabulary stop_reason.
_OPENAI_FINISH_REASON_MAP = {
    "length": "max_tokens",
}

def _normalize_stop_reason(finish_reason):
    if finish_reason is None:
        return None
    return _OPENAI_FINISH_REASON_MAP.get(finish_reason, finish_reason)

def extract_text_and_stop_reason(resp, dialect: str = "anthropic"):
    """Return (text, stop_reason) from a response of either dialect.

    stop_reason uses the Anthropic vocabulary ('end_turn', 'max_tokens',
    'stop_sequence') so callers' TruncationError handling works unchanged;
    OpenAI's finish_reason 'length' maps to 'max_tokens'. Streaming
    responses are parsed chunk-wise; stop_reason may be None there.
    """
    if isinstance(resp, dict):
        if "choices" in resp:
            choice = (resp.get("choices") or [{}])[0]
            message = choice.get("message", {})
            return message.get("content") or "", _normalize_stop_reason(choice.get("finish_reason"))
        text_content = ""
        for block in resp.get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")
        return text_content, resp.get("stop_reason")

    if _is_sse_body(resp):
        return _extract_sse_text_and_stop_reason(resp.text, dialect)

    data = _parse_response_json(resp.text)
    if "choices" in data:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        return message.get("content") or "", _normalize_stop_reason(choice.get("finish_reason"))
    for block in data["content"]:
        if block["type"] == "text":
            return block["text"], data.get("stop_reason")
    return "", data.get("stop_reason")

def get_max_tokens_with_thinking(max_tokens):
    return max_tokens + 8000

_client = None

def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _client

def _emit_llm_event(event: dict):
    """Append one LLM call event to the active project's llm_events.jsonl.

    Telemetry sink for the GUI (token stats, timing, prompt inspector). A
    telemetry failure must never break generation, so all errors are swallowed
    by design here.
    """
    try:
        from core import paths
        path = paths.get_llm_events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass

def set_client(client):
    """Install a custom httpx.Client (test seam for httpx.MockTransport)."""
    global _client
    _client = client

def _load_extra_headers() -> dict:
    """Parse GESAKU_EXTRA_HEADERS (JSON object) for gateway-specific headers.

    OpenRouter wants HTTP-Referer/X-Title; other gateways have their own
    requirements. One generic escape hatch instead of vendor special-cases.
    """
    raw = os.getenv("GESAKU_EXTRA_HEADERS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProviderError(
            f"GESAKU_EXTRA_HEADERS is not valid JSON: {e}\n"
            f"Got: {raw[:120]!r}"
        ) from e
    if not isinstance(parsed, dict):
        raise ProviderError(
            f"GESAKU_EXTRA_HEADERS must be a JSON object, got {type(parsed).__name__}"
        )
    return {str(k): str(v) for k, v in parsed.items()}

def resolve_provider(model_key: str) -> str:
    """Resolve the provider dialect for one role.

    Precedence: GESAKU_{ROLE}_PROVIDER > GESAKU_PROVIDER > inference
    (OPENAI_API_KEY set and ANTHROPIC_API_KEY unset -> openai, else anthropic).
    """
    explicit = os.environ.get(PROVIDER_ENV_VARS[model_key], "").strip().lower()
    if explicit:
        if explicit not in ("anthropic", "openai"):
            raise ProviderError(
                f"{PROVIDER_ENV_VARS[model_key]} must be 'anthropic' or 'openai', got {explicit!r}"
            )
        return explicit
    default = os.environ.get("GESAKU_PROVIDER", "").strip().lower()
    if default:
        if default not in ("anthropic", "openai"):
            raise ProviderError(
                f"GESAKU_PROVIDER must be 'anthropic' or 'openai', got {default!r}"
            )
        return default
    if os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        return "openai"
    return "anthropic"

def _resolve_model(provider: str, model_key: str) -> str:
    model = os.environ.get(MODEL_ENV_VARS[model_key], "").strip()
    if model:
        return model
    return DEFAULT_MODELS[provider][model_key]

def _resolve_base_url(provider: str, model_key: str) -> str:
    url = os.environ.get(BASE_URL_ENV_VARS[provider], "").strip()
    if url:
        return url.rstrip("/")
    return DEFAULT_BASE_URLS[provider]

