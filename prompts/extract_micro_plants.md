You extract optional callback candidates from a kept novel chapter.

CHAPTER {chapter} excerpt:

{excerpt}

OPEN CALLBACKS already tracked (id — description; planted earlier):
{open_list}

TASK:
1. Identify at most 1 NEW concrete detail in THIS chapter that a later chapter
   could pay off: a specific object, distinctive phrase, promise, injury, or
   small staged situation. It must be dramatized on the page, not merely
   mentioned in passing. Ordinary props (a cup, a door) are NOT candidates.
   Prefer details with sensory weight or stakes. Return [] if nothing qualifies.
2. If any OPEN CALLBACK above is paid off in this chapter with a changed
   meaning or use (not a name-drop), list its id under harvested_ids.
   Do not mark something harvested just because the object appears again.

Return JSON only:
{{
  "new_plants": [{{"text": "…", "kind": "object|phrase|promise|injury|situation"}}],
  "harvested_ids": ["id", …]
}}
