# LLM Client & JSON Repair (`core/llm.py`)

Every LLM call in the codebase flows through `call_llm()`. This is the
single interception point — `mock_llm.MockLLM` works by rebinding it.

## Providers

`call_llm` speaks two wire dialects and works with any endpoint that
serves either: first-party Anthropic/OpenAI, OpenRouter, Groq, Together,
LiteLLM proxies, DeepSeek's Anthropic-compat endpoint, vLLM/Ollama.

Dialect resolution per role (`writer`/`judge`/`review`):

1. `GESAKU_{ROLE}_PROVIDER` (e.g. `GESAKU_JUDGE_PROVIDER=anthropic`)
2. `GESAKU_PROVIDER` (global default)
3. Inference: `OPENAI_API_KEY` set and `ANTHROPIC_API_KEY` unset → `openai`, else `anthropic`

Invalid values raise `ProviderError` naming the exact env var to fix.

### Wire differences handled internally

| Concern | Anthropic dialect | OpenAI dialect |
|---|---|---|
| Endpoint path | `/v1/messages` | `/chat/completions` |
| Auth | `x-api-key` + `anthropic-version` | `Authorization: Bearer` (only when key set — local gateways reject placeholder keys) |
| System prompt | top-level `system` | `{role: "system"}` message |
| Reasoning-style models (`o*`, `gpt-5*`) | — | `max_completion_tokens`, no `temperature` |
| Stop signal | `stop_reason` | `finish_reason` (`length` → `max_tokens`) |
| `beta_context=True` | 1M-context beta header | ignored (Anthropic-only), loud stderr note |

Responses are normalized to Anthropic vocabulary (`stop_reason`), so
`TruncationError` and every caller retry loop behave identically across
providers. Unsolicited SSE stream bodies are parsed for both chunk shapes.

### Config variables

| Variable | Purpose |
|---|---|
| `GESAKU_PROVIDER` | Global dialect: `anthropic` \| `openai` |
| `GESAKU_{ROLE}_PROVIDER` | Per-role dialect override |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | Anthropic-dialect credentials + endpoint (any compat gateway) |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI-dialect credentials + endpoint (include the `/v1` prefix if the gateway uses one) |
| `GESAKU_{ROLE}_MODEL` | Model id — free-form string, gateway namespacing (`deepseek/deepseek-v4-pro`) works as-is |
| `GESAKU_EXTRA_HEADERS` | JSON object merged into every request (OpenRouter `HTTP-Referer`/`X-Title`, etc.) |
| `GESAKU_LLM_TIMEOUT_{SHORT,STANDARD,LONG,XLONG}` | Per-call HTTP budgets (defaults 120 / 300 / 900 / 1800 s) |

## Timeout Policy

Callers pick a **named budget** instead of inventing a number. The
orchestrator uses the same four names for its subprocess caps
(`pipeline_infra.timeout_for`), so one env var covers a whole stage:

| Role | Default | Used for |
|---|---|---|
| `short` | 120 s | summaries, titles, micro-plant extraction |
| `standard` | 300 s | chapter drafting, revision, judge passes |
| `long` | 900 s | deep reviews, outline roadmap |
| `xlong` | 1800 s | world/character/canon bibles, full-novel evals |

```python
call_llm(prompt, timeout_role="xlong")   # preferred
call_llm(prompt, timeout=42)             # explicit value always wins
```

`timeout=None` (the default) resolves through `llm_timeout(timeout_role)`.
Stage scripts should pass a role; reserve an explicit integer for a genuine
one-off. Hardcoded per-call-site numbers are what made this unconfigurable
before — four separate "raise the timeout" commits during one production run.

## Key Types

| Symbol | Purpose |
|---|---|
| `call_llm(prompt, system, model_key, max_tokens, temperature, beta_context, timeout, timeout_role, raise_on_truncation)` | POST to the resolved provider endpoint with retries (5 attempts, exponential backoff; 4xx auth errors fail fast). |
| `llm_timeout(role)` | Resolve a named budget (`short`/`standard`/`long`/`xlong`), env-overridable. |
| `resolve_provider(model_key)` | Dialect resolution (see precedence above). |
| `DEFAULT_MODELS` | Role → model per provider, used when the env var is unset. |
| `ProviderError` | Missing/invalid provider config; message names the env var. |
| `TruncationError` | Raised when the normalized stop reason is `max_tokens` (unless `raise_on_truncation=False`). Callers retry with larger budgets. |
| `EmptyResponseError` | Raised when a 200 carried no text and was not a truncation. Retried inside `call_llm`; see Retry Conventions. |
| `REFINEMENT_ATTEMPTS` | Writer calls per outline-refinement block. `foundation` derives `gen_outline_part2`'s subprocess cap from it, so the two cannot drift. |
| `extract_text_from_response` / `extract_text_and_stop_reason` | Take `dialect=`; handle JSON, SSE, and dict responses for both shapes. |
| `set_client(client)` | Test seam — install an `httpx.Client` (e.g. `MockTransport`). |
| `parse_json_response(text) -> dict \| list` | **Healing parser** — see below. |

The client is split by concern (module-size rule): `core/llm_base.py` (config,
timeouts, provider/model resolution, client, telemetry, wire parsing),
`core/llm.py` (`call_llm` plus re-exports), `core/llm_tools.py` (the loop) and
`core/llm_toolwire.py` (tool dialect schemas/parsing). `llm.py` imports the
others — never the reverse — so `llm.call_llm_tools` / `llm.ToolLoopResult`
remain the documented entry points and every import order works. Transport retry
policy is named once (`llm_base.LLM_MAX_TRANSPORT_ATTEMPTS` /
`LLM_BACKOFF_BASE_SECONDS`).

| Symbol | Purpose |
|---|---|
| `call_llm_tools(messages, tools, executor, *, budget=None, ...)` | Multi-turn tool loop. `executor(name, input_dict)` is host-owned. Returns `ToolLoopResult`. Budget default **12** (`DEFAULT_TOOL_BUDGET`); pipeline owner `pipeline_infra.judge_tool_budget()` / `GESAKU_JUDGE_TOOL_BUDGET`. Transport retries do not reset budget. `agent_stop`: `end_turn` \| `budget_exhausted` \| `max_tokens` \| `error`. |
| `ToolLoopResult` | `text`, `stop_reason`, `agent_stop`, `tool_calls_used`, `budget`, `trace[]`, tokens, provider/model. |
| `DEFAULT_TOOL_BUDGET` | **12** — committed default for tool loops. |
| `probe_tool_path()` (`pipeline.preflight`) | One real tool call to detect gateways that strip tool blocks. |

## Tool loop (`call_llm_tools`)

Dual-dialect tool calling on the same client:

| Concern | Anthropic | OpenAI |
|---|---|---|
| Schema | `tools[]` name/description/input_schema | `tools[]` type=function |
| Model calls tool | `tool_use` content blocks, `stop_reason=tool_use` | `message.tool_calls`, `finish_reason=tool_calls` |
| Host returns result | `tool_result` in next user message | `role: tool` + `tool_call_id` |

Rules:

- `executor(name, input_dict) -> str|dict` is host-owned and read-only at call sites we ship.
- Budget: `budget=None` → `DEFAULT_TOOL_BUDGET` (12). Pipeline named owner: `pipeline_infra.judge_tool_budget()`.
- Transport retries do **not** reset the tool budget.
- Streamed responses accumulate fragments by block index: Anthropic tool arguments arrive as `input_json_delta` fragments and OpenAI ones as split `tool_calls[].function.arguments` strings. Neither is treated as a whole call.
- When budget is exhausted mid-turn, remaining tool calls are not executed; `agent_stop=budget_exhausted`, and one final tool-free harvest POST asks for the verdict. The harvest note is merged into the pending user turn — consecutive same-role turns are invalid on strict Anthropic-dialect endpoints.
- `agent_stop` is wire-level (`end_turn`/`budget_exhausted`/`max_tokens`/`error`). Continuity maps `leads_exhausted` from its verdict schema.
- Offline mock: `MockLLM().install_tools([{\"text\",\"tool_calls\"}, ...])`.

## The Healing Parser

Lives in `core/json_repair.py` (split out of this module); `llm.parse_json_response`
re-exports it and remains the documented entry point.

LLMs return damaged JSON constantly. `parse_json_response` recovers in layers:

1. Strip ``` fences
2. Locate first `{`/`[` and find its matching close via brace counting
   (ignores braces inside strings)
3. `repair_unescaped_quotes` — escape raw `"` inside string values using
   boundary lookahead (`is_json_boundary`)
4. Insert missing commas, strip trailing commas
5. `fix_truncated_json` — close open strings/braces for truncated output
   (logs a `[WARN] HEALED` to stderr with caller location)

The result is *syntactically* valid JSON. It says nothing about *shape* —
that is `validation.py`'s job.

## Retry Conventions

- Transport failures: handled inside `call_llm`.
- Truncation: callers catch `TruncationError` and re-ask with a larger
  `max_tokens` (see `draft_chapter.py`, `evaluate.call_judge_json`).
- **Empty 200**: a combo-pool gateway intermittently answers `200` with a body
  that never opened a text block — thinking-only, or an empty stream. That is a
  transport fault, not a generation, so `call_llm` raises `EmptyResponseError`
  and retries like one. The test is `not text.strip() and stop_reason !=
  "max_tokens"`: a *normal* stop reason does not make an empty answer valid
  (that false negative killed a `gen_canon` step), and a `max_tokens` stop keeps
  its `TruncationError` semantics so callers still retry with a bigger budget.
- Syntax errors + schema violations: callers catch and feed the error text
  back as a "fix your previous response" prompt (self-correction).

Wire-format behavior is pinned by `tests/test_provider_llm.py`
(httpx.MockTransport asserts actual request shapes and response
normalization per dialect).

Related: [output-validation.md](output-validation.md) ·
[path-resolution.md](path-resolution.md)

## Call Telemetry

Every `call_anthropic` attempt appends one JSON line to the active project's
`llm_events.jsonl` (path helper: `paths.get_llm_events_path()`). Event fields:
`ts, model_key, model, ok, attempt, tokens_in, tokens_out, duration_ms,
stop_reason, prompt_chars, response_chars, prompt_head` — failures add
`error`. This feeds the webui's token stats and LLM inspector; telemetry
failures are swallowed by design and never break generation.

`_response_telemetry(resp, dialect)` is the single owner of token/stop-reason
extraction, and it must read an **SSE stream** as well as a JSON body: gateways
routinely answer with `text/event-stream` even when streaming was not
requested, and the numbers then live inside the frames. Two traps there:

- Take the **last non-zero** usage block, not the first. An Anthropic-shaped
  stream opens with placeholder zeros on `message_start` and reports the real
  totals on the final `message_delta`; recording the first block logs every
  call as 0 tokens (this happened — 663 events, none with usage).
- Accept both key spellings (`input_tokens`/`output_tokens` and
  `prompt_tokens`/`completion_tokens`); gateways mix them.

`prompt_chars` counts the user prompt **plus** the system prompt, since the
latter is prepended and usually dwarfs it. A provider that reports nothing is
recorded as `null` (rendered `—`), never as `0` — see the console's telemetry
tab, which distinguishes "none reported" from a measured zero.
