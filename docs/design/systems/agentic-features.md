---
type: system
status: active
last_updated: 2026-07-17
---

# sed.i — Agentic Features Reference

Factual account of every LLM-driven and agent-pattern feature currently in production.
No proposals. Where a feature is partial or has a known gap, that is noted explicitly.

Related docs:
- `docs/design/product/research-brief-workflow.md` — user-facing walkthrough of the research pipeline
- `docs/design/systems/graphrag-multiagent-research.md` — research doc: SOTA patterns and future proposals
- `docs/design/systems/mcp-wiki.md` — MCP transport, OAuth, tool reference
- `evals/research-brief/results/report.md` — eval results for the research brief pipeline

---

## Table of contents

1. [Feature inventory](#1-feature-inventory)
2. [Research Brief — multi-agent pipeline](#2-research-brief--multi-agent-pipeline)
3. [MCP tools and skills](#3-mcp-tools-and-skills)
4. [Memory consolidation](#4-memory-consolidation)
5. [Ingestion pipeline — LLM enrichment](#5-ingestion-pipeline--llm-enrichment)
6. [Entity extraction and graph](#6-entity-extraction-and-graph)
7. [LLMOps instrumentation](#7-llmops-instrumentation)
8. [Eval infrastructure](#8-eval-infrastructure)
9. [Known gaps](#9-known-gaps)

---

## 1. Feature inventory

| Feature | Location | Pattern | Status |
|---|---|---|---|
| Research Brief | `app/tasks/research.py` | Lead agent + parallel subagents + verification | Shipped |
| Research memory | `app/tasks/research_memory.py` | Post-synthesis extraction + pgvector retrieval at planning time | Shipped |
| MCP tool server | `app/mcp/` | Tool-use surface for external agents | Shipped |
| MCP Skills | `app/mcp/skills.py` | Instruction sequences for MCP callers | Shipped (3 skills) |
| Memory consolidation | `app/tasks/memory.py` | Nightly profile update + research gap distillation | Shipped |
| Article analysis | `app/tasks/article_analysis.py` | Single-pass LLM enrichment | Shipped |
| Tagging | `app/tasks/tagging.py` | Single-pass structured chat | Shipped |
| Entity extraction | `app/tasks/entity_extraction.py` | Single-pass structured chat | Shipped |
| Entity deduplication | `app/tasks/entity_dedup.py` | LLM-assisted dedup | Shipped |
| Highlight connections | `app/core/hybrid_search.py` | Embedding similarity + LLM insight | Shipped |
| Synthesis (MCP) | `app/mcp/tools/synthesis.py` | Single-pass + draft assist | Shipped |
| Cluster summaries | `app/tasks/clustering.py` | LLM summary per reading cluster | Shipped |

---

## 2. Research Brief — multi-agent pipeline

**Entry point:** `app/tasks/research.py`
**Celery task:** `run_research_lead.delay(run_id)`
**State store:** `ResearchRun` model in Postgres

### Architecture

Matches Anthropic's published lead-agent + parallel-subagents + verification pattern.

```
run_research_lead()
  │
  ├─ 1. broad hybrid_search (15 results) → library_titles for calibration
  │
  ├─ 2. LLM planning call → SubQuestionPlan (2-6 sub-questions)
  │      model: gpt-4o / TASK_RESEARCH_PLANNING
  │      messages: [system: static rules + schema hint] [user: question + library_titles + prior_context]
  │
  ├─ 3. group() of run_research_subagent tasks (one per sub-question, parallel)
  │      │
  │      ├─ query expansion (gpt-4o-mini) → 2 alternative phrasings
  │      ├─ hybrid_search × 3 queries → union by article ID
  │      ├─ relevance filter (gpt-4o-mini) → CoT reasoning → relevant_ids[]
  │      ├─ chunk retrieval (pgvector embedding similarity)
  │      └─ article summary (gpt-4o-mini) → 2-3 sentences scoped to sub-question
  │         grounding constraint: only from provided excerpts, not parametric memory
  │
  └─ 4. chord callback: collect_subagent_results()
         │
         ├─ merge item_ids_retrieved, subagent_results, increment iteration_count
         ├─ decide: synthesize OR iterate
         │   synthesize when: all covered OR hit target_count OR hit max_iterations OR no new articles
         │   iterate when: some sub-questions still coverage=none AND iterations remain
         │
         ├─ on iterate → run_research_lead(resume=True)
         │   planner receives prior_context: which sub-questions returned coverage=none
         │   reformulates those sub-questions with different vocabulary / narrower scope
         │
         └─ on synthesize → synthesize_run() → verify_synthesis()
```

### State persistence between Celery tasks

All state lives in `ResearchRun` (Postgres), not in worker memory. Each step reads
what the previous step wrote. Workers are stateless.

| Field | Written by | Read by |
|---|---|---|
| `sub_questions` | `run_research_lead` (planning) | `collect_subagent_results`, `synthesize_run` |
| `searches_run` | `run_research_lead` (idempotency keys) | `run_research_lead` (dedup on resume) |
| `subagent_results` | `collect_subagent_results` | `synthesize_run`, `verify_synthesis` |
| `item_ids_retrieved` | `collect_subagent_results` | `verify_synthesis` |
| `iteration_count` | `collect_subagent_results` | `collect_subagent_results` (max check) |
| `result` | `synthesize_run` | `verify_synthesis`, API |
| `status` | every step | all steps (guard clauses) |

### Synthesis and verification

`synthesize_run()` calls `gpt-4o` with `TASK_RESEARCH_SYNTHESIS`. The synthesis prompt
(system message) instructs:
- Ground every claim in provided excerpts only — no parametric knowledge
- State tensions explicitly: name the specific articles on each side
- Gaps only for sub-questions with `coverage: none`

`verify_synthesis()` runs post-synthesis with no LLM call:
- Strips citations not in `item_ids_retrieved` (fabrication removal)
- Strips gap entries for sub-questions with `coverage_assessment: full` or `partial`
- Injects a gap entry for any `coverage: none` sub-question missing from `gaps[]`
- Logs: `citations_removed`, `gaps_stripped`, `gaps_injected`, `final_gap_count`

### Budget and reliability

```python
_DEFAULT_BUDGET = {
    "max_tokens": 50000,
    "max_iterations": 3,
    "max_subagents": 6,
    "timeout_s": 300,
    "target_count": 8,
}
```

Provider failover: `llm_client` retries on the other provider (OpenAI ↔ Bedrock)
if the primary call fails. `recover_orphaned_runs()` is a Celery beat task that
marks stale non-terminal runs as `partial`.

### Prompt architecture (as of 2026-07-17)

All 5 prompts are `(system, user_template)` tuples. System message carries static
instructions; user message carries only dynamic content. This enables OpenAI's
automatic prefix caching (50% discount on repeated system-message tokens).

| Prompt | System content | User content |
|---|---|---|
| `_PLANNING_PROMPT` | Decomposition rules, count calibration, schema hint | question + library_titles + prior_context |
| `_QUERY_EXPANSION_PROMPT` | Vocabulary bridging rules, schema hint | sub_question |
| `_RELEVANCE_FILTER_PROMPT` | 5-step CoT reasoning, schema hint | sub_question + candidates |
| `_ARTICLE_SUMMARY_PROMPT` | Grounding constraint, format rules | sub_question + title + chunks + highlights |
| `_SYNTHESIS_PROMPT` | Grounding rules, synthesis quality rules, tension naming, schema | question + per_sq_context |

### What is and isn't a true agentic loop

**Is**: the planning → parallel search → collect → iterate/synthesize cycle is a
real multi-agent pattern with persistent state and a machine-checkable exit condition
(`iteration_count >= max_iterations` or `len(item_ids_retrieved) >= target_count`).

**Is not**: a true ReAct loop. On resume, the planner now receives which sub-questions
failed and reformulates them — but it does not observe tool outputs mid-execution and
decide which tool to call next. The pipeline structure is fixed; only the sub-question
wording changes on retry.

**Is not**: tool-use in the LLM API sense. All LLM calls use `structured_chat` (schema
in prompt, instructor validates). The model does not choose which tool to call — the
pipeline calls the model at fixed points.

### Cross-run memory

After each completed run, `extract_research_memory` (fire-and-forget Celery task,
`app/tasks/research_memory.py`) writes one `ResearchMemory` row per sub-question
to the `research_memory` table. Each row holds the sub-question embedding (1536-dim),
coverage quality (`full`/`partial`/`none`), a 1-2 sentence topic summary (for
full/partial coverage), or a gap description (for none).

At the start of each new `run_research_lead` (first iteration only), the planner
embeds the question, queries `research_memory` by cosine similarity (threshold 0.75,
top-5, 90-day window via pgvector IVFFlat), and prepends a formatted "Past research
context" block to `prior_context` in the planning user message. The planner uses this
to avoid re-generating sub-questions for already-answered topics and to reformulate
sub-questions that previously found no coverage.

The nightly `consolidate_memory` task also reads recurring `none`-coverage entries
from `research_memory` and distills them into `UserProfile.persistent_gaps`.

---

## 3. MCP tools and skills

**Entry point:** `app/mcp/server.py`, `app/mcp/tools/`
**Transport:** Streamable HTTP at `/mcp-transport`
**Auth:** OAuth 2.1 + PKCE; access token = sed.i JWT

### Deployed tools

| Tool | Purpose |
|---|---|
| `list_lists()` | Lists user's reading lists |
| `get_list_content(list_id)` | Articles in a list |
| `get_content_item(item_id)` | Single article with full text option |
| `search_content(query)` | Hybrid search across library |
| `find_similar(item_id)` | Semantic neighbors of an article |
| `get_highlights(content_item_ids?)` | User's annotations |
| `get_reading_stats()` | Read counts, recency, activity |
| `add_content(url)` | Save a URL to library |
| `add_to_list(item_id, list_id)` | Add article to a list |
| `create_list(name)` | Create a reading list |
| `summarize_list(list_id)` | LLM summary of a list (cached) |
| `get_draft(list_id)` | Read current draft for a list |
| `update_draft(list_id, content)` | Append to draft |
| `query_library(question)` | Natural language query → synthesis |
| `synthesize_topic(topic, depth)` | MCP-side synthesis (quick/deep) |
| `assist_draft(list_id, instruction)` | Draft a paragraph from highlights |

### MCP Skills

`app/mcp/skills.py` defines named instruction sequences for external agents.
Registered as an MCP resource so only the relevant skill is loaded per request.

| Skill | Status | What it sequences |
|---|---|---|
| `weekly-digest` | Usable | `get_reading_stats` → `list_lists` → `summarize_list` per list → `search_content` on focus → synthesize |
| `draft-from-highlights` | Usable | `get_draft` → `search_content` → `get_highlights` → draft → `update_draft` |
| `connect-new-save` | Exploratory / not production | `get_content_item` → `find_similar` → `get_highlights` → synthesize connection |

`connect-new-save` is explicitly marked in the code as not providing distinct value
over existing `ConnectionsPanel` + `find_similar`. It is not surfaced in the UI.

### What MCP provides vs the REST API

The REST API is a machine-to-machine contract: callers must know exact endpoints,
parameters, and response shapes. MCP tools carry natural-language descriptions;
an LLM reads the descriptions to decide which tool to call and how to combine them.
This means an external agent (Claude Desktop, Claude.ai) can compose tool calls
for tasks not explicitly programmed — e.g., using `search_content` + `get_highlights`
+ `assist_draft` in sequence to respond to "write a paragraph on what I've read
about X using my own annotations."

---

## 4. Memory consolidation

**Entry point:** `app/tasks/memory.py`
**Celery beat:** nightly scheduled task
**Pattern:** Anthropic Dreaming — scheduled background review between sessions

The task reviews recent user activity (reads, highlights, saves) and calls
`llm_client.structured_chat()` to produce or update a `UserProfile` row with three
fields: `current_focus` (specific sub-domain), `reading_velocity` (fast/deep/browsing),
and `memory_text` (3-6 sentence prose briefing).

As of 2026-07-17, the task also reads `research_memory` for recurring `none`-coverage
sub-questions since the last consolidation. These are injected as a `research_gaps_section`
into the delta consolidation prompt, and the LLM writes a `persistent_gaps` field on
`UserProfile` summarizing systematic topics the library cannot answer.

Two prompts:
- **Bootstrap** (`_BOOTSTRAP_PROMPT`): first-run or never-consolidated user; derives
  profile from scratch from up to 30 days of activity.
- **Delta** (`_DELTA_PROMPT`): subsequent runs; merges new activity delta onto the
  existing profile. Includes `research_gaps_section` when gaps exist.

The `UserProfile` is not yet read by the research planner at planning time. The
current cross-run signal path is `research_memory` → planner (see section 2). The
`persistent_gaps` field is available for future use as a higher-level planning hint.

---

## 5. Ingestion pipeline — LLM enrichment

Every saved article passes through a Celery chain after extraction:

```
URL save
  → extraction (readability / Jina / PDF backend)
  → embedding (text-embedding-3-small)
  → chunk_embeddings (structure-aware ~350-token chunks)
  → tagging (gpt-4o-mini, TASK_TAGGING) → 4-6 specific tags
  → article_analysis (gpt-4o, TASK_ARTICLE_ANALYSIS) → summary, themes, reading level
  → entity_extraction (gpt-4o-mini, TASK_ENTITY_EXTRACTION) → named entities + types
  → entity_embedding → entity vectors in pgvector
  → entity_dedup (gpt-4o-mini, TASK_ENTITY_DEDUP) → merge near-duplicate entities
```

All LLM calls use `llm_client.structured_chat()` with Pydantic response models.
Tagging uses instructor for retry-on-validation-error. Article analysis and entity
extraction use the same pattern.

---

## 6. Entity extraction and graph

**Entry points:** `app/tasks/entity_extraction.py`, `app/core/entity_graph.py`

Named entities (people, organizations, concepts, places) are extracted per article
and stored in `entities` + `article_entities` tables with pgvector embeddings.
Entity graph search (`_entity_search` in `hybrid_search.py`) expands retrieval:
a query for "Geoffrey Hinton" surfaces articles mentioning him even if the query
term doesn't appear verbatim.

Entity deduplication runs as a separate Celery task (`entity_dedup.py`): LLM
compares near-neighbor entity pairs (by embedding similarity) and merges duplicates.

The entity graph feeds into `hybrid_search(mode="full")` which is used by the
research brief pipeline, so entity-bridged retrieval is active in every subagent.

---

## 7. LLMOps instrumentation

### Braintrust (LLM observability)

Active when `BRAINTRUST_API_KEY` is set. `_make_openai_client()` wraps the OpenAI
client with `braintrust.wrap_openai()` — all OpenAI API calls are automatically
traced as child spans.

Five granular `braintrust_span` context managers wrap each research pipeline step,
grouping child LLM spans by logical step in the trace UI:

| Span name | Task constant | Step |
|---|---|---|
| `planning` | `TASK_RESEARCH_PLANNING` | Sub-question decomposition |
| `query_expansion` | `TASK_RESEARCH_EXPANSION` | Alternative query generation |
| `relevance_filter` | `TASK_RESEARCH_FILTER` | LLM relevance scoring |
| `article_summary` | `TASK_RESEARCH_SUMMARY` | Per-article summarization |
| `synthesis` | `TASK_RESEARCH_SYNTHESIS` | Brief generation |

A `verification` span logs post-synthesis metadata as output:
`{citations_removed, gaps_stripped, gaps_injected, final_gap_count}`.

**Known gap:** Bedrock calls are not traced in Braintrust. They appear only in
OTEL task-level spans. Bedrock prompt-level observability is deferred.

### OTEL → Grafana

Celery task-level spans only. No LLM call internals.

### Sentry

Error capture only. No performance tracing at the LLM level.

---

## 8. Eval infrastructure

### Evals in `tests/evals/` (pytest-based, CI-gated)

| Suite | Cases | Primary metric | Hard-fail threshold |
|---|---|---|---|
| Search routing | 17 queries | Classification accuracy | 100% |
| Search quality | 32 queries | Recall@10, MRR | R@10 ≥ 0.75, MRR ≥ 0.60 |
| Tagging quality | 10 articles | Specificity, count, domain match | No forbidden tags |
| MCP behavioral | Test DB | Response shape, user isolation | Zero contract violations |

### Evals in `evals/` (script-based, manual)

**Research Brief G-Eval** (`evals/research-brief/`):
- 21-case dataset, hand-labeled against the real dev library
- Rubric v2: 6 dimensions, weights summing to 1.0, PASS_THRESHOLD = 0.70
- Judge: `gpt-4o-mini` (calibrated against `gpt-4o` — see `results/judge_calibration.json`)
- Baseline (variant C): avg = 0.744, stored in `evals/research-brief/baselines.json`
- Soft-fail: CI posts a PR comment but does not block merge on LLM-judge scores

**Retrieval eval** (`evals/retrieval/`):
- Full dataset, R@10 / MRR / NDCG@10
- Hard-fail gated in CI

### Rubric history

| Version | Key change | Effect |
|---|---|---|
| v1 | `sub_question_coverage` matched generated sub-questions against fixed expected list | Penalized valid briefs with different decompositions; B_old avg 0.515 < A avg 0.605 |
| v2 | Replaced with `question_fidelity` (judges against `core_intent`) + `useful_expansion` | C avg 0.744, +0.139 vs A |
| v2.1 | `gap_accuracy` CoT: STOP rule moved to steps 1-2, cant-answer/can-answer distinction sharpened | Fixed `gpt-4o-mini` judge misreading `library_can_answer` as gaps |

### Judge calibration

`gpt-4o-mini` was validated as a drop-in for `gpt-4o` on 5 of 6 rubric dimensions.
`gap_accuracy` required a rubric fix (STOP guard at the top of CoT steps) before
mini agreed with gpt-4o. After the fix, both models scored identically on all 3
calibration cases. Cost saving: ~15× per eval run (~$0.45 → ~$0.03 per 21-case pilot).

---

## 9. Known gaps

| Gap | Area | Notes |
|---|---|---|
| No true ReAct loop | Research Brief | Resume reformulates sub-questions but doesn't observe tool outputs mid-flight and dynamically choose next action |
| No tool-use API calls | All LLM features | All calls use `structured_chat`; model fills a fixed schema, doesn't choose tools |
| Bedrock not traced in Braintrust | LLMOps | Bedrock calls only in OTEL task-level spans; no prompt-level detail |
| `connect-new-save` skill not in UI | MCP Skills | Produces narrative in Claude conversation only; not surfaced as a product feature |
| Entity extraction eval prose-only | Evals | `docs/design/systems/entity-extraction-eval.md` exists but no runnable harness or regression baseline |
| Memory consolidation eval incomplete | Evals | `evals/memory-consolidation-prompt/` directory exists, no runner or baselines |
| `weekly-digest` not server-side | MCP Skills | Only runs in a Claude MCP conversation; not a scheduled Celery task surfaced in the UI |
