# Eval: Library Research Brief

Evaluates the multi-agent research brief feature against a baseline
(single-pass `synthesize_topic`) on 21 questions grounded in the dev library.

## What is being evaluated

Three variants:

| Variant | Description |
| --- | --- |
| A | Baseline: single `synthesize_topic` quick call. One search, title+description+one highlight. No decomposition, no gap report. |
| B | Research Brief v1: lead agent decomposes into sub-questions, parallel subagent retrieval with all highlights, engagement weighting, structured brief with gap report. |
| C | Research Brief v1 + memory: same as B, planning step seeded with `current_focus` and `active_knowledge_gaps` from consolidated user profile. |

## Dataset

`dataset/pilot.py` — 20 cases across 6 structural categories:

| Category | Count | What it tests |
| --- | --- | --- |
| MULTI_ANGLE | 4 | Both sides of a question must be retrieved |
| VOCAB_DIVERGE | 3 | Same topic stored under different tags/titles |
| PARTIAL_COVER | 5 | Library answers some sub-questions but not others |
| SINGLE_ANGLE | 2 | Tight question; decomposition should stay focused |
| TENSION | 4 | Library has contradictory takes that must be named |
| ENGAGEMENT_BIAS | 3 | High-highlight articles should dominate the brief |

Each case provides:

- `expected_sub_qs` — human-labeled sub-questions a good decomposition should produce
- `answerable_from_library` / `unanswerable_sub_qs` — indices into expected_sub_qs
- `key_article_titles` — articles that must appear in the brief
- `must_not_fabricate` — specific claims that should not appear (library has nothing)
- `ideal_coverage` — expected coverage level ("full" | "partial" | "thin")

## Rubric

`rubric.py` — G-Eval, 5 dimensions:

| Dimension | Weight | What it measures |
| --- | --- | --- |
| sub_question_coverage | 0.30 | Did the brief address the right sub-questions? |
| source_grounding | 0.25 | Are claims tied to real retrieved articles? |
| gap_accuracy | 0.20 | Does the gap report correctly identify unanswerable sub-Qs? |
| synthesis_quality | 0.15 | Does the brief synthesize, not just list? |
| tension_surfacing | 0.10 | Are contradictions named explicitly? |

Pass threshold: weighted score >= 0.70.

## What needs to be built before this eval can run

### 1. The feature itself (Variant B)

The following backend components must exist:

- `app/models/research.py` — `ResearchRun` model
- `alembic/versions/NNN_add_research_runs.py` — migration
- `app/tasks/research.py` — `run_research_lead`, `run_research_subagent`,
  `collect_subagent_results`, `synthesize_run`, `verify_synthesis`
- `app/api/research.py` — `GET /research/{run_id}` status endpoint

See `docs/plans/multi-agent-orchestrator-impl.md` for the step-by-step build plan.

### 2. Engagement scoring in subagents

Subagents must fetch all highlights (not just the most recent) and compute:

```python
engagement_score = highlight_count * 2 + is_read * 1 + draft_citation_count * 3
```

This score must be passed into `_build_context` so high-engagement articles
receive their full highlight set in the synthesis context.

### 3. Full-text / full-highlight retrieval in subagents

Current `synthesize_topic` fetches `title + description + one highlight (ORDER BY
created_at DESC)`. Subagents need `include_full_text=False` but must fetch all
highlights for each article, not just the most recent one.

### 4. Structured `ResearchBrief` output schema

The runner needs the agent to return a `ResearchBrief` Pydantic model:

```python
class SubQuestionFinding(BaseModel):
    sub_question: str
    coverage: Literal["full", "partial", "none"]
    finding: str
    key_sources: list[SourceCitation]
    tensions: list[str]

class GapItem(BaseModel):
    sub_question: str
    what_is_missing: str
    partial_coverage: list[str]  # item_ids

class ResearchBrief(BaseModel):
    summary: str
    sub_question_findings: list[SubQuestionFinding]
    cross_cutting_tensions: list[str]
    gaps: list[GapItem]
    engagement_note: str
    confidence: Literal["high", "medium", "low"]
```

### 5. `runner.py` and `scorer.py`

These are stubbed in the directory but not yet implemented. Once the feature
is built, `runner.py` should:

1. Load the 20 pilot cases from `dataset/pilot.py`
2. For each case, run all three variants (A, B, C) against the live dev DB
3. Score each output using `scorer.py` (G-Eval rubric, LLM-as-judge)
4. Print the aggregate table + per-case delta table
5. Write `results/latest.json`

## Running the eval (once all prerequisites are met)

```bash
cd content-queue-backend
poetry run python ../evals/research-brief/runner.py --size pilot --variants A,B,C
```

## What "done" looks like

- Variant B mean weighted score > 0.70
- Variant B beats Variant A by >= 5pp on sub_question_coverage
- Variant B gap_accuracy > 0.65 (correctly identifies unanswerable sub-Qs in
  PARTIAL_COVER and TENSION cases)
- No hard fail conditions triggered (no fabricated citations, no missing gap
  reports on PARTIAL_COVER cases)
