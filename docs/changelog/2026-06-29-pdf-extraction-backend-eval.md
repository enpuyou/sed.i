# PDF Extraction Backend Comparison — Eval Results

## What prompted this

`extraction_implementations.py`'s docstring listed three approaches considered for PDF
extraction (`pymupdf_layout`, GPT-4o-mini vision, YOLO) but only YOLO was ever built. The
user asked to actually build and evaluate the other two, plus a fourth (Tesseract OCR), to
get a data-backed answer on which backend handles which kind of PDF best — specifically
whether YOLO's visibly better handling of figures, tables, and equations actually holds up
under measurement, not just by eye.

Full plan: `docs/plans/pdf-extraction-backends-eval.md` (status: complete).

## How it was built

- All experiment code (3 new backends, eval harness, 5-slice PDF corpus) lived in
  `content-queue-backend/experiments/pdf-extraction-eval/` — gitignored, never pushed,
  per explicit instruction to keep this dev-only.
- Corpus: 5 categories (academic single-column, academic 2-column, academic
  figure/table-heavy, non-academic, scanned/image-only), one 2-3 page slice each, sourced
  from existing repo fixtures plus two real documents fetched from the web (an arXiv paper,
  an AWS whitepaper).
- Metrics: time, cost, and three accuracy signals (text similarity, structural accuracy,
  LLM-judge), plus a `visual_precision`/`visual_recall` metric added specifically to
  measure figure/table/equation handling — built after manually inspecting every cropped
  image YOLO produces and finding the original figure-count check couldn't distinguish
  "found the real figure" from "found journal-logo junk."
- One production bug found and fixed along the way: `extract_with_yolo()`'s subprocess
  (`app/tasks/_yolo_worker.py`) crashed with a circular-import error on this machine
  (Python's auto `sys.path` prepend made `app/tasks/email.py` shadow the stdlib `email`
  module). Fixed with a one-line `-P` flag in `extraction_implementations.py` — this is a
  real, tracked, pushed fix, separate from the gitignored experiment.

## Final results (2026-06-29)

Comprehensive composite score (0-100, 80% accuracy / 20% efficiency) per backend per
document:

| Document | yolo | pymupdf_layout | gpt4o_vision | ocr | Winner |
|---|---|---|---|---|---|
| academic_2_column | **88.2** | 67.3 | 45.7 | 65.9 | yolo |
| academic_figure_table_heavy | **96.4** | 92.4 | 39.1 | 57.4 | yolo |
| academic_single_column | 94.8 | **96.6** | 46.9 | 54.7 | pymupdf_layout |
| non_academic | **93.1** | 67.3 | 46.7 | 65.6 | yolo |
| scanned | 35.2 | 35.3 | 69.8 | **93.8** | ocr |

Composite = `0.8 × accuracy + 0.2 × efficiency`. `accuracy` = average of whichever of
{text_similarity, structural_accuracy, llm_judge, visual_precision, visual_recall} have
ground truth for that document (missing ones are excluded from the average, not counted as
0). `efficiency` = average of normalized time + cost, normalized **per document** (min-max
across the 4 backends on that document, not a global scale) — so "fast" means "fast
relative to what's achievable on this specific document," not skewed by YOLO's fixed
~5-12s subprocess overhead (ADR-0007) dragging down every document's scale uniformly.

### Full per-metric breakdown

| Document | Backend | time_s | cost_usd | text_sim | struct_acc | llm_judge | vis_prec | vis_recall |
|---|---|---|---|---|---|---|---|---|
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

Total measured cost across all 20 backend runs + LLM-judge overhead (text-fidelity judge +
per-image visual judge): **$0.1118**.

### What each metric means

| Metric | What it measures | How |
|---|---|---|
| `time_s` | Wall-clock extraction latency, per slice, averaged | `time.perf_counter()` around the backend call |
| `cost_usd` | Measured dollar cost of the extraction call itself | $0 for yolo/pymupdf_layout/ocr (local compute); real OpenAI token usage for gpt4o_vision |
| `text_sim` | Does known "key passage" text appear (possibly reordered/paraphrased) in the extracted text | Sliding-window fuzzy match (`difflib.SequenceMatcher`) per passage, averaged |
| `struct_acc` | Title and author correctly identified | Whitespace-normalized substring/fuzzy match against hand-authored ground truth |
| `llm_judge` | Holistic fidelity/completeness rating | GPT-4o-mini scores 1-5 given extracted text + expected passages, normalized to 0-1 |
| `vis_prec` | Of the images a backend re-embedded, what fraction are real content (figure/table/equation) vs. decorative junk (logos, banners) | GPT-4o-mini judges each cropped `<img>` as REAL or JUNK |
| `vis_recall` | Of the real visual elements known to exist on the slice, what fraction did the backend find | `min(real_detected, expected_visual_count) / expected_visual_count` |
| **composite score** | Single 0-100 ranking per backend per document | `0.8 × accuracy + 0.2 × efficiency`, see above |

`—` means the slice has no ground truth for that dimension (e.g.
`academic_figure_table_heavy`'s slice starts mid-document, so there's no title/author to
check; `scanned` has no visual ground truth since none of these backends were tuned for
scan-only figure detection).

Full reproduction details, raw console output, per-image judge detail, and openable HTML
output per backend are preserved locally in
`content-queue-backend/experiments/pdf-extraction-eval/` (gitignored, not pushed) — this
changelog entry is the complete, durable summary; nothing here depends on that directory
still existing.

## Key findings

- **YOLO wins 3 of 5 documents outright**, driven by being the only backend with reliable
  visual-content recall (figures, tables, *and* equations preserved as images) on those
  layouts. Its one real weakness: only 38% precision on `academic_2_column` — it also crops
  5 pieces of journal-branding junk (a cover banner, 2 publisher logos, a dates box)
  alongside the 3 real figures.
- **`pymupdf_layout` wins on `academic_single_column`** by a narrow margin — both backends
  score 100% accuracy there, so `pymupdf_layout`'s much lower latency (0.6s vs YOLO's 6.5s)
  tips the composite score. After being fixed to crop tables/formulas as images (not just
  `picture` regions — `pymupdf.layout` exposes the identical label vocabulary as YOLO, this
  was a backend-implementation gap, not a model-capability gap), it partially closes the
  visual-fidelity gap elsewhere, but still misses figures entirely on 2 of 4 categories.
  Root cause not fully diagnosed — candidate explanations: (a) the `pymupdf-layout` ONNX
  model has a different training distribution than YOLOv8-doclaynet (which was specifically
  fine-tuned on DocLayNet, a large academic/scientific-document dataset) and generalizes
  worse to 2-column academic and non-academic layouts; (b) the `conf=0.35` threshold used
  for YOLO may be wrong for the ONNX model's confidence scale — if valid detections on those
  layouts score below 0.35, they'd be silently filtered out, producing 0% recall without
  being a fundamental model-capability gap. Distinguishing between these requires inspecting
  the ONNX model's raw detections (boxes + confidences) before the threshold filter on the
  failing categories.
- **`gpt4o_vision` and `ocr` both score 0%/0% on every visual-content category** — neither
  re-embeds images at all. Hard disqualifier for either if preserving figures/tables/
  equations as images matters.
- **`ocr` wins decisively on `scanned`** (93.8 composite) — the one category it was built
  to dominate, and the only one where the other three backends meaningfully degrade.
- **`pymupdf_layout` is the fastest backend by far** (sub-second per slice vs YOLO's
  ~5-7s subprocess cold-load, ADR-0007) but that speed alone isn't enough to beat YOLO's
  accuracy advantage wherever real figures are present.

## What happens next

This eval is informative only — no production backend was changed. `extract_with_yolo`
remains the live path in `_process_pdf`. If a future decision is made to add
`pymupdf_layout` as a fast-path default for simple single-column documents (where it tied
or beat YOLO on accuracy while being ~10x faster), that would be a separate ADR, informed
by these results but not decided here.

## Cleanup performed

- `pytesseract` uninstalled from the project's shared Poetry venv (was never in
  `pyproject.toml`)
- `tesseract` system binary uninstalled via Homebrew (plus its auto-installed dependencies)
- YOLO model weights cache (`~/.cache/huggingface/hub/models--hantian--yolo-doclaynet`,
  ~6MB) left in place — that's the same model production `extract_with_yolo` uses, not
  eval-specific
- All eval code, corpus, and reports remain locally on disk in the gitignored
  `experiments/` directory for future reference; nothing was pushed
