# Failure Injection Fixes — 2026-07-28

Ten reliability gaps, found via deliberate failure-injection testing
(`docs/retros/2026-07-28-failure-injection-plan.md`) and one live incident
discovered mid-testing, fixed in a single follow-up pass. Four were
confirmed via live-fire tests (real concurrent requests, real hung
tasks, mocked-but-real exception paths — no production cost incurred);
the rest were confirmed by direct code reading against the same
methodology. No user-facing feature changed; this is entirely
reliability/correctness work in the Celery task pipeline and auth layer.

Full test methodology and live-fire evidence: `docs/retros/2026-07-28-failure-injection-plan.md`.

---

## 1. Redundant per-article entity-embedding dispatch (the 6.5M-message incident)

**Problem:** `article_analysis.py`'s `analyze_article()` dispatched
`embed_new_entities_task.delay(user_id)` once per article successfully
analyzed, not once per user. A backfill or bulk re-analysis of N
articles for one user fired N redundant dispatches of the same per-user
embedding sweep — most of which did a full DB round-trip only to find
nothing to embed, since the existing hourly `embed-new-entities-sweep`
beat task already covers the same ground.

**How found:** Discovered by accident while running Test #1 (Redis-down
during ingestion). `LLEN celery` on local dev Redis returned 6,523,419 —
far beyond what the test session itself could have produced. Sampling
5,000 queued messages found 4,882 were `embed_new_entities_task`, with
distinct task IDs (ruling out a duplicate-dispatch loop) but identical
per-user payloads, some referencing articles analyzed 10 days earlier
(`entities_analyzed_at` already set) — meaning the dispatches were
redundant even at publish time. Traced to `article_analysis.py:367`.

**Fix:** Deleted the per-article dispatch entirely
(`app/tasks/article_analysis.py`). New entities are now picked up
exclusively by the existing hourly sweep, whose own docstring already
described itself as "an hourly safety net" — the per-article dispatch
was an unnecessary latency optimization, not a requirement.

---

## 2. `analyze_article_task`'s `max_retries=3` was dead configuration

**Problem:** `analyze_article()` caught every exception internally
(including transient OpenAI rate limits/timeouts) and returned a
`{"status": "failed"}` dict rather than re-raising. Celery only retries
a task when the task function actually raises — a caught-and-returned
error looks like a *successful* task execution to Celery. The declared
`max_retries=3` on `analyze_article_task` never fired for any failure
class; an article was permanently marked failed on the very first
transient hiccup.

**How found:** Test #3 (OpenAI outage during `structured_chat`) —
mocked `openai.RateLimitError` and traced the exception path live
through `analyze_article_with_llm()` → `analyze_article()` →
`analyze_article_task`, confirming Celery never saw a raised exception
reach the task boundary.

**Fix:** `analyze_article()` now re-raises errors in the existing
`TRANSIENT_CHAT_ERRORS` tuple (rate limit/timeout/connection — promoted
from a private name in `llm_client.py` to a shared one) instead of
swallowing them; `analyze_article_task` catches those specifically and
calls `self.retry()` with exponential backoff, matching the retry
pattern already used elsewhere in the codebase (`embedding.py`,
`extraction.py`). Non-transient errors keep the original
swallow-and-return-`"failed"` behavior unchanged.

---

## 3. `tagging.py` silently returned `[]` on transient LLM failures

**Problem:** `generate_tags_with_llm()` caught every exception
(including transient ones) and returned an empty list. Its caller,
`generate_tags()`, treated an empty list as `status: "completed",
message: "no tags generated"` — a permanent, successful-looking
outcome. An OpenAI outage was indistinguishable from the model
legitimately finding zero tags, with no retry and no distinguishable
failure signal anywhere.

**How found:** Same Test #3 investigation as #2 above, tracing the
`structured_chat` blast radius across call sites — this was the worst
of the three patterns found (silent data loss, vs. #2's inert-but-loud
failure, vs. `research.py`'s correctly-designed crash-and-recover).

**Fix:** Same shape as #2. `generate_tags_with_llm()` re-raises
`TRANSIENT_CHAT_ERRORS` instead of swallowing them; `generate_tags()`
propagates that up (with rollback) instead of returning `[]`;
`generate_tags_task` retries with exponential backoff, falling through
to a terminal `"failed"` status after `max_retries` is exhausted.

---

## 4. Unguarded `.delay()` at ingestion — stuck items with no recovery

**Problem:** `ingest_url()`'s `extract_metadata.delay()` call (and
several similar `.delay()` calls inside `extraction.py`) had no
try/except. A broker hiccup at dispatch time — confirmed reachable via
a Redis restart's several-second `BusyLoadingError` window, during
which `docker ps` still reports the container "healthy" — left the
content item permanently stuck at `processing_status='pending'` with no
sweep anywhere that detects or retries it.

**How found:** Test #1. Timing a precise mid-pipeline Redis kill proved
unreliable, but a `POST /content` call during Redis's post-restart
`BusyLoadingError` window hit the same bug class earlier in the
pipeline by accident — confirmed via a stuck item's `id` sitting at
`pending` indefinitely in Postgres.

**Fix:** Rather than wrap every `.delay()` call site individually
(fragile — easy to miss one, doesn't address the same class of gap
elsewhere), added `recover_stale_pending_items` — a beat task
(`app/tasks/extraction.py`, every 5 minutes, matching
`recover_orphaned_runs_task`'s existing convention) that re-dispatches
`extract_metadata` for any item still `pending` after a 10-minute
staleness window. `extract_metadata` sets `processing_status='processing'`
as soon as it actually starts, so a still-`pending` item past that
window reliably means the original dispatch never reached a worker.

---

## 5. Refresh-token double-submit race (JWT flow)

**Problem:** `/auth/refresh` read `record.revoked_at`, checked it in
Python, and only wrote the revocation in a later `db.commit()`. Two
concurrent requests with the same refresh token could both read
"not yet revoked" before either committed, both passing theft
detection and both minting valid new token pairs from the same
consumed token.

**How found:** Live concurrency test — two real OS threads via
`TestClient`, `threading.Barrier` forcing simultaneous entry. Same-instant
localhost requests did **not** trigger the race 5/5 times (Postgres
round-trip too fast for true SQL-level overlap even with synchronized
thread entry); artificially widening the window to simulate realistic
network-latency-separated requests reproduced it reliably — both
requests returned 200 with distinct valid token pairs for the same
input token.

**Fix:** Replaced the read-then-write with a single atomic
conditional `UPDATE ... WHERE revoked_at IS NULL`
(`app/api/auth.py`), checking the row count to detect whether this
request actually won the race. READ COMMITTED serializes concurrent
UPDATEs on the same row, so the second request's WHERE clause reliably
sees the first's committed revocation and matches zero rows — theft
detection now fires correctly. Re-verified live with the same
widened-window test: exactly one request now succeeds, the other is
correctly rejected as theft.

---

## 6. Same TOCTOU race in the MCP OAuth refresh flow

**Problem:** `app/mcp/oauth.py`'s refresh-token grant did `r.get()`
then `r.delete()` on the Redis-backed refresh token — two separate
round-trips, not atomic. Same race shape as #5, different subsystem
(Redis GET+DELETE instead of a Postgres read-then-write).

**How found:** Code-reading, flagged alongside #5 in the original
candidate list as the same bug class; not independently live-tested
(the mechanism and fix are unambiguous once #5 was confirmed).

**Fix:** Replaced `GET` + `DELETE` with Redis's native `GETDEL`
(atomic get-and-delete, available since Redis 6.2; confirmed present
on the project's Redis 7.4 and in the `redis-py` client in use). Also
fixed a latent failure-mode regression this introduced: if token
issuance fails *after* `GETDEL` has already consumed the old token, the
old code's "issue new tokens first, then delete old" ordering meant a
failure left the old token intact for retry — `GETDEL`'s atomicity
requires consuming the token first, so on a downstream failure the
handler now re-stores the deleted entry via `setex` rather than leaving
the caller with no valid refresh token at all.

---

## 7. Three beat fan-out tasks missing the overlap-guard lock

**Problem:** `cluster_all_users_task` and `consolidate_all_users_task`
already used a Redis-backed lock (`app/core/task_locks.py`) so a
still-draining previous run can't overlap with the next beat firing.
`deduplicate_entities_task` (weekly), `embed_new_entities_beat_task`
(hourly), and `backfill_missing_entities_task` (daily) had no such
guard — a slow run overlapping its own next firing causes duplicate
work, and at worst compounds queue backlog the way finding #1 did.

**How found:** Code-reading during the original failure-mode survey
(Tier 2, item #5) — a plain config/coverage gap, not something
requiring a live trigger to confirm.

**Fix:** Added `acquire_run_lock()` calls to all three tasks, matching
the existing convention exactly (TTL sized to the beat cadence minus a
safety margin: 6 days for weekly, 50 minutes for hourly, 20 hours for
daily).

---

## 8. `hybrid_search`'s fail-open lanes had no logging

**Problem:** `_semantic_search` and `_entity_search` both catch every
exception and return `[]` by design (a documented, intentional
contract — one lane's outage shouldn't break the others in a
multi-lane fusion). But neither logged anything on that path, and the
whole `hybrid_search.py` module had no logger at all — a real OpenAI or
Redis outage during search was completely indistinguishable from a
legitimate zero-result query, with zero signal anywhere that anything
had degraded.

**How found:** Code-reading (Tier 2, item #6).

**Fix:** Added a module-level logger and a `logger.warning()` call at
each lane's outer exception handler, identifying the user and the
underlying error. Deliberately did not change either lane's return
contract or `hybrid_search()`'s signature — this fixes the
observability blind spot without touching any of the 4 call sites that
depend on `hybrid_search()`'s existing `list[dict]` return type.

---

## 9. `register()` had 7 sequential commits with no outer transaction

**Problem:** New-user registration wrote across 7+ separate
`db.commit()` calls (user row, verification token, welcome article,
highlights, two example articles, a vinyl record), with `.delay()`
dispatches interleaved between them. A crash partway through left a
partially onboarded user — e.g. an account with no verification token,
or content items with no owning user fully committed.

**How found:** Code-reading (Tier 2, item #7) — low-likelihood (the
whole sequence is fast, no LLM calls in the hot path) but a real gap.

**Fix:** Collapsed the sequence to a single `db.commit()`
(`app/api/auth.py`) — intermediate steps now `db.flush()` to obtain
auto-generated IDs without committing. All background dispatches (email
verification, article extraction, Discogs fetch) now fire only after
that single commit succeeds, so nothing gets queued against rows that
didn't actually persist. Verified via the existing
`test_register_creates_onboarding_content` test, which still passes
unchanged.

---

## 10. Database connection pool exhaustion presented as a silent 30s hang

**Problem:** SQLAlchemy's default `pool_timeout` (30s) was never
overridden. With only 5 total connections (`pool_size=3,
max_overflow=2`), exhaustion under load meant a request blocked
silently for 30 seconds before finally raising — indistinguishable from
a slow query rather than a fast, clear failure.

**How found:** Code-reading (Tier 2, item #8) — flagged as "worth
knowing before testing this one, so the failure shape isn't a
surprise," not independently live-tested this pass.

**Fix:** Set `pool_timeout=10` explicitly (`app/core/database.py`).
Still gives brief contention a chance to clear, but exhaustion now
surfaces roughly 3x faster and as an explicit, documented timeout
value rather than an unexamined library default.

---

## Deliberately not fixed

**Solo-pool `time_limit`/`soft_time_limit` (Test #2).** Confirmed dead
under `--pool=solo` via live-fire test (a task with `soft_time_limit=5,
time_limit=8` ran a full uniniterrupted 60-second sleep). The real fix
is an infrastructure change — switching Celery's pool type off
`--pool=solo`, or adding an external watchdog process — not an
application code change. Scoped out of this batch by explicit decision:
it changes worker concurrency behavior for every task, not just hung
ones, and deserves its own testing pass rather than being bundled here.
Documented in `ARCHITECTURE.md` §9 and
`docs/retros/2026-07-28-failure-injection-plan.md`.

---

## Verification

- `ruff check app/` — clean on all touched files.
- Full backend test suite: 812/820 passing. The 8 failures are
  pre-existing, content-dependent retrieval/tagging quality evals
  (`tests/evals/`) that call real OpenAI and compare against a fixed
  recall baseline against live dev-database content — confirmed
  identical on the unmodified baseline via `git stash`, unrelated to
  any change in this pass.
- `tests/mcp/test_oauth.py`'s `FakeRedis` test double needed a
  `getdel()` method added to match fix #6 — done, all 30 oauth tests
  pass.
- Fix #5 (refresh-token race) and fix #2/#3's retry paths were
  re-verified live after the fix, not just unit-tested in isolation —
  same live-fire methodology as the original failure-injection tests.
