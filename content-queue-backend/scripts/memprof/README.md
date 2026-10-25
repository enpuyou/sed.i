# memprof: black-box memory benchmark for the sed.i backend

Starts the backend as production does (uvicorn API, `celery -A app.core.celery_app worker --loglevel=info --pool=solo --beat`),
drives it over HTTP against fake local servers, samples process memory at 20 Hz, and writes one JSON file.
The same harness runs unchanged against any backend checkout, so a "before" and an "after" run are comparable.

## Run

Use the backend `.venv` interpreter (it has psycopg2, redis, psutil, numpy, fastapi, uvicorn). Docker containers
`content_queue_postgres` (localhost:5433, postgres/postgres) and `content_queue_redis` (localhost:6379) must be running.

```bash
cd /Users/enpuyou/cmpsc/projects/content-queue/content-queue-backend
.venv/bin/python scripts/memprof/run_benchmark.py --backend-dir <backend dir> --label before|after \
    --out <results.json> [--scenarios S1,S4_worker_article_1MB] [--commit <str>]
.venv/bin/python scripts/memprof/compare.py before.json after.json > table.md
```

- `--backend-dir`: the checkout to test. Every subprocess runs with this as cwd and `PYTHONPATH`, and the harness aborts unless
  `import app` resolves inside it. Nothing is written into it (no `.pyc`, beat schedule goes to a temp dir, `.env` is never read:
  `ENV_FILE` points at a nonexistent file).
- `--label`: the scratch database is `memprof_<label>`.
- `--scenarios`: comma list of ids or id prefixes (`S1`, `S4`, `S5_worker_article_50MB`). Default: all.
- `--commit`: label for the code under test. Auto-detected for a git checkout (`working-tree (HEAD x + uncommitted changes)` if dirty);
  required for a non-git snapshot. The JSON also holds `source_hash`, a SHA-1 over `app/**/*.py` and `alembic/versions/*.py`.
- `--settle` (default 30 s) and `--reps` (cap) exist for smoke tests; do not use them for comparable runs.
- Raw samples go to `<out without .json>/<scenario>_rep<k>.csv` (`t_s, rss_mb, children_rss_mb, nchildren, footprint_mb`).

Ports used: API 8010, fake OpenAI 9100, page server 9200. A full run takes about 65 to 75 minutes on the baseline code
(S5 alone about 9 minutes). Cleanup (stop processes, drop `memprof_<label>`, `FLUSHDB` on Redis db 15) runs in a `finally` block;
the JSON is rewritten after every scenario so partial results survive a crash.

## Isolation

Environment of every subprocess is built from scratch (only PATH/HOME/TMPDIR/LANG are inherited): `DATABASE_URL` (scratch db),
`REDIS_URL=redis://localhost:6379/15`, test `SECRET_KEY`, `OPENAI_API_KEY=dummy`, `OPENAI_BASE_URL` (fake server), `DEBUG=true`,
blank Cohere/Braintrust/Sentry/PostHog/OTEL/Resend/AWS/Discogs/Prefect keys, `PREFECT_ENABLED=false`, `S3_STORAGE_ENABLED=false`.
`HTTP(S)_PROXY` points at a dead port with `NO_PROXY=127.0.0.1,localhost`, so any accidental outbound call fails fast.
The test user is inserted directly into the database (hash from `app.core.security.get_password_hash`) and a JWT comes from
`POST /auth/login`; `/auth/register` is never called. The database `content_queue` and Redis db 0 are never touched
(`create_db`/`drop_db` only accept `memprof_*` names; Redis is `FLUSHDB`ed only after asserting db 15).
A watchdog kills the API/worker process group when parent + child RSS exceeds 6000 MB and records `killed_at_mb` (a result).

## Data and fakes

- Seed (`seed.py`, fixed RNG seeds, identical rows every run): users `u100`, `u500`, `u2000` with that many completed items each
  (~40 KB HTML `full_text`, ~6,000 words from a 30k-word Zipf vocabulary, 1536-dim embedding, tags, auto_tags, summary,
  10 % read, 30 % of items with 5 chunks). `search_vector` is filled by the production trigger (about 52 KB per row in text form).
  `u2000` also owns a list containing all 2000 items. `uw` is an empty user for the worker scenarios.
  Rows are written with an explicit column list, so a fix that adds a NOT NULL column without a default will break seeding;
  new nullable columns stay NULL.
- Fake OpenAI (`fake_openai.py`): `/v1/embeddings` returns HTTP 400 for more than 2048 inputs per request (real behaviour),
  otherwise deterministic 1536-dim unit vectors. It does NOT enforce the real 300k-total-token or 8192-tokens-per-input limits.
  `/v1/chat/completions` returns tool-call arguments generated from the request's JSON schema. `/stats` counters are recorded per
  worker scenario (`embed_calls`, `embed_inputs`, `embed_rejected_400`, `max_inputs_in_call`, `chat_calls`).
- Page server (`pageserver.py`, `gen.py`): synthetic article HTML of 100KB to 50MB; a streamed 100 MB `application/zip` body.
- Known harness-independent noise: `analyze_article_task` logs "Instance <Entity> is not present in this Session" for every article
  with the canned analysis (seen in `worker_log.errors`). It is an app/fake interaction, identical before and after.

## Scenarios

API scenarios: fresh uvicorn per repetition, unmeasured warm-up (login, `GET /content?limit=1`, `GET /lists`, for S1 a duplicate POST
answered by the exact-match lookup) so first-request costs sit in the baseline, 3 s settle, scenario, 30 s settle. 3 repetitions.

| Id | What it does | Measured process |
|---|---|---|
| `S1_ingest_n{100,500,2000}` | user with N items; 5 sequential `POST /content` with new local URLs (rows deleted after each rep) | API |
| `S2_recommended_n2000` | `GET /content/recommended?limit=20`, 2000 items | API |
| `S3_list_content_n2000` | `GET /lists/{id}/content`, no query params, list of all 2000 items; records status, item count, bytes | API |
| `S3b_list_content_n2000_limit50` | same with `limit=50` (+ `offset`/`skip`=0 if present). Skipped (with `query_params_found`) when the OpenAPI schema has no `limit` parameter | API |

Worker scenarios run on an empty library (rows of the API scenarios are deleted) so beat backfill sweeps do not add noise.
Per repetition: fresh Redis db, fresh worker with the production command, one unmeasured 100KB warm-up article (first-use imports),
drain + 10 s, then baseline. Submission via `POST /content`; "drained" means all items `completed`/`failed`, broker queue and
`unacked` empty, and worker CPU under 5 % for 10 s. Then 30 s settle. Measured process: the worker parent (children are in
`peak_children_rss_mb`).

| Id | What it does | Reps |
|---|---|---|
| `S4_worker_article_{100KB,1MB,5MB,20MB}` | one synthetic article of that size | 3 (20MB: 2) |
| `S5_worker_article_50MB` | one 50 MB article, 6 GB watchdog | 1 |
| `S6_worker_nonhtml` | URL serving a streamed 100 MB `application/zip` | 1 |
| `S7_regression_small` | 20 distinct 100KB articles back to back; all must complete with chunks and embeddings | 2 |

Outcome fields (worker scenarios): `processing_status`, `processing_error`, `word_count`, `content_chunks` (rows), `embedding_present`,
`full_text_chars`, plus `pipeline_state` (`drained`/`timeout`/`killed`/`worker_died`), `fake_openai` counters and a `worker_log` digest.

## Metrics

See `metric_definitions` in the JSON. In short: RSS from psutil at 20 Hz (peak, baseline = median of the 2 s before the scenario,
delta = peak - baseline); footprint = macOS `/usr/bin/footprint <pid>` (phys_footprint, includes compressed pages) at about 1 Hz,
used for "settled" because the macOS compressor makes idle RSS unreliable. Each scenario has `reps[]` and `summary{metric:{median,min,max,n}}`.

## Interpreting

- Compare `peak_delta_mb` medians first; check `min`/`max` for overlap before calling a change real (API plateau effects and macOS
  compression add run-to-run spread; settled footprint after large pages varied by up to 150 MB in earlier runs).
- A lower peak is only a win if the outcome is equal or better: `compare.py` prints outcomes side by side. A fix that makes S4_20MB
  cheap by dropping chunks, or S7 incomplete, is a regression. Baseline S4_20MB/S5 end with 0 chunks because the fake server rejects
  the single oversized embed call.
- If S3 `items` or `bytes` shrink after a fix, the response shape changed (default limit); `S3b` shows the explicit paged call.
- `killed_at_mb` set means the 6 GB watchdog fired; compare it to the other side's peak.
- macOS numbers are not Railway (Linux) numbers; use them for before/after ratios.
