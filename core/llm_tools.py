"""Multi-turn tool-calling loop for the LLM client.

Split from `core/llm.py` (module-size rule), and split again from the wire
layer (`core/llm_toolwire.py`), which owns dialect schemas, request building,
and response parsing. This module owns the loop: budget accounting, tool
execution, transcript assembly, the budget-exhausted harvest, and the trace.

Budget-capped by design: the loop stops at `budget` tool calls and reports an
explicit `agent_stop` (`end_turn` | `budget_exhausted` | `max_tokens` |
`error`). It never runs free.
"""

from __future__ import annotations

import json
import sys
import time

import httpx

# Base layer only — importing llm.py here would close the cycle
# (llm.py re-imports these modules).
from core.llm_base import (
    LLM_BACKOFF_BASE_SECONDS,
    LLM_MAX_TRANSPORT_ATTEMPTS,
    _emit_llm_event,
    _resolve_base_url,
    _resolve_model,
    _response_telemetry,
    get_client,
    llm_timeout,
    resolve_provider,
)
from core.llm_toolwire import (  # noqa: F401  (re-exported loop-facing names)
    DEFAULT_TOOL_BUDGET,
    ToolLoopResult,
    _build_tool_request,
    _normalize_tools_for_dialect,
    _parse_sse_tool_turn,
    _parse_tool_turn,
    _parse_tool_turn_from_response,
)


def _post_llm(url, headers, payload, timeout, *, emit_prefix, model_key, model, prompt_chars):
    """Single POST with call_llm-style transport retries. Returns (resp, attempt)."""
    max_retries = LLM_MAX_TRANSPORT_ATTEMPTS
    backoff = LLM_BACKOFF_BASE_SECONDS
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            client = get_client()
            resp = client.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp, attempt
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            _emit_llm_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                      + f".{int(time.perf_counter() * 1000) % 1000:03d}Z",
                "model_key": model_key,
                "model": model,
                "ok": False,
                "attempt": attempt,
                "duration_ms": round((time.perf_counter() - t0) * 1000),
                "error": str(e)[:200],
                "prompt_chars": prompt_chars,
                "loop": emit_prefix,
            })
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in [400, 401, 403, 404]:
                raise e
            if attempt == max_retries:
                raise e
            print(f"API tool-loop call failed (attempt {attempt}/{max_retries}): {e}. Retrying in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 2


def _harvest_messages(msgs, note: str, provider: str) -> list:
    """Append the harvest note without creating consecutive same-role turns.

    The transcript ends in a `user` turn after a budget cut (tool results) or
    with `budget=0` (the original prompt), so a naive extra user message would
    break strict Anthropic-dialect role alternation.
    """
    out = [dict(m) for m in msgs]
    block = {"type": "text", "text": note}
    if out and out[-1].get("role") == "user":
        content = out[-1].get("content")
        if isinstance(content, str):
            out[-1]["content"] = content + note
        elif isinstance(content, list):
            out[-1]["content"] = list(content) + [block]
        else:
            out[-1]["content"] = [block]
        return out
    out.append({"role": "user", "content": note})
    return out


def _harvest_final_text(provider, model, system, msgs, max_tokens, temperature, timeout, *,
                        model_key, beta_context=True):
    """One last tool-free POST to harvest verdict text after budget exhaust."""
    note = (
        "\n\nBUDGET EXHAUSTED: tool budget is spent. Emit your final JSON verdict now "
        "using only evidence already gathered. No further tool calls."
    )
    harvest_msgs = _harvest_messages(msgs, note, provider)
    url_path, headers, payload = _build_tool_request(
        provider, model, system, harvest_msgs, [], max_tokens, temperature, beta_context=beta_context
    )
    base = _resolve_base_url(provider, model_key)
    url = f"{base}{url_path}"
    prompt_chars = len(system or "") + sum(
        len(str(m.get("content", ""))) for m in harvest_msgs
    )
    t0 = time.perf_counter()
    try:
        resp, attempt = _post_llm(
            url, headers, payload, timeout,
            emit_prefix="tool_loop_final", model_key=model_key, model=model,
            prompt_chars=prompt_chars,
        )
        text, _calls, stop, ok, _head = _parse_tool_turn_from_response(resp, provider)
        tin, tout, _sr = _response_telemetry(resp, provider)
        _emit_llm_event({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                  + f".{int(time.perf_counter() * 1000) % 1000:03d}Z",
            "model_key": model_key,
            "model": model,
            "ok": ok,
            "attempt": attempt,
            "tokens_in": tin,
            "tokens_out": tout,
            "duration_ms": round((time.perf_counter() - t0) * 1000),
            "stop_reason": stop,
            "prompt_chars": prompt_chars,
            "response_chars": len(resp.text or ""),
            "loop": "tool_loop_final",
            "tool_calls": 0,
        })
        return text, stop, tin, tout, ok
    except Exception as e:
        print(f"WARN: tool-loop final harvest failed: {e}", file=sys.stderr)
        return "", None, 0, 0, False


def call_llm_tools(
    messages,
    tools,
    executor,
    *,
    system=None,
    model_key="judge",
    max_tokens=8000,
    temperature=0.2,
    beta_context=True,
    timeout=None,
    timeout_role="xlong",
    budget=None,
    on_tool_result=None,
):
    """Multi-turn tool loop. Budget-capped; never free-running.

    executor(name, input_dict) -> str | dict
    budget=None uses DEFAULT_TOOL_BUDGET (12). Hosts should pass
    pipeline_infra.judge_tool_budget() when the named env table matters.
    Transport retries do not reset the tool budget.

    agent_stop: end_turn | budget_exhausted | max_tokens | error
    (leads_exhausted is applied by the continuity judge from its verdict schema.)

    Unparseable / gateway-stripped bodies set agent_stop=error (never end_turn).
    On budget_exhausted the loop attempts one final tool-free harvest POST so
    the caller still gets verdict text when the model complies.
    """
    if budget is None:
        budget = DEFAULT_TOOL_BUDGET
    budget = max(0, int(budget))
    if timeout is None:
        timeout = llm_timeout(timeout_role)

    provider = resolve_provider(model_key)
    model = _resolve_model(provider, model_key)
    base_url = _resolve_base_url(provider, model_key)

    msgs = [dict(m) for m in (messages or [])]
    trace = []
    tool_calls_used = 0
    tokens_in = 0
    tokens_out = 0
    last_text = ""
    last_stop = None
    agent_stop = "end_turn"
    skipped_calls = 0

    def _result(harvest=False):
        nonlocal last_text, last_stop, tokens_in, tokens_out
        if harvest and agent_stop == "budget_exhausted":
            h_text, h_stop, tin, tout, ok = _harvest_final_text(
                provider, model, system, msgs, max_tokens, temperature, timeout,
                model_key=model_key, beta_context=beta_context,
            )
            tokens_in += tin or 0
            tokens_out += tout or 0
            if ok and h_text:
                last_text = h_text
                if h_stop:
                    last_stop = h_stop
        tr = list(trace)
        if skipped_calls:
            tr.append({
                "ts": "meta",
                "tool": "(transcript)",
                "input": {"complete": False, "skipped_tool_calls": skipped_calls},
                "output_chars": 0,
                "ok": True,
                "error": f"transcript_lossy_dropped_{skipped_calls}_tool_calls",
            })
        return ToolLoopResult(
            text=last_text,
            stop_reason=last_stop,
            agent_stop=agent_stop,
            tool_calls_used=tool_calls_used,
            budget=budget,
            trace=tr,
            model=model,
            provider=provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            messages=msgs,
        )

    while True:
        url_path, headers, payload = _build_tool_request(
            provider, model, system, msgs, tools, max_tokens, temperature, beta_context
        )
        url = f"{base_url}{url_path}"
        prompt_chars = len(system or "") + sum(
            len(str(m.get("content", ""))) for m in msgs
        )
        t0 = time.perf_counter()
        try:
            resp, attempt = _post_llm(
                url, headers, payload, timeout,
                emit_prefix="tool_loop", model_key=model_key, model=model,
                prompt_chars=prompt_chars,
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            agent_stop = "error"
            last_stop = "error"
            trace.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "tool": "(transport)",
                "input": {},
                "output_chars": 0,
                "ok": False,
                "error": str(e)[:200],
            })
            print(f"WARN: tool-loop transport failure: {str(e)[:200]}", file=sys.stderr)
            return _result(harvest=False)

        tin, tout, _sr = _response_telemetry(resp, provider)
        if tin:
            tokens_in += tin
        if tout:
            tokens_out += tout
        text, tool_calls, stop_reason, parse_ok, body_head = _parse_tool_turn_from_response(resp, provider)
        last_text = text if text else last_text
        last_stop = stop_reason
        _emit_llm_event({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                  + f".{int(time.perf_counter() * 1000) % 1000:03d}Z",
            "model_key": model_key,
            "model": model,
            "ok": parse_ok,
            "attempt": attempt,
            "tokens_in": tin,
            "tokens_out": tout,
            "duration_ms": round((time.perf_counter() - t0) * 1000),
            "stop_reason": stop_reason,
            "prompt_chars": prompt_chars,
            "response_chars": len(resp.text or ""),
            "loop": "tool_loop",
            "tool_calls": len(tool_calls),
            "tool_calls_used": tool_calls_used,
            "budget": budget,
        })

        if not parse_ok:
            agent_stop = "error"
            last_stop = "error"
            trace.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "tool": "(parse)",
                "input": {},
                "output_chars": 0,
                "ok": False,
                "error": f"unparseable_tool_response: {body_head!r}",
            })
            print(f"WARN: tool-loop unparseable response body: {body_head!r}", file=sys.stderr)
            return _result(harvest=False)

        if stop_reason == "max_tokens" and not tool_calls:
            agent_stop = "max_tokens"
            break

        if not tool_calls:
            if stop_reason in ("tool_use", "tool_calls"):
                agent_stop = "error"
                last_stop = "error"
                trace.append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                    "tool": "(stripped)",
                    "input": {"stop_reason": stop_reason},
                    "output_chars": 0,
                    "ok": False,
                    "error": "stop_reason_tool_use_but_no_tool_calls_parsed",
                })
                print(
                    f"WARN: tool-loop stop_reason={stop_reason} but no tool calls parsed "
                    f"(gateway may strip tools): {body_head!r}",
                    file=sys.stderr,
                )
                return _result(harvest=False)
            agent_stop = "end_turn"
            break

        if tool_calls_used >= budget:
            agent_stop = "budget_exhausted"
            skipped_calls += len(tool_calls)
            trace.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "tool": "(none)",
                "input": {"skipped_tool_calls": [t.get("name") for t in tool_calls]},
                "output_chars": 0,
                "ok": False,
                "error": "budget_exhausted_before_execution",
            })
            break

        executed = []
        skipped = []
        for tc in tool_calls:
            if tool_calls_used >= budget:
                skipped.append(tc)
                continue
            tool_calls_used += 1
            name = tc.get("name") or ""
            t_in = tc.get("input") or {}
            try:
                out = executor(name, t_in)
                if out is None:
                    out_s = ""
                elif isinstance(out, str):
                    out_s = out
                else:
                    out_s = json.dumps(out, ensure_ascii=False)
                ok = True
                err = None
            except Exception as e:
                out_s = f"ERROR: {e}"
                ok = False
                err = str(e)[:200]
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "tool": name,
                "input": t_in,
                "output_chars": len(out_s),
                "ok": ok,
            }
            if err:
                entry["error"] = err
            trace.append(entry)
            if on_tool_result is not None:
                try:
                    on_tool_result(entry, out_s)
                except Exception as cb_e:
                    print(f"WARN: on_tool_result failed: {cb_e}", file=sys.stderr)
            executed.append({"tc": tc, "out_s": out_s})

        if provider == "anthropic":
            assistant_content = []
            if text:
                assistant_content.append({"type": "text", "text": text})
            for ex in executed:
                tc = ex["tc"]
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc.get("input") or {},
                })
            msgs.append({"role": "assistant", "content": assistant_content})
            msgs.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": ex["tc"]["id"],
                    "content": ex["out_s"],
                } for ex in executed],
            })
        else:
            assistant_msg = {"role": "assistant", "content": text or None}
            assistant_msg["tool_calls"] = [{
                "id": ex["tc"]["id"],
                "type": "function",
                "function": {
                    "name": ex["tc"]["name"],
                    "arguments": json.dumps(ex["tc"].get("input") or {}),
                },
            } for ex in executed]
            msgs.append(assistant_msg)
            for ex in executed:
                msgs.append({
                    "role": "tool",
                    "tool_call_id": ex["tc"]["id"],
                    "content": ex["out_s"],
                })

        if skipped:
            skipped_calls += len(skipped)
            trace.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "tool": "(none)",
                "input": {"skipped_tool_calls": [t.get("name") for t in skipped]},
                "output_chars": 0,
                "ok": False,
                "error": "budget_exhausted_mid_turn",
            })

        if tool_calls_used >= budget:
            agent_stop = "budget_exhausted"
            break

    return _result(harvest=True)
