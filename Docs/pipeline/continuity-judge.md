# Continuity Judge (closed + open passes)

Two-pass continuity checking for kept chapters. Plan of record:
`Docs/reference/agentic-upgrades.md`.

## Closed pass — `pipeline/continuity_closed.py`

Deterministic, no LLM. Writes `eval_logs/continuity_chNN_closed.json`.
Runs fail-soft from `on_chapter_kept`.

| Kind | Trust | Notes |
|---|---|---|
| `seal_leak` | high | Multi-word sealed terms / sealed fact phrases only; always-on frames are not leaks; empty sealed foundation → no findings. Tagged `author_only`. |
| `unknown_entity` | **low** | Noisy by design — warn-only recall list |
| `location` | low | Place-like tokens missing from world bible |
| `canon` | medium | Conservative negation vs public canon; false negatives OK |
| `plant` | medium | `[Plant:]` / `[Harvest:]` tags leaked into prose — scanned through `core.outline.scan_plant_tag_marks` (the tag-format owner), not a local regex |

**`severity: structural` is reserved and unused by gates today.**

## Open pass — `pipeline/continuity_open.py`

Tool-using judge via `core.llm.call_llm_tools`. Models live in
`core.validation` (`ContinuityVerdict` / `ContinuityFinding`).

- Budget: `pipeline_infra.judge_tool_budget()` / `GESAKU_JUDGE_TOOL_BUDGET`
  (default **12**). Hosts **must** pass this into `call_llm_tools`.
- Tools (read-only): `search_canon`, `read_chapter`, `search_prior_chapters`,
  `read_outline_chapter`, `search_characters`, `search_world`
- Findings A are **prompt exclusion**, not hard-enforced
- `overlap_a_tool_targets`: structured match on A chapter ids (equality) +
  distinctive claim tokens in tool queries — not JSON-dump substrings
- `agent_stop`: `leads_exhausted` | `budget_exhausted` | `end_turn` | `error` | `max_tokens`
  - `budget_exhausted` → substrate harvests one final tool-free verdict POST
  - unparseable / stripped / transport → `error`; CLI still writes a sidecar
- Sealed-derived findings are `author_only`; `merged` is author-scope audit
  data, **not writer fuel**
- Output: `eval_logs/continuity_chNN_open.json` (atomic). The `_open` suffix
  keeps it out of the `*_chNN.json` eval-score glob; a bare `continuity_chNN.json`
  would shadow the real eval sidecar the same way the pre-rename retrieval file did.
- Hook: closed pass on keep; **open pass CLI-only until Phase 6**

### Stability probe

`--stability` is a **single-run snapshot** (not a re-run matrix). Correlate
`agent_stop` with `overlap_a_tool_targets` manually:
budget_exhausted + high overlap → re-deriving A; budget_exhausted + low
overlap → budget too tight; leads_exhausted variance → prompt/threshold.

## Preflight

`probe_tool_path()`: one `noop_tool` call (SSE-aware). WARN by default;
`GESAKU_REQUIRE_TOOLS=1` makes broken **or crashing** probe fatal.

## Gates

Warn-only. Do not block drafting/keep on continuity findings.
