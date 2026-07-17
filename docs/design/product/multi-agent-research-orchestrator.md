---
type: product
status: active
last_updated: 2026-07-10
---

# Library Research Brief — Product Design

## The user moment

You have been reading on a topic for weeks or months. You have a specific question
or thesis forming — something you want to think through, write about, or decide on.
You know your library has material. But pulling the right pieces together across
dozens of articles, weighing what you actually engaged with versus what you skimmed,
and identifying where your reading falls short — that takes an hour of manual work.

**The feature:** ask a research question in natural language. The agent decomposes
it, searches your library in parallel across its sub-questions, weights findings
by your reading engagement, synthesizes a structured brief, and tells you what your
library cannot answer so you know what to read next.

Examples of questions this handles:

- "What have I read about AI's effect on knowledge work, and where do the arguments
  diverge?"
- "Synthesize what I know about GLP-1 drugs — both the clinical evidence and the
  cultural commentary."
- "What does my reading say about the tradeoffs between simplicity and features in
  software products?"
- "What are the competing views in my library on AI and labor displacement?"
- "Based on what I've saved on forward-deployed engineering, what's the case for and
  against that career path?"

These are not search queries. They are research questions with structure: multiple
angles, potential tensions, and a meaningful gap between "articles that exist" and
"a synthesized answer."

---

## Why single-shot retrieval fails here

A single `search_content` or `synthesize_topic` quick call fails for three reasons:

**Shape is unknown at the start.** A question like "what are the competing views on
AI and labor?" requires knowing which sub-angles exist before you can search them.
You don't know whether the library covers displacement, augmentation, and geographic
variation until you look. Decomposition has to happen first.

**Coverage requires multiple queries.** The same topic is stored under different
vocabulary — "AI and jobs," "automation," "labor economics," "economic impact of AI."
A single embedding-based search anchored to one phrasing misses material stored
under others. Sub-question-driven parallel search with diverse query formulations
is the fix.

**Synthesis quality depends on depth signal.** Title + description + one highlight
is not enough context to synthesize across 15 articles. The articles the user
highlighted 5 times and drafted from are not equivalent to articles they opened and
closed. Engagement depth has to be a first-class signal in what gets pulled into
the synthesis context — not just what gets retrieved.

---

## What makes this different from Perplexity / Claude Research

Perplexity Deep Research and Claude Research follow the same architecture: decompose
the question, fan out searches across the web, identify gaps, re-search, synthesize.
That architecture is correct and this feature follows it.

The difference is the corpus and the signal layer:

**Personal corpus.** The library is curated by the user. Every article was saved
deliberately. There is no noise from irrelevant web results — but there is also a
real coverage ceiling. The gap report ("your library cannot answer sub-question X")
is a first-class output, not a failure state.

**Engagement signal.** Highlights, re-reads, draft citations are evidence of which
articles the user found worth thinking about — not just worth saving. An article
with 6 highlights and a draft citation is more important to the synthesis than an
article with 0 highlights, even if both are semantically relevant to the query.
This signal layer does not exist in any external corpus tool.

**Personal reading history as context, not just retrieval.** The memory layer
(consolidated nightly from reading activity) seeds the research planning step:
the lead agent knows what topics the user has been focused on, what they have
already synthesized, and what knowledge gaps they have previously identified. This
is not a gimmick — it prevents redundant re-synthesis of topics already covered
and steers sub-question generation toward genuine unknowns.

The combination — Perplexity's workflow discipline, Claude Research's synthesis
quality, applied to a personal corpus with engagement signal — is what makes this
differentiated rather than a re-implementation.

---

## Architecture: lead agent + parallel subagents + verification

Follows the Anthropic multi-agent research architecture
(anthropic.com/engineering/multi-agent-research-system): one lead agent plans and
synthesizes, subagents execute isolated retrieval tasks in parallel, no
subagent-to-subagent communication.

### Step 1 — Planning (lead agent)

Input: the user's research question + memory profile (current_focus,
active_knowledge_gaps, past_synthesis_topics from user_profiles).

Output: 3-6 sub-questions that together constitute a complete answer to the
research question. Each sub-question is independently searchable and independently
assessable for coverage.

Example decomposition of "What have I read about AI and labor displacement?":
- Sub-Q1: What are the empirical claims about job displacement rates and which
  sectors are most affected?
- Sub-Q2: What are the counterarguments — augmentation, new job creation, historical
  analogies to prior automation waves?
- Sub-Q3: What do economists specifically say about timing and policy responses?
- Sub-Q4: What is the cultural and worker-perspective angle — how are people
  experiencing this, not just what the models predict?

The plan is persisted to the state record before any subagent is dispatched.

### Step 2 — Parallel retrieval (subagents)

One Celery task per sub-question, dispatched as a `group()`. Each subagent:

1. Runs hybrid search (semantic + keyword + entity) for its sub-question, generating
   2-3 query variants to catch vocabulary divergence across the library.
2. Fetches full highlights for retrieved articles (not just the most recent one —
   all of them, ranked by count).
3. Computes an engagement score per article: `highlight_count × 2 + is_read × 1 +
   draft_citation_count × 3`. This is not a retrieval re-ranker — it is used at
   synthesis time to decide how much context to allocate per article.
4. Returns: `{sub_question, articles: [{id, title, description, highlights,
   engagement_score}], coverage_assessment: "full" | "partial" | "none"}`.

Coverage assessment is made by the subagent itself, given what it found: "full"
means the sub-question is well-addressed in the library, "partial" means it is
touched but not answered directly, "none" means the library has no relevant
material.

### Step 3 — Gap identification and iteration (lead agent)

After collecting subagent results, the lead agent evaluates:

- Which sub-questions got `coverage: "none"` or `"partial"`?
- Are there sub-questions that emerged from what was retrieved that were not in the
  original plan?

If sub-questions are poorly covered, the lead agent reformulates them with different
vocabulary and dispatches a second round of subagents (hard cap: 3 iterations,
configurable via `budget.max_iterations`). Gap identification drives iteration —
not a generic "find more articles" signal.

### Step 4 — Synthesis (lead agent)

Context construction priorities:

1. Articles with high engagement score get their full highlight set included.
2. Articles with low engagement get title + description only.
3. Total context is token-budgeted (default 6000 tokens, tiktoken-gated).
4. Sub-questions are explicitly labeled in the prompt so the synthesis is structured
   by the research question's shape, not by article order.

Synthesis output (`ResearchBrief`):
```
{
  summary: str,                    # 3-5 sentence overview of what the library says
  sub_question_findings: [         # one entry per sub-question
    {
      sub_question: str,
      coverage: "full" | "partial" | "none",
      finding: str,                # what the library says on this sub-question
      key_sources: [               # articles that substantiate this finding
        {item_id, title, representative_highlight}
      ],
      tensions: [str],             # contradictions found within the library on this sub-Q
    }
  ],
  cross_cutting_tensions: [str],   # contradictions across sub-questions
  gaps: [                          # sub-questions the library cannot answer
    {
      sub_question: str,
      what_is_missing: str,        # what kind of source would fill this
      partial_coverage: [item_id]  # articles that touch it but don't answer it
    }
  ],
  engagement_note: str,            # which sub-questions had high vs low engagement
  confidence: "high" | "medium" | "low"
}
```

### Step 5 — Verification (separate task)

Runs after synthesis as its own Celery task using a cheaper model (gpt-4o-mini).
For each `key_source.item_id` in the brief, verifies the item_id is in the set of
retrieved articles. Citations whose ID is not in the retrieved set are removed
before the brief is returned. This is structural — not prompted.

---

## Gap report as first-class output

The gap report is not a failure state. It is one of the most valuable outputs:

> "Sub-question 'what do economists say about policy responses to displacement?'
> is not answerable from your library. You have 3 articles that mention it
> peripherally (AI and jobs survey, Anthropic economic index, inflation piece)
> but nothing that addresses labor policy specifically. Relevant search terms
> to find this: 'UBI automation', 'labor policy AI', 'automation tax'."

This is what makes the tool useful for someone who reads to produce work. They
know exactly what to find next, with specific search terms derived from what the
library does have.

---

## Engagement signal: how it's used

Engagement signal is a **weighting factor in synthesis context**, not a
retrieval filter. An article with zero highlights is still retrieved if it is
semantically relevant. But at synthesis time, an article with 5 highlights gets
its full highlight set in context; an article with 0 highlights gets title +
description only.

This reflects the real structure of reading behavior: you save things speculatively,
but you highlight things you found genuinely important. The synthesis should reflect
what you actually engaged with, not just what you saved.

Draft citations (articles referenced in reading list drafts) are the strongest
signal — they indicate the user considered the article important enough to cite in
their own writing.

---

## State record and reliability

One Postgres row per run, versioned schema. Every step transition is a write.
See `docs/plans/multi-agent-sota.md` for the full reliability and failure-handling
specification.

Key behaviors:

- Subagent results are merged idempotently — task retry cannot duplicate retrieved IDs.
- Recovery beat task (every 5 minutes) detects orphaned runs (non-terminal status +
  stale updated_at > 10 minutes) and marks them `partial`.
- Budget enforced before every iteration: `iteration_count < max_iterations` and
  `tokens_used < max_tokens`. Breach → `partial` status + synthesize from data so far.
- Rate limit: max 3 active runs per user (non-terminal status count check in a
  single Postgres transaction, immune to race conditions).

---

## Memory integration

The memory layer (consolidated nightly by `consolidate_all_users_task`) contributes
to the planning step:

- `current_focus` — steers sub-question generation toward the user's active topics.
- `active_knowledge_gaps` — previously identified gaps surfaced first; no re-synthesis
  of what the user already knows.
- `past_synthesis_topics` — prevents exact duplicate research runs; if the same
  question was answered 2 weeks ago, the lead agent notes this and surfaces the
  prior result before running again.

Memory does not filter retrieval — all relevant articles are still retrieved.
It informs planning, not access control.

---

## What this is not

- Not a replacement for reading. The brief synthesizes what is in the library;
  it does not evaluate the quality of the underlying sources.
- Not equivalent to Claude Research or Perplexity at their corpus scale. The
  library is personal and curated; coverage is bounded by what the user has saved.
  The gap report makes this explicit rather than obscuring it with fabricated
  coverage.
- Not a writing tool. The brief is research output, not a draft. `assist_draft`
  is the writing tool; this feature feeds into it.

---

## Open questions before implementation

1. **Engagement score formula**: highlight_count × 2 + draft_citation × 3 is a
   starting point. Needs calibration against real usage — is draft_citation actually
   a better signal than highlight_count, or is it too rare to matter?
2. **Sub-question count**: 3-6 is the proposed range. Too few misses angles; too
   many dilutes parallel search. Needs an eval against real questions.
3. **Coverage assessment by subagent**: asking the subagent to self-assess coverage
   is a prompt judgment. Needs an eval rubric to measure calibration — does
   "partial" actually mean partial?
4. **Gap report actionability**: the "relevant search terms" in the gap report are
   LLM-generated. Needs grounding against what the user's library actually doesn't
   have, not generic suggestions.
