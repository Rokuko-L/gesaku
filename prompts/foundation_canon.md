Extract baseline canon facts from the seed concept, world bible, and character registry below. These facts are true from the start of the story but have not yet been revealed to any reader. Output structured facts only. Do not repeat world-building verbatim; extract only what a writer needs to track as immutable truth: character relationships, hidden backstory, magic system rules, faction alignments, secret histories, key locations, and their significance.

REVEAL SCHEDULING (mandatory):
Every bullet MUST carry a `visible_from=N` tag — the first chapter at which the fact may appear as *established* knowledge to the writer/judge (not necessarily stated aloud by characters).

- `visible_from=1` for world rules, geography, public relationships, and anything the reader can know from page one.
- `visible_from=N` (N > 1) for secrets, hidden identities, mask/red-herring roles, and any twist whose premature leakage would spoil the story. Choose N as the chapter where that truth is meant to land (or shortly before the climax reveal if the exact chapter is uncertain).
- The novel has approximately {total_chapters} chapters. Put major twists in the latter half unless the seed says otherwise.

Prefer more `visible_from=1` facts than sealed ones. Seal only what a drafting model must not know early. For sealed facts, write the *truth* plainly (the pipeline will withhold it) — do not coyly hide the wording inside the fact itself.

Bullet format (exact):
- visible_from=1: <fact>
- visible_from=N: <fact>

Seed: {seed}

World Bible:
{world}

Character Registry:
{characters}

Output only the bullet list of canon facts. No preamble, no markdown headers.
