---
type: retro
status: complete
last_updated: 2026-07-28
consumer: both
---

# Failure Injection Plan — Production Failure Modes Survey

Goal: build real operational experience by deliberately triggering known,
code-verified failure modes and observing actual behavior — not guessing
what "should" happen. This doc is Phase 1 (survey + plan). Phase 2 (actually
running each test) gets its own retro per test, appended below or as
separate dated docs, following the format of
`2026-05-13-extension-safari-port.md`.

No incident/postmortem trail exists anywhere in this repo today (only
feature retros) — this is deliberately building that muscle before a real
incident forces it.

---

## How candidates were found

Full-codebase read of every Redis/Postgres/Celery/OpenAI touchpoint,
checking each against its own docstring's degrade-open claim. Ranked by
confidence (code-verified vs. theoretical) and realism at Railway-hosted,
personal-scale traffic.

---

## Candidate list

### Tier 1 — high confidence, cheap to trigger, distinct failure classes

#### 1. Redis (Celery broker) down during content ingestion dispatch

**Where**: `app/tasks/extraction.py:582,660` — `generate_embedding.delay()`
calls after `extract_full_content` finishes, with **no try/except at all**.
`processing_status` is already committed to `"completed"` before this fires.

**Expected (per the fail-open pattern used elsewhere in the codebase)**:
should either retry the dispatch, or at minimum leave the item in a state
some other process can detect and recover.

**What will actually happen (code-verified)**: nothing catches the broker
error. The content item is stuck permanently at `processing_status=completed`
with no embedding, no tags, no entities, and nothing ever notices or
retries — it just silently stays broken forever. No sweep task checks for
"completed but missing embedding" the way `process_all_missing_embeddings`
sweeps a different gap.

**Test plan**: save a URL, let extraction succeed, then kill Redis (`docker
stop` the local Redis container, or `CONFIG SET` a fake down state) in the
~1-second window between extraction finishing and `generate_embedding.delay()`
firing. Confirm the item is left in `completed` with `embedding IS NULL`
and stays that way indefinitely — no self-healing.

**Acceptance bar**: this is the one where we *expect* to find a real bug
(no existing safety net) rather than confirm one works. Success = confirming
the gap exists with a reproducible repro, then deciding whether to fix it
now or file it.

---

#### 2. Celery worker hangs — `time_limit`/`soft_time_limit` don't fire under `--pool=solo`

**Where**: `app/core/celery_app.py` (`task_time_limit=30*60`), `research.py`'s
per-task overrides (`time_limit=300`, subagent `120`). Production runs
`--pool=solo` (nixpacks.celery.toml) — solo pool has no timer mechanism;
`apply_target()` silently discards the timeout kwargs (traced through
`celery/concurrency/solo.py` directly).

**Expected**: a hung task (e.g. an LLM call that never returns) should be
killed after its configured time limit, freeing the worker for the next task.

**What will actually happen**: the task limit is dead configuration. A
hung task blocks the single worker process forever — and since beat runs
in the same process under solo pool, `recover_orphaned_runs_task` and
every other periodic job also stalls. This is worse than "recovery is slow"
— recovery itself stops running.

**Test plan**: dispatch a task that sleeps/hangs indefinitely (a scratch
Celery task with `time.sleep(9999)`, or patch `llm_client.chat` to hang) with
a short `time_limit` override, confirm it's never killed. Separately confirm
`recover_orphaned_runs_task`'s beat schedule stops firing while the hang
is in progress (check for the expected 5-minute log lines during the hang).

**Acceptance bar**: confirm the timeout truly never fires (not just "fires
late"), and confirm the blast radius — does it *only* stall the hung task's
queue, or does it stall beat-scheduled recovery too, as the trace suggests.

---

#### 3. OpenAI outage/rate-limit during a `structured_chat` call

**Where**: `llm_client.py` — `chat()` has typed retry + Bedrock fallback;
`structured_chat()` does not fall back to Bedrock at all. Used by
`article_analysis.py`, `tagging.py`, `research.py`, `research_memory.py` —
i.e. the entire tagging/entity/research pipeline.

**Expected**: per ADR-0003's provider-resilience framing, a primary-provider
outage should fail over to Bedrock the same way `chat()` does.

**What will actually happen**: `structured_chat` calls raise straight
through on an OpenAI outage. Depending on the caller's own try/except
(varies per call site — some are wrapped, e.g. `article_analysis.py`'s
retry loop; some aren't), this either retries via Celery's task-level retry
or fails the whole pipeline step.

**Test plan**: mock `instructor`/OpenAI client to raise `RateLimitError` (or
a connection timeout) on every call, run a tagging task and a research
planning step, observe: does either recover via Celery retry, or does the
research run end up `failed`/stuck with a clear error, or does it fail
silently?

**Acceptance bar**: confirm whether this is "degrades to a clear failed
state" (acceptable) vs. "silently drops the task with no user-visible
signal" (a real gap) — the distinction matters more than whether it fails
at all, since *some* production outage is inevitable.

---

#### 4. Refresh-token double-submit race

**Where**: `app/api/auth.py`'s `/auth/refresh` — plain `UPDATE`, no row
lock, default READ COMMITTED isolation. Same shape in `app/mcp/oauth.py`'s
Redis-backed refresh grant (`GET` then `DELETE`, not atomic — a real
TOCTOU gap, not just a DB isolation question).

**Expected**: per the "theft detection" design (revoke-on-reuse, described
in ARCHITECTURE.md), a refresh token should be usable exactly once —
concurrent reuse should trigger the theft-detection path (revoke all
tokens for that user).

**What will actually happen**: two concurrent `/auth/refresh` calls with
the same token can both pass the "not yet revoked" check before either
commits its revocation, and both mint valid new token pairs. Neither
sees the other as theft.

**Test plan**: fire two parallel `POST /auth/refresh` requests with the
same refresh token (a simple `asyncio.gather` or two threads), confirm
both succeed and both new token pairs work — i.e. the theft-detection
guarantee silently doesn't hold under concurrency, even though the code
reads as if it does.

**Acceptance bar**: this is a real correctness gap regardless of live-fire
results (READ COMMITTED + non-atomic check-then-act is a textbook race) —
the test's value is having a concrete, reproducible demonstration rather
than just knowing it in the abstract, and deciding whether the realistic
likelihood (a user's browser retrying a failed request, or two tabs
refreshing near-simultaneously) makes this worth fixing now.

---

### Tier 2 — real, lower priority for live-fire (documented, not scheduled yet)

5. **Only 2 of 5 beat fan-out tasks have the overlap-guard lock**
   (`cluster_all_users_task`, `consolidate_all_users_task` do;
   `deduplicate_entities_task`, `embed_new_entities_beat_task`,
   `backfill_missing_entities_task` don't). Plain gap, not an outage
   edge case — a config/code fix, not something to chaos-test.
6. **`hybrid_search`'s blanket `except Exception: return []`** makes a
   Redis+OpenAI outage during search indistinguishable from a real
   zero-result query — "graceful" in the sense of not crashing, but with
   zero signal that anything went wrong. Worth fixing (e.g. distinguish
   "no results" from "search degraded") separately from a live test.
7. **`register()` has 7+ sequential commits with interleaved unguarded
   `.delay()` calls**, no outer transaction — a crash mid-registration
   leaves a partially onboarded user. Low likelihood (fast, no LLM calls
   in the hot path) but real.
8. **DB pool exhaustion presents as a 30-second hang**, not an immediate
   error (`pool_timeout` isn't overridden from SQLAlchemy's default) —
   worth knowing before testing this one, so the failure shape isn't a
   surprise. Original candidate #2 from the first pass — still viable,
   just re-ranked below the Tier 1 items above since it's a slower,
   noisier test to run than the others.

### Tier 3 — verified working correctly (not bugs, confirmed by reading code)

9. Rate limiter, LLM budget checker, and `task_locks.py`'s overlap guard
   all genuinely degrade open exactly as documented — full
   `try/except Exception` around every Redis call. The team's stated
   fail-open pattern holds up under direct code reading; it's just
   inconsistently applied elsewhere (MCP OAuth, the raw `.delay()` calls
   in `extraction.py` didn't get the same treatment — see #1 and the MCP
   OAuth note under #4's related finding).
10. Entity dedup's `merge_entity` and `extract_research_memory` both have
    solid per-item commit/rollback isolation — one bad merge or failed
    row doesn't corrupt the batch. Confirmed good, not a test candidate.

---

## Selected for live-fire testing

Per the ranking above, testing **#1, #2, #3, #4** — each code-verified,
cheap to trigger deliberately, and reveals a distinct class of gap
(unguarded dispatch, dead timeout config, missing provider fallback,
concurrency race) rather than overlapping findings.

Order: **#1 → #4 → #3 → #2**, cheapest/fastest first, saving #2 (the hang
test, which needs the longest observation window) for last.

Each test gets: setup steps, exact trigger mechanism, what to observe,
and a written verdict (bug confirmed / gap confirmed / works as claimed)
appended to this doc or a follow-up dated retro after execution.

---

## Test #1 execution log — Redis down during ingestion dispatch

Attempted the planned test (kill Redis at the `extract_full_content` →
`generate_embedding.delay()` boundary). Never precisely hit that exact
window — timing against live worker logs proved unreliable once an
unrelated backlog (see incident below) made task pickup latency
unpredictable. Two things were confirmed anyway, one of them more
severe than the original target:

**Confirmed: `ingest_url()`'s first dispatch has the same unguarded-`.delay()`
gap as the originally-targeted `generate_embedding.delay()`.** Hit this
by accident — `docker ps` reported Redis "healthy" while it was still
internally in `BusyLoadingError` (loading its RDB snapshot after a
restart). A `POST /content` call during that window created a
`content_items` row (committed before the dispatch) but
`extract_metadata.delay()` raised `OperationalError` uncaught, so the
pipeline never started. Verified directly: item `6664c628-...` sat at
`processing_status: "pending"`, no title, no error, indefinitely — no
sweep task anywhere checks for stale `pending` rows. This is the same bug
class as the original target (#1 in the candidate list) but hits earlier
in the pipeline and is easier to trigger — Redis doesn't need to be fully
down, just briefly not-yet-ready after a restart.

**Confirmed: Docker `healthy` health-check status can diverge from actual
readiness for several seconds after a restart**, specifically when Redis
has a large RDB snapshot to reload (see incident below — 8 seconds to go
from `healthy` to actually answering `PING` after restarting with a
6.5M-key snapshot). This is a general operational fact worth remembering
independent of the app bug above: **never treat a container orchestrator's
health status as proof of readiness for a stateful service with
recoverable persisted data** — check the actual protocol (a real `PING`,
not just "is the process up") before assuming a dependency is safe to use.

**Not yet completed**: the originally-planned precise mid-pipeline race
(#1 in the candidate list, `generate_embedding.delay()` specifically).
Deferred until the environment is in a clean state — see cleanup plan
below.

---

## Unplanned incident: ~6.5M stale messages in local dev Redis

While running test #1, discovered `LLEN celery` = 6,523,419 — far beyond
anything the test session itself could have produced. Investigated as its
own incident since it was large enough to block further testing and
raised the obvious question the user asked: **could this happen in prod too.**

### Root cause (confirmed, not guessed)

Two independent, compounding factors:

1. **Redis persists its queue across restarts by design** (RDB snapshot;
   `content_queue_redis` container has been running since **2026-07-02**
   — nearly 4 weeks — per `docker inspect`). Any Celery message published
   and not yet consumed survives every `docker stop`/`start` cycle. Local
   dev's Celery worker only runs when someone is actively running
   `make dev`/`make worker` — nowhere near 24/7. The user pointed out the
   likely dominant contributor here directly: this project has run many
   prior experiment/eval sessions (the reranker eval work earlier this
   session alone dispatched dozens of real ingestion/search/research
   pipeline runs against the local stack), each publishing real Celery
   messages — and any session where the worker wasn't running the whole
   time, or was restarted mid-session (as happened repeatedly during
   today's own testing), leaves behind messages nothing drains in the
   background between sessions. Four weeks of accumulated experiment
   traffic, not idle time, is the more accurate framing than "the
   container sat unused."

2. **A real, independent dispatch-multiplier bug**, found by sampling:
   5,000/5,000 sampled queue messages had **distinct task IDs** (ruled out
   a duplicate-dispatch loop) and the overwhelming majority
   (4,882/5,000 in one sample) were `app.tasks.entity_embedding.embed_new_entities_task`.
   Traced to `app/tasks/article_analysis.py:367`:
   `embed_new_entities_task.delay(str(item.user_id))` fires **once per
   article successfully analyzed**, not once per user in aggregate. A
   user with a library of N articles getting (re-)analyzed — e.g. via the
   `backfill_missing_entities_task` sweep, or a bulk-import — generates N
   redundant dispatches of the *same* per-user embedding sweep, each one
   doing a full DB round-trip to discover `{"status": "nothing_to_embed"}`
   most of the time, since `embed-new-entities-sweep` already covers the
   same ground on its own 1-hour beat schedule. Confirmed via direct
   Postgres query against sampled task args: some target articles were
   analyzed 10 days ago (`entities_analyzed_at` already set), meaning
   their queued `embed_new_entities_task` messages were stale and
   redundant even at publish time, not just by the time they were sampled.

Neither factor alone explains 6.5M messages; together (weeks of
persistence × N-articles-worth of redundant per-analysis dispatches, atop
normal ingestion volume) they do.

### Does this happen in production too?

**Yes, the design bug (factor 2) is identical in prod** — same code,
same per-article dispatch. **The accumulation dynamic (factor 1) is
different but not absent**: Railway runs the Celery worker as its own
always-on service (`nixpacks.celery.toml`), so it isn't offline between
dev sessions the way a laptop is — but per ADR-0007 and this session's
earlier findings, `--pool=solo` means **one single-threaded worker
process**, and Celery's own `time_limit`/`soft_time_limit` don't fire
under solo pool (a separate finding from this session's failure-mode
survey). A worker that's still up but slow, stuck behind a large batch
of legitimately-dispatched work, or briefly restarted during a deploy,
accumulates the exact same way — just from a smaller, deploy-triggered
starting point rather than weeks of laptop-off time. **A large bulk
backfill or a bad deploy loop hitting `--pool=solo`'s single-threaded
ceiling is the realistic prod version of this**, not "Redis holds 4 weeks
of messages" (Railway's Redis presumably doesn't sit idle for weeks the
way a dev laptop's does, but there's no queue-depth monitoring anywhere
in the observability stack per the earlier architecture review, so there
would be no alert if it did start backing up).

### Design fixes worth making

1. **Fix the dispatch multiplier**: `article_analysis.py:367` should not
   dispatch `embed_new_entities_task` per-article. Either drop the
   dispatch entirely (the hourly `embed-new-entities-sweep` beat task
   already covers it) or debounce it (e.g. only dispatch if this is the
   first unembedded entity seen in some window). Cheapest fix: delete the
   dispatch, rely on the existing hourly sweep — the sweep's own docstring
   already calls itself "an hourly safety net," implying the per-article
   dispatch was meant as a latency optimization, not a requirement.
2. **Add queue-depth observability** — this is genuinely the same finding
   as item 11 in the earlier gap-action-plan work ("beat-scheduled tasks
   lack a documented overlap/backpressure story... no queue-depth or
   worker-saturation alerting mentioned anywhere in the observability
   stack"). This incident is a concrete, lived demonstration of exactly
   that gap: nothing would have surfaced 6.5M queued messages except
   manually running `LLEN celery` out of curiosity while debugging
   something else.
3. **Consider a message TTL** on Celery's broker transport
   (`result_expires`/message `expires` kwarg on `.delay()` for tasks
   where a very stale dispatch is worse than a dropped one — e.g. a
   backfill sweep's dispatches becoming irrelevant if not consumed within
   a day, versus a user-facing extraction task where you'd rather retry
   forever than silently drop it). Not a blanket fix — needs per-task-type
   judgment about which failure mode (stale-but-eventually-processed vs.
   silently-dropped) is worse.
4. **The solo-pool timeout gap (found earlier in this session's survey)
   compounds this** — a genuinely stuck task under `--pool=solo` doesn't
   just fail slowly, it blocks the single worker process from draining
   *anything else*, including whatever backlog exists. Fixing that
   (moving off solo pool, or adding an external watchdog) reduces how
   bad a backlog episode can get even if the dispatch-multiplier bug
   above isn't fixed first.

### Cleanup

Local dev queue (6.5M stale messages) was cleared via `redis-cli FLUSHDB`
on `content_queue_redis` (dev-only state, no production consequence) —
confirmed with the user before running, per the destructive-action
safety check.

---

## Test #2 execution log — solo-pool `time_limit`/`soft_time_limit` don't fire

Dispatched a scratch task (`app/tasks/_chaos_scratch.py`, deleted after
the test) with `time_limit=8, soft_time_limit=5` and a `time.sleep(60)`
body, on a worker running `--pool=solo` (production's actual pool).

**Result: CONFIRMED BROKEN.** The task ran the full 60 seconds
uninterrupted — no `SoftTimeLimitExceeded` raised at 5s, no hard kill at
8s. Matches the code-level trace from the candidate list (`solo.py`'s
`apply_target()` has no timer mechanism to enforce either limit) — this
is now a live-fire-confirmed finding, not just a reading of the source.

Direct log evidence (`/tmp/chaos2_worker_v2.log`, worker dispatched
13:00:09):

```text
[13:00:09,946] Task ...chaos_hang_task[9705422b-...] received
[13:00:09,957] chaos_hang_task: starting, will sleep 60s (limits: soft=5s hard=8s)
[13:01:09,963] chaos_hang_task: finished sleeping — should NEVER see this line
[13:01:09,972] Task ...chaos_hang_task[9705422b-...] succeeded in 60.0238353330642s: 'completed_without_being_killed'
```

The task's own log line at the 60s mark is self-documenting
("should NEVER see this line") and the task result string
(`'completed_without_being_killed'`) confirms it ran to natural
completion rather than being interrupted — both `soft_time_limit=5` and
`time_limit=8` were silently no-ops under `--pool=solo`.

**Blast radius**: not scoped in this pass — beat-schedule stall during
the hang was not independently re-verified this session (originally
planned as part of Test #2's acceptance bar). Treat as still-open if a
precise before/after beat-log comparison is wanted later; the solo-pool
single-process fact makes it structurally certain (beat and worker share
one process/thread under solo), so it's a reasonable inference even
without a fresh direct observation.

**Cleanup**: `_chaos_scratch.py` deleted, `celery_app.py` import
reverted — confirmed via `git diff --stat` showing no residual changes.

---

## Test #3 execution log — OpenAI outage during `structured_chat`

**Root gap, confirmed live (mocked, no real API calls/cost — per the
user's explicit cost-consciousness constraint for this whole exercise):**
patched `structured_chat`'s underlying OpenAI call to raise
`openai.RateLimitError`. Confirmed `_openai_structured_chat()` has no
try/except of any kind — the error propagates completely raw, with no
retry and no Bedrock fallback, unlike `chat()`'s typed-retry +
cross-provider fallback path. This matches the code-reading prediction
exactly.

The interesting part is what happens next — traced three real call sites
to characterize the actual blast radius, since it differs sharply by
caller:

**1. `tagging.py` — silent degradation (worst pattern).** The call site
wraps `structured_chat` in `except Exception as e: logger.error(...);
return []`. An OpenAI outage and "the model legitimately found zero
tags" are indistinguishable from the caller's perspective — the article
just ends up with no tags, permanently, with no retry and no
distinguishable failure state. Same shape as `hybrid_search`'s
`except Exception: return []` (Tier 2 item #6) — a recurring pattern in
this codebase worth naming as such.

**2. `article_analysis.py` — clear failure state, but retry config is
dead code.** `analyze_article_with_llm()` has no try/except and lets the
exception through; but its caller `analyze_article()` catches it at the
outer level and returns `{"status": "failed", "error": str(e)}` — a
real, distinct, queryable failure state (better than tagging.py's silent
`[]`). **However**: `analyze_article_task` is declared with
`max_retries=3`, and that retry never fires. Confirmed live via a direct
mocked call through `analyze_article_with_llm()`: the `RateLimitError`
does get raised, but `analyze_article()`'s own try/except at the outer
level catches it and returns a normal dict — Celery never sees a raised
exception reach the task boundary, so from Celery's perspective the task
*succeeded* (with a payload that happens to say `status: failed`).
`max_retries=3` is unreachable configuration for this failure class —
the same "declared safety mechanism that doesn't actually fire" pattern
as Test #2's solo-pool timeouts, but caused by a swallowed exception
rather than an inert config flag. The article is permanently marked
failed on the very first transient OpenAI hiccup, with zero automatic
retry despite the task being configured for 3.

**3. `research.py` (`run_research_lead`, `synthesize_run`) — the correct
pattern, working as designed.** Neither function has any try/except
around its `structured_chat` calls — the exception propagates all the
way to the Celery task boundary and the task genuinely fails from
Celery's point of view. `run_research_lead_task` explicitly declares
`max_retries=0` (retry intentionally disabled here, not just unused) and
none of the research tasks call `self.retry()` anywhere, so an
auto-retry was never the intended recovery path. Instead, the real
safety net is `recover_orphaned_runs_task` — a 5-minute beat sweep that
marks any run stuck in a non-terminal status for >10 minutes as
`"partial"` with `error: {"code": "orphaned", ...}`. This is a
genuinely different and better design than either tagging.py or
article_analysis.py: it accepts that the task will hard-fail and
designs the recovery around that fact at the workflow level, rather than
trying to catch-and-continue at the call site. Not independently
re-verified via a live 10-minute wait this session (would just re-prove
the beat schedule fires, already established); the code path itself is
unambiguous.

**Verdict**: the root gap (no Bedrock fallback in `structured_chat`) is
confirmed and real, but its severity is entirely caller-dependent — this
codebase has all three tiers of failure handling live simultaneously for
the *same* underlying bug: silent data loss (tagging), a fixed-but-inert
retry config (article_analysis), and a correctly-designed
crash-and-recover pattern (research). The fix that matters most isn't
"add Bedrock fallback to structured_chat" in isolation — it's
**standardizing on the research.py pattern** (let it fail loud, recover
via a stale-state sweep) and removing the false confidence of
`max_retries=3` on `analyze_article_task` and the silent-`[]` swallow in
tagging.py. Bedrock fallback would still help (turns some fraction of
these into non-events), but doesn't fix the two bad patterns underneath.

---

## Test #4 execution log — refresh-token double-submit race

**Result: CONFIRMED, race is real** — but required widening the timing
window to trigger reliably, which matters for how to read the result.

**First attempt** (two real OS threads, `threading.Barrier` forcing
simultaneous entry into the request, separate `TestClient` instances per
thread, real Postgres via the local dev container): 5/5 runs came back
1-success/1-correctly-rejected — the race did *not* trigger even though
both threads entered `/auth/refresh` at the same instant. Read commit +
GIL handoff granularity on localhost Postgres (sub-millisecond
round-trip) meant one request's full SELECT→UPDATE→COMMIT sequence
consistently finished before the other's SELECT ran, despite starting
"simultaneously" — the two threads' actual SQL calls didn't end up
truly overlapped at that speed, even though nothing in the code prevents
it.

**Second attempt** (same setup, plus a monkeypatch on `Session.query`
that pauses each thread right after its `RefreshToken` SELECT until the
*other* thread has also completed its SELECT — i.e. artificially
widening the read-before-either-write window to simulate two requests
separated by real network/production latency rather than requiring
exact same-nanosecond localhost scheduling): **both requests returned
200 with valid, distinct new token pairs for the same original refresh
token.** Theft detection (the `revoked_at is not None` check at
`auth.py:356`) did not trigger for either request — both read the
record while it was still unrevoked, both proceeded to rotate it.

**Verdict**: the race is real and matches the code-reading prediction
exactly (READ COMMITTED + non-atomic check-then-act) — but it is
narrower in practice than "any two concurrent refreshes will always
race," since same-instant localhost requests didn't trigger it 5/5
times without artificially widening the window. The realistic trigger
condition is closer to "two requests separated by tens-to-hundreds of
milliseconds of real network jitter" (e.g. a browser silently retrying
a slow/dropped refresh call while the original is still in flight, or
two tabs refreshing near-simultaneously with independent network paths)
than "the exact same instant" — which is, if anything, a *more*
realistic prod scenario than what the first attempt tested, not a less
realistic one. The fix (`SELECT ... FOR UPDATE` on the refresh-token row,
or a conditional `UPDATE ... WHERE revoked_at IS NULL RETURNING *` with
the application checking whether a row came back) is unaffected by this
nuance — the underlying non-atomicity is confirmed regardless of exact
trigger probability.

Same TOCTOU shape flagged in the candidate list for `app/mcp/oauth.py`'s
Redis-backed refresh grant (`GET` then `DELETE`, not atomic) was not
independently live-tested this session — noted as the same bug class,
not yet verified with its own repro.

**Cleanup**: scratch test scripts deleted from the scratchpad directory;
test user/token rows deleted via the script's own teardown step (with a
minor known artifact — the widened-window monkeypatch intercepted all
`RefreshToken` queries app-wide during the run, not just the two test
threads', so one of the two 200 responses in the second attempt
reflects an unrelated pre-existing token being caught by the same
patched code path rather than a third party's token being part of the
race itself; the core two-thread-same-token result is unaffected).

---

## Summary — all four tests

| # | Failure mode | Verdict | Severity |
| --- | --- | --- | --- |
| 1 | Redis-down during ingestion dispatch | Confirmed (via an adjacent, easier-to-trigger variant: `extract_metadata.delay()` during Redis's post-restart `BusyLoadingError` window) | High — item stuck forever, no sweep detects it |
| 2 | `time_limit`/`soft_time_limit` under `--pool=solo` | Confirmed, live-fire | High — hung task blocks the single worker *and* beat scheduling indefinitely |
| 3 | `structured_chat` has no Bedrock fallback | Confirmed, live-fire; blast radius spans silent-loss (tagging), inert-retry-config (article_analysis), and correctly-designed crash-and-recover (research) | Mixed — the underlying gap is uniform, the caller-side handling is not |
| 4 | Refresh-token double-submit race | Confirmed, live-fire (required widening the timing window) | Medium — real correctness gap, narrower real-world trigger window than the naive "always races" framing |

**Common thread across all four**: this codebase has a real, working
fail-open pattern (Tier 3 items — rate limiter, budget checker, task
locks, `recover_orphaned_runs_task`) that is inconsistently applied. Every
confirmed gap above is a place where that pattern *wasn't* extended —
not a sign the team doesn't know how to build resilient systems, but a
coverage gap in applying a known-good pattern uniformly. That framing
should drive prioritization: extending existing patterns to the gaps
found here is lower-risk and faster than designing new mechanisms.

**Not completed this pass**: Test #1's originally-precise scope
(`generate_embedding.delay()` specifically, vs. the adjacent
`extract_metadata.delay()` variant actually confirmed) — the adjacent
finding is the same bug class hit earlier in the pipeline, and is
judged sufficient to close out Tier 1 item #1 without a further,
narrower repro.

---

## Fixes applied

All ten confirmed findings (the four Tier 1 live-fire tests plus the
four Tier 2 documented gaps plus the dispatch-multiplier bug from the
6.5M-message incident) were fixed in a single follow-up pass, except
one — see the full report for problem/discovery/fix detail on each:
`docs/changelog/2026-07-28-failure-injection-fixes.md`.

**Deliberately not fixed — documented only**: the solo-pool
`time_limit`/`soft_time_limit` gap (Test #2). The real fix is an
infrastructure change (switch Celery's pool type off `--pool=solo`, or
add an external watchdog process) rather than an application code
change — a deploy-topology decision with its own concurrency-model
tradeoffs, scoped out of this batch by explicit user decision rather
than fixed. Recommended direction: move to `prefork` or `gevent` (real
timeout enforcement) or add a lightweight external watchdog that checks
`task_track_started` timestamps and kills/restarts a worker stuck past
its task's time limit — either needs its own testing pass before
shipping, since it changes worker concurrency behavior for every task,
not just the hung ones.
