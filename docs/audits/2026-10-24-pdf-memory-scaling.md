# PDF extraction memory vs page count, baseline code, 2026-10-24

Question: how does YOLO PDF extraction memory scale with page count, where does it grow, and can extraction run in page ranges. Baseline code only (read-only snapshot, no repo edits). Local only, no network.

Code under test: `scratchpad/before/content-queue-backend/app/tasks/{extraction_implementations.py,_yolo_worker.py,extraction.py}`. Platform: macOS 26.4 (Darwin 25.4), arm64, Python 3.11.12, repo `.venv`. Earlier result for context: `2026-10-24-memory-profiling-live.md` (child 657-689 MB on 6-8 page PDFs).

## 1. Method

Test PDFs (`scratchpad/scale/*.pdf`), built with PyMuPDF `insert_pdf` by cycling single pages of `pdf/TabPFN_copy.pdf` (8 pages) and `pdf/3d-printed-protein.pdf` (6 pages), saved with `garbage=4, deflate=True`:

| File | Pages | Source pages | Size |
|---|---|---|---|
| mix_8 | 8 | both PDFs | 1.0 MB |
| mix_25 | 25 | both, cycled | 9.5 MB |
| mix_50 | 50 | both, cycled | 9.5 MB |
| mix_100 | 100 | both, cycled | 9.5 MB |
| mix_200 | 200 | both, cycled | 9.6 MB |
| img_100 | 100 | 3d-printed-protein pages only (large images), cycled | 8.5 MB |

`garbage=4` deduplicates repeated image objects, so the file size stays near 9 MB while the number of rendered pages grows. The rendering and crop work per page is unchanged by this; only the input byte size is smaller than a non-deduplicated 200-page file would be.

Invocation (driver `scratchpad/scale/run.py`), same as `extract_with_yolo`: `/usr/bin/time -l python -P app/tasks/_yolo_worker.py`, stdin = `pickle.dumps((pdf_bytes, url))`, `HF_HUB_OFFLINE=1`, `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, `PYTHONPATH` = snapshot root, cwd = snapshot root. Dummy `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` (config import needs them; `.env` not read). A watchdog sampled child RSS at 5 Hz and would kill the run above 6 GB. No run came close. Metrics: `maximum resident set size` and `peak memory footprint` from `time -l`, wall time, stdout size, decoded HTML size. 3 runs for 8/25/50 pages, 1 for 100/200/img_100.

Parent-side overhead (`scratchpad/scale/parent.py`): a fresh process imports `app.tasks.extraction` (worker-equivalent import), loads the PDF bytes and the saved HTML, then measures `ru_maxrss` growth for (A) the pickled payload + pickled stdout + `pickle.loads` result, as held in `extract_with_yolo`, and (B) the real `_process_pdf` run on that HTML with `extract_with_yolo` stubbed.

In-child stage trace (`scratchpad/scale/trace.py`): a mirror of `_extract_yolo_sync` with RSS markers after model load, prescan, every 25 pages, join, wrap, and pickle.

## 2. Results

### 2.1 YOLO child, per run

| Pages | PDF | Runs | Max RSS (MB) | Peak footprint (MB) | Wall (s) | Output HTML (MB) | HTML per page (KB) |
|---|---|---|---|---|---|---|---|
| 8 | mix_8 | 3 | 635, 637, 637 | 549, 551, 551 | 12.2, 12.2, 12.0 | 0.74 | 92 |
| 25 | mix_25 | 3 | 680, 685, 719 | 630, 640, 631 | 16.8 x3 | 5.42 | 217 |
| 50 | mix_50 | 3 | 613, 707, 765 | 640, 632, 648 | 24.2, 23.4, 22.9 | 11.19 | 224 |
| 100 | mix_100 | 1 | 781 | 748 | 35.9 | 24.42 | 244 |
| 200 | mix_200 | 1 | 963 | 990 | 61.5 | 49.07 | 245 |
| 100 (large images) | img_100 | 1 | 893 | 902 | 38.9 | 45.72 | 457 |

No run aborted; highest value 990 MB footprint, far below the 6 GB limit. All runs returned code 0. Output HTML hashes are identical across the 3 runs for each of mix_8, mix_25, mix_50 (sha256 prefixes f10e1eba8e3fc847, e8c6054b4f232699, 683b6bb820562a06), so the baseline is deterministic and usable as a golden reference.

Observations:
- Floor (8 pages) is about 635 MB RSS / 550 MB footprint: torch, ultralytics, model, fitz, app imports, model cold load. In the trace this is 299 MB after imports, 493 MB after model load, 523 MB after prescan, about 670 MB after the first YOLO inference (inference arena allocation).
- Growth is roughly linear in output size: RSS +1.7 MB per page and footprint +2.3 MB per page between 8 and 200 pages (about 7 MB RSS and 9 MB footprint per MB of output HTML). The figure-heavy img_100 (457 KB HTML per page) reaches 893-902 MB at 100 pages, about 120 MB above the text-mixed mix_100.
- Run-to-run RSS at 25 and 50 pages varies by up to 150 MB (613-765 MB at 50 pages); footprint varies by 16 MB. Use footprint for scaling and RSS as an upper bound.
- Wall time: about 0.26 s per page after about 9-10 s of fixed cost (cold model load plus imports plus prescan). Prescan alone: 0.37 s (8 pages), 1.7 s (50), 6.2 s (200), about 0.03 s per page. At this rate the 300 s subprocess timeout is reached near 1,100 pages.
- Linear extrapolation to 1,000 pages at 245 KB of HTML per page: about 2.3 GB RSS in the child, not measured, and the timeout would cut it off first.

### 2.2 Where the child's memory sits (trace, mix_200 and img_100)

RSS in MB at markers. The macOS compressor lowers RSS mid-run (the 75-125 page rows for mix_200), so read peak, not intermediate RSS.

| Marker | mix_200 RSS / peak | img_100 RSS / peak |
|---|---|---|
| after imports | 299 / 299 | 298 / 298 |
| after model load | 493 / 493 | 398 / 402 |
| after prescan | 523 / 524 | 391 / 418 |
| page 1 | 668 / 668 | 541 / 542 |
| page 25 | 698 / 744 | 621 / 621 |
| page 50 | 752 / 752 | 429 / 621 |
| last page, loop done | 488 / 760 | 475 / 621 |
| after `"\n".join(html_pages)` | 608 / 760 | 571 / 621 |
| after `_wrap_html` | 706 / 760 | 661 / 661 |
| after `pickle.dumps(html)` | 804 / 804 | 745 / 746 |
| HTML size at end | 49.1 MB | 45.7 MB |

The per-page loop peaks within the first 25-50 pages (760 MB, 621 MB) and then does not rise. Memory after the loop minus before the loop is flat or lower. The last three steps each add one copy of the full HTML (+100 to +120 MB at 49 MB of HTML, about 2 to 2.5x due to allocator behavior and the str-to-bytes encoding in pickle) and set the final peak. In the real worker run, the same steps give 963 MB.

### 2.3 Parent side

The parent holds the PDF (`resp.content`) for the whole call. Growth in `ru_maxrss` over the parent's imported baseline:

| Pages | PDF (MB) | HTML (MB) | Baseline RSS (MB) | (A) payload + stdout + `pickle.loads` | (B) `_process_pdf` extra | Total peak growth (MB) |
|---|---|---|---|---|---|---|
| 8 | 1.0 | 0.7 | 156 | 3 | 5 | 9 |
| 25 | 9.5 | 5.4 | 179 | 26 | 46 | 72 |
| 50 | 9.5 | 11.2 | 196 | 43 | 90 | 133 |
| 100 | 9.5 | 24.4 | 236 | 95 | 192 | 286 |
| 200 | 9.6 | 49.1 | 310 | 157 | 382 | 539 |
| img_100 | 8.5 | 45.7 | 298 | 146 | 344 | 490 |

Parent peak growth is about 11 MB per MB of output HTML. At 200 pages the parent's added memory (539 MB) exceeds the child's growth above its floor (about 330 MB). `_process_pdf` parses the HTML with BeautifulSoup twice (`html_soup` at line 681, `body_soup` at line 724), serializes twice (`str(html_soup)`, `str(body_soup)`), and runs `re.sub` over `full_text` for the word count. Items downstream (DB write of `full_text`, chunking) also hold the same string; not measured here.

During the child's run, the parent holds the PDF bytes, the pickled payload (one extra copy of the PDF), and the incoming stdout bytes.

## 3. Where memory grows with pages (code reading)

In `_extract_yolo_sync` (`extraction_implementations.py:423`):

1. Per-page working data is released each iteration. `_detect_layout_yolo` renders the page to a PNG (a 150-dpi pixmap, "8-14 MB per page" per the code comment), writes it to a temp file, and `del`s the pixmap and bytes before inference. `results`, `regions`, and `caption_rects` are locals that go out of scope per page. `_extract_page_content` crops each figure region to a pixmap (`crop_dpi` 150), base64-encodes the PNG, and puts the result in `content_items`, which becomes the page string. No per-page image is retained after the page string is built. The trace confirms flat RSS across the loop.
2. What accumulates across pages: `html_pages` (list of every page's HTML string, including the base64 `<img src="data:image/png;base64,...">` figure crops). This is the dominant payload: 245 KB per page on average in mix_200, 457 KB in img_100. Everything else in `DocumentScanResult` is small: `scan.pages` is one `PageScan` per page (about 150 bytes; the full pickled scan is 30 KB at 200 pages), plus scalar counters.
3. The prescan (`_prescan_document`) keeps `page_block_data`, the per-page text block dicts for the entire document, until it returns (`page_block_data` is a local, so it is freed after the call). Prescan peak delta was 1-22 MB in these runs and 6 s of CPU at 200 pages. Not a growth driver at these sizes; it would grow linearly with text volume.
4. After the loop: `"\n".join(html_pages)` (copy 1), `_wrap_html` f-string (copy 2, with the body interpolated into the template), then in `_yolo_worker.py` `pickle.dumps(html)` (copy 3) while `html` and `html_pages` are still referenced. Each is a full-size copy. These set the child's peak (section 2.2).
5. `fitz` keeps the document open for the loop, and PyMuPDF caches parsed objects; I did not isolate this component. The trace shows no growth from it past the first 50 pages.

In the parent (`extract_with_yolo`, `_process_pdf`): `pdf_bytes` + `pickle.dumps((pdf_bytes, url))` + `result.stdout` (bytes) + `pickle.loads` (str) + two BeautifulSoup trees + two serializations (section 2.3).

Output size, not input size, drives the growth. The input PDF stayed near 9 MB from 25 to 200 pages while memory rose 680 to 963 MB.

## 4. Can the pipeline process page ranges independently

Reading `_extract_page_content`, `_detect_layout_yolo`, `_merge_continuation_paragraphs`, `_heading_level`, and `_block_to_html`: per-page extraction depends only on (a) the page itself, (b) `scan.pages[page_idx]`, and (c) `scan.repeating_texts`. `_merge_continuation_paragraphs` runs within a page, so paragraphs that continue across a page break are not merged today, and a range split would not change that. `_heading_level` uses a hardcoded `BODY_SIZE = 10.5` and compiled-regex globals (idempotent), so it needs no document-wide state. The YOLO model is a process-level cache and inference is per page.

### 4.1 Document-wide state

Computed once by `_prescan_document` over all pages, to pass to each range:

| State | Computed from | Used by |
|---|---|---|
| `scan.repeating_texts` | margin blocks on all pages; threshold `min(3, max(2, int(num_pages*0.4)))` depends on total page count | per-page header/footer drop in `_extract_page_content`; also feeds the per-page header/footer band fallback in prescan pass 3 |
| `scan.pages[i]` (`num_columns`, `header_y`, `footer_y`, `page_num_rect`) | per-page text and drawings, but header/footer fallback uses `repeating_texts` | per-page extraction and sorting |
| `scan.doc_num_columns`, `column_ambiguity` | majority vote and mean over all pages | logging only (not used in output) |
| `scan.title`, `scan.abstract` | first page | `_wrap_html` meta tags (head of the output) |
| `scan.total_pages` | `len(doc)` | `_compute_confidence_score` |

Accumulators updated per page and consumed only at the end by `_compute_confidence_score`: `images_attempted`, `images_extracted`, `yolo_conf_sum`, `yolo_conf_count`, `total_words_extracted`, `pages_with_content`.

### 4.2 What must be merged afterward

- Page HTML strings, in page order.
- The six accumulators. `yolo_conf_sum` is a float sum; adding per-range partial sums changes the association order and can change the last bits. To stay bit-identical, return the per-region confidences (a list of floats per page) and sum them sequentially in the merge step, or keep one running sum.
- Then one call to `_compute_confidence_score(scan)` and one `_wrap_html(body, confidence, title, abstract)`. The confidence value appears in the head and selects the warning banner that comes before the body, so the head cannot be written until all pages are done.

## 5. Design proposals

### Option 1: trim copies inside the single subprocess (recommended first)

No ranges, one model load, same output.

1. Worker writes the HTML as UTF-8 (`sys.stdout.buffer.write(html.encode("utf-8"))`) and the parent decodes it, replacing `pickle.dumps/loads` of a 49 MB string (removes one full copy in the child and the pickle copy in the parent).
2. Build the output from pieces without the join + f-string copies: split the `_wrap_html` template into prefix and suffix around `{body}`; after the loop compute confidence, write prefix, each page string, suffix to stdout in sequence. The child then holds `html_pages` only (about 1 copy) instead of 4.
3. Optionally cap: refuse or truncate above N pages (the profiling report already lists this as fix 14) and raise the timeout in step with the page count (0.26 s per page).

Estimated change: about 30-40 lines across `_yolo_worker.py`, `extract_with_yolo`, and `_wrap_html` (split into head/tail helpers), plus 1-2 tests. Expected effect at 200 pages: child peak from about 960 MB toward about 700 MB (floor 635 + one copy of HTML + loop working set); parent (A) drops by about one third. Parent (B), `_process_pdf`, is not touched and stays at about 11 MB per MB of HTML.

Equivalence risk: low. Output bytes are the same string; only transport changes. Edge: the f-string in `_wrap_html` formats `warning_html` and `body` with fixed whitespace (`    {warning_html}\n    {body}\n`), so the prefix/suffix split must reproduce it exactly. The byte comparison in section 6 detects any slip.

### Option 2: page ranges across several subprocess calls

Parent flow: call the worker once for the prescan (returns the `DocumentScanResult` fields, pickled 30 KB at 200 pages), then call it for each range `[a, b)` of K pages (K = 25-50) passing `(pdf_bytes, url, scan, a, b)`; each call returns page HTML strings plus per-page word counts, image counts, and region confidences. Parent merges (section 4.2) and wraps.

Alternatively, the worker stays alive and reads range commands from stdin (one model load), which avoids the reload cost but needs a framed protocol.

Costs and limits:
- Each fresh subprocess pays the import + model load (about 5-7 s here, from the 12 s total at 8 pages) unless a persistent worker is used. At K=50 and 200 pages that is 4 loads, about +20 s on 61 s.
- Child peak is bounded near the floor plus K pages (about 650-700 MB regardless of page count). The parent still receives and holds every page: its memory is not bounded (539 MB growth at 200 pages), and the worker plus `_process_pdf` plus downstream chunking still need the whole string. Ranges alone do not bound the Celery worker's peak. Bounding it also requires changing `_process_pdf` (see risk below) and the storage format.
- Estimated change: about 120-180 lines (split `_extract_yolo_sync` into prescan and range functions, new worker protocol, merge in `extract_with_yolo`, tests).

Equivalence risk: moderate. Per-page outputs are expected to be identical because per-page code reads only `scan.pages[i]` and `repeating_texts`, but the `repeating_texts` threshold depends on total page count, so the prescan must run on the whole document, not on a slice (do not open sub-documents). Floating point ordering of `yolo_conf_sum` must be preserved as noted. Torch thread count and inference determinism across processes appear stable (three identical runs at 8, 25, 50 pages).

### Option 3: add streaming through `_process_pdf` (needed for any real parent-side bound)

`_process_pdf` re-serializes the whole document with BeautifulSoup. Operating on page 0 only (title, author lines, abstract, thumbnail) and appending later pages as raw strings would cut parent memory sharply, but is not byte-equivalent: `str(soup)` normalizes entities, attribute order, and void tags across the entire document, and `html_soup.find("h1"/"h2")` currently searches all pages when page 0 has no such heading. Treat as a separate change with its own equivalence definition (normalized HTML compare). Not recommended without a measured need.

### Recommendation

Do Option 1 and the page cap. Option 2 only if documents above about 500 pages must be supported, and then together with Option 3, because the child is not the only unbounded component. At the page counts measured here (up to 200 pages, 49 MB of HTML) the child never exceeds 1 GB.

## 6. Verifying output equivalence before and after

Golden set: the repo PDFs (`pdf/TabPFN_copy.pdf`, `pdf/3d-printed-protein.pdf`) plus `mix_8`, `mix_25`, `mix_50`, `mix_100`, `mix_200`, `img_100` from `scratchpad/scale/`.

1. Baseline hashes: the saved `scratchpad/scale/<name>.<run>.html` files are the baseline output of `extract_with_yolo` (`sha256` for mix_8/25/50 stable across 3 runs). Record sha256 and byte length for each PDF (two more baseline runs for the 100/200-page files to confirm determinism before relying on them).
2. After the change, run the same driver (`run.py`) and compare sha256 of the returned string against the golden hashes. Pass criterion: byte-identical for all files. For Option 2, also compare the merged result with a single-subprocess run at K = 1, 7, 50, and K larger than the page count.
3. Call `extract_with_yolo` itself (not only the worker) in a test, so the transport change (pickle to raw bytes) is covered, including non-ASCII text and an empty result.
4. If a difference appears, diff per page (`<div class='page'>` boundaries) and compare the confidence meta tag first; a confidence difference points to accumulator ordering, a page-level difference points to `scan.pages` or `repeating_texts`.
5. Unit test for `_wrap_html` split: `head + body + tail == _wrap_html(body, ...)` for confidence values below 60, 60-79, and 80 and above, with a title containing quotes.

## 7. Limits of this measurement

- macOS only; footprint and RSS differ on Linux, and the Railway container limits are unknown. Both the compressor effect (RSS) and allocator return behavior differ.
- Synthetic PDFs are repeated pages from two papers (dedup'd images); real long documents may have different figure density and text volume. HTML per page ranged 92 KB to 457 KB across these files, which shifts the slope.
- Single run at 100, 200, and img_100; 3 runs at 8, 25, 50 with up to 150 MB RSS spread at 50 pages.
- Extrapolation to 1,000 pages is linear and unmeasured.
- Parent numbers are `ru_maxrss` growth in a separate process with the child's output replayed from disk, not a live Celery worker; they exclude Postgres write, chunking, and embedding of the stored `full_text`.
- Component attribution inside the child (join, wrap, pickle, fitz cache) comes from RSS markers in a mirror script, not from the production worker; the production worker's peak (963 MB) was higher than the mirror's (804 MB) at 200 pages, so the mirror understates by about 160 MB.

Scratch files: `/private/tmp/claude-501/-Users-enpuyou-cmpsc-projects-content-queue/0f23005f-e565-49e6-8903-da77693d3d82/scratchpad/scale/` (`build.py`, `run.py`, `trace.py`, `parent.py`, `pre.py`, `results.jsonl`, `*.err`, `*.html`).
