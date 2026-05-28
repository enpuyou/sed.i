"""
Practical PDF extraction implementations for academic papers.

Three approaches:
1. pymupdf_layout: Uses pymupdf-layout (onnxruntime ONNX model) - accurate, fast, free
2. GPT-4o-mini: Vision-based via OpenAI API - costs $0.03/page
3. YOLO: ML-based, fast, free after download, needs torch

Architecture: every extraction starts with a fast pre-scan (_prescan_document)
that analyses text metadata only (no pixel rendering) to detect:
  - Per-page column layout (1, 2, or 3 columns)
  - Repeating header/footer text patterns
  - Horizontal rules delimiting header/footer bands
  - Page-number rectangle locations
  - Document title

These results are stored in DocumentScanResult and used to drive the main
extraction pass, replacing all hardcoded percentages with data-driven decisions.
A confidence score (0-100) is computed from the scan quality and embedded in
the output HTML so callers can decide whether to surface a warning to the user.
"""

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
import fitz

logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "vision_dpi": 150,
    "crop_dpi": 150,
    "region_padding_pts": 2,
    "min_region_area": 800,
}


# ============================================================================
# Pre-scan: fast text-metadata analysis before any pixel rendering
# ============================================================================


@dataclass
class PageScan:
    """Per-page layout metadata collected during the pre-scan pass."""

    num_columns: int = 1
    header_y: float = 0.0  # bottom of detected header band (pts)
    footer_y: float = 0.0  # top of detected footer band (pts)
    page_num_rect: Optional[fitz.Rect] = None  # bounding rect of page-number text


@dataclass
class DocumentScanResult:
    """
    Results from the document pre-scan.

    Populated by _prescan_document() before any visual model runs.
    Everything derived purely from PDF text/drawing metadata — fast and free.
    """

    # Per-page results (index = page number)
    pages: List[PageScan] = field(default_factory=list)

    # Repeating header/footer text (normalized) — drop from every page
    repeating_texts: Set[str] = field(default_factory=set)

    # Document-level layout info
    title: str = ""
    abstract: str = ""  # New: detected abstract text
    doc_num_columns: int = 1  # document-level fallback if per-page is ambiguous

    # Confidence sub-scores (filled in by _compute_confidence_score)
    pages_with_layout: int = 0  # pages where column layout was unambiguous
    total_pages: int = 0
    images_extracted: int = 0
    images_attempted: int = 0
    column_ambiguity: float = 0.0  # 0 = clear, 1 = very ambiguous


def _prescan_document(doc: fitz.Document) -> DocumentScanResult:
    """
    Fast pre-scan: analyse text metadata only (no pixel rendering).

    Detects per-page:
      - Column layout (1 or 2 columns) using X-distribution of text blocks
      - Header band bottom via horizontal rules from page.get_drawings()
        falling in the top 20% of the page, or by repeating-text band
      - Footer band top similarly
      - Page-number rect: margin block containing only digits

    Also builds the repeating-text filter (blocks in top/bottom bands that
    appear on 3+ pages → running headers/footers).

    Accuracy > speed: we analyse every page, not just a sample.
    """
    from collections import Counter

    num_pages = len(doc)
    scan = DocumentScanResult(total_pages=num_pages)

    # ── Pass 1: collect per-page block positions and text ──────────────────
    # We store raw block info so we can compute repeating texts across all pages.

    page_block_data: List[List[dict]] = []  # per page, list of block dicts

    for page in doc:
        blocks_info = []
        page_h = page.rect.height
        page_w = page.rect.width

        for block in page.get_text("dict", flags=0)["blocks"]:
            if block.get("type") != 0:
                continue
            bbox = block["bbox"]
            raw = " ".join(
                span.get("text", "")
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()
            if not raw:
                continue
            blocks_info.append(
                {
                    "bbox": bbox,
                    "raw": raw,
                    "normalized": re.sub(
                        r"\b\d+\b", "#", re.sub(r"\s+", " ", raw.lower())
                    ).strip(),
                    "x_center": (bbox[0] + bbox[2]) / 2,
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "width": bbox[2] - bbox[0],
                }
            )

        page_block_data.append(blocks_info)

    # ── Pass 2: repeating-text filter ──────────────────────────────────────
    text_page_count: Counter = Counter()

    for page_idx, page in enumerate(doc):
        page_h = page.rect.height
        header_zone = page_h * 0.12  # generous top band for scan
        footer_zone = page_h * 0.88

        seen = set()
        for bi in page_block_data[page_idx]:
            bbox = bi["bbox"]
            # Only margin blocks for the repetition scan.
            # Use top edge for header check and bottom edge for footer check
            # so we catch blocks that sit fully inside the margin bands.
            if bbox[1] > header_zone and bbox[3] < footer_zone:
                continue
            norm = bi["normalized"]
            if len(norm) < 4:
                continue
            if norm not in seen:
                seen.add(norm)
                text_page_count[norm] += 1

    min_pages = min(3, max(2, int(num_pages * 0.4)))
    scan.repeating_texts = {t for t, c in text_page_count.items() if c >= min_pages}
    logger.info(
        f"[pre-scan] {len(scan.repeating_texts)} repeating header/footer patterns"
    )

    # ── Pass 3: per-page analysis ──────────────────────────────────────────
    ambiguity_scores = []

    for page_idx, page in enumerate(doc):
        page_h = page.rect.height
        page_w = page.rect.width
        blocks_info = page_block_data[page_idx]

        ps = PageScan()

        # ---- Column layout ------------------------------------------------
        # Use X-centres of body text blocks (exclude margin bands)
        header_zone = page_h * 0.12
        footer_zone = page_h * 0.88

        left_cnt = right_cnt = body_cnt = 0
        for bi in blocks_info:
            bbox = bi["bbox"]
            # Use top edge (bbox[1]) for zone check so tall paragraphs aren't excluded.
            # A block whose top is in the body zone is a body block even if its
            # bottom extends into the margin band.
            if bbox[1] < header_zone or bbox[1] > footer_zone:
                continue
            if bi["width"] < 20:
                continue
            xc = bi["x_center"]
            body_cnt += 1
            if xc < page_w * 0.42:
                left_cnt += 1
            elif xc > page_w * 0.58:
                right_cnt += 1

        if body_cnt > 0:
            lf = left_cnt / body_cnt
            rf = right_cnt / body_cnt
            # Two-column if both sides have at least some blocks.
            # Use a lower threshold for the minor side (0.10) to handle pages
            # where one column is sparse (e.g. conclusion + figure vs. full author list).
            # The dominant side must still be substantial (>0.20) to avoid false positives.
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

        # ---- Header/footer band from horizontal rules ---------------------
        # page.get_drawings() returns vector graphics; narrow horizontal lines
        # (height < 3pt, width > 30% of page) near the top or bottom are rules.
        header_rule_y = None  # bottom of header rule
        footer_rule_y = None  # top of footer rule

        try:
            for drawing in page.get_drawings():
                rect = drawing.get("rect")
                if rect is None:
                    continue
                h = rect.height
                w = rect.width
                if h > 3 or w < page_w * 0.25:
                    continue  # not a narrow horizontal rule
                mid_y = (rect.y0 + rect.y1) / 2
                # Header rules must be in the top 10% of the page.
                # Rules between 10%-20% are more likely table borders than
                # journal running-header rules — ignore them for the header band.
                if mid_y < page_h * 0.10:
                    if header_rule_y is None or rect.y1 > header_rule_y:
                        header_rule_y = rect.y1
                elif mid_y > page_h * 0.80:
                    if footer_rule_y is None or rect.y0 < footer_rule_y:
                        footer_rule_y = rect.y0
        except Exception:
            pass

        # Fallback: use position of lowest repeating-text block in top band
        if header_rule_y is None:
            repeating_header_y = 0.0
            for bi in blocks_info:
                bbox = bi["bbox"]
                if bbox[1] < page_h * 0.15 and bi["normalized"] in scan.repeating_texts:
                    repeating_header_y = max(repeating_header_y, bbox[3])
            header_rule_y = (
                repeating_header_y if repeating_header_y > 0 else page_h * 0.07
            )

        if footer_rule_y is None:
            repeating_footer_y = page_h
            for bi in blocks_info:
                bbox = bi["bbox"]
                if bbox[3] > page_h * 0.85 and bi["normalized"] in scan.repeating_texts:
                    repeating_footer_y = min(repeating_footer_y, bbox[1])
            footer_rule_y = (
                repeating_footer_y if repeating_footer_y < page_h else page_h * 0.93
            )

        # Add a small buffer so we exclude the rule itself and any adjacent text
        ps.header_y = header_rule_y + 4
        ps.footer_y = footer_rule_y - 4

        # ---- Page number rect ---------------------------------------------
        # Look for margin blocks containing only digits (possibly with spaces)
        for bi in blocks_info:
            bbox = bi["bbox"]
            raw = bi["raw"].strip()
            if re.fullmatch(r"\d[\d\s]*", raw) and len(raw) <= 6:
                if bbox[3] < ps.header_y + 10 or bbox[1] > ps.footer_y - 10:
                    ps.page_num_rect = fitz.Rect(bbox)
                    break

        scan.pages.append(ps)

    # ── Document-level column detection (majority vote) ────────────────────
    col_counts = [ps.num_columns for ps in scan.pages]
    scan.doc_num_columns = 2 if col_counts.count(2) > len(col_counts) / 2 else 1
    scan.column_ambiguity = (
        sum(ambiguity_scores) / len(ambiguity_scores) if ambiguity_scores else 0.0
    )

    # ── Title detection (first page, largest font block near top) ──────────
    if page_block_data:
        first_page = doc[0]
        best_size = 0.0
        best_text = ""
        for block in first_page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    sz = span.get("size", 0)
                    txt = span.get("text", "").strip()
                    if sz > best_size and len(txt) > 5:
                        best_size = sz
                        best_text = txt
        scan.title = best_text

    # ── Abstract detection (first page) ────────────────────────────────────
    # Look for a block starting with "Abstract" or "Summary" distinct from body text
    if page_block_data:
        first_page_blocks = page_block_data[0]
        abstract_text = ""

        # Sort blocks by Y position
        sorted_blocks = sorted(first_page_blocks, key=lambda b: b["bbox"][1])

        for i, bi in enumerate(sorted_blocks):
            norm = bi["normalized"]
            raw = bi["raw"]

            # Check for explicit "Abstract" heading or start of text
            if norm.startswith("abstract") or norm.startswith("summary"):
                # If it's just the heading "Abstract", take the next block
                if len(norm) < 15:
                    if i + 1 < len(sorted_blocks):
                        next_bi = sorted_blocks[i + 1]
                        # Verify the next block is close (within 50pts) and centered-ish
                        if next_bi["bbox"][1] - bi["bbox"][3] < 50:
                            abstract_text = next_bi["raw"]
                            break
                else:
                    # The block itself contains the abstract text (e.g. "Abstract: The...")
                    # Strip the "Abstract" prefix if distinct
                    if raw.lower().startswith("abstract"):
                        abstract_text = raw[8:].strip(" .:")
                    else:
                        abstract_text = raw
                    break

        scan.abstract = abstract_text

    pages_unambiguous = sum(1 for ps in scan.pages if ps.num_columns in (1, 2))
    scan.pages_with_layout = pages_unambiguous

    logger.info(
        f"[pre-scan] title='{scan.title[:40]}', "
        f"doc_columns={scan.doc_num_columns}, "
        f"ambiguity={scan.column_ambiguity:.2f}, "
        f"pages={num_pages}"
    )
    return scan


# ============================================================================
# APPROACH 3: YOLO Vision Detection
# ============================================================================

# Module-level YOLO model cache
_yolo_model = None


def _rss_mb() -> int:
    """Return current process RSS in MB using /proc/self/status (Linux) or resource module."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024  # kB → MB
    except OSError:
        pass
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    except Exception:
        return -1


def _get_yolo_model():
    """Load and cache the YOLO doclaynet model."""
    global _yolo_model
    if _yolo_model is None:
        import tracemalloc

        tracemalloc.start()
        before_rss = _rss_mb()

        logger.warning(
            "YOLO model cold-load starting (torch + ultralytics lazy import) — "
            "RSS before=%dMB; expect 1-3GB spike",
            before_rss,
        )
        from ultralytics import YOLO
        from huggingface_hub import hf_hub_download

        # Download yolov8n-doclaynet from HuggingFace (cached after first run)
        model_path = hf_hub_download(
            repo_id="hantian/yolo-doclaynet", filename="yolov8n-doclaynet.pt"
        )
        _yolo_model = YOLO(model_path)

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        after_rss = _rss_mb()
        logger.warning(
            "YOLO model loaded from %s — RSS delta=%dMB peak_alloc=%dMB",
            model_path,
            after_rss - before_rss,
            peak // (1024 * 1024),
        )
    return _yolo_model


def _extract_yolo_sync(pdf_bytes: bytes, url: str = "") -> str:
    """
    Core YOLO extraction logic. Runs inside an isolated subprocess spawned by
    extract_with_yolo() — never called directly from the main worker process.
    """
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        html_pages = []

        model = _get_yolo_model()
        scan = _prescan_document(doc)

        for page_num, page in enumerate(doc):
            logger.info(f"[YOLO] Page {page_num + 1}/{len(doc)}")

            regions, caption_rects = _detect_layout_yolo(page, model)
            scan.images_attempted += len(regions)

            page_html, num_images = _extract_page_content(
                page, regions, caption_rects, scan, page_num
            )
            scan.images_extracted += num_images
            html_pages.append(f"<div class='page'>{page_html}</div>")
            logger.info(f"  ✓ Extracted {num_images} images")

        confidence = _compute_confidence_score(scan)
        return _wrap_html("\n".join(html_pages), confidence, scan.title, scan.abstract)

    except Exception as e:
        logger.error(f"YOLO extraction failed: {e}", exc_info=True)
        return ""
    finally:
        if doc is not None:
            doc.close()


def extract_with_yolo(pdf_bytes: bytes, url: str = "") -> str:
    """
    Run YOLO PDF extraction in an isolated subprocess.

    torch + ultralytics (~1.5GB) load inside the subprocess and are freed by
    the OS when it exits — they never enter the main worker's address space.
    stderr from the subprocess flows to the parent so logs appear normally.
    """
    import pathlib
    import pickle
    import subprocess
    import sys

    # Compute paths from __file__ so this works regardless of cwd.
    # __file__ = .../content-queue-backend/app/tasks/extraction_implementations.py
    _here = pathlib.Path(__file__).parent  # .../app/tasks/
    _project_root = str(_here.parent.parent)  # .../content-queue-backend/
    _worker = str(_here / "_yolo_worker.py")

    try:
        payload = pickle.dumps((pdf_bytes, url))
        env = {
            **os.environ,
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "PYTHONPATH": _project_root,
        }
        result = subprocess.run(
            [sys.executable, _worker],
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
    except subprocess.TimeoutExpired:
        logger.error("YOLO subprocess timed out after 300s")
        return ""
    except Exception as e:
        logger.error("YOLO subprocess failed: %s", e)
        return ""


def _detect_layout_yolo(page: fitz.Page, model) -> Tuple[List[Dict], List[fitz.Rect]]:
    """
    Run YOLOv8-doclaynet on a page.

    Returns:
        regions: visual regions (figure/table/formula) with box_2d
        caption_rects: bounding rects of caption blocks (to suppress from text)
    """
    import tempfile

    VISUAL_LABELS = {"picture", "table", "formula"}
    SKIP_LABELS = {"page-footer", "caption"}

    page_w = page.rect.width
    page_h = page.rect.height

    try:
        # Render page to image and save to temp file (avoids numpy ABI conflict)
        pix = page.get_pixmap(dpi=CONFIG["vision_dpi"])
        png_bytes = pix.tobytes("png")

        img_w = pix.width
        img_h = pix.height
        scale_x = page_w / img_w
        scale_y = page_h / img_h

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(png_bytes)
            tmp_path = tmp.name

        del pix, png_bytes  # free ~8-14MB per page before YOLO runs

        import torch

        with torch.inference_mode():
            results = model(tmp_path, conf=0.35, verbose=False)
        os.unlink(tmp_path)

        regions = []
        captions = []
        all_blocks = []
        caption_rects = []

        for detection in results[0].boxes.data:
            px1, py1, px2, py2, _, class_id = detection.tolist()
            label = results[0].names[int(class_id)].lower()

            x0 = px1 * scale_x
            y0 = py1 * scale_y
            x1 = px2 * scale_x
            y1 = py2 * scale_y

            all_blocks.append((y0, y1, label))

            if label == "caption":
                captions.append((x0, y0, x1, y1))
                caption_rects.append(fitz.Rect(x0, y0, x1, y1))
                continue

            if label not in VISUAL_LABELS:
                continue

            area = (x1 - x0) * (y1 - y0)
            if area < CONFIG["min_region_area"]:
                continue

            our_label = "figure" if label == "picture" else label
            regions.append(
                {
                    "label": our_label,
                    "box_2d": [x0, y0, x1, y1],
                    "area": area,
                    "y_center": (y0 + y1) / 2,
                    "x_center": (x0 + x1) / 2,
                }
            )

        # Infer figure regions from captions where no picture was detected
        for cap_x0, cap_y0, cap_x1, cap_y1 in captions:
            covered = any(
                r["box_2d"][3] >= cap_y0 - 30 and r["box_2d"][1] < cap_y0
                for r in regions
            )
            if covered:
                continue

            blocks_above = [
                y1
                for _y0, y1, lbl in all_blocks
                if y1 <= cap_y0 - 5 and lbl not in SKIP_LABELS
            ]
            fig_y0 = max(blocks_above) + 3 if blocks_above else 5
            fig_y1 = cap_y0 - 3
            fig_x0 = max(0, cap_x0 - 5)
            fig_x1 = min(page_w, cap_x1 + 5)

            area = (fig_x1 - fig_x0) * (fig_y1 - fig_y0)
            if area < CONFIG["min_region_area"]:
                continue

            gap_height = fig_y1 - fig_y0
            if gap_height < 60:
                continue

            # Use data-driven header band from pre-scan (not hardcoded %)
            # We don't have scan here, so use a conservative 8% fallback.
            # The real filter is applied again in _extract_page_content.
            if fig_y0 < page_h * 0.08:
                continue

            logger.info(
                f"  Inferred figure from caption gap: y={fig_y0:.0f}-{fig_y1:.0f}"
            )
            regions.append(
                {
                    "label": "figure",
                    "box_2d": [fig_x0, fig_y0, fig_x1, fig_y1],
                    "area": area,
                    "y_center": (fig_y0 + fig_y1) / 2,
                    "x_center": (fig_x0 + fig_x1) / 2,
                }
            )

        regions = _deduplicate_regions(regions)
        logger.info(
            f"  Detected {len(regions)} visual regions: {[r['label'] for r in regions]}"
        )
        return regions, caption_rects

    except Exception as e:
        logger.error(f"YOLO detection failed: {e}")
        return [], []


# ============================================================================
# Content Extraction (shared)
# ============================================================================

_JOURNAL_META_RE = None


def _looks_like_journal_metadata(text: str) -> bool:
    """
    Return True if text looks like journal header/footer metadata that should
    be dropped even when it only appears on one page (e.g. page 1 masthead).

    Matches: DOIs, https:// URLs, copyright lines, page-range numbers,
    journal abbreviations with volume/issue/year, "Received/Accepted" lines.
    """
    global _JOURNAL_META_RE
    if _JOURNAL_META_RE is None:
        _JOURNAL_META_RE = re.compile(
            r"""
            https?://           # any URL
            | 10\.\d{4}/        # DOI prefix
            | ©|\bCopyright\b   # copyright symbol or word
            | \bpubs\.\w+\.\w+  # pubs.acs.org / pubs.rsc.org style
            | \bReceived\b.*\bAccepted\b   # Received … Accepted dates
            | \b\d{4}\s*,\s*\d+\s*,\s*\d+ # year, vol, page  e.g. 2025, 26, 1725
            | \bArticle\b\s*$   # trailing "Article" label
            | \bLetter\b\s*$
            | \bCommunication\b\s*$
            """,
            re.VERBOSE | re.IGNORECASE,
        )
    return bool(_JOURNAL_META_RE.search(text))


def _deduplicate_regions(regions: List[Dict]) -> List[Dict]:
    """
    Remove regions that are substantially contained within a larger region.

    When YOLO detects both a full figure and sub-panels of that figure,
    we keep only the outermost (largest area) region.

    HOWEVER: if regions are similar in size (within 2x of each other), they're
    likely subplots in a grid — keep them all. Only merge when one is clearly
    much larger than the other (>4x area).

    A region is dropped if:
      - >70% of its area is covered by another region, AND
      - The keeper region is >4x larger (suggesting it's a container, not a sibling)
    """
    sorted_regions = sorted(regions, key=lambda r: r["area"], reverse=True)
    kept = []
    for candidate in sorted_regions:
        cx0, cy0, cx1, cy1 = candidate["box_2d"]
        c_area = candidate["area"]
        absorbed = False
        for keeper in kept:
            kx0, ky0, kx1, ky1 = keeper["box_2d"]
            k_area = keeper["area"]

            # Only consider this keeper if it's much larger (>4x area)
            if k_area < c_area * 4:
                continue

            ix0 = max(cx0, kx0)
            iy0 = max(cy0, ky0)
            ix1 = min(cx1, kx1)
            iy1 = min(cy1, ky1)
            if ix1 > ix0 and iy1 > iy0:
                inter_area = (ix1 - ix0) * (iy1 - iy0)
                if c_area > 0 and inter_area / c_area > 0.7:
                    absorbed = True
                    break
        if not absorbed:
            kept.append(candidate)
    return kept


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


def _extract_page_content(
    page: fitz.Page,
    regions: List[Dict],
    caption_rects: List[fitz.Rect],
    scan: DocumentScanResult,
    page_idx: int,
) -> Tuple[str, int]:
    """Extract images + text from page using pre-scan data for header/footer bands."""
    import numpy as np

    pad = CONFIG["region_padding_pts"]
    page_w = page.rect.width
    page_h = page.rect.height

    # Get per-page scan result
    ps = scan.pages[page_idx] if page_idx < len(scan.pages) else PageScan()
    num_columns = ps.num_columns
    header_y = ps.header_y  # bottom of header band (data-driven)
    footer_y = ps.footer_y  # top of footer band (data-driven)

    # Build rects for detected visual regions (figure/table/formula)
    visual_rects = []
    for region in regions:
        x0, y0, x1, y1 = region["box_2d"]
        rect = fitz.Rect(
            max(0, x0 - pad),
            max(0, y0 - pad),
            min(page_w, x1 + pad),
            min(page_h, y1 + pad),
        )
        visual_rects.append((rect, region))

    # Map each caption rect to a sort position adjacent to its nearest visual region.
    # If the caption is above the region: sort just before the image (region.y0 - 1).
    # If the caption is below the region: sort just after the image (region.y1 + 1).
    caption_anchor: Dict[int, Tuple[float, float]] = {}
    for i, crect in enumerate(caption_rects):
        best_gap = float("inf")
        best_anchor = None
        for vrect, _ in visual_rects:
            gap = min(abs(crect.y0 - vrect.y1), abs(vrect.y0 - crect.y1))
            if gap < best_gap:
                best_gap = gap
                best_anchor = vrect
        if best_gap < 120 and best_anchor is not None:
            x_mid = (best_anchor.x0 + best_anchor.x1) / 2
            # Caption above image: sort it just before the image
            caption_above = crect.y1 <= best_anchor.y0 + 10
            if caption_above:
                caption_anchor[i] = (best_anchor.y0 - 1, x_mid)
            else:
                caption_anchor[i] = (best_anchor.y1 + 1, x_mid)

    # skip_rects: only visual regions (text overlapping these is dropped)
    skip_rects = list(visual_rects)

    images_extracted = 0
    content_items = []  # [(y0, html, x_center)]

    # Crop and embed images
    for rect, region in visual_rects:
        label = region.get("label", "figure")
        x_center = (rect.x0 + rect.x1) / 2
        try:
            region_pix = page.get_pixmap(clip=rect, dpi=CONFIG["crop_dpi"])

            # Reject blank crops (pure white or near-white — no content)
            arr = np.frombuffer(region_pix.samples, dtype=np.uint8).reshape(
                region_pix.h, region_pix.w, region_pix.n
            )
            if arr.std() < 5:
                logger.info(f"  Skipping blank {label} region (std={arr.std():.1f})")
                continue

            png_bytes = region_pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            img_html = (
                f'<div class="figure-block">'
                f'<img src="data:image/png;base64,{b64}" alt="{label}" />'
                f"</div>"
            )
            content_items.append((rect.y0, img_html, x_center))
            images_extracted += 1
        except Exception as e:
            logger.warning(f"Failed to crop {label}: {e}")

    # Extract text blocks, skipping those in header/footer bands or overlapping visuals
    try:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        raw_text_items = []  # will be passed to paragraph merger

        for block in blocks:
            if block.get("type") != 0:
                continue

            block_rect = fitz.Rect(block["bbox"])
            block_area = block_rect.get_area()

            # Drop blocks that overlap a visual (figure/table) region
            skip = False
            for srect, _ in skip_rects:
                intersection = block_rect & srect
                if not intersection.is_empty and block_area > 0:
                    if intersection.get_area() / block_area > 0.3:
                        skip = True
                        break
            if skip:
                continue

            # Drop header/footer text using data-driven bands from pre-scan.
            # Two signals: (a) in the header/footer band, (b) looks like metadata.
            in_header = block_rect.y0 < header_y
            in_footer = block_rect.y1 > footer_y
            if in_header or in_footer:
                raw = " ".join(
                    span.get("text", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                normalized = re.sub(
                    r"\b\d+\b", "#", re.sub(r"\s+", " ", raw.lower())
                ).strip()
                # Keep if NOT repeating AND NOT journal metadata
                if scan.repeating_texts and normalized in scan.repeating_texts:
                    continue
                if _looks_like_journal_metadata(raw):
                    continue
                # Also drop pure page-number blocks
                if ps.page_num_rect and block_rect.intersects(ps.page_num_rect):
                    continue
                # Isolated digits in margin = page number
                if re.fullmatch(r"\s*\d{1,4}\s*", raw):
                    continue

            lines_html = _block_to_html(block)
            if not lines_html:
                continue

            # Check if this text block is a caption anchored to a visual region.
            sort_y = block_rect.y0
            x_center = (block_rect.x0 + block_rect.x1) / 2
            is_caption = False
            for i, crect in enumerate(caption_rects):
                intersection = block_rect & crect
                if not intersection.is_empty and block_area > 0:
                    if (
                        intersection.get_area() / block_area > 0.3
                        and i in caption_anchor
                    ):
                        sort_y, x_center = caption_anchor[i]
                        is_caption = True
                        break

            # Heuristic fallback: detect "Figure N:" / "Table N:" / "Scheme N:" prefix
            # blocks that the model missed as captions (YOLO often misses table captions).
            # Apply even when other caption_rects exist — those may belong to other figures.
            _CAPTION_PREFIX_RE = re.compile(
                r"^(Figure|Fig\.|Table|Scheme|Supplementary Figure|Algorithm)\s*\d+[.:]",
                re.IGNORECASE,
            )
            if not is_caption and visual_rects:
                raw_text = " ".join(
                    span.get("text", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                if _CAPTION_PREFIX_RE.match(raw_text):
                    best_gap = float("inf")
                    best_anchor = None
                    for vrect, _ in visual_rects:
                        gap = min(
                            abs(block_rect.y0 - vrect.y1), abs(vrect.y0 - block_rect.y1)
                        )
                        if gap < best_gap:
                            best_gap = gap
                            best_anchor = vrect
                    if best_gap < 120 and best_anchor is not None:
                        x_mid = (best_anchor.x0 + best_anchor.x1) / 2
                        caption_above = block_rect.y1 <= best_anchor.y0 + 10
                        sort_y = (
                            best_anchor.y0 - 1 if caption_above else best_anchor.y1 + 1
                        )
                        x_center = x_mid
                        is_caption = True

            if is_caption:
                inner = lines_html.removeprefix("<p>").removesuffix("</p>")
                lines_html = f'<figcaption class="figure-caption">{inner}</figcaption>'

            raw_text_items.append((sort_y, lines_html, x_center, block_rect, block))

        # Merge continuation paragraphs (cross-column/line-break joins)
        merged_text_items = _merge_continuation_paragraphs(
            raw_text_items, num_columns, page_w
        )

        content_items.extend(merged_text_items)

    except Exception as e:
        logger.error(f"Text extraction failed: {e}")

    content_items.sort(
        key=lambda item: _column_sort_key(item[0], item[2], page_w, num_columns)
    )
    return "\n".join(item[1] for item in content_items), images_extracted


def _merge_continuation_paragraphs(
    items: List[Tuple],
    num_columns: int,
    page_w: float,
) -> List[Tuple]:
    """
    Conservatively merge text blocks that are likely continuation of the same
    paragraph, split by column/line boundaries.

    Detects: superscript-terminated lines where the next block in the same
    column continues the sentence (no capital letter start, no section number).

    Returns items in same format: (sort_y, html, x_center).
    """
    if not items:
        return []

    result = []
    i = 0
    while i < len(items):
        sort_y, html, x_center, block_rect, block = items[i]

        # Only try to merge <p> blocks (not headings, captions, images)
        if not html.startswith("<p>"):
            result.append((sort_y, html, x_center))
            i += 1
            continue

        # Check if the next block is a continuation candidate
        merged = html
        while i + 1 < len(items):
            next_sort_y, next_html, next_x_center, next_rect, next_block = items[i + 1]
            if not next_html.startswith("<p>"):
                break

            curr_col = 0 if x_center <= page_w / 2 else 1
            next_col = 0 if next_x_center <= page_w / 2 else 1
            cross_column = num_columns == 2 and curr_col != next_col

            # Extract text content for heuristics
            curr_inner = merged[3:-4]  # strip <p> and </p>
            next_inner = next_html[3:-4]

            # Next block must start with lowercase (continuation, not new sentence)
            next_text_start = re.sub(r"<[^>]+>", "", next_inner).lstrip()
            next_starts_lower = bool(next_text_start) and next_text_start[0].islower()

            if not next_starts_lower:
                break

            # Current block ends with superscript: clear column-break signal
            ends_with_sup = curr_inner.rstrip().endswith("</sup>")

            # Current block ends mid-sentence (last non-space char is lowercase)
            curr_text_end = re.sub(r"<[^>]+>", "", curr_inner).rstrip()
            ends_mid_sentence = bool(curr_text_end) and curr_text_end[-1].islower()

            should_merge = ends_with_sup or ends_mid_sentence

            if not should_merge:
                break

            # Vertical proximity check (only for same-column merges).
            # Cross-column merges are inherently non-adjacent vertically.
            if not cross_column:
                curr_lines = len(block.get("lines", [])) or 1
                curr_h = block_rect.height
                line_h = curr_h / curr_lines
                y_gap = next_rect.y0 - block_rect.y1
                if y_gap > line_h * 2.5:
                    break

            merged = f"<p>{curr_inner} {next_inner}</p>"
            # keep sort_y and x_center from the first block (left/top position)
            i += 1

        result.append((sort_y, merged, x_center))
        i += 1

    return result


# Matches section numbers like: "1.", "2.", "2.1.", "2.1.3.", "A.", "A.1."
_SECTION_NUM_RE = None


def _heading_level(block: dict) -> int:
    """
    Return 1-3 if the block looks like a section heading, else 0.

    Enhanced detection for academic papers (including TabPFN style):
    - Path A: Font size > body (relaxed for short blocks)
    - Path B: Section numbers ("1. Intro", "B.4 Ablation") - handles non-bold
    - Path C: Spaced small-caps ("A BSTRACT", "1 I NTRODUCTION")
    """
    global _SECTION_NUM_RE
    if _SECTION_NUM_RE is None:
        # Matches "1.", "1.1", "A", "B.4" with optional training dot
        # Relaxed to allow "B.4" (no trailing dot) followed by text
        _SECTION_NUM_RE = re.compile(r"^(?:[A-Z]|\d+)(?:\.\d+)*(?:\.|)\s+\S")

    spans = [
        span
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ]
    if not spans:
        return 0

    sizes = [s.get("size", 0) for s in spans]
    max_size = max(sizes) if sizes else 0

    full_text = " ".join(s.get("text", "").strip() for s in spans).strip()
    # Length check: headers usually aren't huge paragraphs
    if len(full_text) > 200 or len(full_text) < 2:
        return 0

    # Block geometry checks
    num_lines = len(block.get("lines", []))
    is_short_block = len(full_text) < 120 and num_lines <= 2

    first_flags = spans[0].get("flags", 0)
    first_bold = bool(first_flags & 16)

    # REJECT: blocks starting with superscript/footnote markers
    # 1. Explicit superscript flag
    if first_flags & 1:
        return 0

    # 2. Implicit superscript (first char spans are significantly smaller than the rest)
    if len(sizes) > 1:
        first_size = sizes[0]
        # If first char is < 80% of the max font size in the block, it's likely a footnote number
        if first_size < max_size * 0.8:
            return 0

    all_caps = full_text.isupper() and len(full_text) > 3

    # ── Path C: Spaced Capitals (Small Caps emulation) ──
    # e.g. "A BSTRACT", "1 I NTRODUCTION"
    words = full_text.split()
    is_spaced_caps = False
    if words:
        avg_word_len = sum(len(w) for w in words) / len(words)
        collapsed = full_text.replace(" ", "")
        # Heuristic: mostly short "words" (spaced letters), collapses to valid uppercase
        is_spaced_caps = (
            len(words) > 1
            and avg_word_len < 4.0
            and collapsed.isupper()
            and len(collapsed) > 3
        )

    if is_spaced_caps:
        # Treat as heading. Level depends on size or default to h2.
        return 1 if max_size > 11 else 2

    # ── Path B: explicit section number prefix ──
    # Relaxed: allow non-bold if block is short
    if _SECTION_NUM_RE.match(full_text):
        if first_bold or is_short_block:
            prefix = full_text.split()[0]
            depth = prefix.count(".")
            return min(depth + 1, 3)

    # ── Path A: font-size-based ──
    BODY_SIZE = 10.5

    if max_size < BODY_SIZE + 1.5:
        # Strict low-size cutoff for pure size-based path
        return 0

    # Relaxed style check: allow plain text if it's a short block detected by size
    # (Existing logic required bold/caps, but 12pt short lines are likely headers)
    if not first_bold and not all_caps and not is_short_block:
        return 0

    if max_size >= BODY_SIZE + 5:
        return 1
    elif max_size >= BODY_SIZE + 3:
        return 2
    else:
        return 3


def _line_to_text(line: dict, markup: bool = True) -> str:
    """Render a single PDF line dict to a text/HTML string."""
    line_text = ""
    for span in line.get("spans", []):
        text = span.get("text", "")
        if not text:
            continue
        flags = span.get("flags", 0)
        if not text.strip():
            line_text += text
            continue
        if markup:
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            inner = text.strip()
            if flags & 1:
                text = f"{leading}<sup>{inner}</sup>{trailing}"
            elif flags & 16:
                text = f"{leading}<strong>{inner}</strong>{trailing}"
            elif flags & 2:
                text = f"{leading}<em>{inner}</em>{trailing}"
        line_text += text
    return re.sub(r"\s+", " ", line_text).strip()


def _first_line_heading_level(block: dict) -> int:
    """
    Check whether the first line of a block starts with a bold section-number
    prefix (e.g. "2.1. Formulation..." or "3. CONCLUSION").

    This handles journal papers where the section heading and paragraph body
    are merged into a single PDF text block (the heading is just the first line).

    Returns heading level (1-3) or 0.
    """
    global _SECTION_NUM_RE
    if _SECTION_NUM_RE is None:
        _SECTION_NUM_RE = re.compile(r"^(?:[A-Z]|\d+)(?:\.\d+)*\.\s+\S")

    lines = block.get("lines", [])
    if not lines:
        return 0

    first_spans = [s for s in lines[0].get("spans", []) if s.get("text", "").strip()]
    if not first_spans:
        return 0

    first_bold = bool(first_spans[0].get("flags", 0) & 16)
    if not first_bold:
        return 0

    first_text = " ".join(s.get("text", "").strip() for s in first_spans).strip()
    if not _SECTION_NUM_RE.match(first_text):
        return 0

    prefix = first_text.split()[0]  # e.g. "2.1."
    depth = prefix.count(".")
    return min(depth + 1, 3)


def _block_to_html(block: dict) -> str:
    """
    Convert a PyMuPDF text block dict to HTML.

    Returns <h1>-<h3> for headings, <p> for body text.
    Bold/italic inline formatting is preserved inside body paragraphs
    but stripped inside headings (the tag itself conveys emphasis).

    Handles split heading+body blocks: when the first line is a bold section-
    number heading but subsequent lines are body text (common in journal PDFs),
    emits '<hN>heading</hN><p>body...</p>' rather than losing the heading.
    """
    lines = block.get("lines", [])
    if not lines:
        return ""

    level = _heading_level(block)

    # If the whole block wasn't detected as a heading, check whether the
    # *first line* is a heading merged with body text below it.
    first_line_level = 0
    if not level and len(lines) > 1:
        first_line_level = _first_line_heading_level(block)

    if first_line_level:
        # Split: bold spans on first line → heading, rest → paragraph body.
        # Non-bold spans on the first line (e.g. ' The' after 'Section Title.')
        # are paragraph continuations, not part of the heading.
        first_line = lines[0]
        heading_parts = []
        body_first_line_parts = []
        in_heading = True
        for span in first_line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            is_bold = bool(span.get("flags", 0) & 16)
            if in_heading and is_bold:
                heading_parts.append(text.strip())
            else:
                in_heading = False  # once we hit non-bold, rest is body
                body_first_line_parts.append(text)

        heading_text = re.sub(r"\s+", " ", " ".join(heading_parts)).strip()
        heading_html = f"<h{first_line_level}>{heading_text}</h{first_line_level}>"

        body_lines = []
        if body_first_line_parts:
            first_body = re.sub(r"\s+", " ", "".join(body_first_line_parts)).strip()
            if first_body:
                body_lines.append(first_body)
        for line in lines[1:]:
            t = _line_to_text(line, markup=True)
            if t:
                body_lines.append(t)

        if body_lines:
            body_content = " ".join(body_lines)
            return heading_html + f"<p>{body_content}</p>"
        return heading_html

    # Standard rendering
    lines_html = []
    for line in lines:
        line_text = _line_to_text(line, markup=(not level))
        if line_text:
            lines_html.append(line_text)

    if not lines_html:
        return ""

    content = " ".join(lines_html)
    if level:
        return f"<h{level}>{content}</h{level}>"
    return f"<p>{content}</p>"


# ============================================================================
# Confidence Scoring
# ============================================================================


def _compute_confidence_score(scan: DocumentScanResult) -> int:
    """
    Compute a 0-100 confidence score for the extraction quality.

    Sub-scores:
      - Layout detection rate: what fraction of pages had unambiguous column layout
      - Column clarity: how balanced (low ambiguity = high confidence)
      - Title detected: +10 points
      - Header/footer patterns found: +10 points
      - Image extraction rate (if any were attempted)

    Thresholds for user-facing warning:
      < 60: Low confidence — warn user, results may be poorly structured
      60-79: Medium — show note that some elements may be missing
      >= 80: High — no warning needed
    """
    score = 0

    # Layout detection rate (0-40 points)
    if scan.total_pages > 0:
        layout_rate = scan.pages_with_layout / scan.total_pages
        score += int(layout_rate * 40)

    # Column clarity (0-20 points): low ambiguity = high clarity
    clarity = max(0.0, 1.0 - scan.column_ambiguity * 3)
    score += int(clarity * 20)

    # Title detected (0-10 points)
    if scan.title:
        score += 10

    # Header/footer filter quality (0-10 points)
    if len(scan.repeating_texts) > 0:
        score += 10

    # Image extraction rate (0-20 points)
    if scan.images_attempted > 0:
        img_rate = min(1.0, scan.images_extracted / scan.images_attempted)
        score += int(img_rate * 20)
    else:
        # No images attempted — either text-only paper or detection missed everything.
        # Give partial credit (10) since text-only papers are valid.
        score += 10

    return min(100, score)


def _confidence_label(score: int) -> str:
    if score >= 80:
        return "high"
    elif score >= 60:
        return "medium"
    else:
        return "low"


# ============================================================================
# HTML Wrapper & Main Entry
# ============================================================================


def _wrap_html(
    body: str, confidence: int = 0, title: str = "", abstract: str = ""
) -> str:
    """Wrap content in HTML template with embedded confidence score and metadata."""
    label = _confidence_label(confidence)
    warning_html = ""
    if confidence < 60:
        warning_html = f"""
    <div class="extraction-warning" data-confidence="{confidence}">
      <strong>Low extraction confidence ({confidence}/100).</strong>
      This PDF may have an unusual layout (multi-column, complex figures, or
      non-standard formatting). Some content may be missing or mis-ordered.
      Check the original PDF if something looks wrong.
    </div>"""
    elif confidence < 80:
        warning_html = f"""
    <div class="extraction-notice" data-confidence="{confidence}">
      Extraction confidence: {confidence}/100 (medium).
      Most content should be correct, but complex figures or multi-column
      sections may have minor ordering issues.
    </div>"""

    # Escape metadata for HTML attributes
    safe_title = title.replace('"', "&quot;")
    safe_abstract = abstract.replace('"', "&quot;")

    return f"""<!DOCTYPE html>
<!-- extraction-confidence: {confidence} ({label}) -->
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="extraction-confidence" content="{confidence}">
    <meta name="extraction-title" content="{safe_title}">
    <meta name="extraction-description" content="{safe_abstract}">
    <title>Extracted PDF</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .page {{
            background: white;
            padding: 40px;
            margin: 20px 0;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .figure-block {{
            margin: 1.5em 0;
            text-align: center;
        }}
        .figure-block img {{
            max-width: 100%;
            height: auto;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 4px;
        }}
        .figure-caption {{
            font-size: 0.85em;
            font-style: italic;
            color: #555;
            text-align: center;
            margin: 0.25em auto 1em;
            max-width: 90%;
        }}
        h1, h2, h3 {{ margin: 1em 0 0.4em; line-height: 1.3; }}
        h1 {{ font-size: 1.6em; }}
        h2 {{ font-size: 1.25em; }}
        h3 {{ font-size: 1.05em; }}
        p {{ margin: 0.5em 0; }}
        .extraction-warning {{
            background: #fff3cd;
            border: 1px solid #ffc107;
            border-radius: 4px;
            padding: 12px 16px;
            margin-bottom: 20px;
            font-size: 0.9em;
        }}
        .extraction-notice {{
            background: #e8f4fd;
            border: 1px solid #90caf9;
            border-radius: 4px;
            padding: 10px 14px;
            margin-bottom: 16px;
            font-size: 0.85em;
            color: #555;
        }}
    </style>
</head>
<body>
    {warning_html}
    {body}
</body>
</html>"""
