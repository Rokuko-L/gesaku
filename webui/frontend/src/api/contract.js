/**
 * API CONTRACT — the exact shapes the FastAPI backend will serve.
 *
 * The mock client (client.js) returns objects of these shapes from fixtures.
 * On wiring day, only client.js changes. If a screen needs a field that is
 * not in these types, add it HERE first, then to fixtures, then to screens —
 * and check core/llm.py telemetry + state.json actually produce it.
 *
 * Producers today:
 *  - Project        → projects/<name>/state.json + registry JSONL + word counts
 *  - RunState       → state.json (+ subprocess liveness in RunManager)
 *  - LogLine        → run_pipeline stdout stream (step/banner already emit)
 *  - LlmEvent       → <project>/llm_events.jsonl via core/llm._emit_llm_event
 *  - ScorePoint     → registry JSONL rows written by pipeline_infra.log_result
 *  - Settings       → .env (ANTHROPIC_API_KEY/BASE_URL, GESAKU_*_MODEL)
 */

/**
 * @typedef {Object} Project
 * @property {string} name              // directory name under projects/
 * @property {string} title             // novel title from state.json ("Untitled" ok)
 * @property {string|null} genre        // active_genre.json genre_name
 * @property {"foundation"|"drafting"|"revision"|"export"|"idle"} phase
 * @property {number} foundationScore   // best-so-far foundation score (0 if none)
 * @property {number|null} novelScore   // final whole-novel eval (post-revision)
 * @property {number} revisionCycle
 * @property {number} chaptersTotal
 * @property {number} chaptersDone      // chapters with an accepted draft
 * @property {number} words             // total words across chapter files
 * @property {string|null} updatedAt    // ISO timestamp of last state change
 * @property {boolean} running          // does an active subprocess exist
 */

/**
 * @typedef {Object} RunState
 * @property {string} project
 * @property {Project["phase"]} phase
 * @property {number} iteration         // foundation iteration or chapter number
 * @property {number} foundationScore
 * @property {number} loreScore
 * @property {number} chaptersTotal     // planned chapter count (0 if unset)
 * @property {number} chaptersDone      // chapters drafted so far
 * @property {number} revisionCycle
 * @property {boolean} running          // RunManager liveness (pid probe)
 * @property {number|null} pid
 * @property {string|null} runStartedAt // ISO, when the run was launched
 * @property {number|null} exitCode     // set once the supervised run exits
 */

/**
 * @typedef {Object} LogLine
 * @property {string} ts                // ISO
 * @property {"step"|"banner"|"warn"|"raw"} level
 * @property {string} text
 */

/**
 * Mirrors core/llm.py llm_events.jsonl exactly.
 * @typedef {Object} LlmEvent
 * @property {string} ts
 * @property {"writer"|"judge"|"review"} modelKey
 * @property {string} model
 * @property {boolean} ok
 * @property {number} attempt
 * @property {number|null} tokensIn
 * @property {number|null} tokensOut
 * @property {number} durationMs
 * @property {string|null} stopReason
 * @property {number} promptChars
 * @property {number} responseChars
 * @property {string} promptHead        // first 300 chars
 * @property {string} [error]
 */

/**
 * One keep/discard decision from the registry.
 * @typedef {Object} ScorePoint
 * @property {number} iteration
 * @property {number} score
 * @property {boolean} kept
 * @property {string} phase             // "foundation" | "chapter" | ...
 */

/**
 * @typedef {Object} Settings
 * @property {string} baseUrl           // ANTHROPIC_BASE_URL
 * @property {string} apiKeyMasked      // e.g. "sk-ant-…f4d2"; never the raw key
 * @property {{writer: string, judge: string, review: string}} models
 * @property {{foundation: number, chapter: number}} thresholds  // 0-10 gates
 * @property {{maxChapterAttempts: number, revisionCycles: number, plateauDelta: number}} heuristics
 * @property {{genre: string, chapterCount: number, notes: string}} defaults
 */

/**
 * One drafted chapter.
 * @typedef {Object} Chapter
 * @property {number} num               // 1-based
 * @property {string} id                // "ch_04"
 * @property {string} title             // from the "# Chapter N: Title" heading
 * @property {number} words
 * @property {"kept"|"discarded"|"pending"} status
 * @property {number|null} score        // kept attempt's score (results.tsv)
 * @property {Array<{score: number, words: number, status: "keep"|"discard"}>} attempts
 * @property {string} prose             // full markdown body
 */

/**
 * One evaluation attempt (one eval_logs/<ts>_chNN.json).
 * @typedef {Object} EvalAttempt
 * @property {string} ts                // "20260805_003805"
 * @property {number} overall           // overall_score (post-penalty)
 * @property {number} rawJudge          // raw_judge_score
 * @property {string} weakest           // weakest_dimension summary line
 * @property {number} lengthPenalty
 * @property {number} orientationPenalty
 * @property {number} slopPenalty
 * @property {Object<string, {score: number, weakestMoment: string, fix: string, note: string}>} dims
 * @property {string[]} strongest
 * @property {string[]} weakestSentences
 * @property {string[]} aiPatterns
 * @property {string[]} topRevisions
 * @property {number} newCanon
 * @property {number} unexplained
 */

/** evals.json shape: { ch_01: EvalAttempt[], ... } (chapter-id keyed) */

/**
 * Entity graph (GET /api/entity-graph). Two dialects:
 *  - heuristic (llm:false): nodes {id,label,kind,status,desc,mentions}, edges {from,to,label}
 *  - llm-arranged (llm:true): nodes gain {group, importance 1-10, desc=one-line summary},
 *    edges gain {kind: "ally"|"rival"|"family"|"mentor"|"secret"|"serves"}.
 * POST /api/entity-graph asks the writer model to re-arrange and caches it
 * (projects/<name>/.entity_graph.json).
 */

/**
 * One adversarial cut from edit_logs/chNN_cuts.json.
 * @typedef {Object} Cut
 * @property {string} quote
 * @property {string} type              // OVER-EXPLAIN | REDUNDANT | GENERIC | TELL | ...
 * @property {string} reason
 * @property {"CUT"|"REWRITE"} action
 * @property {string|null} rewrite
 */

/**
 * One revision brief (briefs/chNN_panel.md | chNN_auto.md).
 * @typedef {Object} RevisionBrief
 * @property {number} chapter
 * @property {string} kind              // "panel" | "auto"
 * @property {string} title             // first # heading line
 * @property {string} problem           // ## PROBLEM section body
 * @property {string} keep              // ## WHAT TO KEEP section body
 * @property {string} directives        // ## WHAT TO CHANGE section body
 */

/**
 * One full-novel review pass (edit_logs/<ts>_review.json).
 * @typedef {Object} NovelReview
 * @property {string} ts
 * @property {number} stars
 * @property {string} summary           // critic_summary markdown
 * @property {Array<{number: number, title: string, severity: string, suggestion: string}>} items
 * @property {number} majorItems
 * @property {number} totalItems
 */

/** revision.json shape: { briefs: RevisionBrief[], cuts: Object<string, Cut[]>, reviews: NovelReview[] } */

/**
 * Beats & harvests ledger (GET /api/ledger).
 * @typedef {Object} Ledger
 * @property {Array<{label: string, done: boolean}>} premiseBeats
 * @property {Array<{chapter: number, title: string, beats: string[]}>} roadmap
 * @property {Array<{thread: string, planted: number|null, harvest: number|null, status: "paid off"|"open"}>} threads
 * @property {Array<{thread: string, planted: number|null, harvest: number|null, status: "paid off"|"open"}>} [plannedThreads]
 * @property {Array<{id: string, text: string, kind: string, sourceChapter: number|null, harvestChapter: number|null, status: string}>} [callbacks]
 * @property {number} chaptersTotal
 */

/**
 * One synthesized A/B match (chapters with a discarded + kept attempt).
 * @typedef {Object} TournamentMatch
 * @property {string} id                // "match_001"
 * @property {number} chapter
 * @property {{label: string, score: number, words: number, elo: number, prose: string}} a
 * @property {{label: string, score: number, words: number, elo: number, prose: string, kept: boolean}} b
 */
