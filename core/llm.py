"""Multi-provider LLM client (Anthropic + OpenAI dialects), response extraction, and JSON repair.

Any endpoint that speaks either wire format works: first-party APIs, OpenRouter,
Groq, Together, LiteLLM proxies, DeepSeek's Anthropic-compat endpoint, vLLM/Ollama.
Dialect is chosen per role via GESAKU_{ROLE}_PROVIDER (or GESAKU_PROVIDER);
see Docs/core/llm-client.md for the full config matrix.
"""

import os
import sys
import json
import re

import httpx


# Base layer (config, client, wire parsing) lives in core/llm_base.py;
# the tool loop in llm_tools/llm_toolwire. Imported here so core.llm stays
# the documented entry point. No sibling imports this module back.
from core.llm_base import (  # noqa: F401
    LLM_MAX_TRANSPORT_ATTEMPTS, LLM_BACKOFF_BASE_SECONDS,
    ROLES, DEFAULT_MODELS, MODEL_ENV_VARS, PROVIDER_ENV_VARS, BASE_URL_ENV_VARS,
    KEY_ENV_VARS, DEFAULT_BASE_URLS, LLM_TIMEOUTS, LLM_TIMEOUT_ENV,
    REFINEMENT_ATTEMPTS, REFINEMENT_BLOCK_SIZE,
    ProviderError, TruncationError, EmptyResponseError, llm_timeout, resolve_provider,
    extract_text_from_response, extract_text_and_stop_reason,
    get_max_tokens_with_thinking, get_client, set_client,
    _looks_like_reasoning_model, _REASONING_MODEL_RE, _warn_unused_trailing,
    _parse_response_json, _iter_sse_objects, _extract_sse_text_and_stop_reason,
    _usage_pair, _usage_from_sse, _response_telemetry, _is_sse_body,
    _normalize_stop_reason, _emit_llm_event, _load_extra_headers,
    _resolve_model, _resolve_base_url,
)
from core.llm_tools import call_llm_tools  # noqa: F401
from core.llm_toolwire import (  # noqa: F401
    DEFAULT_TOOL_BUDGET, ToolLoopResult,
)
def _build_request(provider: str, model: str, system, prompt, max_tokens, temperature, beta_context):
    """Build (url_path, headers, payload) for the resolved provider dialect.

    Auth headers are only added when the corresponding key env var is set —
    local gateways (Ollama/vLLM/LM Studio) reject non-empty placeholder keys.
    """
    headers = {"content-type": "application/json"}
    headers.update(_load_extra_headers())
    api_key = ""

    if provider == "anthropic":
        api_key = os.environ.get(KEY_ENV_VARS[provider], "")
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        if beta_context:
            headers["anthropic-beta"] = "context-1m-2025-08-07"
        payload = {
            "model": model,
            "max_tokens": get_max_tokens_with_thinking(max_tokens),
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        return "/v1/messages", headers, payload

    # OpenAI dialect
    api_key = os.environ.get(KEY_ENV_VARS[provider], "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if beta_context:
        # The 1M-context beta is Anthropic-only; silently degrade on openai.
        print(
            "[llm] beta_context requested but provider is openai — ignored (Anthropic-only beta)",
            file=sys.stderr,
        )
    use_reasoning_params = _looks_like_reasoning_model(model)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["messages"].insert(0, {"role": "system", "content": system})
    if use_reasoning_params:
        payload["max_completion_tokens"] = get_max_tokens_with_thinking(max_tokens)
    else:
        payload["max_tokens"] = get_max_tokens_with_thinking(max_tokens)
        payload["temperature"] = temperature
    return "/chat/completions", headers, payload


def call_llm(
    prompt,
    system=None,
    model_key="writer",
    max_tokens=4000,
    temperature=0.3,
    beta_context=False,
    timeout=None,
    timeout_role="standard",
    raise_on_truncation=True,
):
    """Single-shot chat completion. Every non-tool LLM call flows through here."""
    if timeout is None:
        timeout = llm_timeout(timeout_role)
    provider = resolve_provider(model_key)
    model = _resolve_model(provider, model_key)
    base_url = _resolve_base_url(provider, model_key)
    url_path, headers, payload = _build_request(
        provider, model, system, prompt, max_tokens, temperature, beta_context
    )
    url = f"{base_url}{url_path}"

    import time

    max_retries = LLM_MAX_TRANSPORT_ATTEMPTS
    backoff = LLM_BACKOFF_BASE_SECONDS
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            client = get_client()
            resp = client.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            tokens_in, tokens_out, stop_reason = _response_telemetry(resp, provider)
            text, text_stop = extract_text_and_stop_reason(resp, dialect=provider)
            if not text.strip() and text_stop != "max_tokens":
                # Gateways in a combo pool intermittently answer 200 with a
                # body that never opened a text block (thinking-only or empty
                # stream), sometimes with a normal stop_reason. Recording that
                # as a successful empty generation fails the caller's
                # validation and can kill a whole step; it is a transport
                # fault, so retry it like one. A truncated reply keeps its
                # TruncationError path below.
                raise EmptyResponseError(
                    f"empty response body from {model} ({len(resp.text)} bytes, "
                    f"stop={text_stop!r})"
                )
            _emit_llm_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                      + f".{int(time.perf_counter() * 1000) % 1000:03d}Z",
                "model_key": model_key,
                "model": model,
                "ok": True,
                "attempt": attempt,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "duration_ms": round((time.perf_counter() - t0) * 1000),
                "stop_reason": stop_reason,
                "prompt_chars": len(str(prompt)) + (len(system) if system else 0),
                "response_chars": len(resp.text),
                "prompt_head": str(prompt)[:300],
            })
            if raise_on_truncation and text_stop == "max_tokens":
                raise TruncationError(
                    f"Response truncated at ~{len(text.split())} words "
                    f"(stop_reason: max_tokens)"
                )
            return text
        except TruncationError:
            raise
        except (httpx.HTTPStatusError, httpx.RequestError, EmptyResponseError) as e:
            _emit_llm_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                      + f".{int(time.perf_counter() * 1000) % 1000:03d}Z",
                "model_key": model_key,
                "model": model,
                "ok": False,
                "attempt": attempt,
                "duration_ms": round((time.perf_counter() - t0) * 1000),
                "error": str(e)[:200],
                "prompt_chars": len(str(prompt)),
                "prompt_head": str(prompt)[:300],
            })
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in [400, 401, 403, 404]:
                raise e
            if attempt == max_retries:
                raise e
            print(f"API call failed (attempt {attempt}/{max_retries}): {e}. Retrying in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 2


# The healing parser lives in its own module; re-exported here because
# `llm.parse_json_response` is the documented entry point (Docs/core/llm-client.md).
from core.json_repair import (  # noqa: E402,F401
    fix_truncated_json, is_json_boundary, parse_json_response,
    repair_unescaped_quotes,
)
