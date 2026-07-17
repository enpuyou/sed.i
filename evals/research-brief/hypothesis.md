# Hypothesis: Research Brief Agent

## Evaluating

The Library Research Brief feature: a multi-agent loop that decomposes a research
question into sub-questions, retrieves evidence per sub-question using hybrid search
with engagement weighting, identifies gaps where the library cannot answer, and
synthesizes a structured brief.

## Variants

- **A (baseline)**: single-pass `synthesize_topic` — one hybrid search, title +
  description + one highlight per article, no sub-question decomposition, no gap
  report.
- **B (research brief v1)**: lead agent decomposes into sub-questions, parallel
  subagent retrieval with all highlights, engagement weighting in context
  construction, structured brief with gap report.
- **C (research brief v1 + memory seeding)**: same as B, but planning step seeded
  with `current_focus` and `active_knowledge_gaps` from the user memory profile.

## Hypothesis

> B will outperform A on sub-question coverage and gap identification because
> decomposition surfaces angles that a single embedding query anchored to the
> original question misses. C will produce more focused sub-question generation
> than B for questions that overlap the user's documented interests, because memory
> seeding steers the planning step toward known unknowns rather than re-deriving
> them from the question alone.

Falsified by: B and A produce equivalent sub-question coverage on the pilot set,
suggesting that decomposition adds no retrieval breadth beyond what a single
well-formed query achieves.

## Success cases (B expected to beat A)

1. Questions with vocabulary divergence across the library — where the same topic
   is tagged/titled differently in different articles (e.g. "AI and work" stored
   as "cognitive fatigue", "labor displacement", "AI intensifies work").
2. Questions with genuine multi-angle structure — where the right answer requires
   finding both supporting and contradicting material (e.g. "competing views on
   GLP-1 drugs").
3. Questions where the library has partial coverage — gap report should correctly
   identify unanswerable sub-questions rather than fabricating coverage.

## Failure cases (A should not regress)

1. Single-angle factual lookups — "what does my library say about the STAR interview
   method?" — A's single search should be sufficient; B should not add noise.
2. Very narrow topics with one or two articles — decomposition should not hallucinate
   sub-questions that have no library coverage.
