# PDF Extraction System

## Overview

When a user saves a PDF URL, the backend fetches the bytes, detects `content_type = "pdf"`, and routes to `_process_pdf()` in `extraction.py:668`. The live extraction pipeline is `extract_with_yolo()` in `extraction_implementations.py:472`. The output is an HTML string stored as `full_text` on the `ContentItem`, rendered by the Reader the same way article HTML is.

The pipeline is a hybrid: **PyMuPDF (`fitz`) handles all text extraction and image cropping; YOLO handles visual region detection only.** Neither replaces the other — they cover different jobs.

---

## Entry point

```
extract_metadata (extraction.py:321)
  → fetch PDF bytes over HTTP
  → _detect_content_type → "pdf"
  → _process_pdf (extraction.py:668)
      → extract_with_yolo (extraction_implementations.py:472)
      → post-process: title/author from HTML heading + PDF metadata
      → strip author bylines / duplicate abstract from HTML body
```

`_process_pdf` also:
- Extracts the title by finding the first `<h1>`/`<h2>` in the returned HTML (more reliable than font-size heuristics), then removes that heading from the body so the Reader doesn't show it twice
- Reads PDF metadata (`fitz.Document.metadata`) for author and title fallback
- Reads the `extraction-description` meta tag injected by the pipeline for the item description (abstract)

---

## Subprocess isolation (ADR-0007)

`extract_with_yolo()` never runs in the main Celery worker process. It:

1. Pickles `(pdf_bytes, url)` and passes it to `app/tasks/_yolo_worker.py` via `subprocess.run()`
2. The subprocess loads `torch` + `ultralytics` (~1–1.5 GB RSS spike), runs `_extract_yolo_sync()`, pickles the HTML result to stdout, and exits
3. The OS reclaims all that memory when the subprocess exits — the Celery worker's footprint is unaffected

Key detail: the subprocess is invoked with `sys.executable -P` (the `-P` flag) to prevent Python from prepending the script's own directory (`app/tasks/`) to `sys.path`. Without it, `app/tasks/email.py` shadows the stdlib `email` module, which `torch`'s import chain needs — causing a circular-import crash before YOLO ever runs. This was a real production bug found during the backend eval (2026-06-29) and fixed with that one-line flag.

Timeout: 300 seconds. On timeout or non-zero exit code, returns empty string and logs an error.

---

## Stage 1 — Pre-scan (`_prescan_document`)

Before any pixel rendering, `_prescan_document()` runs a fast three-pass analysis of the PDF's text metadata layer:

**Pass 1 — block collection**: For each page, collect all text blocks with their bounding boxes, raw text, and normalized text (digits replaced with `#`, whitespace collapsed, lowercased).

**Pass 2 — repeating text filter**: Scan the top 12% and bottom 12% of every page for text blocks. Any normalized string appearing on ≥ 3 pages (or ≥ 40% of pages, whichever is smaller) is added to `scan.repeating_texts`. These are running headers/footers that will be stripped from every page during extraction.

**Pass 3 — per-page analysis**: For each page:

- **Column layout**: Count text block X-centers in left zone (< 42% of page width) and right zone (> 58%). If both sides have ≥ 10% of blocks and the dominant side has ≥ 20%, classify as 2-column. Otherwise 1-column. Document-level column count is a majority vote across pages.
- **Header/footer band boundaries**: Look for narrow horizontal vector rules (height < 3pt, width > 25% of page) in the top 10% or bottom 20% of the page via `page.get_drawings()`. If no rule is found, fall back to the Y-position of the lowest repeating-text block in the margin zone. Results stored as `ps.header_y` (bottom of header band) and `ps.footer_y` (top of footer band) with a 4pt buffer. These are data-driven, not hardcoded percentages.
- **Page number rect**: Margin blocks containing only digits (≤ 6 chars) that sit inside the detected header/footer band are flagged as page number locations and suppressed during text extraction.

**Title and abstract detection** (first page only):
- Title: largest font-size span on page 1 with > 5 characters
- Abstract: first block whose normalized text starts with `"abstract"` or `"summary"`, or the block immediately following a standalone `"Abstract"` heading

The pre-scan result (`DocumentScanResult`) is passed through the entire extraction so every per-page decision is driven by measured document structure rather than hardcoded percentages.

---

## Stage 2 — YOLO region detection (`_detect_layout_yolo`)

For each page:

1. Render the page to a PNG at 150 DPI via `page.get_pixmap()`
2. Save to a temp file (avoids NumPy ABI conflicts with in-memory arrays)
3. Free the pixmap from memory (`del pix, png_bytes`) before running inference — each page render is ~8–14 MB
4. Run `yolov8n-doclaynet` (YOLOv8n fine-tuned on DocLayNet, `hantian/yolo-doclaynet` on HuggingFace) with `conf=0.35` and `torch.inference_mode()`
5. Map pixel detections back to PDF point coordinates via `scale_x = page_w / img_w`, `scale_y = page_h / img_h`

**Labels handled:**
- `picture`, `table`, `formula` → kept as extractable visual regions (renamed: `picture` → `figure`)
- `caption` → tracked separately; used to anchor captions to their figures in the output sort order, and to suppress caption text from the plain text stream
- `page-footer` and all other labels → ignored

**Minimum area filter**: regions smaller than 800 pt² are dropped.

**Caption-gap inference**: if YOLO detects a `caption` label but no `picture` above it, the pipeline infers a figure bounding box from the vertical gap between the caption and the nearest non-caption block above. The inferred region is only kept if the gap is ≥ 60pt tall and doesn't fall in the top 8% of the page (header zone).

**Deduplication**: regions are sorted by area descending. A candidate is dropped if > 70% of its area overlaps with an already-kept region that is > 4× larger. Regions within 2× size of each other are treated as subplots and both kept.

**Model loading**: `_get_yolo_model()` downloads the `.pt` file from HuggingFace on first call (cached in `~/.cache/huggingface/`) and holds it in a module-level `_yolo_model` global for the lifetime of the subprocess.

---

## Stage 3 — Content extraction (`_extract_page_content`)

For each page, using the regions from YOLO and the bands from pre-scan:

### Image cropping

Each visual region is cropped out of the original page at 150 DPI and checked for blankness: if the pixel array standard deviation is < 5 (near-pure-white), the crop is discarded. Valid crops are base64-encoded and embedded as `<img src="data:image/png;base64,…">` inside a `<div class="figure-block">`.

### Text extraction

`page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)` returns every text block with per-span font size, bold/italic flags, and bounding boxes. Each block goes through:

1. **Visual overlap filter**: if > 30% of the block's area overlaps a detected visual region, skip it (the region is shown as an image instead)
2. **Header/footer band filter**: blocks whose top edge is above `header_y` or whose bottom edge is below `footer_y` are checked against `repeating_texts` and the journal metadata regex (`_looks_like_journal_metadata`). Blocks that are repeating, look like metadata (DOIs, copyright, URLs, received/accepted dates), or are pure page numbers are dropped. Non-repeating content in the margin (e.g. footnotes) is kept.
3. **Heading detection** (`_heading_level`): Three paths:
   - **Font size**: block max font size > body size (10.5pt) + 1.5pt, with bold/all-caps requirement; relaxed for short blocks
   - **Section number prefix**: `"1."`, `"2.1."`, `"A."`, etc. matched by regex; bold or short block required
   - **Spaced small-caps**: e.g. `"A BSTRACT"`, `"1 I NTRODUCTION"` — detected by average word length < 4 and collapsed form is all-uppercase
   - Heading level (h1–h3) derived from font size delta or section number depth
4. **Split heading+body blocks**: some journal PDFs merge a bold section heading with its following paragraph into a single PDF block. `_first_line_heading_level()` detects when only the first line is a bold section-number prefix, and emits `<hN>heading</hN><p>body…</p>` rather than losing the heading.
5. **Inline markup**: superscript (`flags & 1` → `<sup>`), bold (`flags & 16` → `<strong>`), italic (`flags & 2` → `<em>`)
6. **Caption detection**: blocks overlapping a YOLO-detected `caption` rect are re-tagged as `<figcaption class="figure-caption">`. Fallback: blocks matching `Figure N:` / `Table N:` / `Scheme N:` / `Algorithm N:` prefix regex within 120pt of a visual region are also tagged as captions even if YOLO missed them.

### Paragraph merging (`_merge_continuation_paragraphs`)

PDF text blocks often split mid-sentence at column or line boundaries. Consecutive `<p>` blocks are merged if:
- The next block starts with a lowercase letter (continuation, not a new sentence)
- AND the current block ends with a superscript (`</sup>`) OR ends mid-sentence (last non-space character is lowercase)
- AND (for same-column pairs) the vertical gap between them is ≤ 2.5× the estimated line height

Cross-column merges (left column → right column in 2-column layout) are allowed when the two signals above fire without the vertical proximity requirement.

### Sort order

All content items (images + text blocks) carry a `(y0, x_center)` sort key. `_column_sort_key()` respects detected layout: single-column sorts purely by `y0`; two-column sorts as `(column_index, y0)` so the left column reads completely before the right.

Captions are anchored to their visual region: a caption above its figure gets sort key `(region.y0 - 1, x_mid)`, a caption below gets `(region.y1 + 1, x_mid)`.

---

## Stage 4 — Confidence scoring (`_compute_confidence_score`)

A 0–100 score is computed from four signals and embedded in the output HTML as `<meta name="extraction-confidence">` and `<!-- extraction-confidence: N (label) -->`:

| Signal | Max pts | How |
|---|---|---|
| Words per page | 35 | Linear scale: 0 at 0 words/page → full at 200+ words/page. Catches scanned image PDFs and rendering failures. |
| YOLO mean detection confidence | 30 | Average `conf` across all detected visual regions. If no regions detected (text-only paper), gives 15 neutral pts instead of 0. |
| Pages with content | 25 | Fraction of pages that produced ≥ 1 extracted word. |
| Image extraction success rate | 10 | Fraction of attempted crops that passed the blank-detection check. If no crops attempted, gives 5 neutral pts. |

**Thresholds for user-facing warnings** (rendered inline in the HTML):
- `< 60` → `extraction-warning` div: "Low extraction confidence — some content may be missing or mis-ordered"
- `60–79` → `extraction-notice` div: medium confidence note
- `≥ 80` → no warning

---

## Output format

`_wrap_html()` wraps the per-page `<div class="page">` blocks in a minimal HTML document with:
- `<meta name="extraction-confidence">` — numeric score (0–100)
- `<meta name="extraction-title">` — title detected during pre-scan
- `<meta name="extraction-description">` — abstract text (read by `_process_pdf` and stored as `item.description`)
- Inline CSS for basic reader typography (font, line-height, max-width 900px, figure centering)
- Confidence warning/notice div if applicable

---

## Backend comparison (eval 2026-06-29)

Four backends were evaluated against a 5-category corpus (academic single-column, academic 2-column, figure/table-heavy academic, non-academic, scanned/image-only). All four use `fitz` for PDF parsing and rendering; the variable is the region-detection layer:

| Backend | Region detection | Cost | Time (per slice) |
|---|---|---|---|
| `yolo` (prod) | YOLOv8n-doclaynet (ML model, HuggingFace) | $0 | ~5–7s (subprocess spin-up) |
| `pymupdf_layout` | `pymupdf-layout` ONNX model (same vendor, separate package) | $0 | ~0.3–0.6s |
| `gpt4o_vision` | GPT-4o-mini vision API (page image → structured extraction) | ~$0.01/slice | ~8–48s (network) |
| `ocr` | Tesseract (pixel-to-text, no layout intelligence) | $0 | ~2–5s |

Composite scores (80% accuracy / 20% efficiency):

| Document category | yolo | pymupdf_layout | gpt4o_vision | ocr | Winner |
|---|---|---|---|---|---|
| academic_2_column | **88.2** | 67.3 | 45.7 | 65.9 | yolo |
| academic_figure_table_heavy | **96.4** | 92.4 | 39.1 | 57.4 | yolo |
| academic_single_column | 94.8 | **96.6** | 46.9 | 54.7 | pymupdf_layout |
| non_academic | **93.1** | 67.3 | 46.7 | 65.6 | yolo |
| scanned | 35.2 | 35.3 | 69.8 | **93.8** | ocr |

Composite = `0.8 × accuracy + 0.2 × efficiency`. `accuracy` = average of whichever of {text_similarity, structural_accuracy, llm_judge, visual_precision, visual_recall} have ground truth for that document. `efficiency` = average of normalized time + cost, normalized per-document (min-max across the 4 backends on that document).

Full per-metric breakdown:

| Document | Backend | time_s | cost_usd | text_sim | struct_acc | llm_judge | vis_prec | vis_recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| academic_2_column | yolo | 6.20 | 0.00 | 96% | 100% | 100% | 38% | 100% |
| academic_2_column | pymupdf_layout | 0.49 | 0.00 | 95% | 100% | 100% | 0% | 0% |
| academic_2_column | gpt4o_vision | 48.42 | 0.01 | 86% | 100% | 100% | 0% | 0% |
| academic_2_column | ocr | 5.36 | 0.00 | 93% | 100% | 100% | 0% | 0% |
| academic_figure_table_heavy | yolo | 5.97 | 0.00 | 92% | — | 100% | 100% | 100% |
| academic_figure_table_heavy | pymupdf_layout | 0.44 | 0.00 | 96% | — | 100% | 100% | 67% |
| academic_figure_table_heavy | gpt4o_vision | 26.52 | 0.01 | 95% | — | 100% | 0% | 0% |
| academic_figure_table_heavy | ocr | 3.33 | 0.00 | 93% | — | 100% | 0% | 0% |
| academic_single_column | yolo | 6.53 | 0.00 | 75% | 100% | 100% | 100% | 100% |
| academic_single_column | pymupdf_layout | 0.60 | 0.00 | 79% | 100% | 100% | 100% | 100% |
| academic_single_column | gpt4o_vision | 46.76 | 0.01 | 93% | 100% | 100% | 0% | 0% |
| academic_single_column | ocr | 4.71 | 0.00 | 97% | 50% | 75% | 0% | 0% |
| non_academic | yolo | 5.24 | 0.00 | 93% | 100% | 100% | 100% | 100% |
| non_academic | pymupdf_layout | 0.29 | 0.00 | 96% | 100% | 100% | 0% | 0% |
| non_academic | gpt4o_vision | 8.87 | 0.01 | 92% | 100% | 100% | 0% | 0% |
| non_academic | ocr | 1.83 | 0.00 | 96% | 100% | 100% | 0% | 0% |
| scanned | yolo | 5.21 | 0.00 | 38% | — | 25% | — | — |
| scanned | pymupdf_layout | 0.11 | 0.00 | 38% | — | 0% | — | — |
| scanned | gpt4o_vision | 5.12 | 0.01 | 74% | — | 100% | — | — |
| scanned | ocr | 1.85 | 0.00 | 93% | — | 100% | — | — |

`—` = no ground truth for that dimension on that slice (e.g. `academic_figure_table_heavy` starts mid-document so no title/author; `scanned` has no visual ground truth since none of these backends were tuned for scan-only figure detection).

Total measured cost across all 20 backend runs + LLM-judge overhead: **$0.1118**.

Metric definitions:

| Metric | What it measures | How |
|---|---|---|
| `time_s` | Wall-clock extraction latency | `time.perf_counter()` around the backend call |
| `cost_usd` | Dollar cost of the extraction call | $0 for yolo/pymupdf_layout/ocr; real OpenAI token usage for gpt4o_vision |
| `text_sim` | Known key-passage text appearing in extracted output | Sliding-window fuzzy match (`difflib.SequenceMatcher`) per passage, averaged |
| `struct_acc` | Title and author correctly identified | Whitespace-normalized substring/fuzzy match against hand-authored ground truth |
| `llm_judge` | Holistic fidelity/completeness | GPT-4o-mini scores 1–5 given extracted text + expected passages, normalized 0–1 |
| `vis_prec` | Of re-embedded images, fraction that are real content (not logos/banners) | GPT-4o-mini judges each cropped `<img>` as REAL or JUNK |
| `vis_recall` | Of known real visual elements, fraction the backend found | `min(real_detected, expected_visual_count) / expected_visual_count` |

**Key findings:**

- YOLO wins 3 of 5 categories because it's the only backend with reliable visual-content recall — it re-embeds figures, tables, and equations as images. Its main weakness: 38% visual precision on `academic_2_column` (it also crops journal-branding junk alongside real figures).
- `pymupdf_layout` wins `academic_single_column` by a narrow margin — both backends have identical accuracy on that category, and `pymupdf_layout`'s ~10× lower latency (0.6s vs 6.5s) tips the composite. But it scored 0%/0% on visual recall for 2 of 4 categories, where YOLO found everything.
- `gpt4o_vision` and `ocr` both scored 0%/0% on visual precision/recall across all categories — neither has a region-detection step, so no figures or tables are ever re-embedded as images. This is a hard disqualifier for any use case where preserving figures matters.
- `ocr` dominates on scanned/image-only PDFs (93.8 composite vs. ~35 for everything else) — the one category where there is no text layer to parse.

**Why `pymupdf_layout` misses figures on some categories:** not fully diagnosed. Candidate causes: different training data distribution vs. YOLOv8-doclaynet (DocLayNet-specific fine-tuning), or confidence threshold mismatch (the `conf=0.35` tuned for YOLO may be wrong for the ONNX model's confidence scale, causing valid detections to be silently filtered out).

No production backend was changed by this eval. `extract_with_yolo` remains the live path. A potential future optimization: use `pymupdf_layout` as a fast-path for simple single-column documents (where it matches YOLO's accuracy at 10× lower latency) — would need a separate ADR.

Full eval details: `docs/plans/pdf-extraction-backends-eval.md`, `docs/changelog/2026-06-29-pdf-extraction-backend-eval.md`. Raw artifacts in `content-queue-backend/experiments/pdf-extraction-eval/` (gitignored, local only).

---

## Key files

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/extraction.py:668` | `_process_pdf` — entry point, title/author post-processing |
| `content-queue-backend/app/tasks/extraction_implementations.py` | All extraction logic: pre-scan, YOLO detection, text extraction, confidence scoring |
| `content-queue-backend/app/tasks/_yolo_worker.py` | Subprocess entry point (deserializes input, calls `_extract_yolo_sync`, serializes output) |
