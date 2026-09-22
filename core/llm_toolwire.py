"""Wire layer for the tool loop: dialect schemas, request building, parsing.

Split out of `core/llm_tools.py` (module-size rule). Holds everything that
knows about HTTP shapes and provider dialects; the loop itself is in
`llm_tools.py`.
"""

from __future__ import annotations

import json
import os
import sys

# Base layer only — importing llm.py here would close the cycle
# (llm.py re-imports these modules).
from core.llm_base import (
    KEY_ENV_VARS,
    _is_sse_body,
    _iter_sse_objects,
    _load_extra_headers,
    _looks_like_reasoning_model,
    _normalize_stop_reason,
    _parse_response_json,
    get_max_tokens_with_thinking,
)

# Committed default for tool-using loops (continuity judge). Named-table
# owner in pipeline: pipeline_infra.judge_tool_budget(); core keeps this
# constant so the loop stays pipeline-free.
DEFAULT_TOOL_BUDGET = 12


class ToolLoopResult:
    """Final state of a call_llm_tools multi-turn tool loop."""

    def __init__(
        self,
        *,
        text: str = "",
        stop_reason: str | None = None,
        agent_stop: str = "end_turn",
        tool_calls_used: int = 0,
        budget: int = DEFAULT_TOOL_BUDGET,
        trace: list | None = None,
        model: str = "",
        provider: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        messages: list | None = None,
    ):
        self.text = text
        self.stop_reason = stop_reason
        self.agent_stop = agent_stop
        self.tool_calls_used = tool_calls_used
        self.budget = budget
        self.trace = trace if trace is not None else []
        self.model = model
        self.provider = provider
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.messages = messages if messages is not None else []

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "stop_reason": self.stop_reason,
            "agent_stop": self.agent_stop,
            "tool_calls_used": self.tool_calls_used,
            "budget": self.budget,
            "trace": list(self.trace),
            "model": self.model,
            "provider": self.provider,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


def _normalize_tools_for_dialect(provider: str, tools: list) -> list:
    """Accept either dialect's tool schema; emit the provider's shape."""
    normalized = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if provider == "anthropic":
            if t.get("type") == "function" and "function" in t:
                fn = t["function"]
                normalized.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}},
                })
            else:
                normalized.append({
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
                })
        else:
            if t.get("type") == "function" and "function" in t:
                normalized.append(t)
            else:
                normalized.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
                    },
                })
    return normalized


def _build_tool_request(provider, model, system, messages, tools, max_tokens, temperature, beta_context):
    """Build (url_path, headers, payload) for a tool-enabled chat turn."""
    headers = {"content-type": "application/json"}
    headers.update(_load_extra_headers())
    tools_norm = _normalize_tools_for_dialect(provider, tools)

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
            "messages": messages,
        }
        if tools_norm:
            payload["tools"] = tools_norm
        if system:
            payload["system"] = system
        return "/v1/messages", headers, payload

    api_key = os.environ.get(KEY_ENV_VARS[provider], "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if beta_context:
        print(
            "[llm] beta_context requested but provider is openai — ignored (Anthropic-only beta)",
            file=sys.stderr,
        )
    msgs = list(messages)
    if system:
        msgs.insert(0, {"role": "system", "content": system})
    payload = {"model": model, "messages": msgs}
    if tools_norm:
        payload["tools"] = tools_norm
    if _looks_like_reasoning_model(model):
        payload["max_completion_tokens"] = get_max_tokens_with_thinking(max_tokens)
    else:
        payload["max_tokens"] = get_max_tokens_with_thinking(max_tokens)
        payload["temperature"] = temperature
    return "/chat/completions", headers, payload


def _parse_tool_turn(data: dict, provider: str):
    """Return (text, tool_calls, stop_reason) for one API response.

    tool_calls: [{id, name, input}]
    """
    if provider == "openai" or (isinstance(data, dict) and "choices" in data):
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(
                b.get("text", "") for b in text if isinstance(b, dict) and b.get("type") in (None, "text")
            )
        raw_calls = message.get("tool_calls") or []
        tool_calls = []
        for c in raw_calls:
            fn = c.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except Exception:
                args = {"_raw": args_raw}
            tool_calls.append({
                "id": c.get("id") or f"call_{len(tool_calls)}",
                "name": fn.get("name", ""),
                "input": args if isinstance(args, dict) else {"value": args},
            })
        stop = _normalize_stop_reason(choice.get("finish_reason"))
        return text, tool_calls, stop

    content = data.get("content") or []
    texts = []
    tool_calls = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text") or "")
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id") or f"toolu_{len(tool_calls)}",
                "name": block.get("name", ""),
                "input": block.get("input") or {},
            })
    return "\n".join(texts), tool_calls, data.get("stop_reason")


def _merge_partial_json(base, partial: str) -> dict:
    """Turn a possibly-truncated streamed JSON argument blob into a dict."""
    if not partial:
        return base if isinstance(base, dict) else {}
    try:
        args = json.loads(partial)
    except Exception:
        return {"_raw": partial}
    if isinstance(args, dict):
        if isinstance(base, dict) and base:
            merged = dict(args)
            for k, v in base.items():
                merged.setdefault(k, v)
            return merged
        return args
    return {"value": args}


def _merge_openai_tool_delta(frags: dict, c: dict) -> None:
    """Accumulate one streamed OpenAI tool_call delta into `frags` by index.

    Arguments arrive as string fragments; id/name arrive once (first delta).
    A delta without an index continues the call currently being streamed (real
    OpenAI always sends `index`; inventing a new call here would split one call
    into two, the second with an empty name).
    """
    idx = c.get("index")
    if idx is None:
        idx = next(reversed(frags), 0)
    frag = frags.setdefault(idx, {"id": "", "name": "", "args": ""})
    if c.get("id"):
        frag["id"] = c["id"]
    fn = c.get("function") or {}
    if fn.get("name"):
        frag["name"] = frag["name"] + fn["name"]
    args = fn.get("arguments")
    if args:
        frag["args"] = frag["args"] + (args if isinstance(args, str) else json.dumps(args))


def _parse_sse_tool_turn(raw: str, provider: str):
    """(text, tool_calls, stop_reason, saw_payload) for an SSE tool response.

    Streamed fragments are accumulated by index: Anthropic `input_json_delta`
    carries the tool arguments, and OpenAI `tool_calls[].function.arguments`
    arrives as string fragments — neither may be treated as a whole call.
    """
    text_parts: list[str] = []
    stop_reason = None
    saw_sse_payload = False

    if provider == "openai":
        frags: dict = {}
        for item in _iter_sse_objects(raw):
            saw_sse_payload = True
            for choice in item.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text_parts.append(delta["content"])
                for c in delta.get("tool_calls") or []:
                    _merge_openai_tool_delta(frags, c)
                if choice.get("finish_reason"):
                    stop_reason = _normalize_stop_reason(choice["finish_reason"])
        tool_calls = []
        for idx in sorted(frags):
            frag = frags[idx]
            tool_calls.append({
                "id": frag["id"] or f"call_{idx}",
                "name": frag["name"],
                "input": _merge_partial_json({}, frag["args"]),
            })
        return "\n".join(text_parts), tool_calls, stop_reason, saw_sse_payload

    # Anthropic: text and tool arguments are separate content blocks; the
    # block index ties the `content_block_start` (name/id) to the argument
    # fragments that follow in `content_block_delta`/`input_json_delta`.
    blocks: dict[int, dict] = {}
    order: list[int] = []

    def _block_index(item: dict) -> int:
        """Frame index; gateways that omit it stay on the last block opened."""
        idx = item.get("index")
        if isinstance(idx, int):
            return idx
        return order[-1] if order else 0

    for item in _iter_sse_objects(raw):
        saw_sse_payload = True
        itype = item.get("type")
        if itype == "message":
            for block in item.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block.get("text") or "")
                elif block.get("type") == "tool_use":
                    blocks[len(order)] = {
                        "id": block.get("id") or "",
                        "name": block.get("name", ""),
                        "partial": "",
                        "input": block.get("input") or {},
                    }
                    order.append(len(order))
            if item.get("stop_reason"):
                stop_reason = item["stop_reason"]
        elif itype == "content_block_start":
            idx = _block_index(item)
            block = item.get("content_block") or {}
            if block.get("type") == "tool_use":
                if idx in blocks and blocks[idx].get("id"):
                    # A start frame without an index must never overwrite an
                    # already-opened tool call (that silently drops it). Give
                    # the new call the next free slot instead.
                    idx = max(blocks) + 1
                blocks[idx] = {
                    "id": block.get("id") or "",
                    "name": block.get("name", ""),
                    "partial": "",
                    "input": block.get("input") or {},
                }
                if idx not in order:
                    order.append(idx)
        elif itype == "content_block_delta":
            delta = item.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text_parts.append(delta.get("text") or "")
            elif dtype == "input_json_delta":
                idx = _block_index(item)
                b = blocks.setdefault(idx, {"id": "", "name": "", "partial": "", "input": {}})
                if idx not in order:
                    order.append(idx)
                b["partial"] += delta.get("partial_json") or ""
        elif itype == "message_delta":
            if (item.get("delta") or {}).get("stop_reason"):
                stop_reason = item["delta"]["stop_reason"]

    tool_calls = []
    for idx in sorted(order):
        b = blocks.get(idx) or {}
        if not b.get("name") and not b.get("id"):
            continue
        tool_calls.append({
            "id": b.get("id") or f"toolu_{idx}",
            "name": b.get("name", ""),
            "input": _merge_partial_json(b.get("input") or {}, b.get("partial") or ""),
        })
    return "\n".join(text_parts), tool_calls, stop_reason, saw_sse_payload


def _parse_tool_turn_from_response(resp, provider: str):
    """JSON or SSE body -> (text, tool_calls, stop_reason, parse_ok, body_head).

    Gateways often return unsolicited SSE. Parse failure must not look like
    natural completion.
    """
    raw = resp.text or ""
    body_head = raw[:200]
    if _is_sse_body(resp):
        text, tool_calls, stop_reason, saw_payload = _parse_sse_tool_turn(raw, provider)
        if not saw_payload and not text and not tool_calls and stop_reason is None:
            return "", [], None, False, body_head
        return text, tool_calls, stop_reason, True, body_head

    try:
        data = _parse_response_json(raw)
    except Exception:
        return "", [], None, False, body_head
    if not isinstance(data, dict) or not data:
        return "", [], None, False, body_head
    text, tool_calls, stop_reason = _parse_tool_turn(data, provider)
    return text, tool_calls, stop_reason, True, body_head



# Imported back into llm_tools (loop) and llm (public entry point).
