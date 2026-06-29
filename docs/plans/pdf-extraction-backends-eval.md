---
type: plan
status: complete
last_updated: 2026-06-29
consumer: agent
---

# Plan: PDF Extraction Backends + Comparative Eval

Date: 2026-06-27
Status: Complete — all 7 phases (0-6) implemented and run successfully.
Final results (durable, tracked record): `docs/changelog/2026-06-29-pdf-extraction-backend-eval.md`.
Full per-metric breakdown and raw artifacts:
`content-queue-backend/experiments/pdf-extraction-eval/` (gitignored, local
only — README.md, baselines.json, verification_dump.txt, rendered_output/).
Eval-only dependencies (`pytesseract`, `tesseract` binary) have been
uninstalled; no production code path was changed by this eval other than
the unrelated `-P` subprocess-flag bugfix noted in the changelog entry.

## Goal

Implement three new PDF extraction backends alongside the existing YOLO
pipeline — `pymupdf_layout` (ONNX layout model), `gpt4o_vision` (GPT-4o-mini
vision API), and `ocr` (Tesseract pixel-to-text) — then build an eval harness
that runs all four backends against a corpus of representative page slices
(2-5 pages per source document, not full documents) spanning distinct PDF
layout categories, and reports a score per category per backend across four
metric families: **time** (extraction latency), **cost** (dollar cost per
extraction — $0 for the three local backends, real per-call cost for
`gpt4o_vision`), and **accuracy** (three signals: text similarity,
structural accuracy, LLM-judge). End state: a data-backed answer to "which
backend should be the default for which kind of PDF," not just four
implementations existing in parallel.

## Non-goals

- Does not change which backend runs in production today — `extract_with_yolo`
  stays the live path in `_process_pdf` until this eval produces a reason to
  switch (separate follow-up decision/ADR, not part of this plan)
- Does not touch the production dependency group, the production import
  graph, or any file under version control's normal push/PR flow — see
  "Isolation" below. Nothing built in this plan is wired into `_process_pdf`
  or pushed to GitHub at all
- Does not build a user-facing setting to pick a backend per-PDF
- Does not retrofit the new backends with the same subprocess-memory-isolation
  treatment YOLO has (ADR-0007) unless eval results justify promoting one to
  production
- Does not cover non-PDF extraction (article HTML, etc.) — scoped to PDF only
- Does not require full-document ground truth — corpus is short representative
  page slices per category, not complete papers/reports
- Does not follow the existing `tests/evals/` convention's *location* (that
  directory is tracked/pushed) — only its *pattern* (dataset file + runner +
  baselines.json), reimplemented inside the gitignored experiment directory

## Isolation: everything lives in one gitignored directory

Per your instruction, all experiment code, eval harness code, the PDF
corpus, and result baselines live in a single new top-level directory that
is added to `.gitignore` and never pushed to GitHub:

```text
content-queue-backend/experiments/pdf-extraction-eval/
├── backends/
│   ├── pymupdf_layout.py
│   ├── gpt4o_vision.py
│   └── ocr.py
├── corpus/                  # the page-slice PDFs (Phase 1)
├── pdf_extraction_eval_dataset.py
├── test_pdf_extraction_evals.py
├── conftest.py               # local fixtures, not shared with tests/evals/
├── baselines.json
└── README.md                 # how to run, what's here, why it's gitignored
```

This is a deliberate change from the original draft of this plan, which
spread files across `app/tasks/`, `tests/evals/`, and `pdf/` (all tracked,
all pushed). Consolidating into one gitignored directory means: nothing here
needs the "never imported from production" exit-criteria checks the original
draft required, because the whole directory is invisible to git and therefore
can't accidentally ship — simpler guarantee, not just a stronger one.

## Acceptance criteria

- Done when: running
  `pytest experiments/pdf-extraction-eval/test_pdf_extraction_evals.py -v -s`
  produces a report with all four backends scored against every page-slice in
  the eval corpus, on all four metric families — **time** (wall-clock
  latency), **cost** (measured dollar cost; $0 for YOLO/`pymupdf_layout`/
  `ocr`, real per-call cost for `gpt4o_vision`), and **accuracy** (text
  similarity, structural accuracy, LLM-judge score) — including
  `gpt4o_vision` every run (not gated behind a separate on-demand job)
- Done when: the report produces one aggregate score per category per
  backend — every category established in Phase 1 has a clear winner/ranking
  visible directly in the output, not just raw per-slice rows
- Done when: `experiments/pdf-extraction-eval/baselines.json` has committed
  (locally — this whole directory is gitignored, "committed" here means
  "saved as the working baseline file on disk") scores for all four backends
  across all categories
- Done when: each new backend (`pymupdf_layout`, `gpt4o_vision`, `ocr`) is
  callable as an isolated function matching the signature shape of
  `extract_with_yolo(pdf_bytes, url) -> str` (HTML) — `pymupdf_layout` and
  `ocr` match exactly; `gpt4o_vision` additionally returns per-call token
  usage so real cost can be measured (see Phase 3) — fully self-contained
  inside `experiments/pdf-extraction-eval/backends/`, no dependency on or
  import from the production `app/` package required to run a backend
  function in isolation (though calling `llm_client` for `gpt4o_vision`
  necessarily imports from `app.core.llm_client` — see Architecture decisions)
- Done when: `git status` after running the full eval shows no new tracked
  files — `experiments/` is fully covered by `.gitignore`

## Current state

Only one PDF extraction backend is implemented and reachable in production:
`extract_with_yolo()` in `extraction_implementations.py:472`, invoked from
`_process_pdf()` in `extraction.py:668`, which is itself reached from
`extract_metadata` when `content_type == "pdf"` (see
`docs/design/systems/data-flows.md` §6 for the full traced pipeline,
including why it runs in an isolated subprocess per ADR-0007).

`extraction_implementations.py`'s own docstring (lines 1-21) names three
approaches it considered: `pymupdf_layout` (ONNX model via the
`pymupdf-layout` package — already a Poetry production dependency, but never
imported or called anywhere in the codebase), `GPT-4o-mini` vision (described
as costing ~$0.03/page, no implementation exists), and YOLO (the only one
built). None of these is literal OCR (render-to-pixels, recognize text via an
OCR engine like Tesseract) — that's a fourth category this plan adds.

`extraction_pdf_robust.py` is a 0-byte placeholder file, never imported —
dead weight, not a draft of anything.

Existing eval infrastructure: `tests/evals/` has a working pattern for
`search` and `tagging` — `<feature>_eval_dataset.py` (ground-truth dataset),
`test_<feature>_evals.py` (pytest-based metric runner that prints a report
with `-s`), shared fixtures in `conftest.py`, and `baselines.json` (CI
regression gate — fails if a metric drops below a committed value). That
directory is tracked and pushed — useful as a *pattern* reference for this
plan's harness, but not the *location*, per your instruction that this work
stay in one gitignored directory.

Two sample PDFs exist today (`pdf/TabPFN_copy.pdf`,
`pdf/3d-printed-protein.pdf`), served by dev-only endpoints in
`app/api/test_pdf.py` — these are tracked/pushed files, used in this plan
only as candidate *source* material to slice 2-5 pages from; the slices
themselves land in the new gitignored corpus, not in `pdf/`.

## Architecture decisions

- **Decision**: Where do the three new backends and the eval harness live?
  **Options**: (A) spread across `app/tasks/` (backends) + `tests/evals/`
  (harness) + `pdf/` (corpus), matching existing code-organization
  conventions; (B) one new directory,
  `content-queue-backend/experiments/pdf-extraction-eval/`, holding
  everything, added wholesale to `.gitignore`.
  **Chosen**: (B), per your explicit instruction. This trades convention-
  consistency for a much simpler isolation guarantee — nothing in this
  experiment can leak into a PR or get reviewed/merged by accident, because
  git doesn't see it at all. The cost: this code won't benefit from the
  shared `tests/evals/conftest.py` fixtures or CI runs automatically: it
  needs its own local `conftest.py` and is run manually, not via the
  project's normal `make test`.
  **Reversibility**: easy — if any backend is later promoted to production,
  *that* work involves deliberately copying/rewriting the relevant code into
  the tracked tree, not un-gitignoring this directory wholesale.

- **Decision**: How does the eval harness invoke each backend uniformly?
  **Options**: (A) each backend module exports a function with an identical
  signature (`(pdf_bytes: bytes, url: str) -> str`, returning extraction HTML,
  matching `extract_with_yolo`'s existing shape) and the eval harness imports
  all four directly; (B) build a backend registry/protocol class now, in case
  more backends are added later.
  **Chosen**: (A) — matching the existing signature is enough uniformity for
  four functions. A registry is the kind of premature abstraction the
  project's working principles explicitly warn against; if a fifth backend
  shows up, promoting four call sites to a registry is a small, safe
  refactor, not a redesign.
  **Reversibility**: easy.

- **Decision**: Do new backends run in an isolated subprocess like YOLO does
  (ADR-0007)?
  **Options**: (A) yes, same subprocess-per-call isolation; (B) no, run
  in-process since this is eval-only code, run manually, never in the Celery
  worker.
  **Chosen**: (B). ADR-0007's subprocess isolation exists to protect the
  long-lived Celery worker's memory footprint in production. This eval
  harness is a one-shot, manually-invoked pytest process that exits when the
  run completes — there's no accumulated-memory problem to solve. If any
  backend is later promoted to production, *that* promotion is where the
  ADR-0007 tradeoff gets re-evaluated, not here.
  **Reversibility**: easy — isolation is a `subprocess.run()` wrapper that
  can be added later without touching the backend functions themselves.

- **Decision**: Dependency placement — `pytesseract`/`tesseract` (new),
  `pymupdf-layout` (already a prod dependency, currently unused),
  `llm_client`/OpenAI (already prod, reused for `gpt4o_vision`).
  **Options**: (A) add `pytesseract` to `pyproject.toml`'s
  `[tool.poetry.group.dev.dependencies]`; (B) install it ad hoc in the local
  virtualenv only, with no `pyproject.toml` change at all, since this whole
  experiment never gets pushed anyway.
  **Chosen**: (B). Since `experiments/` is gitignored and this is explicitly
  dev-only exploratory work, even a dev-group `pyproject.toml` entry is more
  ceremony than needed — it would still appear in every contributor's
  `poetry install` and show up in `git diff` for everyone, just to support
  code nobody else can see. Document the manual
  `pip install pytesseract` (inside the project's existing Poetry venv) and
  the system `tesseract` binary requirement in the experiment directory's
  own `README.md` instead. `pymupdf-layout` and the OpenAI client need no
  new installation at all — both already resolve inside the existing venv.
  **Reversibility**: easy — trivial to add a proper dependency entry later if
  any backend gets promoted out of the experiment directory.

- **Decision**: Ground truth authoring strategy for text-similarity scoring.
  **Options**: (A) hand-transcribe full expected text for every corpus PDF;
  (B) hand-transcribe only key passages/sections per PDF (title, abstract,
  one body paragraph) rather than the full document.
  **Chosen**: (B), reinforced by the corpus now being short 2-5 page slices
  rather than full documents — there's even less text to transcribe per
  slice than originally planned. One slice per category, 5 categories total.
  Comparing extracted text against known key passages (does the
  abstract's first sentence appear near-verbatim, is the title exact, does a
  known body sentence appear with high similarity) gives a strong accuracy
  signal at a fraction of the authoring cost. The structural-accuracy metric
  separately checks title/author/figure-count, so passage-level text
  similarity is reinforcement, not the only signal.
  **Reversibility**: easy — ground truth lives in the dataset file, can be
  expanded incrementally per PDF without changing the runner.

## Phases

### Phase 0 — Gitignore the experiment directory

**Goal**: The isolation guarantee exists before any experiment file is
created, not after.
**Entry criteria**: none.

**Changes**:

1. `.gitignore` (root): add `content-queue-backend/experiments/` (or
   `/experiments/` if run from within `content-queue-backend/` — match
   existing `.gitignore` path conventions in that file)

**Exit criteria**:

- [ ] `git status` shows nothing after creating a throwaway file inside
      `content-queue-backend/experiments/pdf-extraction-eval/` — confirms the
      ignore rule works before Phase 1 starts populating real content

**Risks**: None — this is an additive, reversible one-line change.

### Phase 1 — Sample PDF corpus (page slices, not full documents)

**Goal**: Have a corpus of short page slices (2-5 pages each) on disk inside
the (now-confirmed-gitignored) experiment directory, covering distinct
layout categories — variety of layout/structure matters here, not document
length or completeness.
**Entry criteria**: Phase 0 complete (ignore rule verified working). I (the
agent) will source these directly rather than asking you for documents.

**Categories established** (final list — drives every later phase's
aggregation):

1. **Academic, single-column** — e.g. a 2-5 page slice of
   `3d-printed-protein.pdf` (verify it's single-column) or a similar
   open-access arXiv paper
2. **Academic, 2-column** — e.g. a slice of `TabPFN_copy.pdf` (verify
   2-column) or similar
3. **Academic, figure/table-heavy** — a slice chosen specifically for
   density of figures/tables/captions, distinct from the two above, since
   that's the dimension YOLO/pymupdf_layout's region-detection should matter
   most for
4. **Non-academic** — a few pages from a report/whitepaper/slide-deck-style
   PDF (different typography, less rigid structure than academic papers)
5. **Scanned/image-only** — 2-5 pages with no real text layer (self-generated:
   render existing text pages to images at print resolution, then reassemble
   as an image-only PDF — avoids any sourcing/licensing question entirely
   since it derives from already-permissible source pages)

**Changes**:

1. `content-queue-backend/experiments/pdf-extraction-eval/corpus/` (new,
   gitignored): one short PDF per category above, each 2-5 pages, named by
   category (e.g. `academic-2col.pdf`, `scanned.pdf`) — may slice from the
   existing tracked `pdf/TabPFN_copy.pdf`/`pdf/3d-printed-protein.pdf` as
   source material for two of the academic categories, without modifying
   those tracked files themselves
2. `content-queue-backend/experiments/pdf-extraction-eval/README.md` (new):
   one paragraph per slice — source, category, why it's representative,
   license/provenance (favor arXiv/open-access source material and
   self-generated scans so this stays uncomplicated); also documents how to
   run the eval and the manual `pytesseract`/`tesseract` install step

**Exit criteria**:

- [ ] Exactly one short (2-5 page) PDF exists per established category
- [ ] Each slice's provenance/license is documented
- [ ] Categories are finalized here — Phases 5-6 treat this list as fixed

**Risks**: Even short slices of copyrighted material need a provenance check;
mitigated by preferring arXiv/open-access papers and self-generated scans, and
by keeping slices short (a 2-5 page excerpt is a much smaller exposure than a
full paper).

### Phase 2 — `pymupdf_layout` backend

**Goal**: A callable `extract_with_pymupdf_layout(pdf_bytes, url) -> str`
matching YOLO's output shape (HTML with `<div class='page'>` wrapping,
embedded figures, confidence/title/abstract meta tags via the same
`_wrap_html` helper if reusable).
**Entry criteria**: Phase 1 corpus exists for manual smoke-testing during
development.

**Changes**:

1. `content-queue-backend/experiments/pdf-extraction-eval/backends/pymupdf_layout.py`
   (new, gitignored): loads the `pymupdf-layout` ONNX model, runs layout
   detection per page, extracts text per detected region. May import
   *read-only* helper functions from `app.tasks.extraction_implementations`
   (e.g. `_wrap_html`) if they're generic enough to reuse — verify during
   implementation whether they're YOLO-coupled or general — but this
   experiment module is never imported *from* production code, only the
   reverse
2. No changes to `extraction.py`/`_process_pdf` — this backend is not wired
   into the production fork (per non-goals)

**Exit criteria**:

- [ ] `extract_with_pymupdf_layout()` returns valid HTML for every Phase 1
      corpus slice without raising
- [ ] Manually spot-checked against 1-2 corpus slices for plausibility before
      moving to eval

**Risks**: `pymupdf-layout`'s actual API surface is unverified — this plan
assumes it exposes a layout-detection model comparable to YOLO's region
output (label + bounding box), but that needs confirming against the
package's real interface before estimating effort.

### Phase 3 — `gpt4o_vision` backend

**Goal**: A callable `extract_with_gpt4o_vision(pdf_bytes, url) -> str`
that renders each page to an image and asks GPT-4o-mini to return
structured extraction (title, author, abstract, body text, figure
locations) as HTML.
**Entry criteria**: none beyond Phase 1 corpus.

**Changes**:

1. `content-queue-backend/experiments/pdf-extraction-eval/backends/gpt4o_vision.py`
   (new, gitignored): renders pages via `fitz`/`pillow` (both already
   resolve in the existing venv), sends each page image + a
   structured-extraction prompt to `app.core.llm_client` (existing OpenAI
   wrapper, imported from production code — reused, not duplicated — but
   this experiment module is still never imported *from* `app/`, only the
   reverse), assembles per-page responses into the same HTML shape as the
   other backends
2. Return per-call token usage (input/output tokens from the OpenAI
   response) alongside the extracted HTML — not just the HTML string like
   the other three backends — so Phase 6 can compute a real measured dollar
   cost per slice instead of a rough per-page estimate. This is the one
   backend whose return shape needs a small wrapper (e.g.
   `(html, usage_dict)`) beyond the common `(pdf_bytes, url) -> str`
   signature; Phase 6's runner handles this backend's extra return value
   explicitly rather than forcing all four backends into an identical tuple
   shape they don't need

**Exit criteria**:

- [ ] `extract_with_gpt4o_vision()` returns valid HTML for every corpus slice
- [ ] Token usage is captured per call, sufficient for Phase 6 to compute
      measured (not estimated) dollar cost per slice

**Risks**: Real dollar cost per eval run (bounded by the small page-slice
corpus, not full documents). Acceptable given the corpus size decided in
Phase 1 — revisit only if the corpus grows substantially later.

### Phase 4 — `ocr` backend (Tesseract)

**Goal**: A callable `extract_with_ocr(pdf_bytes, url) -> str` that renders
pages to images and runs Tesseract OCR, with no layout-model intelligence —
the most literal reading of "OCR-based" from the original ask.
**Entry criteria**: none beyond Phase 1 corpus.

**Changes**:

1. Manual `pip install pytesseract` inside the project's existing Poetry
   venv (no `pyproject.toml` change — per the Architecture decision above);
   document the system-level `tesseract` binary requirement (not
   pip-installable: `brew install tesseract` / `apt install tesseract-ocr`)
   in the experiment directory's `README.md`, with a `pytest.skip` guard in
   its local `conftest.py` if the binary isn't found (same pattern as the
   existing `OPENAI_API_KEY` skip-guard in `tests/evals/test_search_evals.py`,
   reimplemented locally since this directory doesn't share that conftest)
2. `content-queue-backend/experiments/pdf-extraction-eval/backends/ocr.py`
   (new, gitignored): render each page to an image (`fitz`, already resolves
   in the venv), run `pytesseract.image_to_string` per page (no layout
   segmentation — pure top-to-bottom pixel-to-text), wrap into the same HTML
   shape (without figure detection — OCR alone doesn't know where images are
   without help)

**Exit criteria**:

- [ ] `extract_with_ocr()` returns valid HTML for every corpus slice
- [ ] Skips gracefully (not a hard failure) on machines without the
      `tesseract` system binary installed

**Risks**: System-level binary dependency (not Python-installable) is a real
local-dev friction point — needs `brew install tesseract` /
`apt install tesseract-ocr` documented in the experiment `README.md`. Since
this never runs in CI (gitignored, manually invoked), the only impact is on
whoever runs the eval locally without the binary — that person's OCR cells
skip (acceptable — see Phase 6 reporting note on partial results).

### Phase 5 — Eval dataset + ground truth

**Goal**: `pdf_extraction_eval_dataset.py` following the existing
`search_eval_dataset.py`/`tagging_eval_dataset.py` convention — per-slice
ground truth (expected title, author, key passages, figure count) plus the
five categories from Phase 1, tagged for per-category aggregation.
**Entry criteria**: Phase 1 corpus finalized (ground truth is authored
against final page-slice files, not placeholders).

**Changes**:

1. `content-queue-backend/experiments/pdf-extraction-eval/pdf_extraction_eval_dataset.py`
   (new, gitignored): per slice — `key`, `path` (relative to this
   experiment's `corpus/`), `category` (one of the five from Phase 1),
   `expected_title`, `expected_author`, `expected_key_passages` (list of
   short strings expected to appear near-verbatim — since slices are short,
   2-4 passages per slice is enough), `expected_figure_count` (approximate,
   ±tolerance; `None`/0 for the scanned-PDF category where figure detection
   isn't expected to work)
2. `content-queue-backend/experiments/pdf-extraction-eval/conftest.py` (new,
   gitignored, local to this directory — not the shared
   `tests/evals/conftest.py`): a fixture loading PDF bytes from this
   experiment's own `corpus/` paths declared in the dataset (same pattern as
   how `eval_articles` seeds rows in the search eval's conftest, reimplemented
   locally since this directory is isolated from `tests/evals/`)

**Exit criteria**:

- [ ] Ground truth authored for every Phase 1 corpus slice
- [ ] Every slice has a `category` matching one of the five fixed categories
- [ ] Dataset file imports cleanly, no missing files referenced

**Risks**: Ground-truth authoring is manual, human-judgment work — quality of
the whole eval depends on this being done carefully, not rushed. Mitigated
somewhat by short slices (2-5 pages) meaning less ground truth to author per
category than full documents would require.

### Phase 6 — Eval runner + metrics

**Goal**: `test_pdf_extraction_evals.py` that runs all four backends against
every corpus slice, computes four per-slice metrics — **time, cost, and two
accuracy signals (text similarity, structural accuracy), plus LLM-judge as a
third accuracy signal** — and rolls each metric up into **one aggregate
score per category per backend** — every category from Phase 1 gets a clear
winner on every metric, not just a table of raw per-slice rows the user has
to interpret manually. Follows the existing `test_search_evals.py`
reporting-on-`-s` convention.
**Entry criteria**: Phases 2-5 complete (all four backends callable, dataset
exists).

**Metrics tracked** (four families — this is the explicit list driving the
report's columns):

1. **Time**: wall-clock `time.perf_counter()` around each backend call, per
   slice — captures the real cost of YOLO's subprocess spin-up (ADR-0007),
   pymupdf_layout's model inference, GPT-4o-mini's network round-trip, and
   OCR's per-page Tesseract call, on equal footing
2. **Cost**: dollar cost per slice, per backend. YOLO / `pymupdf_layout` /
   `ocr` are $0 — local compute only, no metered API. `gpt4o_vision` cost is
   computed per call from actual token usage returned by the OpenAI
   response (input image tokens + output tokens × current per-token
   pricing), not the rough ~$0.03/page estimate in Phase 3 — that estimate
   was only a sizing guess for the cost-guard log line; the eval itself
   should record real measured cost
3. **Accuracy** — three signals, kept separate rather than combined (per the
   existing Risks note below):
   - **Text similarity** (per slice): for each expected key passage, compute
     best-match normalized similarity (e.g. `difflib.SequenceMatcher` ratio
     or token-overlap %) against the backend's extracted text; average across
     a slice's passages
   - **Structural accuracy** (per slice): exact/fuzzy match on extracted
     title vs expected, author vs expected, figure count within tolerance —
     scored as a 0-1 composite
   - **LLM-judge** (per slice): prompt an LLM with (extracted text, expected
     key passages/summary) asking for a 1-5 fidelity/completeness score —
     reuses `app.core.llm_client`, runs every time per the cost note in
     Phase 3 (this judge call's own cost is tracked separately as eval
     overhead, not attributed to the backend being judged)

**Changes**:

1. `content-queue-backend/experiments/pdf-extraction-eval/test_pdf_extraction_evals.py`
   (new, gitignored): implements the four metric families above, per slice,
   per backend.
   - **Aggregation**: average each metric (time, cost, text similarity,
     structural accuracy, LLM-judge) across all slices sharing a `category`,
     per backend — this produces the category × backend grid that's the
     actual deliverable, with per-slice numbers available as supporting
     detail, not the headline output
   - Report: printed category × backend grid — one row per category, one
     column-group per backend, with **time, cost, and the three accuracy
     scores** each (five numbers per cell), modeled on
     `tests/evals/test_search_evals.py`'s existing report-printing pattern
     (pattern reused, file lives in the gitignored experiment directory)
2. `content-queue-backend/experiments/pdf-extraction-eval/baselines.json`
   (new, gitignored — this is a local working file, not a CI gate, since
   nothing in this directory runs in CI): add entries for each
   category × backend × metric combination once initial scores are known
   (e.g. `pdf_academic_2col_yolo_text_similarity`,
   `pdf_academic_2col_gpt4o_vision_cost_usd`,
   `pdf_scanned_ocr_text_similarity`, etc.) — a local record of current
   scores for comparing future runs against, written by hand or by a small
   script after reviewing a run's output (not a CI gate, since this never
   runs in CI)

**Exit criteria**:

- [ ] `pytest experiments/pdf-extraction-eval/test_pdf_extraction_evals.py -v -s`
      (run from `content-queue-backend/`) runs end to end, all four backends,
      full corpus, prints the category × backend grid
- [ ] Every one of the five categories has an aggregate score for **time,
      cost, and all three accuracy signals**, for every backend (a backend
      that fails/skips on a category — e.g. OCR missing its system binary —
      shows as explicitly missing in that cell, not silently dropped from
      the grid)
- [ ] `gpt4o_vision`'s cost figures are derived from actual token usage per
      call, not the Phase 3 sizing estimate
- [ ] `baselines.json` has initial values recorded for every
      category × backend × metric combination
- [ ] Report answers "which backend wins on which category, on which
      metric" by inspection, without manual interpretation of raw numbers

**Risks**: Combining four metric families (time, cost, and three accuracy
signals) into the per-category grid needs a clear, documented approach —
show all five side by side per category/backend cell rather than collapsing
them into a single composite "winner" number, since a false-precision
composite score would hide which metric actually drove a result (a backend
could win on cost and lose on accuracy, or vice versa — that tradeoff should
stay visible, not be averaged away). The category-level aggregate answers
"which backend wins on which category, on which metric" directly; a true
single-number ranking is a manual judgment call for whoever reads the
report, not something the harness should assert on its own.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `pymupdf-layout`'s actual API doesn't match the assumed YOLO-comparable interface | Medium | Medium | Confirm package API first in Phase 2 before estimating remaining effort |
| GPT-4o-mini eval cost per run | Low (bounded by short page-slice corpus, runs every time per your decision) | Low | Cost-log line in Phase 3; revisit only if corpus grows substantially beyond the current 5-category, 2-5-page-slice scope |
| Tesseract system binary missing on the machine running the eval | Medium (depends on local dev machine, not CI — this never runs in CI) | Low | Skip-guard pattern (like existing `OPENAI_API_KEY` skip); that category/backend cell shows as explicitly missing in the report, not silently dropped |
| Ground-truth authoring quality varies, skewing comparisons | Medium | High | Have a second pass/review of the ground-truth dataset before trusting baseline numbers; treat first baseline as provisional |
| Scope: four new backends is substantially larger than "an eval" | Accepted | — | Tracked explicitly via phases — Phases 2-4 (backends) are separable from Phases 5-6 (eval); could review backends independently if needed |
| Local-only `pip install pytesseract` (no `pyproject.toml` entry) silently breaks if the venv is recreated | Low | Low | Documented in the experiment `README.md`; easy one-line fix when it happens, not worth a permanent dependency entry for code nobody else runs |
| Self-generated scanned-PDF slice isn't representative of real-world scans (no skew, noise, etc.) | Medium | Low | Acceptable for a first pass — flag in the experiment `README.md` that this slice is synthetic, not a real scanned document, in case results need that caveat later |

## Resolved decisions (from your rounds of answers)

- Corpus is short page slices (2-5 pages), not full documents — variety of
  layout matters, not document completeness
- Agent sources all corpus slices directly — no documents needed from you
- `gpt4o_vision` runs in every eval invocation, no on-demand/CI-gating split
- Report must produce an aggregate score per category, for every category
  established in Phase 1 (five categories, listed in Phase 1) — not just
  side-by-side raw rows
- All new backends remain dev/eval-only — confirmed, nothing in this plan
  wires any of the three new backends into production code
- **All experiment code, eval harness code, the PDF corpus, and baselines
  live in one directory
  (`content-queue-backend/experiments/pdf-extraction-eval/`) that is added to
  `.gitignore` and never pushed to GitHub** — this superseded the original
  plan's `app/tasks/` + `tests/evals/` + `pdf/` spread; see "Isolation"
  section above and the Architecture decisions for what this changes
  (no CI integration, no shared `tests/evals/conftest.py`, dependencies
  installed manually rather than via `pyproject.toml`)
- **Metrics expanded from three (text similarity, structural accuracy,
  LLM-judge) to four families: time, cost, and accuracy (the three signals
  above)** — cost was previously only a Phase 3 cost-guard log line, not a
  reported/aggregated metric; it's now tracked per slice and rolled up into
  the same category × backend grid as the others, with `gpt4o_vision` cost
  computed from real measured token usage rather than the rough per-page
  estimate

## Open questions

None outstanding — proceeding to Phase 1 (corpus sourcing) next.
