"""
backend/pdf_deduplicator.py
─────────────────────────────────────────────────────────────
Barcode / Text-based PDF page deduplication engine.

Strategy (auto-detected on page 1):
  1. TEXT mode (fast path):
       Uses fitz.get_text() to extract searchable text, then
       matches a 13-digit numeric ID via regex.
       This is an order of magnitude faster than rendering pixels.
  2. BARCODE mode (fallback):
       Renders at 150 DPI and scans for 1D barcodes using pyzbar
       with progressive ROI (ultra-tight crop rect).

The mode is determined once on the first page and applied to all
subsequent pages.

Keeps only pages with distinct/unique ID values.

Streaming / In-Memory optimization:
  - Valid (unique) pages are inserted directly into a RAM document
    (out_doc or a shared combined_doc) during the scan loop.
  - This eliminates the need to re-open the source file a second
    time and avoids unnecessary intermediate disk writes.

Exponential + Binary Search (Cluster Jump) optimization:
  - Relies on the domain property that identical pages are always
    grouped into contiguous clusters (A A A ... B B B ... C C C ...).
  - Once the ID for a cluster is known, the algorithm jumps forward
    with exponentially growing steps (1, 2, 4, 8, 16, ...) to find
    the approximate boundary of the cluster, then binary-searches the
    exact first page of the next cluster.
  - Result: O(K * log(N/K)) page reads instead of O(N), where K is
    the number of unique clusters and N is the total page count.
    For large files this is typically 20-50x fewer Barcode/OCR calls.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import ctypes
from typing import Callable, Optional

from backend.pdf_processor import save_pdf_safely, is_network_path, open_pdf_smart

import pymupdf as fitz
from PIL import Image
from pyzbar.pyzbar import decode

# ── Type aliases for callbacks ──────────────────────────────

# progress_cb(file_idx, total_files, page_num, total_pages)
ProgressCb = Callable[[int, int, int, int], None]

# log_cb(file_idx, page_num, barcode_value_or_none, action_str)
#   action_str is one of: "KEEP", "REMOVE", "NO_BARCODE"
LogCb = Callable[[int, int, Optional[str], str], None]

# ── Regex pattern for 13-digit numeric ID ───────────────────
# Matches 13 consecutive digits, or 13 digits separated by single spaces.
# Bounded so it doesn't match inside a longer number.
_ID_PATTERN = re.compile(r'(?<!\d)(?<!\d )(?:\d{13}|\d(?: \d){12})(?! ?\d)')

# ── Network path detection ───────────────────────────────────
_is_network_path = is_network_path


# ── Text extraction helpers (fast path) ─────────────────────

def _extract_text_id(page: fitz.Page) -> Optional[str]:
    """
    Extract searchable text from a PDF page via fitz and look
    for a 13-digit numeric ID using regex.
    Returns the ID string (spaces removed) or None.
    """
    text = page.get_text()
    if not text:
        return None
    match = _ID_PATTERN.search(text)
    if match:
        return match.group(0).replace(" ", "")
    return None


# ── Barcode scanning helpers (fallback) ─────────────────────

def _scan_barcode(page: fitz.Page, clip: fitz.Rect, dpi: int = 150) -> tuple[Optional[str], Optional[fitz.Rect]]:
    """
    Render a region of the page at the given DPI in grayscale,
    then decode any 1D barcode found (ignoring QR codes).
    Returns (barcode text or None, exact fitz.Rect of the barcode).
    """
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, clip=clip)
    pil_img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    decoded = decode(pil_img)
    if decoded:
        for d in decoded:
            if d.type != 'QRCODE':
                # Map pixel coordinates back to PDF points
                scale = 72.0 / dpi
                pdf_x0 = clip.x0 + d.rect.left * scale
                pdf_y0 = clip.y0 + d.rect.top * scale
                pdf_x1 = pdf_x0 + d.rect.width * scale
                pdf_y1 = pdf_y0 + d.rect.height * scale
                exact_rect = fitz.Rect(pdf_x0, pdf_y0, pdf_x1, pdf_y1)
                return d.data.decode('utf-8'), exact_rect
    return None, None


def _determine_crop_rect(page: fitz.Page) -> tuple[fitz.Rect, Optional[str]]:
    """
    Progressive ROI on the first page to find the best scan region.
    Tries:
      1. Bottom-left quarter (1/4)
      2. Left half (1/2)
      3. Full page

    Returns (tight_crop_rect, barcode_value_or_none).
    If no barcode is found in any region, returns (full_page_rect, None).
    """
    rect = page.rect
    w = rect.width
    h = rect.height

    # 1. Bottom-left 1/4 (left half, bottom half)
    r_bl = fitz.Rect(rect.x0, rect.y0 + h / 2, rect.x0 + w / 2, rect.y1)
    # 2. Left 1/2
    r_lh = fitz.Rect(rect.x0, rect.y0, rect.x0 + w / 2, rect.y1)
    # 3. Whole page
    r_whole = rect

    for test_rect in [r_bl, r_lh, r_whole]:
        code, exact_rect = _scan_barcode(page, test_rect)
        if code and exact_rect:
            # Expand the exact rect by a safe margin (e.g., 40 points)
            margin = 40
            tight_crop = fitz.Rect(
                max(rect.x0, exact_rect.x0 - margin),
                max(rect.y0, exact_rect.y0 - margin),
                min(rect.x1, exact_rect.x1 + margin),
                min(rect.y1, exact_rect.y1 + margin)
            )
            return tight_crop, code

    # Fallback: no barcode found, use full page for subsequent pages
    return r_whole, None


def _get_page_code(
    doc: fitz.Document,
    page_num: int,
    strategy: str,
    crop_rect: fitz.Rect,
) -> Optional[str]:
    """
    Read only the ID code of a page using the already-determined strategy.
    Used by the cluster-jump search functions to probe arbitrary page indices
    without touching the output document.
    """
    page = doc.load_page(page_num)
    if strategy == "TEXT":
        return _extract_text_id(page)
    else:
        code, _ = _scan_barcode(page, crop_rect)
        if not code:
            # Barcode may have shifted — re-evaluate ROI (read-only probe)
            _, code = _determine_crop_rect(page)
        return code


# ── Cluster Jump: Exponential + Binary Search ────────────────
#
# Assumption: identical pages (same barcode) form contiguous clusters.
# Given the current cluster's code and start position, find the index of
# the FIRST page whose code differs (= start of the next cluster).
#
# Phase 1 — Exponential jump:
#   Walk forward with step = 1, 2, 4, 8, 16, ... until a page with a
#   different code is found or the document ends.
#
# Phase 2 — Binary search:
#   Narrow down the exact boundary between the last "same" page and the
#   first "different" page found in Phase 1.
#
# Returns the index of the first page of the next cluster, or total_pages
# if the current cluster extends to the end of the document.

def _find_next_cluster_start(
    doc: fitz.Document,
    cluster_start: int,
    current_code: str,
    strategy: str,
    crop_rect: fitz.Rect,
) -> int:
    """
    Return the page index of the first page that belongs to a new cluster
    (i.e., has a barcode/ID code different from *current_code*), starting
    the search from *cluster_start*.

    Uses Exponential (Galloping) search followed by Binary Search.

    Returns total_pages when the current cluster reaches end-of-document.
    """
    total_pages = len(doc)

    # ── Phase 1: Exponential jump to find upper bound ────────
    step = 1
    lo = cluster_start          # last known "same-code" index
    hi = cluster_start + step   # candidate "different-code" index

    while hi < total_pages:
        probe_code = _get_page_code(doc, hi, strategy, crop_rect)
        if probe_code != current_code:
            break               # hi is now inside the next cluster
        lo = hi
        step *= 2
        hi = cluster_start + step

    # Clamp hi to valid range
    hi = min(hi, total_pages - 1)

    # Edge case: entire remaining document is the same cluster
    if hi == total_pages - 1:
        last_code = _get_page_code(doc, hi, strategy, crop_rect)
        if last_code == current_code:
            return total_pages  # no next cluster

    # ── Phase 2: Binary search in [lo, hi] ──────────────────
    # Invariant:
    #   doc[lo] has code == current_code
    #   doc[hi] has code != current_code  (or hi == total_pages - 1 edge)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        mid_code = _get_page_code(doc, mid, strategy, crop_rect)
        if mid_code == current_code:
            lo = mid    # boundary is further right
        else:
            hi = mid    # boundary is further left or at mid

    # hi is the first page of the next cluster
    return hi


# ── Main deduplication function ─────────────────────────────

def deduplicate_pdf(
    pdf_path: str,
    output_folder: str,
    file_idx: int = 0,
    total_files: int = 1,
    target_doc: Optional[fitz.Document] = None,
) -> dict:
    """
    Deduplicate a single PDF file based on a unique page ID.

    Strategy auto-detection on page 1:
      - Try fitz searchable text extraction first (near-instant).
      - If that fails, fall back to pyzbar 1D barcode scanning at 150 DPI.

    The chosen strategy is then used for all remaining pages.

    Cluster-jump algorithm (Exponential + Binary Search):
      - After identifying the ID of the current cluster, the algorithm
        jumps forward exponentially to find where the next cluster begins,
        then binary-searches the exact boundary.
      - Only the representative page of each cluster (the first page) is
        saved to the output document.
      - Pages that are probed during the jump/binary-search phase are read
        but NOT inserted (they are duplicates by definition).

    Valid (unique) pages are streamed / inserted directly into the output
    document — no second pass or re-open required.

    Args:
        pdf_path:      Absolute path to the input PDF.
        output_folder: Directory to save the deduplicated output (Split mode).
                       Ignored when target_doc is provided (Combine mode).
        file_idx:      0-based index of this file in the batch.
        total_files:   Total number of files in the batch.
        target_doc:    If provided, unique pages are inserted into this shared
                       RAM document (Combine mode). The caller is responsible
                       for saving and closing target_doc.
                       If None, a new per-file document is created and saved
                       to output_folder (Split mode).

    Returns:
        {
            "success": True/False,
            "input_path": str,
            "output_path": str or None,  # None when target_doc is provided
            "total_pages": int,
            "kept_pages": int,
            "removed_pages": int,
            "pages_read": int,           # diagnostic: actual pages decoded
            "strategy": "TEXT" or "BARCODE",
            "error": str or None
        }
    """
    # ── Open source PDF (preload into RAM if on a network disk) ──────────
    # Network disks (UNC or mapped drives) cause PyMuPDF to make thousands
    # of small random reads over SMB, each incurring network RTT.  Reading
    # the whole file sequentially once and opening from a memory stream
    # eliminates that latency entirely.
    try:
        doc = open_pdf_smart(pdf_path)
    except Exception as exc:
        return {
            "success": False,
            "input_path": pdf_path,
            "output_path": None,
            "total_pages": 0,
            "kept_pages": 0,
            "removed_pages": 0,
            "pages_read": 0,
            "strategy": None,
            "error": f"Failed to open PDF: {exc}",
        }

    total_pages = len(doc)
    seen_codes: set[str] = set()
    kept_count: int = 0
    pages_read: int = 0     # diagnostic counter

    # Strategy state (determined once from page 0)
    strategy: Optional[str] = None
    crop_rect: Optional[fitz.Rect] = None

    # In Split mode: create a fresh per-file RAM document.
    # In Combine mode: stream into the shared target_doc provided by caller.
    is_combine = target_doc is not None
    out_doc = target_doc if is_combine else fitz.open()

    # ── Page 0: auto-detect strategy ────────────────────────
    page0 = doc.load_page(0)
    pages_read += 1

    found_code = _extract_text_id(page0)
    if found_code:
        strategy = "TEXT"
    else:
        crop_rect, found_code = _determine_crop_rect(page0)
        strategy = "BARCODE"

    # ── Cluster-jump main loop ───────────────────────────────
    # cluster_start: index of the first (representative) page of the
    # current cluster. This is the page we KEEP.
    cluster_start = 0

    while cluster_start < total_pages:
        # Get the code for the representative page of this cluster.
        # Page 0 is already read above; subsequent clusters use _get_page_code.
        if cluster_start == 0:
            current_code = found_code
        else:
            current_code = _get_page_code(doc, cluster_start, strategy, crop_rect)
            pages_read += 1

        if current_code and current_code not in seen_codes:
            # This is a genuinely new unique cluster → keep its first page
            seen_codes.add(current_code)
            out_doc.insert_pdf(doc, from_page=cluster_start, to_page=cluster_start)
            kept_count += 1

        if total_pages == 1:
            break  # single-page document; nothing left to search

        # Find where the next cluster begins using Exponential + Binary Search.
        # _find_next_cluster_start reads O(log(cluster_size)) additional pages.
        next_start = _find_next_cluster_start(
            doc, cluster_start, current_code, strategy, crop_rect
        )

        # Approximate pages_read accounting: log2 of pages jumped over.
        # (Exact tracking would require passing a counter into the helper.)
        import math
        skipped = next_start - cluster_start - 1
        if skipped > 0:
            pages_read += max(1, int(math.log2(skipped + 1)) * 2)  # exp + binary

        cluster_start = next_start

    doc.close()

    # ── Split mode: save the per-file result to disk now ────
    output_path = None
    if not is_combine:
        if kept_count > 0:
            basename = os.path.basename(pdf_path)
            name, ext = os.path.splitext(basename)
            output_name = f"(Dedup) {name}"
            output_path = save_pdf_safely(
                doc=out_doc,
                output_folder=output_folder,
                base_name=output_name,
                garbage=3,
                deflate=True,
            )
        out_doc.close()

    return {
        "success": True,
        "input_path": pdf_path,
        "output_path": output_path,  # None in Combine mode (caller handles it)
        "total_pages": total_pages,
        "kept_pages": kept_count,
        "removed_pages": total_pages - kept_count,
        "pages_read": pages_read,    # diagnostic: how many pages were actually decoded
        "strategy": strategy,
        "error": None,
    }


def deduplicate_batch(
    pdf_paths: list[str],
    output_folder: str,
    progress_cb: Optional[ProgressCb] = None,
    combine: bool = False,
    combined_output_path: Optional[str] = None,
) -> list[dict]:
    """
    Deduplicate multiple PDF files as a batch.
    Each file is processed independently (per-file dedup scope).

    Args:
        pdf_paths:            List of absolute paths to the input PDFs.
        output_folder:        Directory for per-file output (Split mode).
        progress_cb:          Optional progress callback (called once per file).
        combine:              If True, all unique pages from every file are
                              streamed into a single shared RAM document
                              which is then saved once to combined_output_path.
        combined_output_path: Absolute output path for the combined PDF.
                              Required when combine=True.

    Returns a list of result dicts (one per file).
    """
    results = []
    total_files = len(pdf_paths)

    if combine:
        # ── Combine mode: one shared RAM document for all files ──
        combined_doc = fitz.open()

        for idx, path in enumerate(pdf_paths):
            if progress_cb:
                progress_cb(idx, total_files, 0, 0)  # per-file notification only
            result = deduplicate_pdf(
                pdf_path=path,
                output_folder=output_folder,
                file_idx=idx,
                total_files=total_files,
                target_doc=combined_doc,   # stream directly into shared doc
            )
            results.append(result)

        # Write combined output once at the end
        if combined_doc.page_count > 0 and combined_output_path:
            comb_folder = os.path.dirname(combined_output_path)
            comb_base = os.path.basename(combined_output_path)
            combined_output_path = save_pdf_safely(
                doc=combined_doc,
                output_folder=comb_folder,
                base_name=comb_base,
                garbage=3,
                deflate=True,
            )
        combined_doc.close()

    else:
        # ── Split mode: each file produces its own output PDF ──
        for idx, path in enumerate(pdf_paths):
            if progress_cb:
                progress_cb(idx, total_files, 0, 0)  # per-file notification only
            result = deduplicate_pdf(
                pdf_path=path,
                output_folder=output_folder,
                file_idx=idx,
                total_files=total_files,
                target_doc=None,           # per-file doc, saved inside deduplicate_pdf
            )
            results.append(result)

    return results


# ── Public Detection API (Cluster-Jump) ─────────────────────

def detect_unique_pages_dedup(pdf_source, return_total: bool = False) -> list[int] | tuple[list[int], int]:
    """
    Run strategy auto-detection and Cluster-Jump exponential + binary search
    on pdf_source (either in-memory bytes or file path str).
    Uses open_pdf_smart() for zero-latency network reading.
    Returns list of 1-based page numbers representing the first page of each unique cluster.
    If return_total is True, returns tuple of (kept_pages, total_pages).
    """
    doc = open_pdf_smart(pdf_source)
    try:
        total_pages = len(doc)
        if total_pages == 0:
            return ([], 0) if return_total else []

        # Trang 0: Tự động phát hiện chiến lược TEXT hay BARCODE
        page0 = doc.load_page(0)
        found_code = _extract_text_id(page0)
        if found_code:
            strategy = "TEXT"
            crop_rect = None
        else:
            crop_rect, found_code = _determine_crop_rect(page0)
            strategy = "BARCODE"

        kept_pages: list[int] = []
        seen_codes: set[str] = set()
        cluster_start = 0

        while cluster_start < total_pages:
            if cluster_start == 0:
                current_code = found_code
            else:
                current_code = _get_page_code(doc, cluster_start, strategy, crop_rect)

            if current_code and current_code not in seen_codes:
                seen_codes.add(current_code)
                kept_pages.append(cluster_start + 1)  # 1-based page index

            if total_pages == 1:
                break

            # Nhảy phi mã + tìm kiếm nhị phân đến đầu cụm kế tiếp O(K * log(N/K))
            next_start = _find_next_cluster_start(
                doc, cluster_start, current_code, strategy, crop_rect
            )
            cluster_start = next_start

        if return_total:
            return kept_pages, total_pages
        return kept_pages
    finally:
        doc.close()

