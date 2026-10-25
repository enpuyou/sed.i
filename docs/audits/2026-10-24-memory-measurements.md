# Backend memory measurements, 2026-10-24

All measurements are local (macOS, Python 3.11.12, backend `.venv`). No production, Railway, Redis or OpenAI access. `OPENAI_API_KEY=dummy` was set for all runs. Scratch scripts are in `<scratch>/` (h1.py, h2.py, h3.py, h4.py, h6.py, gen.py).

## Summary

| Hypothesis | Result | Key number |
|---|---|---|
| H1 import baseline; torch/ultralytics/cv2 not loaded at import | CONFIRMED | API 164 MB, worker 125 MB, bare fastapi+sqlalchemy 59 MB; torch/ultralytics/cv2/fitz/prefect/boto3/instructor/braintrust/opentelemetry not in `sys.modules` after import |
| H2 `find_existing_active_item` fallback loads full rows | CONFIRMED (SQLite simulation) | N=2000: 101 MB tracemalloc peak / 142 MB RSS; with `load_only`: 3 MB / 16 MB |
| H3 extraction peak vs page size | CONFIRMED | peak is about 36-41x page size (1 MB: 41 MB, 5 MB: 189 MB, 20 MB: 730 MB) |
| H4 chunking peak | CONFIRMED, moderate | 1 MB HTML: 14 MB RSS; 5 MB: 67 MB RSS (about 13x) |
| H5 Celery result backend Redis usage | INCONCLUSIVE for live Redis (none available); offline size estimate shows it is small | 121-509 B value + 53 B key per result; roughly 0.3-0.7 KB per result in Redis |
| H6 worker leak over 200 iterations | REFUTED (plateau) | RSS 158-168 MB across 200 iterations, no monotonic growth |
| YOLO subprocess | MEASURED | 631 MB (8-page, 1.1 MB PDF) and 689 MB (6-page, 8.8 MB PDF) max RSS |

## H1: import baseline and attribution

Method: `h1.py` imports the target in a fresh interpreter and reports `psutil` RSS before and after (3 runs each, run-to-run spread under 1 MB). `-X importtime` logs (`imp_api.txt`, `imp_worker_only.txt`) used for time attribution. Per-package RSS delta measured by importing each package alone in a fresh interpreter.

| Process | RSS at start | RSS after import |
|---|---|---|
| bare interpreter | 17 MB | 17 MB |
| `import fastapi, sqlalchemy` | 17 MB | 59 MB |
| `import app.main` (API) | 17 MB | 164 MB |
| `import app.core.celery_app` (worker; imports all task modules at celery_app.py:98) | 17 MB | 125 MB |

Heavy packages found in `sys.modules` after import: `trafilatura`, `numpy`, `openai` only (both API and worker). Not loaded: torch, ultralytics, cv2, fitz, prefect, boto3, instructor, braintrust, opentelemetry, sentence_transformers.

RSS cost of each package imported alone (includes its own dependencies and the 17 MB interpreter excluded):

| Package | Delta MB | Loaded at module import? | Imported by |
|---|---|---|---|
| ultralytics | 188 | no (lazy) | `app/tasks/extraction_implementations.py:402` (inside `_get_yolo_model`, runs in YOLO subprocess) |
| torch | 161 | no (lazy) | `extraction_implementations.py:557`, `app/tasks/_yolo_worker.py:30` |
| prefect | 75 | no (lazy) | `app/workflows/ingestion.py:34` (top-level of that module), `extraction.py:509` (lazy) |
| instructor | 56 | no (lazy) | `app/core/llm_client.py:547` |
| openai | 42 | yes | `app/core/llm_client.py:36,38` |
| braintrust | 40 | no (lazy) | `celery_app.py:146`, `llm_client.py:209,783` |
| fitz (pymupdf) | 35 | no in API/worker at startup; loaded in worker on first PDF | `extraction_implementations.py:29` (top-level, module imported lazily), `extraction.py:671` |
| cv2 | 33 | no | not imported by app code (ultralytics dependency) |
| trafilatura | 30 | yes | `app/tasks/extraction.py:13` |
| fastapi | 29 | yes (API) | `app/main.py` and routers |
| sqlalchemy | 25 | yes | models, database |
| boto3 | 24 | no (lazy) | `llm_client.py:222`, `storage.py:25` |
| numpy | 11 | yes | `pgvector`, trafilatura dependencies |

Importtime cumulative (ms, single run): API total about 1950 ms; largest roots openai 339, fastapi 319, mcp 258, sqlalchemy 113, httpx 102, pgvector 101, numpy 97, trafilatura 92. Worker total about 1030 ms; openai 360, sqlalchemy 114, celery 101, trafilatura 75.

Findings:
- torch, ultralytics and cv2 are not loaded in the API or worker parent at import time, consistent with ADR 0007. `extract_with_yolo` runs them in a `subprocess.run` child (`extraction_implementations.py:~485`).
- `app.main` costs about 40 MB more than the worker; the difference is fastapi, `mcp`, posthog, jose, cryptography. The API also imports `llm_client` (openai, 42 MB) and `trafilatura` (30 MB) although it only uses them for ingest dispatch; whether the API needs `app.tasks.extraction` at import was not traced.
- Per-package deltas are not additive (shared dependencies such as pydantic, httpx, numpy).
- `prefect` is imported at the top of `app/workflows/ingestion.py`; it is not loaded by `app.main` or `celery_app` in this measurement, so it is not in the baseline.
- `fitz` becomes resident in the worker parent after the first PDF (`_process_pdf` imports it at line 671 and `extraction_implementations` at line 29), adding about 35 MB for the life of the worker even though YOLO work is in a child.

## H2: `find_existing_active_item` fallback

Method: No Postgres (Docker daemon not running; no local postgres or redis binaries), so SQLite plus a SQLAlchemy model mirroring the relevant columns (id, user_id, original_url, deleted_at, title, description, full_text ~40 KB ASCII, embedding 1536 float32 returned as a numpy float32 array as pgvector does, word_count). Real `ContentItem` has 32 columns; the mimic has 9, so ORM overhead per row is understated. Query is `db.query(M).filter(user_id==, deleted_at IS NULL).all()`, one fresh process per cell, tracemalloc started right before the query, RSS from `psutil` before/after. SQLite streams rows; psycopg buffers the whole result client-side before ORM materialization, so Postgres peak is likely equal to or higher than these numbers.

| N | Mode | tracemalloc peak MB | RSS delta MB |
|---|---|---|---|
| 100 | full `.all()` | 5.1 | 7.2 |
| 100 | `load_only(id, original_url)` | 0.2 | 0.8 |
| 500 | full | 25.3 | 39.6 |
| 500 | load_only | 0.8 | 4.0 |
| 2000 | full | 101.2 | 141.9 |
| 2000 | load_only | 3.1 | 15.9 |

About 50 KB per item in tracemalloc (40 KB text + 6 KB embedding + object overhead), about 70 KB per item RSS. Saving with `load_only`: about 97% (tracemalloc) and 89% (RSS) at N=2000. The fallback path runs on every ingest of a URL with no exact `original_url` match (every new URL), in the API process.

## H3: trafilatura + xml_to_html + BeautifulSoup

Method: `h3.py`, fresh process per size, after a 50 KB warm-up. Synthetic article HTML (nav, h1/h2, paragraphs with links, figures, footer; generator in `gen.py`). Steps as in `extraction.py:617`: `trafilatura.extract(include_images/links/tables, output_format="xml")`, `xml_to_html(xml, original_html=page)`, `BeautifulSoup(html, "html.parser").get_text()`. Peak is `ru_maxrss` minus `ru_maxrss` after warm-up; the page bytes are included in the delta (in production `resp.content` is also resident).

| Page MB | XML MB | HTML MB | Peak after trafilatura MB | Peak after xml_to_html MB | Peak after BS4 MB | trafilatura s | xml_to_html s | Peak / page |
|---|---|---|---|---|---|---|---|---|
| 0.1 | 0.1 | 0.1 | 2 | 3 | 3 | 0.0 | 0.0 | 29x |
| 1.0 | 1.01 | 1.00 | 32 | 37 | 41 | 0.6 | 0.3 | 40x |
| 5.0 | 5.07 | 5.02 | 163 | 182 | 189 | 3.2 | 2.1 | 38x |
| 20.0 | 20.28 | 20.08 | 589 | 708 | 730 | 25.9 | 15.6 | 37x |

Baseline worker RSS in these runs was 128-172 MB, so a 20 MB page puts the process near 900 MB. Ratio is stable at about 37-40x; trafilatura accounts for about 80% of the peak. Note: a first synthetic page with a different vocabulary produced almost no HTML out of `xml_to_html` (paragraphs dropped); that run was discarded and the cause was not investigated. Numbers above use the corrected generator (output size about equal to input size). Real pages have far more markup than text, so the real XML and HTML outputs are smaller than here, but the lxml tree cost scales with input nodes.

## H4: `split_article_into_chunks`

Method: `h4.py`, fresh process, article HTML 1 MB and 5 MB (same generator), warm-up on 50 KB, input string created before the baseline. RSS run and a separate tracemalloc run.

| HTML MB | Chunks | RSS peak delta MB | tracemalloc peak MB | Time s (no tracemalloc) |
|---|---|---|---|---|
| 1.0 | 426 | 14.0 | 12.5 | 0.1 |
| 5.0 | 2118 | 66.9 | 61.3 | 0.6 |

About 13x input. The function calls `html_to_plain` on the full document and again twice per section, each producing regex copies. Not measured here but relevant: `generate_chunk_embeddings` passes all chunks in one `llm_client.embed` call and holds the embeddings as Python lists. Python `list[float]` of 1536 elements measured at 21 MB for 426 chunks and 105 MB for 2118 chunks (tracemalloc); this is an estimate of the post-embedding footprint, since the OpenAI SDK response objects were not exercised. (OpenAI's batch input limit is 2048 inputs, so the 2118-chunk case would also fail at the API; not tested.)

## H5: Celery result backend

Method: no Redis server or `redis-cli` on this machine, so Redis memory was not measured. Instead `celery_app.backend._get_result_meta(..., encode=True)` was used offline to produce the exact stored value for typical return dicts.

| Return value | Stored value bytes | Key bytes (`celery-task-meta-<uuid>`) |
|---|---|---|
| `None` | 121 | 53 |
| `{"dispatched":100,"status":"completed"}` | 159 | 53 |
| `{"status":"completed","chunk_count":426}` | 160 | 53 |
| `{"item_id":..., "status":"ok"}` | 184 | 53 |
| failure dict with 300-char error string | 509 | 53 |

Redis per-key overhead (dict entry, object header, expiry entry) is typically 70-100 bytes, so about 0.3-0.7 KB per result. Backend is `RedisBackend` on the same `REDIS_URL` as the broker; `result_expires` is the Celery default (86400 s, confirmed via `celery_app.backend.expires`); `grep -rn ignore_result app` returns no matches, so every task stores a result. Beat alone fires about 600 tasks/day (two 5-minute scanners at 288 each plus daily/weekly jobs), plus per-item tasks, so well under 1 MB/day from beat. `task_track_started=True` writes an extra STARTED record that is overwritten by the final result (same key). Exceptions store tracebacks (not measured; typically several KB). Verdict: result payloads are small; stated hypothesis cannot be confirmed with a live number. Caveat: `ignore_result` is unset, so result-dependent memory risk exists only if a future task returns a large value.

## H6: leak check

Method: `h6.py`, 200 iterations on a 500 KB synthetic page, RSS printed every 20 iterations. Variant `x2h_only`: `xml_to_html` on a pre-extracted XML plus BS4 get_text. Variant `full`: `trafilatura.extract` + `xml_to_html` + BS4 each iteration.

- `x2h_only`: 158, 161, 158, 159, 159, 159, 161, 158, 159, 158 MB
- `full`: 165, 165, 166, 166, 166, 168, 168, 158, 159, 160 MB

No monotonic growth; RSS stays within about 10 MB of the first sample. No leak in these code paths. `worker_max_tasks_per_child=1000` (celery_app.py:29) has no effect under `--pool=solo` (not tested here; stated in Celery docs and in the task brief), so it is not a mitigation. A single page-size spike is not returned to the OS promptly: the 20 MB case in H3 would leave glibc/malloc arenas fragmented on Linux; not measured here.

## YOLO subprocess

Method: `_yolo_worker.py` invoked as the parent does (`python -P app/tasks/_yolo_worker.py`, pickled `(pdf_bytes, url)` on stdin) under `/usr/bin/time -l`, `HF_HUB_OFFLINE=1` (model already in `~/.cache/huggingface`), `OMP_NUM_THREADS=2`. No network or LLM calls in this code path.

| PDF | Pages | Size | Max RSS | Peak memory footprint | Output HTML |
|---|---|---|---|---|---|
| `pdf/TabPFN_copy.pdf` | 8 | 1.1 MB | 631 MB | 557 MB | 0.74 MB |
| `pdf/3d-printed-protein.pdf` | 6 | 8.8 MB | 689 MB | 604 MB | 2.76 MB |

This is lower than the 1.5 GB in ADR 0007; macOS torch wheels and the CPU-only path differ from Railway Linux, and only 6-8 page PDFs were available. The subprocess memory is returned on exit. The parent additionally holds `pdf_bytes`, a pickled copy of `pdf_bytes` for stdin, and the pickled HTML result (about 2x PDF size plus output).

## Ranked contributors and recommended fixes

1. YOLO subprocess for PDFs: 630-690 MB transient, on top of the worker parent (125 MB). Fix: keep one PDF at a time (already true with solo pool); ensure Railway worker limit leaves at least 1 GB headroom above parent RSS; optionally cap page count or PDF size before launching.
2. trafilatura + xml_to_html on large pages: about 37-40x page size, 730 MB for a 20 MB page. Fix: reject or truncate downloads above a size cap (for example 5 MB, about 190 MB peak) before calling `trafilatura.extract`, using `Content-Length` and a streaming read; `requests.get` at `extraction.py:~605` currently reads the whole body with no cap.
3. `find_existing_active_item` fallback: linear in the user's library, about 50 KB per item, 101 MB at 2000 items, in the API process on every new-URL ingest. Fix: `load_only(ContentItem.id, ContentItem.original_url, ContentItem.is_archived)` (the caller reads `.id` and `.is_archived`), or `yield_per`, or a stored `normalized_url` column with an index.
4. Import baseline: API 164 MB, worker 125 MB. Fix candidates: lazy-import `trafilatura` and `openai` where only some code paths need them (about 30 and 42 MB standalone); avoid importing `fitz` into the worker parent on the PDF path (35 MB resident after the first PDF) by moving the `import fitz` in `_process_pdf` into only the code that needs it.
5. Chunking and chunk embeddings: 13x HTML size for splitting, plus about 49 KB per chunk for Python-list embeddings held until commit (105 MB for 2118 chunks). Fix: embed and insert in batches of at most 100 chunks and compute `html_to_plain` once; also required to stay under OpenAI's per-request input limit.
6. Celery result backend: negligible by size. Fix (hygiene only): `ignore_result=True` on scanner and fire-and-forget tasks, and a shorter `result_expires`.

## Caveats

- macOS arm/x86 (Darwin 25.4) vs Railway Linux: allocator behaviour, torch builds, and `ru_maxrss` units differ. Linux glibc often keeps freed arenas, which would make post-peak RSS higher than seen in H6.
- H2 used SQLite and a 9-column mimic of `ContentItem` (real: 32 columns); no Postgres was reachable and Docker was not running. Per-item numbers for the real driver may be higher.
- H3/H4/H6 use synthetic HTML; real pages with heavy markup, scripts and deeper DOMs will differ. The ratios are from one generator.
- H5 has no live Redis measurement; Redis per-key overhead is an assumption (70-100 B), not measured.
- RSS peaks use `ru_maxrss` (process lifetime high-water mark), measured above a post-warm-up baseline in fresh processes; single run per cell except H1 (3 runs).
- No repo files other than this report were modified. Scratch SQLite databases (about 122 MB) are in the scratchpad `h2db/` directory.
