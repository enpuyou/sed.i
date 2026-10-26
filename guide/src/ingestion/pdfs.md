---
layout: layouts/chapter.njk
title: PDFs
part: 1
order: 3
summary: How a PDF URL becomes a readable article, and why the layout analysis runs in a separate process that exists for a few seconds.
verified: 2026-10-25
permalink: /ingestion/pdfs/
---

## In one minute

A PDF is a list of drawing instructions: put these characters at these coordinates, draw this image here. It has no paragraphs, no reading order and no notion of a column. To make a PDF readable, sed.i first scans the text layer to find columns, running headers and footers and page numbers. It then renders each page to an image and runs a small object-detection model (YOLO, trained on document layouts) that boxes the figures, tables and formulas. PyMuPDF, a PDF library, extracts the text outside those boxes, orders it by column, turns large or bold lines into headings, and crops each box out as a picture embedded in the HTML.

The detection model needs `torch` and `ultralytics`, which would add roughly a gigabyte to the background worker permanently. So the whole pipeline runs in a child process that starts for each PDF, returns one HTML string, and exits, which gives the memory back to the operating system. The result carries a 0 to 100 confidence score so the reader can warn when the layout was probably misread.

## The problem

**How do you get readable, correctly ordered text and figures out of a format that stores only positions?**

The obvious design is to call a text extractor and store what it returns. PyMuPDF's `page.get_text()` does exactly that, and on the two sample papers it returns 3,172 and 3,475 words without any model at all. That is a correct bag of words. It is a poor article, for four reasons.

1. **Order.** In a two-column paper, text blocks are stored in whatever order the producing software wrote them. Reading top to bottom across the page interleaves the left and right columns.
2. **Furniture.** Running headers ("Journal of ..."), footers and page numbers repeat on every page and land in the middle of sentences when pages are joined.
3. **Figures and tables.** A figure is not text. A table extracted as text becomes a run of numbers with no structure. A chart as text is nothing. Readers of papers need these as pictures.
4. **Structure.** A heading is just text in a larger or bolder font. Nothing in the file says "this is a section title".

The harder part of the problem is that the same code must work for papers, reports and slides with no per-journal configuration. So the pipeline measures each document first (how many columns, where the margins end) and only then extracts.

## Mental model

Think of a photocopy of a newspaper page that you must re-type for a screen reader. First you walk down the page marking where the margins, page numbers and column gutters are. Then you circle every photo and chart. Then you type the remaining text in the right order, one column after the other, and attach the circled pictures where they belonged. Finally you grade your own work: did I find text on every page, were the circles confident?

| Term | Meaning here |
|---|---|
| Text layer | The characters and coordinates stored in the PDF. Scanned PDFs have none, only page images. |
| Block | A group of nearby lines that PyMuPDF reports as one unit, usually a paragraph, with a bounding box. |
| Pre-scan | A pass over the text layer only, with no rendering. Finds columns, margin bands, repeating text, title and abstract. |
| YOLO | "You only look once": a fast object-detection model that returns labeled boxes with a confidence from 0 to 1. Here it is the `yolov8n-doclaynet` model, trained on the DocLayNet dataset of page layouts. |
| Region | A box the model labeled `picture`, `table` or `formula` that was kept. |
| Confidence score | A 0 to 100 integer the pipeline computes about its own output. |
| Subprocess | A separate operating-system process started by the worker. Its memory is returned to the OS when it exits. |

{% fig "The PDF pipeline" %}
```text
URL -> fetch_bounded (cap 100 MB) -> bytes
                                       |
          worker process               | pickle (bytes, url) on stdin
   +-----------------------------------v----------------------------+
   | child process: _yolo_worker.py (torch + ultralytics + model)    |
   |  1. pre-scan   columns, margins, repeating text, title, abstract|
   |  2. per page:  render 150 dpi -> YOLO -> figure/table/formula   |
   |  3. per page:  PyMuPDF text blocks -> drop furniture, headings, |
   |                column order, crop regions as base64 PNG         |
   |  4. confidence score, wrap as one HTML document                 |
   +-----------------------------------+----------------------------+
                                       | pickled HTML string on stdout
                                       v
   _process_pdf: title, abstract, authors, thumbnail -> database row
```
{% endfig %}

## Walkthrough: two papers and one synthetic file

The inputs are the two sample papers in the repository's local `pdf/` directory (gitignored; they are real academic papers, so no text from them appears here). The first, a machine-learning paper, is 8 pages, 1.1 MB, one column, with tables and plots. The second, a materials paper, is 6 pages, 8.8 MB, two columns, with figures. A third file was built for this guide with PyMuPDF: four pages, two of them real text and two of them a picture of text with no text layer, which imitates a scan.

All outputs below were captured by running the project's own functions on a development laptop (macOS, arm64, Python 3.11) with the model already in the local Hugging Face cache and `HF_HUB_OFFLINE=1`, so nothing was downloaded.

### Step 1: the pre-scan

`_prescan_document` reads text blocks from every page and works in three passes. It records each block's position and a normalized text (digits replaced by `#`, lowercase). It then counts normalized strings that appear in the top or bottom 12% of the page. A string seen on at least `min(3, max(2, int(pages * 0.4)))` pages is running header or footer text. Finally, per page, it decides the column count and where the header and footer bands end.

Column detection is a vote on where block centers fall:

<!--src content-queue-backend/app/tasks/extraction_implementations.py-->
```python
            minor = min(lf, rf)
            major = max(lf, rf)
            if minor > 0.10 and major > 0.20:
                ps.num_columns = 2
                # Ambiguity: how balanced? Perfectly balanced = low ambiguity.
                ambiguity_scores.append(abs(lf - rf))
            else:
                ps.num_columns = 1
                ambiguity_scores.append(0.0)
        else:
            ps.num_columns = 1
```

`lf` and `rf` are the fractions of body blocks whose center is left of 42% or right of 58% of the page width. A page is two-column when both sides hold some blocks. The header and footer boundaries come from thin horizontal vector lines (rules) found with `page.get_drawings()`, or else from the lowest repeating block.

Captured output of the pre-scan on the two papers (`_prescan_document` called directly):

{% fig "Pre-scan results (captured)" %}
<!--captured python /tmp/guide_ex13/pdf_demo.py-->
```text
== one-column paper (8 pages)
prescan_s 0.37
columns per page [1, 1, 1, 1, 1, 1, 1, 1]  doc_columns 1
repeating patterns 1
title chars 40   abstract chars 0
page0 header_y 43.2  footer_y 732.6  page_h 792.0
page_num_rect found on 8 pages

== two-column paper (6 pages)
prescan_s 0.31
columns per page [2, 2, 2, 2, 2, 2]  doc_columns 2
repeating patterns 3
title chars 10   abstract chars 1758
page0 header_y 60.1  footer_y 669.9  page_h 800.9
page_num_rect found on 1 pages
```
{% endfig %}

Read this output with care, because two results are wrong or doubtful.

- The pre-scan takes 0.3 to 0.4 s for 6 to 8 pages (measured). It is cheap; it is the model that is expensive.
- Columns are right for both papers: 1 and 2.
- The one-column paper has `abstract chars 0`: the pre-scan found no abstract. The detector looks for a block that starts with "abstract" or "summary", and this paper does not label one that way.
- The two-column paper has `title chars 10`. The title rule takes the largest font on page 1 with more than 5 characters, and on a journal first page the largest text is often the journal name or a banner, not the title. That is why `_process_pdf` later replaces the title with the first heading in the extracted HTML (step 5).
- `footer_y` is 669.9 on a 800.9-point page for the two-column paper: its footer band starts 131 points above the bottom edge, which is generous. The bands decide what text is dropped, so a band that is too tall can remove body text. This paper still extracted 575 HTML tokens per page, so the band did no visible harm here.

### Step 2: region detection

For each page, `_detect_layout_yolo` renders a 150-dpi image, writes it to a temporary file, deletes the in-memory copy (a page image is 8 to 14 MB by the code's own comment) and calls the model with `conf=0.35`. Detections come back in pixel coordinates and are scaled to PDF points. Boxes labeled `picture`, `table` or `formula` and larger than 800 square points are kept; `picture` is renamed `figure`. Boxes labeled `caption` are remembered so their text can be attached to the right figure.

<!--src content-queue-backend/app/tasks/extraction_implementations.py-->
```python
        del pix, png_bytes  # free ~8-14MB per page before YOLO runs

        import torch

        with torch.inference_mode():
            results = model(tmp_path, conf=0.35, verbose=False)
```

Two clean-up rules follow. If a caption was found with no figure above it, the code infers a figure box from the vertical gap above the caption, if that gap is at least 60 points tall. And `_deduplicate_regions` drops a box that is more than 70% inside another box that is more than four times larger (a panel inside the whole figure), but keeps boxes of similar size, which are probably the cells of a grid of plots.

The same functions were then run page by page in one process, to see the results:

{% fig "Regions found per page (captured)" %}
<!--captured python /tmp/guide_ex13/inproc.py-->
```text
== one-column paper
page 1 cols=1 regions=[]                       captions=0 crops=0
page 2 cols=1 regions=['formula', 'formula']   captions=0 crops=2
page 3 cols=1 regions=['table']                captions=0 crops=1
page 4 cols=1 regions=['figure', 'table']      captions=1 crops=2
page 5 cols=1 regions=['figure']               captions=1 crops=1
page 6 cols=1 regions=[6 x 'figure']           captions=1 crops=6
page 7 cols=1 regions=['table']                captions=1 crops=1
page 8 cols=1 regions=['table']                captions=1 crops=1

== two-column paper
page 1 cols=2 regions=[5 x 'figure', 'table']  captions=1 crops=6
page 2 cols=2 regions=['figure']               captions=0 crops=1
page 3 cols=2 regions=['figure']               captions=1 crops=1
page 4 cols=2 regions=['figure']               captions=1 crops=1
page 5 cols=2 regions=['figure', 'figure']     captions=2 crops=2
page 6 cols=2 regions=['figure']               captions=1 crops=1
```
{% endfig %}

The one-column paper yields 14 regions (2 formulas, 4 tables, 8 figure panels), the two-column paper 12 (11 figures, 1 table). Every region produced a crop: none was blank. The captions are fewer than the regions on some pages (page 6 of the first paper has six figure boxes and one caption), because a multi-panel figure has one caption.

### Step 3: page content

`_extract_page_content` works in this order for each page.

1. Crop each region from the page at 150 dpi. If the pixel standard deviation is below 5 the crop is blank and is dropped. Otherwise it becomes `<div class="figure-block"><img src="data:image/png;base64,..."></div>`.
2. Read all text blocks with `page.get_text("dict")`, which includes font size and bold and italic flags for each span.
3. Drop a block if more than 30% of its area overlaps a region (the picture shows it), or if it sits in the header or footer band and is repeating text, looks like journal metadata, or is a bare page number.
4. Turn the block into HTML. `_heading_level` decides if it is `<h1>` to `<h3>` through three paths: a font larger than 10.5 pt plus 1.5 pt and bold, all-capital or short; a section-number prefix such as `2.1` or `B.4`; or "spaced small caps" such as `A BSTRACT`, which some journals produce.
5. Re-tag blocks as captions if they overlap a detected caption, or if they start with `Figure N:`, `Table N:` and similar within 120 points of a region.
6. Merge a block into the previous paragraph if it starts in lowercase and the previous one ended mid-sentence.
7. Sort by `(column, y)` on a two-column page, or by `y` alone on a one-column page.

The sort is the answer to the ordering problem:

<!--src content-queue-backend/app/tasks/extraction_implementations.py-->
```python
def _column_sort_key(
    y0: float, x_center: float, page_w: float, num_columns: int = 1
) -> tuple:
    """
    Sort key that respects detected layout.

    Single-column: sort purely by y0 (top to bottom).
    Two-column: left-column blocks (x_center <= page_w/2) sort as (0, y0),
                right-column blocks sort as (1, y0).
    """
    if num_columns == 1:
        return (0, y0)
    col = 0 if x_center <= page_w / 2 else 1
    return (col, y0)
```

The rule is crude: a block belongs to the right column if its center is right of the page midpoint. A full-width figure or a title that spans both columns is therefore sorted as if it were in one column, and it can land far from where a reader expects it.

The output HTML for the two papers had these shapes (counted from the returned string; no text printed):

{% fig "Shape of the returned HTML (captured)" %}
<!--captured python /tmp/guide_ex13/full_demo.py-->
```text
                        one-column paper   two-column paper
html size               739,053 chars      2,761,713 chars
page divs               8                  6
figure blocks           14                 12
figure captions         7                  7
h1 / h2 / h3            5 / 2 / 1          1 / 4 / 2
base64 image data       719,872 chars      2,730,136 chars
```
{% endfig %}

The base64 image data is 97% to 99% of the HTML by size. This matters for memory, covered below.

### Step 4: the confidence score

After the last page, `_compute_confidence_score` adds four sub-scores, each a function of counters collected during the loop:

<!--src content-queue-backend/app/tasks/extraction_implementations.py-->
```python
    if scan.total_pages > 0:
        words_per_page = scan.total_words_extracted / scan.total_pages
        wpp_score = min(1.0, words_per_page / 200.0)
        score += int(wpp_score * 35)

    # ── YOLO mean detection confidence (0-30 pts) ─────────────────────────────
    if scan.yolo_conf_count > 0:
        mean_conf = scan.yolo_conf_sum / scan.yolo_conf_count  # 0.0–1.0
        score += int(mean_conf * 30)
    else:
        # No visual regions detected — text-only paper or YOLO found nothing.
        # Give neutral credit rather than penalising a valid text-only document.
        score += 15

    # ── Pages with content (0-25 pts) ─────────────────────────────────────────
    if scan.total_pages > 0:
        content_rate = scan.pages_with_content / scan.total_pages
        score += int(content_rate * 25)

    # ── Image extraction success rate (0-10 pts) ──────────────────────────────
    if scan.images_attempted > 0:
        img_rate = min(1.0, scan.images_extracted / scan.images_attempted)
        score += int(img_rate * 10)
    else:
        score += 5  # neutral: no images attempted
```

Captured scores on the three files:

{% fig "Confidence sub-scores (captured)" %}
<!--captured python /tmp/guide_ex13/inproc.py-->
```text
                      one-col paper  two-col paper  synthetic scan
words per page            35/35          35/35          11/35
YOLO mean confidence      20/30          20/30          15/30 (neutral)
pages with content        25/25          25/25          12/25
image crops succeeded     10/10          10/10           5/10 (neutral)
total                     90 (high)      90 (high)      43 (low)
```
{% endfig %}

The mean YOLO confidence was 0.688 for the first paper and 0.669 for the second, so each lost 10 of its 30 points even though every box was real. A mean of 0.67 cannot reach full marks, because the model is run with `conf=0.35` and rarely reports above 0.9. In practice the YOLO component caps near 20 to 25 for good documents, so a perfect score is out of reach and 90 is close to the realistic top.

The synthetic file shows the low end. Pages 3 and 4 had no text layer, so the model found no regions, the words-per-page term fell to 11 and only half the pages produced content. The score of 43 is below 60, so the HTML starts with a yellow "Low extraction confidence" banner that tells the reader to check the original PDF. Between 60 and 79 a smaller blue notice appears. At 80 or above, nothing is shown.

{% aside %}
**What the score does not measure.** "Words" here is `len(page_html.split())`: whitespace-separated tokens of the page's HTML, tags included, not words of text. The score does not check that the order is right or that headings are correct. It detects emptiness and detection uncertainty, not wrong layouts. A two-column page read in the wrong order scores the same as a correct one.
{% endaside %}

The synthetic file also shows a real limit: the two image-only pages were not read. There is no OCR step. Running the code on a scanned document yields a page of nothing and a low score, not recognized text. The evaluation behind this choice found OCR best on scans and YOLO best on figures (see Design choices).

### Step 5: post-processing and the database row

`extract_with_yolo` returns one HTML string. `_process_pdf` in `tasks/extraction.py` turns it into fields on the content item:

1. **Title.** The first `<h1>` or `<h2>` with more than 5 characters becomes the title and is removed from the body so the reader does not show it twice. If there is none, the PDF's metadata title, then a title made from the URL.
2. **Author.** Taken from the PDF metadata (`doc.metadata`), not from the page.
3. **Description.** The abstract found by the pre-scan, read back from the `extraction-description` meta tag.
4. **Front matter.** Short `<p>` blocks (under 300 characters) before the abstract are deleted as author and affiliation lines. A standalone "Abstract" heading is deleted too, but the abstract paragraph stays in the body.
5. **Thumbnail.** The first `figure-block` image becomes `thumbnail_url` as a `data:` URL and is removed from the body.
6. **Counts.** Word count from the stripped HTML, reading time at 200 words per minute, `content_vertical` set to `academic`.

<!--src content-queue-backend/app/tasks/extraction.py-->
```python
    # ── Thumbnail from first figure image ─────────────────────────────────────
    # PDFs have no og:image. Use the first figure image as thumbnail and remove
    # it from the body (the reader shows the thumbnail separately in the header).
    if not item.thumbnail_url:
        first_figure = body_soup.find("div", class_="figure-block")
        if first_figure:
            first_img = first_figure.find("img")
            if first_img and first_img.get("src", "").startswith("data:"):
                item.thumbnail_url = first_img["src"]
                first_figure.decompose()
```

The task then sets `processing_status = "completed"`, commits, and queues the embedding task so the paper becomes searchable (see [Chunking and embedding](/retrieval/chunking-and-embedding/)). Note the consequence of step 5.5: the "thumbnail" is a whole PNG as base64 text in a database column, here hundreds of kilobytes. That is a storage choice, not a bug, but it is not a small thumbnail.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/core/fetch.py` | Streams the download; rejects a PDF over `FETCH_PDF_MAX_BYTES` (100 MB). |
| `content-queue-backend/app/tasks/extraction.py` | `extract_metadata` fetches and routes; `_process_pdf` post-processes. |
| `content-queue-backend/app/tasks/extraction_implementations.py` | Pre-scan, YOLO detection, page extraction, headings, confidence, HTML wrapper, and the subprocess launcher `extract_with_yolo`. |
| `content-queue-backend/app/tasks/_yolo_worker.py` | The child's entry point. |
| `content-queue-backend/app/core/storage.py` | Optional S3 upload of the original PDF. |

### Fetching and the byte cap

`fetch_bounded` streams the response in 16 KB pieces. For a PDF it rejects the file if the `Content-Length` header or the running total exceeds the cap, and stops the download if it runs longer than 60 s.

<!--src content-queue-backend/app/core/fetch.py-->
```python
        limit = (
            settings.FETCH_PDF_MAX_BYTES if is_pdf else settings.FETCH_HTML_MAX_BYTES
        )
        declared = resp.headers.get("content-length", "")
        if is_pdf and declared.isdigit() and int(declared) > limit:
            raise FetchRejectedError(
                f"PDF too large ({int(declared) // (1024 * 1024)} MB, "
                f"limit {limit // (1024 * 1024)} MB)"
            )

        deadline = _now() + settings.FETCH_MAX_SECONDS
        pieces: list[bytes] = []
        total = 0
        truncated = False
        for piece in resp.iter_content(chunk_size=_CHUNK_BYTES):
```

The two cases differ on purpose. An HTML page over 10 MB is truncated and still ingested, because half an article is better than none. A PDF over 100 MB is rejected, because half a PDF is not a PDF. The cap is on bytes, not pages, so it does not by itself bound the work (see How it fails).

### Starting the child

<!--src content-queue-backend/app/tasks/extraction_implementations.py-->
```python
    try:
        payload = pickle.dumps((pdf_bytes, url))
        env = {
            **os.environ,
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "PYTHONPATH": _project_root,
        }
        result = subprocess.run(
            # -P: don't prepend the script's own directory (app/tasks/) to
            # sys.path. Without this, app/tasks/email.py shadows the stdlib
            # `email` module, which torch's import chain needs internally —
            # causing a circular-import crash before YOLO ever runs.
            [sys.executable, "-P", _worker],
            input=payload,
            stdout=subprocess.PIPE,
            env=env,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error("YOLO subprocess exited with code %d", result.returncode)
            return ""
        if not result.stdout:
            logger.error("YOLO subprocess produced no output")
            return ""
        return pickle.loads(result.stdout)
```

The parent serializes the PDF bytes and URL with `pickle`, pipes them to the child's standard input and waits at most 300 seconds. Any failure (timeout, non-zero exit, empty output) returns an empty string, and `_process_pdf` turns that into `ValueError("PDF extraction returned empty result")`, which marks the item failed with that message. The `-P` flag exists because the project has its own `email.py` that would otherwise hide Python's standard `email` module. `pickle.loads` on the child's output is safe only because the child is the project's own code; it would not be safe on data from elsewhere.

### The child

<!--src content-queue-backend/app/tasks/_yolo_worker.py-->
```python
def main() -> None:
    pdf_bytes, url = pickle.loads(sys.stdin.buffer.read())

    import torch

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)

    from app.tasks.extraction_implementations import _extract_yolo_sync

    html = _extract_yolo_sync(pdf_bytes, url)

    sys.stdout.buffer.write(pickle.dumps(html))
    sys.stdout.buffer.flush()
```

`torch` and `ultralytics` are imported here, not at the top of any module the worker loads, so the Celery worker never imports them. `_extract_yolo_sync` runs the loop: open the document, load the model, pre-scan, then for each page detect, extract and update counters.

### S3 is off by default

After `_process_pdf` returns, the task calls `upload_pdf`. It does nothing unless `S3_STORAGE_ENABLED` is true and `AWS_S3_BUCKET` is set; both default to off. With the default configuration the original PDF bytes are discarded after extraction, and only the extracted HTML is stored. When enabled, the file goes to `pdfs/<user>/<item>.pdf` and the item records the key, and an endpoint can issue a one-hour presigned link to it.

## Why a subprocess, and what the memory actually does

`torch` is a large numerical library and `ultralytics` is the package that wraps YOLO. Importing them and loading the model makes a Python process grow by about a gigabyte or more. If the Celery worker imported them, every task would run in a process that is that much larger, 24 hours a day, even though PDFs are rare.

The decision record `docs/decisions/0007-celery-memory-ml-tasks.md` (ADR 0007) compares three designs and picks the second:

| Option | Memory between PDFs | Cost |
|---|---|---|
| Load the model in the worker | +1 to 1.5 GB, always | None per PDF |
| Child process per PDF (chosen) | worker baseline, about 300 MB | 1 to 2 s start-up per PDF, per the ADR (the measurement here is larger; see below) |
| Separate inference service | Service holds 1.5 GB, always | A new service to run and pay for |

The ADR says to move to the third option if PDFs become frequent, and notes `_yolo_worker.py` is already a self-contained entry point that could become that service.

### Measured memory against page count

An audit on 2026-10-24 (`docs/audits/2026-10-24-pdf-memory-scaling.md`) ran the child on PDFs built by repeating pages of the two samples, from 8 to 200 pages, on the same laptop class (macOS, arm64). Peak resident memory (RSS) of the child:

{% fig "Child process memory against page count (measured, one to three runs each)" %}
```text
pages   max RSS (MB)   wall time (s)   output HTML (MB)
    8       635             12              0.7
   25       680-719         17              5.4
   50       613-765         23-24          11.2
  100       781             36             24.4
  200       963             62             49.1
```
{% endfig %}

Two lines fit the table. Memory is about 635 MB plus 1.7 MB per page (RSS rose from 635 MB at 8 pages to 963 MB at 200). Time is about 10 s fixed plus 0.26 s per page. The fixed 10 s and 635 MB are the cost of importing the libraries and loading the model; the same demo here measured 12.0 s to load the model and a child peak of 581 MB and 635 MB for the two samples.

What accumulates is not model memory. Per-page working data (the 8 to 14 MB page image) is freed on each iteration. What stays is the list of finished page strings, and 97% to 99% of each string is the base64 text of the cropped figures. The audit counted 245 KB of HTML per page on average, 457 KB on a page-heavy test file. Then three more copies of the whole document appear at the end: the `join`, the `_wrap_html` template and the `pickle.dumps` in the child. Output size, not input size, drives the growth: a 9.5 MB input stayed at 9.5 MB from 25 to 200 pages while memory rose from 680 to 963 MB.

The parent side is not free either. It holds the PDF, the pickled copy of it, the incoming output, and then `_process_pdf` parses the HTML with BeautifulSoup twice. The audit measured about 11 MB of parent growth per megabyte of output HTML: 539 MB at 200 pages, more than the child added above its floor.

What this means in practice:

- At the page counts measured, nothing is near a limit: the highest child footprint was 990 MB.
- The 300-second timeout caps the child at roughly 1,100 pages at the measured speed, but that extrapolation is not measured. At 2.3 GB for the child (linear extrapolation, not measured) a thousand-page book would be a problem for the worker before the timeout fired, and the parent's share would be larger still.
- The audit proposes cheap fixes (write raw bytes instead of a pickle, assemble the output in pieces, cap the page count) and records that none is in the code at the time of writing.
- The numbers are from macOS. Linux reports memory differently, and the production container limit was not part of the test.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Hybrid: PyMuPDF for text, YOLO for visual regions | One model for everything; vision LLM per page; OCR | In the 2026-06-29 evaluation (five document types, 20 runs, total cost $0.11), YOLO won 3 of 5 categories on a composite of 80% accuracy and 20% efficiency. It was the only option with high figure and table recall. OCR won on scanned files, 93.8 against about 35. |
| Measure the document first (pre-scan) | Fixed margin percentages | Journals differ; the pre-scan sets the bands and the column count from the file itself. |
| Child process per PDF | Resident model; inference service; prefork with recycling | See ADR 0007 (`docs/decisions/0007-celery-memory-ml-tasks.md`). Cheapest while PDFs are rare. |
| Figures as inline base64 PNG | Store images in object storage and link | Needs no storage; the article is self-contained. The cost is the memory and row size above. |
| Self-reported confidence with a visible banner | Silent output | Users see a warning when the layout is probably wrong. |
| Cap by bytes (100 MB) | Cap by pages | Simple at download time, before anything is parsed; but it does not bound work. |

A faster alternative exists and is measured: `pymupdf_layout` took 0.3 to 0.6 s per slice against 5 to 7 s for YOLO, and matched it on single-column papers. It missed figures in two of the four categories with figures. The design note records it as a possible fast path for simple documents; it is not wired in.

## How it fails

| Failure | Cause | Result | Handling |
|---|---|---|---|
| Child crashes, times out, or prints nothing | Out of memory, a 300 s overrun, a torch import error | `extract_with_yolo` returns an empty string | `_process_pdf` raises; the item is marked failed with "PDF extraction returned empty result" |
| Circular-import crash in the child | The project's `app/tasks/email.py` shadowed the standard library `email` that torch imports | The child died before YOLO ran | Fixed by running Python with `-P` |
| Memory growth with page count | Base64 figure crops and three full copies at the end | 635 MB floor plus 1.7 MB per page in the child | Not fixed; measured in the audit |
| Worker memory | torch and ultralytics resident in the worker, about 1.5 GB | Worker hit the platform limit | Subprocess isolation; the worker stays near 300 MB |
| Scanned PDF | No text layer, no OCR | Empty pages, confidence below 60, warning banner | Banner only |
| Wrong reading order | Full-width element on a two-column page | Text out of order, score unaffected | None |
| Wrong title | Largest font on page 1 is not the title | Replaced by the first heading in the HTML | Fallbacks in `_process_pdf` |
| PDF served with another content type | Content-Type is not `application/pdf` and the URL has no `.pdf` ending | Treated as HTML: truncated at 10 MB, the PDF cap is not applied | Inferred from `core/fetch.py`; not tested |

## What is weak or missing

Ranked by how likely each is to matter.

1. **No page cap and no streaming.** The byte cap does not bound pages, and the whole document is built in memory three times over. A long, figure-heavy book is the first thing that would break. The audit's cheap fixes (raw-bytes transport, piecewise assembly, a page cap) are written down and not applied.
2. **No OCR.** A scanned PDF yields a low-confidence page of nothing. Adding OCR for pages with no text layer is the measured best option for that category.
3. **The score does not measure order.** It cannot warn about a two-column page read in the wrong order, which is the most reader-visible failure.
4. **Column handling is a single midpoint.** Full-width figures, titles and three-column layouts are mis-sorted.
5. **Hard-coded heuristics.** The 10.5 pt body size, the 300-character author cut-off and the 800 square-point minimum suit the sample papers. There is no corpus-level measure of how often they fail, apart from the five-category evaluation.
6. **Figure precision.** The evaluation scored the model's figure precision at 38% on the two-column academic category, where it also boxed journal branding. Those become junk images in the article.
7. **Thumbnails as `data:` URLs.** Large values in a database column that is read for list views. A separate image store would be smaller and cacheable.
8. **A cold model load on every PDF.** About 12 s of every run (measured above) is import and model load, because each PDF gets a fresh child.

{% say %}
A PDF has text at coordinates but no reading order, so I can't just dump the text. First I scan the text layer to find columns, repeating headers and footers and page numbers. Then I render each page and run a small layout-detection model that boxes the figures, tables and formulas. I extract the text outside those boxes with PyMuPDF, order it by column, turn big or bold lines into headings, and crop the boxes out as images in the HTML. Finally I score my own output from zero to a hundred, and below sixty the reader shows a warning. The model needs torch, which is about a gigabyte, so the whole thing runs in a child process that starts per PDF and exits, which keeps the worker small. Memory in the child is about 635 MB plus 1.7 MB per page, mostly the base64 figure images. The weak points are scanned PDFs with no OCR, no page cap, and a score that cannot tell wrong order from right.
{% endsay %}

## Check yourself

{% qa "Why does the confidence score not tell you whether the text of a two-column paper is in the right order?" %}
The score is built from counters: words per page, mean YOLO confidence, pages with content, and crop success. None of them looks at order. A page read in the wrong order has the same words, the same regions and the same crops as one read correctly, so it scores the same. It only catches empty or uncertain output.
{% endqa %}

{% qa "Why is the YOLO model run in a child process rather than loaded once in the worker, and what would make you change that?" %}
Importing torch and ultralytics adds roughly 1 to 1.5 GB, and the worker would carry it permanently although PDFs are rare. A child frees it on exit at the price of a cold start per PDF (about 12 s measured here). If PDFs became frequent, the cold start and repeated model loads would dominate, and a separate always-warm inference service (the ADR's option 1) would be cheaper per PDF.
{% endqa %}

{% qa "A 400-page PDF of 5 MB arrives. Which limit stops it, and what does it do to memory?" %}
No limit stops it: it is far under the 100 MB byte cap, and the 300 s timeout would be reached only at around 1,100 pages at 0.26 s per page. Using the measured fit, the child needs about 635 + 1.7 x 400, roughly 1.3 GB, and the parent adds several hundred megabytes more while it holds and parses the HTML. The page count, not the file size, drives it.
{% endqa %}

{% qa "The pre-scan reports a title of 10 characters for a journal article. Where does the final title come from?" %}
From `_process_pdf`: it takes the first `h1` or `h2` in the extracted HTML that has more than 5 characters. If there is none it falls back to the title in the PDF metadata, then to a title made from the URL. The pre-scan title is only written into a meta tag.
{% endqa %}

{% qa "Why does the code compute columns and margin bands before running the model, instead of using fixed percentages?" %}
Journals place running headers, page numbers and gutters differently, and a fixed band either removes body text or leaves headers in. Measuring each file (repeating text, thin rules, block positions) is cheap, 0.3 to 0.4 s for the samples, and adapts to the document. It is not perfect: a wrong band still drops text, and the title rule picked a banner for one sample.
{% endqa %}

{% qa "What would you change first to make a 200-page, figure-heavy PDF safer?" %}
Add a page cap, and stop building the whole document in memory three times: write the child's output as raw bytes rather than a pickle, and assemble the HTML from pieces instead of join then template then pickle. Those are the audit's first proposals; they keep the output identical and cut the child's end-of-run copies.
{% endqa %}
