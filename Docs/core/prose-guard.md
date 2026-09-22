# Prose Guard (`core/prose.py`)

A model can return something that is not a chapter. Three shapes occur in
practice, the first two found in `projects/sir the confortable v4`:

1. **An appended notes block.** `---` then `**Revision Notes**` (or
   `### Revision Notes`, `## Revision Notes`, `**REVISION NOTES:**`), in which
   the model explains the edits it made. Six of v4's 24 chapters had one.
2. **A mid-chapter derail.** The prose stops and the model starts talking about
   the task: v4's `ch_20.md` reads `…he's staring at the Spawn like it's nest.
   the question says: "What is the role of differential reinforcement in shaping
   behavior?"` and never comes back.
3. **Artifacts inside otherwise-valid prose.** The chapter is fine, but the
   writer left marks in it: v5 shipped `## BEAT 1..4` headings in ch11/ch12,
   `**Beat 2**`/`**Beat 3**` in ch17, and four Chinese words (`毛巾`, `实际上`,
   `长期`, `仪表盘`) across ch2/ch11/ch12/ch24, because it emitted them and
   nothing looked.

Shapes 1 and 2 are *cut* — the chapter is truncated at the derail. Shape 3 is
*removed in place* by `strip_artifacts`: the prose around a stray word is sound,
so the word goes and the chapter stays.

## API

| Function | Answers |
|---|---|
| `cut_reason(text)` | `"notes"`, `"derail"`, or `None` |
| `find_non_prose_start(text)` | index of the cut, or `None` |
| `strip_non_prose(text)` | the prose only, cut at a sentence boundary |
| `strip_artifacts(text)` | `(clean_text, removed)` — headings, script slips and U+FFFD dropped, with a list of what went |
| `looks_like_heading_marker(line)` | is this line a structural label the writer invented |
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
- Artifact rules — all deliberately **narrow**, because these words are also
  ordinary English:
  - `looks_like_heading_marker` is a line-level decision, not a regex, and the
    bar is "cannot be read as a sentence". A line counts only when it opens
    with `beat` or `scene` plus a number, carries at most six words, ends
    without sentence punctuation, and either stops at the number (`## BEAT 1`)
    or continues only through a title separator (`## Beat 3 — Morning`).
    `Part 2 of my plan was to wait.`, `Scene 4 was the worst of them.` and
    `**Beat 3 was the hardest**` are prose and stay. A false negative leaves a
    visible heading; a false positive deletes a paragraph, and that asymmetry
    sets the bar. `part` is deliberately **not** a marker word: `# Part II` is
    a structural heading, and deleting one to catch the other is the wrong
    trade.
  - The **chapter heading always survives**, wherever it sits: a draft whose
    line 1 is `## BEAT 1` keeps the `# Chapter 5: Title` below it, and a
    chapter with no heading at all keeps its first line of body text. A marker
    that merely happens to be first (no title anywhere) is also kept — for a
    fresh draft it is indistinguishable from a real part divider, and keeping
    one heading is the safe side of that asymmetry.
  - `_FOREIGN_SCRIPT` covers CJK ideographs and kana, Hangul, Cyrillic, Arabic,
    Hebrew, Devanagari and Thai — a stray word in another writing system is a
    translation slip, never prose. Polish diacritics and curly punctuation pass
    through. **CJK punctuation (U+3000–U+303F) and fullwidth forms are NOT
    stripped**: `projects/NewFakeSaint` quotes dialogue as `「…」`, and eating a
    punctuation system to catch a stray word is a worse trade than the word.
    Removal is *deletion*, so a replaced word is not invented; where a slip sat
    mid-sentence the result can be ungrammatical, which is why the report is
    logged. Only the whitespace immediately around each hole is repaired
    (`_remove_and_tidy`) — one stray word must not license a whole-text pass.
  - Removing a marker swallows **one** adjacent blank line; it never collapses
    intentional scene gaps elsewhere in the chapter.
  - `strip_artifacts` returns `(text, removed)` and does not print — the call
    sites log the report.

## Where it is applied

One guard, every write site. A chapter must not be able to enter a project
through a path that skips it.

| Site | Behaviour |
|---|---|
| `pipeline/draft_chapter.py` | strips prose + artifacts; `needs_redraft` → exit 3 so the drafting loop retries |
| `pipeline/gen_revision.py` | strips prose + artifacts; `needs_redraft` → exit 3, **never overwrites a good chapter with a fragment** |
| `pipeline/retrofit_reveal.py` | strips prose + artifacts; `needs_redraft` → keep the original, return False |
| `pipeline/phases/export.py` | strips `manuscript.md`; warns when a chapter needs a redraft |
| `typeset/build_tex.py` | strips the PDF path too — the typeset book is a deliverable |

The artifact pass sits at the *save* sites rather than only at export: a
chapter is scored, revised and revised against as a file, so a heading left in
the file is a heading the judges read and the revisers preserve.

## What it does not do

- It does not repair a derailed chapter. Stripping `ch_20.md` leaves ~199 words,
  which is a fragment: the chapter needs a **redraft**, and export says so rather
  than shipping it quietly. For a project written before the guard existed the
  export can only strip.
- It is not a style filter. `pipeline/slop.py` owns prose *style* failure; this
  owns "the output is not prose".
