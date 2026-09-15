# Prompt Management (`prompts/`)

Static prompt templates live in `prompts/*.md`, loaded via
`paths.load_prompt(name)` (cached per process).

## Current Templates

| File | Consumer |
|---|---|
| `evaluate_full_novel.md` | `pipeline/evaluate.py --full` |
| `compare_chapters.md` | `pipeline/compare_chapters.py` |
| `adversarial_edit.md` | `pipeline/adversarial_edit.py` |
| `review_manuscript.md` | `pipeline/review.py` |
| `sanitize_titles_rewriter.md` | `pipeline/sanitize_outline_titles.py` (default; genre config may override) |
| `foundation_canon.md` | `foundation/gen_canon.py` |
| `retrofit_reveal.md` | `pipeline/retrofit_reveal.py` |
| `gen_novel_tex_system.md` + `gen_novel_tex_template.md` | `foundation/gen_novel_tex.py` |
| `genre_framework_system.md` | `foundation/gen_genre_framework.py` |

## Conventions

1. Templates keep `{placeholder}` and `{{escaped}}` braces exactly as the
   inline literals they replaced — `.format()` / `format_prompt()` call
   sites are unchanged by extraction.
2. New static prompts go here, not inline. Function-local f-string prompts
   may stay local until they stabilize, then extract.
3. **Do not confuse with pipeline fuel** in `fuel/` at repo root (`CRAFT.md`,
   `ANTI-SLOP.md`, `voice.md`, `MYSTERY.md`, `notes_template.md`). Those are
   runtime *data* consumed from fixed root paths — never move or edit them
   as if they were docs.

Related: [../core/path-resolution.md](../core/path-resolution.md)
