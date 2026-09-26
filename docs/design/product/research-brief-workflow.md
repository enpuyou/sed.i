---
type: product
status: active
last_updated: 2026-07-14
---

# Library Research Brief — Agent Workflow

How the multi-agent research pipeline works, end to end. Covers every step from
question submission to delivered brief, with real examples from the eval library.

---

## Table of contents

1. [The user moment](#1-the-user-moment)
2. [Step 1 — Planning](#2-step-1--planning)
3. [Step 2 — Parallel retrieval](#3-step-2--parallel-retrieval)
4. [Step 3 — Relevance filter + article summarization](#4-step-3--relevance-filter--article-summarization)
5. [Step 4 — Collection and iteration decision](#5-step-4--collection-and-iteration-decision)
6. [Step 5 — Synthesis](#6-step-5--synthesis)
7. [Step 6 — Verification and gap injection](#7-step-6--verification-and-gap-injection)
8. [Real examples](#8-real-examples)
9. [What makes this different from a single search](#9-what-makes-this-different-from-a-single-search)
10. [Budget and reliability](#10-budget-and-reliability)

---

## 1. The user moment

You have been reading on a topic for weeks. A thesis is forming — something you
want to think through, write, or decide on. You know your library has material.
But pulling the right pieces together, weighing what you actually engaged with
versus what you skimmed, and identifying where your reading falls short takes an
hour of manual work.

Research Brief is: ask the question in natural language, get back a structured
synthesis of what your library says, with every claim traceable to a specific
article and an honest gap report telling you what to read next.

Questions it handles:

- "What are the competing views on whether AI displaces or augments workers, and
  what does my library say about each?"
- "What does my library say about the reliability problems with LLMs, and how does
  that square with the case for using them?"
- "Synthesize what I know about GLP-1 drugs — both the clinical evidence and the
  cultural commentary."
- "What does my library say about simplicity as a design value in software?"
- "What have I read about AI's effect on how much mental work people have to do —
  does it reduce effort or create new kinds of burden?"

These are not search queries. They have structure: multiple angles, potential
tensions, a gap between "articles that exist" and "a synthesized answer."

---

## 2. Step 1 — Planning

**One LLM call. ~2 seconds.**

The lead agent receives:
- The user's research question
- A list of library article titles (for calibration only)
- The user's memory profile: `current_focus`, `active_knowledge_gaps`,
  `past_synthesis_topics`

It produces a list of 2-6 sub-questions that together constitute a complete answer.

**Calibration rule (from the prompt):**
- Fewer than 3 library titles look directly relevant → 2-3 sub-questions
- 3-6 titles look relevant → 3-4 sub-questions
- 6+ titles look relevant → 4-5 sub-questions

The titles determine quantity, not content. Sub-questions derive from the
question's structure.

**Example: `ai_labor_displacement`**

Question: *"What are the competing views on whether AI displaces or augments
workers, and what does my library say about each?"*

Planning step sees ~12 relevant titles (Anthropic Economic Index, Notes on AI
Labor and China, AI Doesn't Reduce Work—It Intensifies It, etc.) and produces:

```
[
  "What is the empirical evidence for AI displacing jobs, and which sectors are most affected?",
  "What is the counterargument — AI augmenting workers or creating new roles?",
  "What is the worker/human experience angle — how are people experiencing AI-driven workload change?",
  "What do the articles say about automation timing and economic impact?"
]
```

Note: sub-question 3 ("worker experience") is derived from the question's implicit
angle ("what does my library say about each"), not from a specific article title.
The planner is not anchoring to titles.

**Example: `llm_reliability_vs_utility`**

Question: *"What does my library say about the reliability problems with LLMs,
and how does that square with the case for using them?"*

Planning step sees 3 directly relevant titles and produces:

```
[
  "What are the reliability and failure mode arguments against LLMs in my library?",
  "What is the case for LLM utility despite reliability concerns?",
  "What specific behaviors or patterns make LLMs unreliable?"
]
```

The decomposition correctly identifies this as a TENSION question — it generates
both sides of the debate as sub-questions.

**Example: `alzheimers_biomarkers`** (SINGLE_ANGLE, thin library)

Question: *"What does my library say about early detection biomarkers for
Alzheimer's disease?"*

Planning step sees 1 directly relevant title and produces:

```
[
  "What biomarkers or tests are discussed in the library for early Alzheimer's detection?",
  "What evidence or caveats does the library give about the reliability of these methods?"
]
```

2 sub-questions because only 1 relevant article exists. No padding.

---

## 3. Step 2 — Parallel retrieval

**One Celery task per sub-question, dispatched as a `group()`. Run concurrently.**

Each subagent task receives: `(run_id, sub_question)`. It has no knowledge of
the other sub-questions or what other subagents are finding.

### 3a. Multi-query expansion

Before searching, the subagent generates 2 alternative phrasings of its
sub-question. This bridges vocabulary gaps: the same topic may be stored under
different terms depending on what the user was reading when they saved an article.

```
sub_question: "What is the counterargument — AI augmenting workers or creating new roles?"

→ expansion call (1 LLM call, 128 tokens max):
  alt_1: "AI creating new jobs automation augmentation workforce"
  alt_2: "artificial intelligence productivity boost employment growth"
```

All 3 queries run through hybrid search (semantic + keyword + entity graph).
Results are unioned by article ID — no article appears twice. This is not
re-ranking; it is breadth expansion at retrieval time.

**Why this matters:** An article titled "Management as AI Superpower" about
managers using AI tools to handle more work might never surface for a query about
"job displacement" — different vocabulary cluster — but will surface for "AI
productivity boost." Without expansion, that article is invisible to that
sub-question.

### 3b. Hybrid search per query

Each of the 3 query variants runs through `hybrid_search(mode="full")`:
- Semantic: pgvector cosine similarity on 1536-dim article embeddings
- Keyword: Postgres tsvector full-text search
- Entity: entity graph expansion (articles connected through shared entities)
- RRF fusion of all three signals

Default: top 10 results per query variant, deduped, up to 30 candidates total.

---

## 4. Step 3 — Relevance filter + article summarization

**Two LLM calls per subagent: one filter, one summary per relevant article.**

### 4a. Chunk fallback for thin descriptions

Before the relevance filter, the subagent checks each candidate article's
description length. If `len(description) < 60` (scraper artifact, empty field),
it fetches the first text chunk from `content_chunks` and uses that as the
snippet instead.

Without this: the relevance filter sees `"Pluralistic: LLMs are slot-machines |
(no description)"` and cannot make a judgment — likely excludes it. With it, the
filter sees the first 120 characters of actual article text.

18% of the library has thin descriptions. Key articles are frequently affected
because they come from personal blogs or Substack newsletters that don't have
structured metadata.

### 4b. Relevance filter

The subagent sends up to 15 candidates to an LLM filter:

```
Sub-question: "What are the reliability and failure mode arguments against LLMs?"

Candidate articles:
  a1b2c3 | Pluralistic: LLMs are slot-machines | "LLMs produce outputs that
          feel fluent but are structurally random under the hood. The slot-machine
          metaphor captures how..."
  d4e5f6 | The Year in Slop | "AI-generated content flooded the web in 2024.
          Here's what changed..."
  g7h8i9 | What Is Claude? Anthropic Doesn't Know, Either | "Anthropic's
          uncertainty about what Claude actually is..."
  j0k1l2 | Management as AI Superpower | "Managers who treat AI as an extension..."
```

The filter returns: `["a1b2c3", "d4e5f6", "g7h8i9"]` — excluding the management
article as not relevant to reliability failures.

The filter is told to bridge vocabulary gaps explicitly: "an article on 'context
engineering' may directly address a question about 'cognitive load'."

### 4c. Article summarization

For each relevant article, the subagent fetches:
- Top-4 text chunks (by cosine similarity to the sub-question embedding)
- All user highlights (passages they marked while reading)

Then generates a 2-3 sentence focused summary: what does this article specifically
contribute to this sub-question? The summary must name claims, data points, or
arguments — not just topics.

Example output for `Pluralistic: LLMs are slot-machines` on the reliability sub-Q:

> "The article argues that LLMs produce outputs that feel fluent but are
> statistically random at the token level — the 'slot-machine' metaphor
> describes how surface coherence masks structural unreliability. The author
> distinguishes between tasks where this doesn't matter (low-stakes drafting)
> and tasks where it's catastrophic (factual claims, legal or medical advice).
> This is a direct argument against deploying LLMs in high-reliability contexts."

### 4d. Coverage assessment

After summarization, the subagent self-assesses coverage:
- `full` — sub-question is well-addressed; multiple relevant articles with direct
  claims
- `partial` — sub-question is touched but not answered directly; tangential
  coverage only
- `none` — no relevant articles found after filtering

The coverage assessment is stored and used at synthesis time to decide whether
to report a gap.

**Subagent output:**

```json
{
  "sub_question": "What are the reliability and failure mode arguments against LLMs?",
  "coverage_assessment": "full",
  "item_ids": ["a1b2c3", "d4e5f6", "g7h8i9"],
  "articles": [
    {
      "id": "a1b2c3",
      "title": "Pluralistic: LLMs are slot-machines",
      "summary": "The article argues that LLMs produce...",
      "highlights": ["LLMs are not reliable narrators of fact...", "..."],
      "engagement_score": 14
    },
    ...
  ]
}
```

Engagement score: `highlight_count × 2 + is_read × 1 + draft_citation_count × 3`.
Not used for retrieval — used at synthesis time to decide how much context to
allocate per article.

---

## 5. Step 4 — Collection and iteration decision

**`collect_subagent_results` — the chord callback.**

After all subagents complete, the lead agent receives their results and decides:

1. **Synthesize now** if any of these are true:
   - All sub-questions have `coverage: full` or `partial`
   - Iteration budget exhausted (`iteration_count >= max_iterations`)
   - Token budget near limit

2. **Iterate** if:
   - One or more sub-questions have `coverage: none`
   - Budget allows another iteration

On iteration, the lead agent reformulates the `coverage: none` sub-questions
with different vocabulary and dispatches a second round of subagents for those
sub-questions only. Already-retrieved articles are not re-retrieved.

**Example: reformulation**

Round 1 sub-question: "What do economists say about timing and policy responses
to AI job displacement?"
Coverage: `none` — no relevant articles found.

Round 2 reformulation: "Labor policy automation safety net UBI workforce
transition" — tries different vocabulary cluster.

If round 2 still returns `none`, the gap report will flag this sub-question.

---

## 6. Step 5 — Synthesis

**One LLM call. The most expensive step.**

Context is constructed per sub-question, with engagement-weighted article depth:

- **High engagement articles** (`engagement_score ≥ 6`): get full highlights +
  top-4 chunks + article summary in context
- **Low engagement articles** (`engagement_score < 6`): get title + description
  + article summary only

Total context is token-budgeted (6000 tokens). If budget is tight, low-engagement
articles are truncated first.

The synthesis prompt asks the model to produce a `ResearchBrief`:

```json
{
  "summary": "2-4 sentences on what the library collectively says. ...",
  "sub_question_findings": [
    {
      "sub_question": "What are the reliability and failure mode arguments...",
      "coverage_assessment": "full",
      "finding": "The library converges on a structural critique of LLM reliability...",
      "key_sources": [
        {
          "item_id": "a1b2c3",
          "title": "Pluralistic: LLMs are slot-machines",
          "representative_highlight": "LLMs are not reliable narrators of fact..."
        }
      ],
      "tensions": []
    }
  ],
  "cross_cutting_tensions": [
    "The library simultaneously argues for LLM utility (Anthropic's 'What Is Claude?')
     and against reliability (Pluralistic) — these positions are not reconciled."
  ],
  "gaps": [
    {
      "sub_question": "What do economists say about timing and policy responses...",
      "what_is_missing": "Policy analysis of labor transition mechanisms...",
      "partial_coverage": ["uuid-of-anthropic-economic-index"]
    }
  ],
  "engagement_note": "Pluralistic and What Is Claude? both have 5+ highlights...",
  "confidence": "high"
}
```

The synthesis prompt forbids citing general knowledge ("do not draw on general
knowledge — base every claim on a specific excerpt or article summary shown").
It requires synthesis over listing ("The library converges on X" beats "Article A
says X, Article B says Y").

---

## 7. Step 6 — Verification and gap injection

**Separate Celery task, runs after synthesis.**

### 7a. Citation verification

For every `key_source.item_id` in the brief, the verifier checks that the ID
appears in the run's `item_ids_retrieved` set. IDs not in the retrieved set are
removed from `key_sources`. This is structural — not prompted.

This prevents the model from citing articles it might know about from parametric
knowledge but that were never actually in the retrieval results.

### 7b. Partial coverage cleanup

`partial_coverage` arrays in `gaps` entries are filtered to only include IDs
in the retrieved set. Same logic — structural, not prompted.

### 7c. Fabricated gap stripping

If a sub-question has `coverage_assessment: full` or `coverage_assessment: partial`
in the subagent results, any gap entry for that sub-question is removed.

**Why this matters:** The LLM synthesizer sometimes generates gap entries from
parametric knowledge. "There must be other AI safety organizations beyond what I
found" appears in `gaps` even when 6 relevant articles were retrieved with
`coverage: full`. This is hallucination — the model knows there are more
organizations in the world, so it reports a gap. The verifier removes it.

Only sub-questions with `coverage: none` in the subagent results can legitimately
have gap entries.

### 7d. Gap injection for missed entries

If a sub-question has `coverage: none` in the subagent results but no
corresponding entry in `gaps` (the synthesizer forgot to include it), the
verifier injects a gap entry:

```json
{
  "sub_question": "What do economists say about timing and policy responses?",
  "what_is_missing": "No articles in the library directly address: What do
    economists say about timing and policy responses? A source specifically
    covering this angle would be needed to answer it.",
  "partial_coverage": []
}
```

After verification, status → `done`. The brief is returned to the frontend.

---

## 8. Real examples

### Example A: multi-angle question with full coverage

**Question:** *"What does my library say about the reliability problems with
LLMs, and how does that square with the case for using them?"*

**Planning output:**
1. What are the reliability and failure mode arguments against LLMs?
2. What is the case for LLM utility despite reliability concerns?
3. What specific behaviors or patterns make LLMs unreliable?

**Subagent results (after expansion + filtering):**

| Sub-question | Coverage | Key articles found |
|---|---|---|
| Reliability failures | full | Pluralistic: LLMs are slot-machines, The Year in Slop |
| Case for utility | full | What Is Claude? Anthropic Doesn't Know, Either, Focus areas for The Anthropic Institute |
| Specific failure patterns | full | Pluralistic (slot-machine mechanism), The Year in Slop (slop patterns) |

**Brief (selected):**

*Summary:* The library holds a clear tension: it documents both why LLMs are
structurally unreliable (slot-machine token generation, slop proliferation) and
why they are worth using anyway (the Anthropic case for frontier AI, Claude's
documented capabilities). The case for use does not refute the reliability
critiques — it contextualizes them.

*Tensions:* "Pluralistic argues LLMs are structurally unreliable at the token
level. What Is Claude? acknowledges uncertainty about Claude's nature while
maintaining it is worth building. These are not reconciled — they represent
different framings of the same underlying uncertainty."

*Gaps:* none (all sub-questions fully covered).

**Score (C variant):** 0.975. Near-ceiling performance. This is what good looks
like: both sides of the TENSION found, named explicitly, cross-cutting tension
articulated, zero false gaps.

---

### Example B: vocabulary divergence question

**Question:** *"What have I read about AI's effect on how much mental work people
have to do — does it reduce effort or create new kinds of burden?"*

The challenge: the library has relevant articles but they don't use the phrase
"cognitive load" or "mental effort." They're tagged: "context engineering,"
"AI superpower," "AI and labor," "management."

**Planning output:**
1. How has AI been reported to reduce mental work in various industries?
2. In what ways does AI implementation introduce new kinds of mental burdens?
3. What trends have been observed in mental effort required due to AI?
4. Are there case studies illustrating AI reducing or increasing mental workload?

**Multi-query expansion (sub-question 2):**
- alt_1: "AI cognitive burden new tasks mental overhead"
- alt_2: "artificial intelligence attention demands worker stress oversight"

The expansion on alt_2 retrieves "Effective context engineering for AI agents" —
which argues that managing AI agents requires a new kind of cognitive work (context
window management, prompt engineering, supervision). This article never surfaces
for the literal phrase "mental burden."

**Subagent results:**

| Sub-question | Coverage | Key articles |
|---|---|---|
| AI reduces mental work | partial | Management as AI Superpower, Notes on AI Labor and China |
| New mental burdens | partial | AI Doesn't Reduce Work—It Intensifies It, Effective context engineering |
| Trends in mental effort | partial | What 81,000 people told us (economic survey data) |
| Case studies | none | — |

**Brief (selected):**

*Summary:* The library presents a consistent counternarrative to the
"AI reduces work" framing. Three articles converge on the view that AI shifts
mental effort rather than eliminating it — management roles expand, oversight
tasks multiply, and prompt engineering becomes a new cognitive burden.

*Gaps:* Case studies showing concrete before/after comparisons of mental workload
were not found. "Specific industries or job roles" data would require longitudinal
workplace studies, which are not in the library.

**Score (C variant):** 0.800. VOCAB_DIVERGE case where multi-query expansion
and chunk fallback were the difference — both key articles (`AI Doesn't Reduce
Work`, `Pluralistic`) had thin descriptions and would have been filtered out
without the chunk fallback.

---

### Example C: partial coverage with a real gap

**Question:** *"What are the competing views on whether AI displaces or augments
workers?"*

**Planning output:**
1. What is the empirical evidence for AI displacing jobs?
2. What is the counterargument — AI augmenting workers or creating new roles?
3. What is the worker/human experience angle?
4. What do the articles say about automation timing and economic impact?

**Subagent results:**

| Sub-question | Coverage | Key articles |
|---|---|---|
| Displacement evidence | full | Anthropic Economic Index, Notes on AI Labor and China |
| Augmentation argument | full | Management as AI Superpower, AI Doesn't Reduce Work—It Intensifies It |
| Worker experience | partial | AI Doesn't Reduce Work (worker perspective section) |
| Policy and timing | none | — |

**Iteration (round 2 for sub-Q 4):**

Reformulated: "Labor policy automation safety net workforce transition UBI"
Result: `none` — library has no policy analysis articles.

**Gap report:**

> "Sub-question 'What do the articles say about automation timing and policy
> responses?' is not answerable from your library. You have articles that describe
> economic impact (Anthropic Economic Index) but nothing that addresses labor
> policy specifically. To fill this gap, look for: 'UBI automation policy',
> 'labor transition automation fund', or 'Daron Acemoglu automation labor policy'."

**Score (C variant):** 0.750. Gap accurately reported. Displacement and
augmentation both found. Worker experience partially covered (correctly flagged
as partial). The one miss: gap_accuracy 1/5 in this run because the gap injection
missed the policy sub-question — synthesis marked it as partial from the Economic
Index article even though that article doesn't address policy.

---

### Example D: thin library, single-angle

**Question:** *"What does my library say about early detection biomarkers for
Alzheimer's disease?"*

Library has exactly one directly relevant article: "Blood test holds promise for
predicting when Alzheimer's symptoms will appear."

**Planning output (2 sub-questions because only 1 relevant title):**
1. What biomarkers or tests are discussed for early Alzheimer's detection?
2. What evidence or caveats are given about the reliability of these methods?

**Subagent results:**

| Sub-question | Coverage | Key articles |
|---|---|---|
| Biomarkers/tests described | full | Blood test holds promise for predicting... |
| Evidence and caveats | partial | Same article (caveats mentioned but not central) |

**Brief (selected):**

*Summary:* The library has one article on Alzheimer's early detection: a blood
test for phosphorylated tau protein that predicts symptom onset within a 3-year
window. The evidence is from a single longitudinal study; the article notes the
test is not yet in clinical use.

*Gaps:* none reported (correct — all sub-questions answered from the one article,
even if partially).

**Score (C variant):** 0.800. SINGLE_ANGLE handled correctly: no hallucinated
sub-questions, no false gaps, brief stays narrow. The `synthesis_quality` scored
1/5 (only one article — no synthesis possible), which is correct and expected.

---

## 9. What makes this different from a single search

| | Single search (`synthesize_topic`) | Research Brief |
|---|---|---|
| Sub-question decomposition | No — one query | Yes — 2-6 sub-questions |
| Vocabulary bridging | No — one embedding query | Yes — 3 query variants per sub-Q |
| Thin description handling | No — empty descriptions excluded | Yes — chunk fallback |
| Gap report | No | Yes — per sub-question |
| Fabrication prevention | Citation filtering only | Citation filtering + gap stripping |
| Engagement weighting | Flat | High-engagement articles get more context |
| Tension surfacing | No | Cross-cutting tensions explicitly identified |
| Multi-angle questions | One angle | All angles as separate sub-questions |

**Where single search still wins:**
Single-angle factual lookups with precise vocabulary ("what does my library say
about the STAR interview method?") don't benefit from decomposition. The
single-pass approach is faster and doesn't risk scope drift from sub-question
generation. For these cases, the Research Brief's planner should generate 2
sub-questions and stay narrow — when it does (SINGLE_ANGLE cases), scores are
comparable to A baseline (0.725-0.800 range).

---

## 10. Budget and reliability

**Budget (per run):**

| Parameter | Default | What it controls |
|---|---|---|
| `max_iterations` | 3 | Maximum retrieval rounds before forced synthesis |
| `max_subagents` | 6 | Maximum concurrent subagent tasks per iteration |
| `max_tokens` | 50000 | Total token budget across all LLM calls |
| `timeout_s` | 300 | Wall-clock timeout before orphan recovery |
| `target_count` | 8 | Target articles per sub-question before proceeding |

**State machine:**

```
queued → planning → searching → collecting → synthesizing → verifying → done
                                                                      ↘ partial (budget exhausted or timeout)
```

Every status transition is a Postgres write. If a subagent task crashes, the chord
callback doesn't fire — but the recovery beat task (runs every 5 minutes) detects
runs stuck in `searching` for > 10 minutes and marks them `partial`, triggering
synthesis from whatever data exists.

**Concurrency limit:** 3 active runs per user enforced in a single Postgres
transaction (select count + insert — immune to race conditions).

**Idempotency:** `collect_subagent_results` deduplicates retrieved article IDs
across rounds. A retried subagent task cannot add duplicate articles to the
retrieved set.
