---
type: product
status: draft
last_updated: 2026-07-09
---

# Multi-Agent Research Orchestrator

Turns sed.i from a single-shot retrieval tool into a research assistant that
remembers, plans, and synthesizes across the user's library.

Two phases. Phase 1 (memory + Skills) delivers most of the user-visible value
and is independently shippable. Phase 2 (full orchestration) builds on Phase 1's
primitives to handle open-ended research questions where the shape of the work
is discovered during execution, not specified upfront.

---

## Problem

Every current sed.i MCP tool is stateless and single-shot. The agent calling
it starts cold every session — no knowledge of what the user has asked before,
what synthesis style worked, which topics they keep returning to. And for
complex research questions ("what are the competing views I've saved on AI
alignment?", "synthesize everything I know about production ML systems"), a
single retrieval pass is structurally insufficient: you don't know how many
sub-queries you need, whether the first pass found enough, or what gaps remain
until you start looking.

Two distinct problems, two distinct solutions. Skills + memory solves the
structured fast path. Full orchestration solves the open-ended research path.
They compose: Skills become the execution units the orchestrator dispatches.

---

## Phase 1 — Memory + Skills

### What this solves

The agent calling sed.i has no persistent context. It rediscovers the user's
interests, writing style, and recurring topics from scratch every session.
Skills + memory fixes the cold-start problem for the common case: tasks where
the shape of the work is known in advance and can be expressed as a
self-contained instruction set.

### 1.1 Persistent memory layer (`user_memory`)

Two new tables replacing the shallow `reading_patterns` JSONB:

```sql
-- Episodic memory: specific events
CREATE TABLE user_memory_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    event_type TEXT NOT NULL,
        -- 'deep_read' | 'highlight_burst' | 'synthesis_run' |
        -- 'draft_completed' | 'cluster_focus'
    content_item_id UUID REFERENCES content_items(id),
    metadata JSONB,
        -- {"duration_minutes": 45, "highlight_count": 7,
        --  "topic": "agent eval design", "satisfaction": "high"}
    occurred_at TIMESTAMPTZ DEFAULT now()
);

-- Semantic + procedural memory: extracted facts about the user
CREATE TABLE user_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    current_focus TEXT,
        -- "attention mechanisms in transformers" — what they're actively working on
    reading_velocity TEXT,        -- 'fast' | 'deep' | 'browsing'
    preferred_depth_words INT,    -- avg word count of completed reads
    writing_style_notes TEXT,
        -- extracted from drafts: "concise, avoids hedging, uses numbered lists"
    active_knowledge_gaps JSONB,
        -- [{concept, cluster_id, confidence}]
    past_synthesis_topics JSONB,
        -- [{topic, run_id, satisfaction, timestamp}] — what they've researched before
    last_consolidated TIMESTAMPTZ
);
```

**`consolidate_memory` beat task** (`app/tasks/memory.py`):

Nightly Celery beat (3am, per-user). Reviews last 7 days of activity, extracts
structured insights via `llm_client.structured_chat()`, writes to both tables.

```python
@celery_app.task(base=DatabaseTask, bind=True, max_retries=2)
def consolidate_memory(self, user_id: str):
    # 1. Load recent activity
    recent = _load_recent_activity(user_id, days=7, db)
    if not any(recent.values()):
        return

    # 2. Extract structured insights
    insights = llm_client.structured_chat(
        messages=[{"role": "user", "content": CONSOLIDATION_PROMPT.format(
            activity=_format_activity(recent),
            current_profile=_load_profile(user_id, db),
        )}],
        response_model=ConsolidationResult,
        task=TASK_MEMORY_CONSOLIDATION,
    )

    # 3. Upsert profile + insert episodic events
    _upsert_profile(user_id, insights, db)
    _insert_episodic_events(user_id, insights.episodic_events, db)
    db.commit()
```

Beat entry added to `celery_app.py`:

```python
"consolidate-memory-nightly": {
    "task": "app.tasks.memory.consolidate_all_users",
    "schedule": crontab(hour=3, minute=0),
},
```

`consolidate_all_users` is a fan-out task (same pattern as `cluster_all_users`)
that dispatches `consolidate_memory.delay(user_id)` for each active user.

**Who reads from memory:**

- All three Skills (below) — load profile as context before executing
- `synthesize_topic` quick mode — seeds from `current_focus`
- Phase 2 orchestrator — seeds planning prompt from `active_knowledge_gaps`
- `get_reading_stats` MCP tool — can return profile summary

### 1.2 Agent Skills

Skills are self-contained instruction sets the agent loads on demand, following
Anthropic's Agent Skills format (opened as a shared standard March 2026). Each
Skill is a named block of operational guidance that tells a calling agent
(Claude Desktop, Claude.ai) how to sequence sed.i's existing MCP tools for a
specific task. They are not new endpoints — they are instructions registered
alongside tool definitions in the MCP server.

Skills keep the standing context small regardless of how many exist: only the
relevant Skill is loaded per request, not all of them simultaneously.

Three initial Skills:

---

**Skill: `weekly-digest`**

*Triggered when:* user asks about what they saved or read this week, or requests
a weekly summary.

```
## weekly-digest

Goal: summarize what the user saved this week against what they already know,
surfacing what's new and what connects to prior interests.

Steps:
1. get_reading_stats() — get counts and recency
2. list_lists() — identify active lists from this week
3. For each active list: summarize_list(list_id) — cached, prefer over raw content
4. Load user memory: current_focus + past_synthesis_topics
5. search_content(current_focus) — find this week's saves that connect to focus
6. Synthesize: what's new this week, what connects to prior work, what's a new thread

Output: 3-5 sentences per theme. Surface connections to prior reading explicitly
("this connects to your earlier reading on X"). Do not list every article —
synthesize across them.
```

---

**Skill: `connect-new-save`**

*Triggered when:* a new article is ingested (post-ingestion hook) or user asks
"how does [article] connect to what I know?"

```
## connect-new-save

Goal: given a newly saved article, find what in the existing library it
relates to and surface those connections explicitly.

Steps:
1. get_content_item(item_id) — load title, tags, entities
2. For each entity in the article: explore_concept(entity_name) — entity graph traversal
3. find_similar(item_id) — semantic neighbors
4. get_highlights() for the top 3 similar articles — user's own annotations
5. Load user memory: current_focus, active_knowledge_gaps
6. Check: does this article address any active_knowledge_gaps?
7. Synthesize: "This connects to [X] because [Y]. It also relates to [Z] which
   you highlighted [quote]. It fills a gap you had on [concept]."

Surface the connection in 2-4 sentences. Be specific — cite titles and quotes,
not vague similarity scores.
```

---

**Skill: `draft-from-highlights`**

*Triggered when:* user asks to write or draft something using their reading.

```
## draft-from-highlights

Goal: draft a paragraph using the user's own highlights and library sources,
in the user's own writing voice.

Steps:
1. get_draft(list_id) — read current draft state first
2. Load user memory: writing_style_notes — this is the voice to match
3. search_content(instruction) — find articles relevant to the draft instruction
4. get_highlights(content_item_ids=[...]) — pull user's own annotations from those articles
   (user's own words are the best source for their writing voice)
5. Draft one paragraph with inline citations [Author, Title]
6. update_draft(list_id, appended_content) — write back

Constraints:
- Match writing_style_notes from user memory exactly
- Only cite articles from the retrieved set — never fabricate sources
- Write one paragraph per call; ask before adding more
- Only call update_draft — do not modify the library
```

---

**Skill registration** (`app/mcp/server.py`):

```python
SEDI_SKILLS = {
    "weekly-digest": WEEKLY_DIGEST_SKILL,
    "connect-new-save": CONNECT_NEW_SAVE_SKILL,
    "draft-from-highlights": DRAFT_FROM_HIGHLIGHTS_SKILL,
}

# Registered alongside tool definitions — no new endpoints
mcp.add_resource("skills://sedi", lambda: SEDI_SKILLS)
```

### 1.3 Routing layer

A two-tier classifier in front of Skills routing:

**Tier 1 — keyword pre-filter (free, instant):** catches the unambiguous cases.
If the query contains a direct filter operator (`after:`, `tag:`, `author:`), or
matches an obvious single-article lookup pattern (`"find"`, `"show me"`, `"get"`
with no synthesis signal), route `direct` without any LLM call.

**Tier 2 — LLM micro-classifier (gpt-4o-mini, ~50ms, ~$0.001):** for everything
that clears tier 1, a structured LLM call with a small labeled example set
classifies into `skill + skill_name` or `orchestrate`. Rule-based pattern sets
were considered but rejected: they require constant manual patching as query
phrasing varies (`"past 7 days"` vs `"this week"`, `"summarize recent saves"` vs
`"weekly digest"`). A 20-example few-shot prompt is more robust and costs less
than an engineer hour to maintain.

```python
class RouteDecision(BaseModel):
    route: Literal["direct", "skill", "orchestrate"]
    skill: Literal["weekly-digest", "connect-new-save", "draft-from-highlights"] | None

def classify_request(question: str, db=None, user=None) -> tuple[str, str | None]:
    """
    Returns (route, skill_name | None).
    Routes: 'direct' | 'skill' | 'orchestrate'
    Tier 1 (free): obvious filter/lookup queries bypass LLM.
    Tier 2 (gpt-4o-mini): everything else.
    """
    if _is_obvious_direct(question):
        return ('direct', None)
    decision = llm_client.structured_chat(
        messages=[{"role": "user", "content": ROUTING_PROMPT.format(question=question)}],
        response_model=RouteDecision,
        task=TASK_ROUTING,
    )
    return (decision.route, decision.skill)
```

`TASK_ROUTING` routes to `gpt-4o-mini` (same tier as tagging). The prompt
includes ~20 labeled examples covering paraphrase variation for all three Skills
and common orchestration patterns. The example set is the artifact to maintain —
not a regex table.

### 1.4 New MCP tools (Phase 1)

**`synthesize_topic` (quick mode only in Phase 1)**

```python
@mcp.tool()
async def synthesize_topic(topic: str, depth: Literal["quick"] = "quick") -> dict:
    """
    Research a topic across your library. Returns structured synthesis
    with perspectives, key concepts, and source citations.
    quick: single-pass, ~5s, 2 LLM calls.
    """
    profile = _load_user_profile(user.id, db)
    memory_context = f"User is focused on: {profile.current_focus}" if profile else ""

    results = hybrid_search(topic, user, db, limit=10, mode="full")
    context = _build_context(results, topic, db, max_tokens=4000)

    return llm_client.structured_chat(
        messages=[{"role": "user", "content": QUICK_SYNTHESIS_PROMPT.format(
            topic=topic, context=context, memory=memory_context,
        )}],
        response_model=SynthesisResponse,
        task=TASK_SYNTHESIS,
    )
```

**`assist_draft`**

```python
@mcp.tool()
async def assist_draft(list_id: str, instruction: str) -> dict:
    """
    Draft a paragraph using your library as source material.
    Executes the draft-from-highlights Skill inline.
    Bounded write scope: only calls update_draft.
    """
```

Executes the `draft-from-highlights` Skill as inline logic. Loads
`writing_style_notes` from `user_profiles` before drafting.

### 1.5 Build order (Phase 1)

1. `user_memory_events` + `user_profiles` migration — add `CHECK` constraint on `reading_velocity IN ('fast', 'deep', 'browsing')` in the migration; `UserProfile.reading_velocity` uses SQLAlchemy `Enum` type, not bare `str`
2. `consolidate_memory` task + fan-out beat task — `_insert_episodic_events` uses `INSERT ... ON CONFLICT DO NOTHING` on a unique constraint `(user_id, event_type, content_item_id, occurred_at::date)` to prevent nightly re-insertion of the same events
3. `get_reading_stats` MCP tool updated to return profile summary
4. Three Skills registered in `app/mcp/server.py`
5. `synthesize_topic` quick mode + `assist_draft` MCP tools — `_build_context` uses `tiktoken` (cl100k_base) to count tokens; truncates by dropping lowest-scoring chunks first until under budget
6. Routing classifier — two-tier (keyword pre-filter + gpt-4o-mini structured output); `TASK_ROUTING` constant added to `llm_client.py`

Each step is independently testable. Memory consolidation can ship and run
before any Skill is built — it starts collecting episodic data immediately.

---

## Phase 2 — Full Orchestration

### 2.0 What this solves

Skills handle tasks where the work shape is known upfront. Phase 2 handles
open-ended research questions where the shape is discovered during execution:
you don't know upfront how many retrieval rounds you need, what sub-queries
to generate, or whether the first pass found enough.

"What are the competing views I've saved on AI alignment?" cannot be expressed
as a fixed instruction sequence. The lead agent has to plan, evaluate what it
got, decide whether to iterate, and route to different Skills depending on
what gaps emerge.

Skills become the **execution units** the orchestrator dispatches to subagents.
A subagent running `connect-new-save` against each candidate article is
orchestration using Phase 1 primitives.

### 2.1 `research_runs` table

One row per run. Every step transition is a write here — the record is the
source of truth for status, cost, and debuggability.

```sql
CREATE TABLE research_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version INT NOT NULL DEFAULT 1,
    user_id UUID NOT NULL REFERENCES users(id),
    question TEXT NOT NULL,
    mode TEXT NOT NULL,           -- 'deep'
    plan TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
        -- queued | planning | searching | synthesizing | verifying | done | failed | partial
    searches_run JSONB DEFAULT '[]',
        -- [{subagent_id, query, skill, tool_calls, idempotency_key, tokens_used}]
    item_ids_retrieved UUID[] DEFAULT '{}',
    gaps_identified TEXT[] DEFAULT '{}',
    iteration_count INT DEFAULT 0,
    result JSONB,
    budget JSONB NOT NULL,
        -- {max_tokens, max_iterations, max_subagents, timeout_s}
    cost JSONB DEFAULT '{}',
        -- {tokens, latency_ms, tool_call_count, usd_estimate}
    error JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON research_runs (user_id, created_at DESC);
CREATE INDEX ON research_runs (status) WHERE status NOT IN ('done', 'failed');
```

### 2.2 Lead agent task

```python
@celery_app.task(
    base=DatabaseTask, bind=True,
    max_retries=0,        # orchestrator does not retry itself
    time_limit=300,
    soft_time_limit=270,
)
def run_research_lead(self, run_id: str):
    """
    Plan → parallel subagents → collect results → evaluate → iterate if needed →
    synthesize → verify → write result.
    """
```

Key behaviors:

- Reads `research_runs` row on start; if `status != 'queued'` → no-op (idempotency)
- Loads `user_profiles` to seed planning prompt with `current_focus` + `active_knowledge_gaps`
- Persists plan to DB **before** dispatching any subagent
- Dispatches subagents via Celery `group()` for real parallel execution; result collection is handled by `collect_subagent_results` (see below)
- Evaluates coverage after each round: `len(unique_item_ids) >= target_count`
- Reformulates on gap: one cheap LLM call generates a variant query
- Checks `iteration_count < budget.max_iterations` before each new round
- On `SoftTimeLimitExceeded`: marks `partial`, synthesizes from data gathered so far

### 2.2a Subagent result collection (`collect_subagent_results`)

The chord callback is a named Celery task — not an inline lambda. This is required
for Celery to serialize and route it correctly.

```python
@celery_app.task(base=DatabaseTask, bind=True, max_retries=1, time_limit=60)
def collect_subagent_results(self, results: list[dict], run_id: str):
    """
    Chord callback. Receives list of subagent {ok, data, error, meta} dicts.
    Merges retrieved item_ids into research_runs, increments iteration_count,
    then triggers the next step (another search round or synthesize_run).

    Input:  results — one entry per subagent in the group, in dispatch order
    Output: none (writes to DB, dispatches next task)
    """
    db = self.get_db()
    run = db.query(ResearchRun).filter_by(id=run_id).first()
    if not run:
        return

    new_ids: set[str] = set()
    for r in results:
        if r.get("ok") and r.get("data"):
            new_ids.update(r["data"].get("item_ids", []))

    # Merge — no duplicates
    existing = set(str(x) for x in (run.item_ids_retrieved or []))
    merged = existing | new_ids
    run.item_ids_retrieved = list(merged)
    run.iteration_count += 1
    db.commit()

    budget = run.budget
    target = budget.get("target_count", 5)
    if len(merged) >= target or run.iteration_count >= budget["max_iterations"]:
        synthesize_run.delay(run_id)
    else:
        # Another round — lead re-plans and re-dispatches
        run_research_lead.apply_async(args=[run_id], kwargs={"resume": True})
```

The dispatch pattern:

```python
# Inside run_research_lead, after generating subagent payloads:
job = group(run_research_subagent.s(run_id, sid, payload) for sid, payload in subagents)
chord(job)(collect_subagent_results.s(run_id=run_id))
```

`collect_subagent_results` is the only place that writes `item_ids_retrieved` and
decides whether to iterate or synthesize. This keeps the lead task stateless with
respect to inter-round accumulation.

### 2.3 Subagent task

```python
@celery_app.task(
    base=DatabaseTask, bind=True,
    max_retries=2,
    time_limit=60,
    soft_time_limit=55,
)
def run_research_subagent(self, run_id: str, subagent_id: str, payload: dict):
    """
    Input:  {run_id, subagent_id, task_description, skill, search_params, budget}
    Output: {ok, data: {item_ids, summaries} | null, error | null, meta}
    """
```

Subagents execute a named Skill (from Phase 1) or call search functions
directly. They do not call other Celery tasks. No subagent-to-subagent
communication — all coordination through the lead agent and `research_runs`.

Each subagent carries an `idempotency_key` (hash of `run_id + query + skill`).
On retry, the lead agent checks `searches_run` for that key before re-dispatching.

### 2.4 Verification task

```python
@celery_app.task(base=DatabaseTask, bind=True, max_retries=1, time_limit=30)
def verify_synthesis(self, run_id: str, synthesis: dict, item_ids: list[str]):
    """
    Checks each claim in synthesis against item_ids_retrieved.
    Uses gpt-4o-mini (TASK_MCP_SUMMARY model tier).
    Unverified claims are cut or flagged — never silently passed through.
    """
```

Runs after synthesis as a separate task. Uses a cheaper model than synthesis
(model cascade: gpt-4o for synthesis, gpt-4o-mini for verification). Any claim
that fails to trace to a retrieved item ID is removed or marked `[unverified]`
before the result is written.

### 2.5 Async execution model

Multi-round runs have latency measured in tens of seconds to minutes. This
cannot hold an HTTP connection open.

- `synthesize_topic(depth="deep")` enqueues `run_research_lead`, returns
  `{run_id, status_url}` immediately (202 pattern)
- Client polls `GET /research/{run_id}` or receives SSE update on completion
- Lead agent + each subagent are separate Celery tasks on the existing worker pool

New endpoint (`app/api/research.py`):

```text
GET /research/{run_id}
→ {status, result?, cost?, error?, progress: {iteration, searches_run_count}}
```

Scoped to `current_user.id` — cannot read another user's run.

**Rate limiting:** `synthesize_topic(depth="deep")` checks the count of
non-terminal runs for `current_user.id` before creating a new one. Default
limit: 3 concurrent active runs per user. Exceeding the limit returns
`{"error": "run_limit_exceeded", "active_runs": N}` without enqueuing.
This is enforced at the query layer (`SELECT COUNT(*) WHERE status NOT IN
('done','failed','partial') AND user_id = :uid`), not in application logic,
so it's immune to race conditions within a single Postgres transaction.

### 2.6 Reliability

| Failure | Handling |
|---|---|
| Subagent timeout | Lead treats missing result as gap; run continues with partial data |
| Subagent tool call fails | `{ok: false, error}` returned; lead logs, skips, continues |
| Task retry duplicates work | `idempotency_key` checked in `searches_run` before re-dispatch |
| LLM rate limit | `max_retries=2` on subagent tasks with Celery exponential backoff |
| Worker crash mid-run | Recovery beat task (every 5m) detects orphaned runs (`status` non-terminal + `updated_at` stale > 10min); marks `partial` |
| Budget breach | Lead checks `iteration_count < max` and `tokens_used < max_tokens` before each round; terminates to `partial` on breach |
| Cascading retries | Backoff + per-task `time_limit` prevents retry storm against slow downstream |

### 2.7 Agent loops

Two loops, both with machine-verifiable exit conditions:

**Loop 1 — iterative search refinement** (inside lead agent)
- Verifier: `len(unique_item_ids) >= target_count` — binary, instant, free
- Hard cap: `iteration_count < budget.max_iterations` (default 3)
- Reformulation: lead generates a variant query from what's missing — one
  cheap LLM call per retry, not a new full synthesis

**Loop 2 — knowledge gap detection** (inside lead agent, uses entity graph)

- Verifier: SQL — entity nodes adjacent to retrieved articles' entities with
  zero article mentions in the user's library
- Hard cap: depth ≤ 2 in the entity graph
- Terminates when no adjacent unseen entity nodes exist at the search depth

What is explicitly NOT looped: "improve this draft" (subjective verifier),
"find the best articles" (no termination signal). These belong in Skills with
fixed instruction sequences, not agent loops.

### 2.8 Observability

- Every run emits structured OTel spans (already wired via `CeleryInstrumentor`): one span per lead-agent step, one per subagent, one for verification
- `run_id` and `user_id` added as span attributes on all tasks in this run
- `cost.usd_estimate` logged per run; alert fires if average exceeds threshold
- Braintrust LLM tracing already active for OpenAI calls — synthesis + verification
  calls automatically traced when `BRAINTRUST_API_KEY` set

### 2.9 `synthesize_topic` deep mode

Extends the Phase 1 quick-mode tool with `depth="deep"`:

```python
@mcp.tool()
async def synthesize_topic(
    topic: str,
    depth: Literal["quick", "deep"] = "quick",
) -> dict:
    """
    quick: single-pass synthesis, ~5s, synchronous.
    deep: iterative search + parallel subagents + verification, ~30-60s, async.
          Returns {run_id, status_url} immediately; poll for result.
    """
    if depth == "quick":
        return _quick_synthesize(topic, user, db)     # Phase 1 path

    run = _create_run(user, topic, budget=DEFAULT_BUDGET, db)
    run_research_lead.delay(str(run.id))
    return {"run_id": str(run.id), "status_url": f"/research/{run.id}"}
```

### 2.10 Build order (Phase 2)

Requires Phase 1 complete. Each step independently testable.

1. `research_runs` migration + `GET /research/{run_id}` status endpoint
2. `run_research_lead` skeleton: queues, writes plan, marks done — no real search
3. Single subagent calling `hybrid_search()` directly — proves tool contract + idempotency
4. Parallel subagents via `group()` chord — proves real concurrency without corruption
5. Iteration loop + budget enforcement + `partial` termination
6. Recovery beat task for orphaned runs
7. Synthesis task (`TASK_SYNTHESIS` constant added to `llm_client.py`)
8. `verify_synthesis` task (`TASK_VERIFY` constant)
9. `synthesize_topic` deep mode wired to lead agent
10. CI-gated eval suite (`tests/evals/test_synthesis_evals.py`)

### 2.11 Evaluation

**Offline** (`tests/evals/test_synthesis_evals.py`):

- Fixed set of research questions against a library snapshot
- Grading: answer correctness + source grounding (every cited item_id was retrieved) + cost within budget + partial credit for multi-part questions
- Outcome-based — different valid tool-call sequences to the same answer both pass
- Runs in CI under `EVAL=1` flag

**Online**: `cost.usd_estimate` per run tracked; periodic re-grading sample
(LLM-as-judge calibrated against human judgment) to catch drift as library grows.

---

## What this is not (either phase)

- Not cross-provider model failover at Harvey/Anthropic scale
- Not swarm/peer-to-peer topology — orchestrator-worker only, for debuggability
- Not equivalent to Claude Research at its corpus scale — same engineering
  discipline, smaller scale, and that distinction stays explicit
- Phase 2 does not require Phase 1's memory to function, but synthesis quality
  is meaningfully better when `current_focus` and `active_knowledge_gaps` are
  available as planning seeds

---

## Open questions

- **State store**: Postgres is sufficient at current scale. Redis for hot run
  status worth revisiting if polling frequency becomes a problem.
- **Routing threshold**: what query patterns bypass orchestration entirely?
  Needs a small labeled dataset before the classifier is tuned beyond rules.
- **Verification aggressiveness**: gpt-4o-mini assumed. Too aggressive → cuts
  valid claims. Too lenient → pointless. Needs calibration against a grounded
  eval set before shipping.
- **Skills format**: re-check against Anthropic's current Agent Skills
  documentation before implementation — the standard is recent (March 2026)
  and may have evolved.
- **`assist_draft` scope**: currently bounded to appending one paragraph.
  Rewrite-in-place needs a separate design pass (conflict resolution, undo).
