# Prose Guard (`core/prose.py`)

A model can return something that is not a chapter. Two shapes occur in
practice, both found in `projects/sir the confortable v4`:

1. **An appended notes block.** `---` then `**Revision Notes**` (or
   `### Revision Notes`, `## Revision Notes`, `**REVISION NOTES:**`), in which
   the model explains the edits it made. Six of v4's 24 chapters had one.
2. **A mid-chapter derail.** The prose stops and the model starts talking about
   the task: v4's `ch_20.md` reads `…he's staring at the Spawn like it's nest.
   the question says: "What is the role of differential reinforcement in shaping
   behavior?"` and never comes back.

## API

| Function | Answers |
|---|---|
| `cut_reason(text)` | `"notes"`, `"derail"`, or `None` |
| `find_non_prose_start(text)` | index of the cut, or `None` |
| `strip_non_prose(text)` | the prose only, cut at a sentence boundary |
| `prose_words(text)` | words surviving the cut |
| `looks_like_non_prose(text)` | is the surviving prose short — **for reporting** |
| `needs_redraft(text)` | was it *interrupted*, not merely short — **for rejecting** |

`looks_like_non_prose` is a length test (`MIN_CHAPTER_WORDS`). It is the right
question for a warning — an existing chapter that is mostly not prose cannot be
repaired by stripping — but the wrong question for a fresh draft: a deliberate
interlude is legitimately short, and `pipeline/phases/drafting.py` already has an
undershoot path that retries with concrete expansion numbers. Rejection uses
`needs_redraft`, which requires a *detected derail*.

## Detection rules

- The notes qualifier is **required** (`revision|editorial|change|edit`). An
  optional qualifier also matched a bare `Notes` line, which a chapter may
  legitimately contain — and everything after it was silently truncated.
- Derail detection keys on **task machinery**, not prose style: references to
  the user, the prompt, the question, the answer choices, "as an AI". These
  novels are first person, so in-voice deliberation must not trip it.
- Weak reasoning-openers ("let me recall", "I should choose") count only as a
  **dense cluster** — three within 400 characters. A narrator may legitimately
  think one three times across a chapter.
- `_snap_to_sentence` walks the cut back to the end of the previous complete
  sentence, so surviving prose ends cleanly.

## Where it is applied

One guard, every write site. A chapter must not be able to enter a project
through a path that skips it.

| Site | Behaviour |
|---|---|
| `pipeline/draft_chapter.py` | strips; `needs_redraft` → exit 3 so the drafting loop retries |
| `pipeline/gen_revision.py` | strips; `needs_redraft` → exit 3, **never overwrites a good chapter with a fragment** |
| `pipeline/retrofit_reveal.py` | strips; `needs_redraft` → keep the original, return False |
| `pipeline/phases/export.py` | strips `manuscript.md`; warns when a chapter needs a redraft |
| `typeset/build_tex.py` | strips the PDF path too — the typeset book is a deliverable |

## What it does not do

- It does not repair a derailed chapter. Stripping `ch_20.md` leaves ~199 words,
  which is a fragment: the chapter needs a **redraft**, and export says so rather
  than shipping it quietly. For a project written before the guard existed the
  export can only strip.
- It is not a style filter. `pipeline/slop.py` owns prose *style* failure; this
  owns "the output is not prose".
