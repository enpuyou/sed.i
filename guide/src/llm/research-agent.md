---
layout: layouts/chapter.njk
title: The research agent
part: 3
order: 4
summary: How one question becomes a cited brief through a planner, parallel searchers, a synthesizer and a verifier, and where that pipeline is fragile.
verified: 2026-10-26
permalink: /llm/research-agent/
---

## In one minute

A research run takes one question, such as "why does my espresso taste sour and how do I fix it?", and returns a structured brief with citations to articles in the user's own library. A planner model splits the question into 2 to 6 sub-questions. One Celery task per sub-question searches the library, asks a model which hits are relevant, pulls the best passages and writes a short summary of each article. A collector task merges the results and decides whether to search again with reworded sub-questions or to move on. A synthesizer model writes the brief from the collected material. A verifier, which is plain code with no model call, deletes any citation that is not an article the searchers actually retrieved.

The whole run is a row in the `research_runs` table. Every step reads the row, writes to it and hands off, so a worker holds no state between steps. A sweep every 5 minutes marks runs that stopped moving as `partial`. After a run finishes, a last task stores what the library did and did not cover, so later runs can use it.

## The problem

The question this chapter answers: **how do you get a model to answer a question from a personal library, with sources the user can check, in a job that takes a minute?**

The obvious design is one call: search the library for the question, paste the top results into a prompt, ask for an answer. It works for narrow questions. It fails for broad ones in three ways.

1. **One search is one angle.** "Why is my shot sour and how do I fix it?" needs material on causes, on grind, on temperature. A single query returns the passages closest to the whole sentence and misses the parts.
2. **A single prompt has a size limit and no checks.** Everything retrieved must fit in one prompt, and nothing verifies that the cited sources exist.
3. **A minute of work does not fit in a web request.** The work must run in the background, survive a worker restart, and be pollable.

So the work is split into stages, each stage stores its output, and a final stage removes anything the model made up.

## Mental model

Think of a newsroom desk. An editor (the lead) splits a story into questions and hands one to each of several reporters (subagents). Each reporter searches the archive, discards irrelevant clippings and writes a note on each useful one. A desk clerk (the collector) pins the notes together and decides whether the editor must send reporters out again. A writer (the synthesizer) drafts the piece from the notes only. A fact-checker (the verifier) strikes every source in the draft that is not in the notes.

| Term | Meaning here |
|---|---|
| Run | One row of `research_runs`: question, plan, intermediate results, brief, status. |
| Lead | `run_research_lead`: plans, then dispatches the reporters. Also the retry entry point. |
| Subagent | `run_research_subagent`: handles one sub-question. A fixed sequence of calls, not a model choosing tools. |
| Chord | A Celery construct: run a group of tasks in parallel, then call one callback with all their return values. |
| Collector | `collect_subagent_results`: the chord callback. |
| Coverage | `full` (3 or more relevant articles), `partial` (1 or 2), `none` (0), per sub-question. |
| Idempotency key | A string stored before work is dispatched, so the same unit of work is not dispatched twice. |
| Research memory | Rows in `research_memory` recording past sub-questions and what the library covered. |

{% fig "The pipeline and the status each step writes" %}
```text
queued
  |  run_research_lead
  v
planning  -- planner model (gpt-4o) -> sub_questions
  |
searching -- chord( subagent x N ) -> collect_subagent_results
  |            |  coverage none and rounds left: lead again (resume)
  |            v
  |         synthesize_run
synthesizing -- synthesis model (gpt-4o) -> ResearchBrief
  |
verifying -- verify_synthesis (no model)
  |
done            failed: budget_exceeded, user_not_found
partial: orphan sweep, or max iterations without enough articles
```
{% endfig %}

The user-facing side is small. The only way to start a run is the MCP tool `synthesize_topic` with `depth="deep"` (chapter 3.5): it creates the row, enqueues the lead and returns a run id. `GET /research/{run_id}` returns `status`, `result`, `error` and a `progress` object. There is no REST endpoint that creates a run and no resume endpoint (see the discrepancy list at the end of the chapter).

## Walkthrough: one question through every stage

The input is the question "Why does my espresso taste sour and how do I fix it?" against a five-article library written for this guide (the same espresso theme as chapter 2.2). The guide's author ran the real lead, subagent, collector, synthesizer, verifier and memory functions against a scratch Postgres database. Only three things were replaced with scripts: the language-model calls, the embedding calls and `hybrid_search` (each returns canned data), and the Celery chord (the subagents were called in a loop, because no broker was involved). Everything the code builds and writes, including the prompt strings and the JSON stored on the run, is captured. Every sentence a model "wrote" is **illustrative**: the stubs returned text chosen to show the shapes.

### Step 1: the row exists, status queued

`_synthesize_deep` in the MCP tool inserts the row with the default budget and calls `run_research_lead_task.delay(run_id)`. It refuses a fourth active run per user (`run_limit_exceeded`).

<!--src content-queue-backend/app/tasks/research.py#46-52-->
```python
_DEFAULT_BUDGET = {
    "max_tokens": 50000,
    "max_iterations": 3,
    "max_subagents": 6,
    "timeout_s": 300,
    "target_count": 8,
}
```

Only `max_iterations` and `target_count` change what the code does. See "What is weak or missing".

### Step 2: planning

`run_research_lead` first checks that the status is `queued`, then sets `planning`. It runs one broad `hybrid_search` (15 results) to learn what the library holds, then asks the planner for sub-questions. The user message, built by the real template with the five titles:

```text
Library articles (titles only):
  - How espresso extraction works
  - Dialing in a new bag of beans
  - Why light roasts taste sour
  - Water chemistry for coffee
  - A history of the espresso machine

Research question: Why does my espresso taste sour and how do I fix it?
```

(Captured: the `prior_context` slot is empty here; see step 9 for why.) The system message is 250 words (366 tokens, measured with `cl100k_base`). It tells the planner to derive sub-questions from the question, not from the titles, and to use the number of related titles only to choose how many to write: 2 to 3 for fewer than 3 related titles, 4 to 5 for 6 or more, never more than 6.

The planner's reply is parsed into a one-field model. In the walkthrough the stub returned (illustrative):

```json
{"sub_questions": ["What causes sour espresso?",
  "How does grind size change extraction?",
  "What brew temperature is recommended for espresso?",
  "What is the effect of tamping pressure?"]}
```

The code keeps at most 6, stores a numbered `plan` string and the `sub_questions` list, and commits. The status becomes `searching`.

### Step 3: idempotency keys and the chord

Before dispatching, the lead writes one key per sub-question into `searches_run`, and skips any key already present:

<!--src content-queue-backend/app/tasks/research.py#595-605-->
```python
payloads = []
for i, sq in enumerate(sub_questions):
    ikey = f"{run_id}:sq{i}:iter{run.iteration_count}"
    payload = {
        "sub_question": sq,
        "search_params": {"query": sq, "limit": _RELEVANCE_BATCH},
        "budget": {"timeout_s": budget.get("timeout_s", 300)},
    }
    existing_keys = {s.get("idempotency_key") for s in (run.searches_run or [])}
    if ikey not in existing_keys:
        payloads.append((ikey, payload))
```

After the first round the stored value is (captured, first two entries, shortened ids):

```json
[{"idempotency_key": "53d16a9c:sq0:iter0",
  "subagent_id": "53d16a9c:sq0:iter0",
  "sub_question": "What causes sour espresso?"},
 {"idempotency_key": "53d16a9c:sq1:iter0", "...": "..."}]
```

Then the dispatch:

<!--src content-queue-backend/app/tasks/research.py#622-625-->
```python
job = group(
    run_research_subagent.s(run_id, ikey, payload) for ikey, payload in payloads
)
chord(job)(collect_subagent_results.s(run_id=run_id))
```

A **chord** is "run these tasks in parallel, and when all have returned, call this one function with the list of their return values". Celery tracks the count of finished header tasks in the result backend (Redis here). The lead task returns as soon as the chord is queued.

### Step 4: one subagent

Each subagent runs a fixed sequence, in `run_research_subagent`:

1. **Query expansion.** A small model (gpt-4o-mini) gets `Sub-question: What causes sour espresso?` and returns two alternative phrasings, to bridge vocabulary gaps. The sub-question plus the two alternatives are each searched with `hybrid_search` (chapter 2.3), de-duplicated by article id, capped at 15 candidates.
2. **Relevance filter.** One call lists the candidates as `id | title | snippet` (the description, or the first chunk if the description is under 60 characters) and asks for the ids that contribute a claim. The user message from the run:

```text
Sub-question: What causes sour espresso?

Candidate articles (id | title | description):
faf47d07-... | How espresso extraction works | A finer grind slows the water ...
a1604178-... | Dialing in a new bag of beans | Change one variable at a time: ...
bc7d0436-... | Why light roasts taste sour | Light roasts keep more organic acids ...
fff7e690-... | Water chemistry for coffee | Low-bicarbonate water leaves acidity sharp; ...
a26e77a4-... | A history of the espresso machine | Pump machines replaced lever machines ...
```

(The stub kept four ids and dropped the history article.)

3. **Chunks and highlights.** For each kept article the code fetches the 4 chunks closest to the sub-question (`_fetch_top_chunks`, a pgvector `ORDER BY embedding <=> ...` over `content_chunks`) and the user's highlights.
4. **Per-article summary.** One call per kept article, 2 to 3 sentences, "based ONLY on the excerpts and highlights provided". The user message for the first article:

```text
Sub-question: What causes sour espresso?

Article: How espresso extraction works
Description: Grind size, temperature and time decide extraction.

Key excerpts:
  › Water at 90 to 96 C extracts sweetness; cooler water leaves the shot sour.
  › A finer grind slows the water and raises extraction; a coarse grind runs fast ...

User highlights:
  › a coarse grind runs fast and the cup tastes sour
```

The subagent returns a dictionary, not an exception. One of its articles looks like this on the run (captured structure; the `article_summary` text is illustrative):

```json
{"id": "faf47d07-...", "title": "How espresso extraction works",
 "description": "Grind size, temperature and time decide extraction.",
 "highlights": ["a coarse grind runs fast and the cup tastes sour"],
 "engagement_score": 3,
 "chunks": ["Water at 90 to 96 C ...", "A finer grind slows ..."],
 "article_summary": "This article ties sour shots to fast, under-extracted flow ..."}
```

`engagement_score` is `2 * highlight_count + is_read`: here 2 x 1 + 1 = 3. Coverage is computed from how many articles survived the filter: 3 or more is `full`, 1 or 2 `partial`, 0 `none`. In the walkthrough, the "tamping" sub-question found no relevant article, so its coverage was `none`.

### Step 5: the collector decides

`collect_subagent_results` runs once the chord completes. It appends each successful result to `subagent_results`, merges article ids into `item_ids_retrieved`, increments `iteration_count` and commits. Then:

<!--src content-queue-backend/app/tasks/research.py#1055-1066-->
```python
all_covered = len(none_coverage_sqs) == 0
hit_target = len(merged_ids) >= target_count
hit_max = run.iteration_count >= max_iterations
found_new = len(new_ids) > 0

if all_covered or hit_target or hit_max or not found_new:
    if hit_max and not hit_target:
        run.status = "partial"
        db.commit()
    synthesize_run(run_id, db=db)
else:
    run_research_lead(run_id, db=db, resume=True)
```

In the walkthrough one sub-question was `none`, only 4 distinct articles had been found (target 8), 1 of 3 iterations was used, and new articles had appeared. So the collector chose the `else` branch: search again.

### Step 6: resume with reworded sub-questions

`run_research_lead(..., resume=True)` accepts a run whose status is `queued` or `searching`. It tells the planner which sub-questions came back empty (captured):

```text
Previous iteration found no relevant articles for these sub-questions. Reformulate them with different vocabulary or narrower scope:
  - What is the effect of tamping pressure?
```

The planner replies with a full new plan, not only the changed question (illustrative: the stub reworded the fourth to "Does applying more tamping force slow the flow of water?"). The lead overwrites `sub_questions`, then writes new keys. The iteration number is in the key, so round two's keys are `...:sq0:iter1` to `...:sq3:iter1`, all new. All four subagents run again. The run ended with 8 `searches_run` entries and 8 `subagent_results`.

### Step 7: synthesis

Now every sub-question has coverage, so the collector calls `synthesize_run`, which sets `synthesizing` and builds the user message with `_build_per_sq_context`: one block per sub-question, each listing its articles with summary, up to 4 excerpts of 300 characters, up to 3 highlights of 150 characters and the engagement score. The context is capped at 6,000 tokens by dropping whole articles, never by cutting one. The start of the real message from the run:

```text
Research question: Why does my espresso taste sour and how do I fix it?

Per-sub-question results:
--- Sub-question 1: What causes sour espresso?
Coverage: full

  [faf47d07-...] How espresso extraction works
  Summary: This article ties sour shots to fast, under-extracted flow ...
  Supporting excerpts:
    › Water at 90 to 96 C extracts sweetness; cooler water leaves the shot sour.
  User highlights (1):
    (star) a coarse grind runs fast and the cup tastes sour
  [engagement: 3]
```

The synthesis system message is 298 words (393 tokens, measured). It requires citing only ids shown in the context, connecting sources rather than listing them, naming tensions with the article on each side, and adding a `gaps` entry only for sub-questions with coverage `none`. The reply is validated against `ResearchBrief`:

{% fig "schemas/research.py: the shape of the brief" %}
<!--src content-queue-backend/app/schemas/research.py#15-42-->
```python
class SourceCitation(BaseModel):
    item_id: str
    title: str
    representative_highlight: str | None = None


class SubQuestionFinding(BaseModel):
    sub_question: str
    coverage: Literal["full", "partial", "none"]
    finding: str
    key_sources: list[SourceCitation]
    tensions: list[str]


class GapItem(BaseModel):
    sub_question: str
    what_is_missing: str
    partial_coverage: list[str]  # item_ids that touch this sub-Q but don't answer it


class ResearchBrief(BaseModel):
    summary: str
    sub_question_findings: list[SubQuestionFinding]
    cross_cutting_tensions: list[str]
    gaps: list[GapItem]
    engagement_note: str
    confidence: Literal["high", "medium", "low"]
```
{% endfig %}

The code stores `brief.model_dump()` in `result` and sets `verifying`.

For the walkthrough the stub was told to misbehave on purpose (this is illustrative, to exercise the verifier): it cited a real article and an id that was never retrieved, and added a gap about a sub-question that had coverage.

### Step 8: verification

`verify_synthesis` makes no model call. It does three things to `result`. First, it removes every `key_sources` entry whose `item_id` is not in `item_ids_retrieved`:

<!--src content-queue-backend/app/tasks/research.py#1205-1218-->
```python
retrieved = set(str(x) for x in (run.item_ids_retrieved or []))
result = dict(run.result)

citations_removed = 0
cleaned_findings = []
for finding in result.get("sub_question_findings", []):
    finding = dict(finding)
    before = len(finding.get("key_sources", []))
    finding["key_sources"] = [
        s
        for s in finding.get("key_sources", [])
        if str(s.get("item_id", "")) in retrieved
    ]
    citations_removed += before - len(finding["key_sources"])
```

Second, it removes any gap whose sub-question has coverage `full` or `partial` in `subagent_results`. Third, it adds a gap entry for any `none` sub-question the model left out. The log line from the run (captured):

```text
verify_synthesis run=7d095480-... {'citations_removed': 1, 'gaps_stripped': 1,
  'gaps_injected': 1, 'final_gap_count': 1}
```

The invented citation and the invented gap were removed. The injected gap was for "What is the effect of tamping pressure?", the first-round question that was later reworded and answered. See "How it fails": the verifier reads every round's results, so a superseded question is reported as a gap.

The status becomes `done`. The verifier checks that the cited ids were retrieved; it does not check that the cited article supports the claim next to it.

### Step 9: memory, and what the polling API returns

After `done`, `verify_synthesis` queues `extract_research_memory_task` with a 5-second delay. The task writes one `research_memory` row per entry in `subagent_results`: the sub-question, its embedding, the coverage, a 1 to 2 sentence summary (full or partial) or a templated gap sentence (none). In the walkthrough it wrote 8 rows for this one run, because the run had 8 subagent results. Four were repeats of the same text.

At the start of the next run, the planner is supposed to receive the closest past entries. The real query was run in the walkthrough and failed:

```text
_fetch_research_memory: query failed: (psycopg.errors.SyntaxError)
  syntax error at or near "$3"      ...  AND created_at > now() - interval $3
```

The code writes `interval :age_interval` with a bound string such as `'90 days'`. The psycopg 3 driver sends bound values separately from the SQL, and Postgres does not accept `interval $3`. The function catches every exception and returns an empty list, so the planner runs without memory and no error is visible. This is why the planning message in step 2 had no "Past research context" block. The query was reproduced against local Postgres with the same driver the application uses (`psycopg` 3, selected in `core/database.py`). Whether production logs show the warning was not checked. A corrected expression (`make_interval(days => :d)`) returned rows in the walkthrough, and the real `_format_memory_context` rendered them:

```text
Past research context (from your library, previous sessions):
- "What causes sour taste in espresso?" -> partial coverage. Under-extraction ...
- "Does tamping pressure change extraction?" -> no coverage. Gap: No articles ...
```

(Arrow shown as `->` here; the code prints a Unicode arrow. Memory rows were seeded by the walkthrough, and the embedding stub made every match score 1.0, so the similarity values are illustrative.)

The final polling response, with the brief elided (captured):

```json
{"status": "done", "result": "<brief>", "error": null,
 "progress": {"iteration": 2,
   "sub_questions": ["What causes sour espresso?", "..."],
   "searches_run_count": 8}}
```

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/research.py` | All five prompts, the lead, subagent, collector, synthesizer, verifier and the orphan sweep (1,371 lines) |
| `content-queue-backend/app/tasks/research_memory.py` | `extract_research_memory`: one row per sub-question after a run is done |
| `content-queue-backend/app/models/research.py` | `ResearchRun`: status, plan, JSONB columns for each stage |
| `content-queue-backend/app/models/research_memory.py` | `ResearchMemory`: embedding column with an IVFFlat index (`lists = 100`, cosine) |
| `content-queue-backend/app/schemas/research.py` | `ResearchBrief` and its parts |
| `content-queue-backend/app/api/research.py` | `GET /research/{run_id}`, scoped to the current user |
| `content-queue-backend/app/mcp/tools/synthesis.py` | `_synthesize_deep`: creates the run, limits to 3 active runs per user |
| `content-queue-backend/app/core/task_locks.py` | `acquire_run_lock` / `release_run_lock` (Redis `SET NX EX`) |
| `content-queue-backend/app/core/celery_app.py` | Beat schedule: the sweep runs every 300 seconds |

Which function advances which transition:

| Transition | Function |
|---|---|
| queued to planning | `run_research_lead` |
| planning to searching | `run_research_lead` (after the plan is committed) |
| searching to searching (next round) | `collect_subagent_results` calling `run_research_lead(resume=True)` |
| searching to synthesizing | `collect_subagent_results` calling `synthesize_run` |
| synthesizing to verifying | `synthesize_run` |
| verifying to done | `verify_synthesis` |
| any to failed | `run_research_lead` or `synthesize_run` on `BudgetExceededError`; `run_research_lead` if the user is missing |
| non-terminal to partial | `recover_orphaned_runs`; `collect_subagent_results` at max iterations |

Time limits are set on the Celery tasks: the lead has `time_limit=300`, `soft_time_limit=270`; each subagent `time_limit=120`, `soft_time_limit=100`. Note that `collect_subagent_results_task` and `synthesize_run_task` carry no tight limit, so the global 30-minute task limit applies, and that `collect_subagent_results` calls `synthesize_run` inside the same task, so the whole synthesis, verification and (on the resume path) the next planning run inside the callback's own task.

### Budget handling

`BudgetExceededError` is the per-user daily spend ceiling from the LLM gateway (chapter 3.1). The planning call catches it by name and fails the run with a distinct code:

<!--src content-queue-backend/app/tasks/research.py#574-585-->
```python
except BudgetExceededError as e:
    # Surface distinctly rather than letting this propagate uncaught
    # (this function has no other exception handler) and get picked
    # up 10 minutes later by recover_orphaned_runs_task as a generic
    # "stalled" partial — that hides the real cause from the user.
    logger.warning(
        "run_research_lead: budget exceeded for run %s: %s", run_id, e
    )
    run.status = "failed"
    run.error = {"code": "budget_exceeded", "message": str(e)}
    db.commit()
    return
```

Captured with a stub that raised the error at planning: `status=failed`, `error={'code': 'budget_exceeded', 'message': 'User u has spent $5.0012 today, at or above the $5.00 daily ceiling'}`. `synthesize_run` does the same. In a subagent, the error returns `{"ok": False, "error": {"code": "budget_exceeded"}}` and, during per-article summaries, stops asking for summaries and keeps the articles without them.

### The orphan sweep

<!--src content-queue-backend/app/tasks/research.py#1328-1344-->
```python
cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
terminal = ("done", "failed", "partial")
stale = (
    db.query(ResearchRun)
    .filter(
        ResearchRun.status.notin_(terminal),
        ResearchRun.updated_at < cutoff,
    )
    .all()
)
for run in stale:
    run.status = "partial"
    run.error = {
        "code": "orphaned",
        "message": "Run stalled — marked partial by recovery task",
    }
db.commit()
```

Beat fires `recover_orphaned_runs_task` every 5 minutes. It takes a Redis lock first:

<!--src content-queue-backend/app/tasks/research.py#1360-1368-->
```python
lock_name = "recover_orphaned_runs_task"
if not acquire_run_lock(lock_name, ttl_seconds=60 * 10):
    logger.info(f"{lock_name}: previous run still in progress, skipping")
    return 0

try:
    return recover_orphaned_runs(db=self.db)
finally:
    release_run_lock(lock_name)
```

Captured against Redis (logical database 15): the first `acquire_run_lock` returned `True` with a TTL of 600 seconds, the second returned `False`, the task called while the lock was held returned `0` and logged the skip, and after release the task returned `1`, set `status=partial` with `error.code=orphaned`, and left no lock key behind. If Redis is unreachable the lock function returns `True` (fail open).

A sweep marks a run `partial` with no `result`. A run that is merely slow is also marked, because the test is only `updated_at` older than 10 minutes, and `updated_at` moves only when the lead, collector, synthesizer or verifier commits.

### What a failed subagent does to a chord

The subagent wraps its whole body in `except Exception` and returns an error dictionary:

<!--src content-queue-backend/app/tasks/research.py#973-980-->
```python
except Exception as exc:
    logger.exception("run_research_subagent error for run %s: %s", run_id, exc)
    return {
        "ok": False,
        "data": None,
        "error": {"code": "subagent_error", "message": str(exc)},
        "meta": {},
    }
```

That choice matters because of how chords behave. The guide's author ran a three-task chord on Celery 5.6.2 with a Redis broker and backend and one worker (a throwaway app, not sed.i's):

```text
[raises] callback state=FAILURE
[raises] result=ChordError("Dependency cb437575-... raised RuntimeError('subagent 1 blew up')")
[returns_error] callback state=SUCCESS
[returns_error] result={'collected': [{'ok': True, 'i': 0},
  {'ok': False, 'error': {'code': 'subagent_error'}}, {'ok': True, 'i': 2}]}
```

When a header task raises, Celery never calls the callback; the callback's result is marked `FAILURE` with `ChordError`. When the task returns an error value, the callback runs and sees it. sed.i uses the second pattern for ordinary errors, so one failed sub-question does not stop the run: it just contributes no articles.

The pattern does not cover a task killed by its time limit (`SoftTimeLimitExceeded` is caught by `except Exception` and returns an error, but a hard kill or a worker that dies is not), nor a worker crash. In those cases the callback never runs, nothing registers an error callback, the run stays at `searching`, and the sweep marks it `partial` after 10 minutes (inferred from the code and the chord result above; a hard kill was not reproduced). Celery's default acknowledgement happens before the task runs (the app does not set `task_acks_late`), so a task lost to a worker death is not redelivered.

Measured on the development database (131 runs, all created 2026-07-10 to 2026-07-17): 114 are `done` (105 after one round, 9 after two), averaging 4.2 sub-questions and 5.8 distinct articles. For `done` runs, the time from creation to the last write has a median of 50 seconds and a 90th percentile of 73 seconds. The other 17 are `partial` with `error.code` of `orphaned`, `iteration_count` 0 and no result: runs that never got past the start, swept later (cause not recorded; inferred that no worker handled them). The `research_memory` table holds 0 rows there, consistent with that data predating the memory feature, so it says nothing about the query bug above.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Fixed pipeline of model calls, not a model that picks tools | A ReAct loop where the model decides the next search | Predictable cost and steps, each step testable alone. It loses adaptivity: on retry only the wording of sub-questions changes. |
| State in a Postgres row, workers stateless | Pass state in task arguments; keep it in Redis | A restart loses nothing already committed, and the polling API reads the same row. The cost is that each step is a manual state change, with no engine to enforce it. |
| Celery chord for fan-out | `asyncio.gather` in one task; sequential loop | Subagents run on different workers in parallel, and each has its own time limit. The cost is the chord failure mode above. |
| Cheap model for expansion, filter and summaries; strong model for planning and synthesis | One model throughout | Most calls are the many small per-article summaries, so the cheap model carries the volume (see the cost estimate below). Models are set in `core/config.py`: gpt-4o and gpt-4o-mini by default, Bedrock equivalents as fallback. |
| Verifier is code, not a model | A second model that checks claims | Cheap and deterministic for ids. It cannot judge whether a citation supports its sentence. |
| Per-article summary before synthesis | Hand raw chunks to the synthesizer | The synthesizer sees a short, sub-question-scoped note per article, which keeps its context under 6,000 tokens. |
| Cross-run memory table with vector search | Re-plan from scratch every time | Avoids re-asking questions the library could not answer. Intended; currently not working (below). |
| Orchestrate with Celery now, Temporal later | Prefect; Temporal today | [ADR-0005](/talking/decision-log/) reserves Temporal for this agent because it needs durable execution. Temporal was not adopted. |

The ADR (`docs/decisions/0005-pipeline-orchestration.md`) argues that Celery chains give no per-step visibility and that Prefect tasks are not durable, so a multi-step agent belongs on a workflow engine such as Temporal. The code today is the case the ADR describes. It hand-implements what a workflow engine provides: persisted step state (the row), retry-safe step identity (idempotency keys), a watchdog for stuck work (the sweep) and fan-out and join (the chord). A workflow engine would replace the status strings, the sweep and the missing error callback with one durable function. At this scale (about 4 sub-questions per run, a few runs a day in the development data) the hand-built version is workable; the failure list below is what it costs.

### Cost bound

The configured budget fields do not bound cost (see weak points). What does: the daily per-user spend ceiling in the gateway (default 5 US dollars, `LLM_DAILY_BUDGET_USD_PER_USER`), a limit of 3 active runs per user, and the structure of the pipeline. From the code, the maximum number of chat calls in a run is 3 rounds x (1 plan + 6 subagents x (1 expansion + 1 filter + 15 summaries)) + 1 synthesis = 310. With the guide's prompt sizes (measured: system prompts of 110 to 393 tokens), assumed user-message sizes, the `max_tokens` set on each call and the price table in `core/llm_client.py`, the worst case comes to about 0.13 US dollars per run. That figure is an **estimate**, not a measurement; the typical run (4 sub-questions, 1 or 2 rounds) is far below it.

## How it fails

| Failure | What happens | Status |
|---|---|---|
| Daily spend ceiling hit at planning or synthesis | Run set to `failed`, `error.code=budget_exceeded` | Handled since 2026-09-25 (previously stuck until the sweep) |
| Worker dies, task killed, or chord header task raises uncaught | Callback never runs; run stuck at `searching` | Sweep marks `partial` after 10 to 15 minutes |
| Duplicate lead entry in the same round | Planner called again; `sub_questions` overwritten; nothing dispatched; run stays at `searching` until the sweep | Open (below) |
| Memory extraction races the commit | `extract_research_memory` raises `_RunNotReadyError` and retries (max 2, 30 s apart) | Fixed 2026-09-25; before, the memory was silently lost |
| Memory lookup SQL error | Caught, returns no rows, planner runs without memory | Open (below) |
| Synthesizer cites an id that was not retrieved | Removed by the verifier | Works as designed |
| Synthesizer invents a gap for a covered sub-question | Removed by the verifier | Works as designed |

## What is weak or missing

Ranked by how likely each is to matter.

1. **Cross-run memory never reaches the planner.** `interval :age_interval` with a bound string fails on psycopg 3, and the function swallows the error. The fix is a different expression, for example `make_interval(days => :d)`, plus a test that runs the query against Postgres (none of the existing tests call `_fetch_research_memory`). Separately, memory rows are written per subagent result, so a two-round run stores the same sub-question twice, and a question that was `none` in round one is stored as a gap even though round two answered it.
2. **Three of the five budget fields are decoration.** `max_tokens`, `max_subagents` and `timeout_s` are written into the row and never read for any decision (`timeout_s` is copied into the subagent payload, which ignores it). The real limits are the hard-coded `[:6]` slice on sub-questions, the Celery time limits and the daily dollar ceiling. A run cannot stop itself at 50,000 tokens.
3. **Idempotency keys name a slot, not a question.** The key is `run:sq<index>:iter<round>`. If the lead is entered twice in the same round (the walkthrough did this by hand), the planner is called again and its new wording overwrites `sub_questions`, but the keys are unchanged, so nothing is dispatched. Later steps look results up by sub-question text, so results stored under the old wording no longer match. No code path in the repository does this today, because only the collector calls the lead with `resume=True`; but the key does not protect the case it was written for. Keys also protect nothing at the subagent level: running the same subagent twice would append two results.
4. **`partial` is overwritten.** At the iteration limit without enough articles, the collector sets `partial` and then `synthesize_run` runs, and the verifier finally writes `done`. The walkthrough (a scenario where the tamping question never resolved) went through the sequence `searching`, `partial`, `synthesizing`, `verifying`, `done`. So `partial` appears only for orphaned runs, and the user cannot tell that a run ended short of its target.
5. **Reworded sub-questions leave stale results behind.** Round two re-runs every sub-question, not only the failed ones (the documentation says covered ones are skipped; the code does not skip them). Results from all rounds accumulate, and the verifier reports gaps from every round, so a superseded `none` appears as a gap.
6. **No error callback on the chord.** A task that dies without returning leaves the run in `searching` for the sweep to find. Adding a `link_error` callback that marks the run `partial` would shorten this from 10 minutes to seconds.
7. **Citations are checked for existence, not support.** A real article cited for a claim it does not make passes verification.
8. **The sweep's test is a clock.** A healthy run slower than 10 minutes between commits would be marked `partial`; a later `done` write would then overwrite that.

{% say %}
A research run is a database row that moves through a status machine, with a different background task advancing each step. A planner model splits the question into a few sub-questions. Celery runs one subagent per sub-question in parallel, as a chord: each subagent searches the library with a few phrasings, asks a cheap model which hits are relevant, pulls the best passages and writes a short note per article. When they all finish, a collector merges the notes and either re-plans the sub-questions that found nothing, up to three rounds, or goes on. A strong model writes the brief from the notes, and then plain code deletes any citation that is not an article we actually retrieved. A sweep every five minutes marks stuck runs partial. The weak spots are that most of the budget fields are not enforced, the retry key names a slot rather than a question, partial status gets overwritten by done, and the memory lookup has an SQL error that is swallowed. It is the kind of pipeline a workflow engine would handle better.
{% endsay %}

## Check yourself

{% qa "Why does the subagent return an error dictionary instead of raising an exception?" %}
In a Celery chord, a header task that raises means the callback is never called and the callback's result becomes `ChordError`. A returned error value reaches the callback, which can skip that sub-question and continue. The cost is that a crash that bypasses `except Exception` (a hard kill, a dead worker) still leaves the run stuck until the sweep.
{% endqa %}

{% qa "A run reaches the iteration limit with only 5 of 8 target articles. What status does the user see at the end, and why?" %}
`done`. The collector sets `partial`, but `synthesize_run` and `verify_synthesis` run next in the same flow, and the verifier writes `done`. `partial` is set permanently only by the orphan sweep.
{% endqa %}

{% qa "The synthesis model cites an article id that is not in `item_ids_retrieved`. What happens, and what does the check not catch?" %}
The verifier removes that citation and counts it in `citations_removed`. It does not catch a real, retrieved article cited for a claim it does not support, because it compares ids only.
{% endqa %}

{% qa "Why is the research memory feature effectively off, and how would you notice?" %}
The lookup query uses `interval :age_interval` with a bound string, which psycopg 3 sends as `interval $3`, a syntax error. The function catches all exceptions and returns an empty list, so the planner simply gets no past context. You would notice from a log warning ("query failed"), from planning inputs always showing zero injected memory entries, or from a test that runs the query against Postgres.
{% endqa %}

{% qa "What does the idempotency key protect, and what does it fail to protect?" %}
It stops the lead from dispatching the same slot twice in the same round (`run:sq<i>:iter<n>` already in `searches_run`). It names a slot by index, not the question text, so a re-plan in the same round overwrites the questions while dispatching nothing, and it does nothing for duplicate subagent execution.
{% endqa %}

{% qa "Which parts of a workflow engine does this code reimplement by hand?" %}
Durable step state (the row and its status strings), step identity for retries (idempotency keys), a watchdog for stalled work (the 5-minute sweep with a Redis lock), and fan-out and join (the chord). Missing pieces that an engine would provide: automatic retry per step, error handling when a parallel branch dies, and a single place to see where a run is.
{% endqa %}
