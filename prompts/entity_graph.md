Below are a novel's character registry, world bible, and canon log.

Build the entity graph a reader would want to explore.

Rules:
- nodes: every NAMED character with a real role; include factions/locations only when they act like agents in the story. Skip trivia.
- group: a short lowercase faction/arc label that clusters allies (e.g. "royal court", "maledictus conspiracy", "beastfolk"). Reuse the same label for members of the same cluster.
- importance: 1-10 narrative weight (protagonist 9-10, minor named role 1-3).
- summary: one line (max 140 chars) describing who they are and what they want.
- edges: only meaningful relationships (max ~4 per node). kind is exactly one of: ally, rival, family, mentor, secret, serves.
- label: 3-8 words describing the relationship ("hides his resurrection from", "sworn shield of").
- every edge's source/target MUST be a node name, verbatim.
- the CANON log holds facts established while drafting — relationships there may
  have evolved past the registry (betrayals, deaths, new alliances). Canon wins.

Answer with JSON only, shape:
{{"nodes": [{{"name": "...", "group": "...", "importance": 7, "summary": "..."}}],
  "edges": [{{"source": "...", "target": "...", "kind": "ally", "label": "..."}}]}}

=== CHARACTER REGISTRY ===
{characters}

=== WORLD BIBLE ===
{world}

=== CANON (established while drafting) ===
{canon}
