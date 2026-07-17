# Eval: memory-consolidation-prompt
Date: 2026-07-10
Status: In Progress

## Evaluating
The memory consolidation prompt in `app/tasks/memory.py` — specifically whether
the prompt wording and output schema produce profiles that are useful for a
personal reading assistant: specific, trajectory-oriented, behaviorally grounded,
and consistent across runs.

## Variants

**A — Baseline (old prompt)**
Single `_CONSOLIDATION_PROMPT`. Structured output: `current_focus` (str),
`reading_velocity` (enum), `knowledge_gaps` (list), `episodic_events` (list).
Fixed 7-day lookback window. No inline highlight text in activity string.
No distinction between "saved" and "read" signals.
No instructions about trajectory, depth asymmetry, or backlog patterns.

**B — New prompt (prescriptive dimensions)**
Split `_BOOTSTRAP_PROMPT` / `_DELTA_PROMPT`. Hybrid output: `current_focus`,
`reading_velocity`, `memory_text` (free prose). Activity string includes inline
highlights per article, explicit "saved but never opened" section, reading list
names. Prompt explicitly instructs coverage of four dimensions: trajectory, depth
asymmetry, behavioral pattern, unread backlog signal. Bootstrap window from
earliest actual activity (up to 30 days).

**C — Alternative (briefing framing)**
Same hybrid output schema as B. Same richer activity string as B. Different
prompt framing: instead of listing four explicit dimensions, instructs the model
to "write a briefing note from one assistant to another — include only what would
genuinely help a future assistant give better responses to this user." No dimension
checklist. Tests whether emergent prioritization outperforms prescribed structure.

## Hypothesis

> Variant B will score higher than A on specificity and trajectory because the
> explicit dimension checklist forces coverage of patterns (depth asymmetry,
> backlog signal) that the model would otherwise skip.
>
> Variant C will score comparably to B on trajectory and higher than A, but may
> score lower than B on consistency — the open-ended briefing frame gives the
> model more latitude, which helps unusual user shapes but may miss dimensions
> for typical users.
>
> Both B and C will outperform A on the "no hallucination" guard rail because the
> richer activity string (inline highlights, separated save/read signals) gives
> the model concrete evidence to ground claims.

## Falsified by
- B scores ≤ A on specificity across ≥ 6 of 10 cases → dimension checklist
  does not help
- C scores lower than A on trajectory → briefing framing actively confuses the
  model
- Any variant scores > 0.3 on hallucination for cases with sparse data → prompts
  still invite fabrication under thin signal

## Success cases (B and C should clearly improve over A)
1. **Heavy saver, never reads** — A will likely list topics; B/C should name the
   "collecting without consuming" behavioral pattern
2. **Technical depth + news skimmer** — A collapses to one velocity label; B/C
   should name the asymmetry explicitly
3. **Reading list with intent title** — A ignores list names; B/C receive list
   names in activity string and should incorporate the explicit intent signal

## Failure cases (must NOT regress vs A)
1. `current_focus` must remain specific (not generic "artificial intelligence")
   across all variants
2. `reading_velocity` must be inferred from read% + highlight count, not topics
3. Profiles must not name specific articles or claim facts not present in the
   activity string (faithfulness guard rail)
