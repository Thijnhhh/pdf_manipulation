"""
backend/pdf_processor.py
─────────────────────────────────────────────────────────────
PDF rendering and compilation engine using PyMuPDF (fitz).

Public functions:
  • get_thumbnails(source, dpi=120)
      Opens a PDF from a file path or raw bytes.
      Returns a list of Base64-encoded PNG data URLs — one per page.
      Used to populate the grid in Panel 2.

  • get_page_hires(source, page_index, dpi=200)
      Returns a single high-resolution Base64 PNG for the Lightbox.

  • execute_export(assembly_data, source_cache, output_path, mode, progress_cb)
      Assembles queued pages into output PDF(s).
      mode = "split"  → one PDF per SOL code
      mode = "merge"  → one combined PDF across all SOL codes
      progress_cb(pct: int) is called during the loop to push progress to UI.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import os
import re
import uuid
from typing import Callable

import pymupdf as fitz  # PyMuPDF 1.24+ recommends 'import pymupdf'


_SOL_PATTERN = re.compile(r"^(\d{9})-(\d{1,3})$")


def generate_merged_filename(
    sol_codes: list[str],
    output_path: str | None = None,
    max_len: int = 250,
) -> str:
    """
    Generate grouped merged filename according to SO patterns:
      SO1-1,2,3 SO2-1,2 ...
    - Groups SOL codes by 9-digit SO prefix
    - Sorts lines numerically in ascending order and deduplicates them
    - Separates SO clusters with spaces
    - Dynamically limits total base name length so the full path never
      exceeds Windows MAX_PATH (259 usable chars).  When output_path is
      supplied the limit is computed as:
          259 - len(abs(output_path)) - 1 (sep) - 4 (.pdf) = remaining chars
      clamped to [10, 255].  Falls back to max_len (default 250) when
      output_path is None or the folder string is unusually long.
    - Falls back to 'merged_output' if empty or invalid
    """
    # ── Compute dynamic character budget ────────────────────────────────────
    _WINDOWS_MAX_PATH = 259  # Windows MAX_PATH (260) minus the null terminator
    _EXT_LEN          = 4    # len(".pdf")
    _MIN_NAME_LEN     = 10   # safety floor so we always produce a usable name

    if output_path is not None:
        abs_folder = os.path.abspath(output_path)
        # +1 for the path separator between folder and filename
        remaining = _WINDOWS_MAX_PATH - len(abs_folder) - 1 - _EXT_LEN
        effective_max = max(_MIN_NAME_LEN, min(255, remaining))
    else:
        effective_max = max_len

    # ── Build the grouped name ───────────────────────────────────────────────
    clean_sol_codes = list(dict.fromkeys(s.strip() for s in sol_codes if s and isinstance(s, str) and s.strip()))
    so_groups: dict[str, set[int]] = {}
    so_order: list[str] = []

    for sol in clean_sol_codes:
        m = _SOL_PATTERN.match(sol.strip())
        if not m:
            continue
        so_base = m.group(1)
        line_num = int(m.group(2))
        if so_base not in so_groups:
            so_groups[so_base] = set()
            so_order.append(so_base)
        so_groups[so_base].add(line_num)

    if not so_groups:
        return "merged_output"

    parts = []
    for so_base in so_order:
        sorted_lines = sorted(so_groups[so_base])
        lines_str = ",".join(str(ln) for ln in sorted_lines)
        parts.append(f"{so_base}-{lines_str}")

    merged_name = " ".join(parts)

    if len(merged_name) > effective_max:
        merged_name = merged_name[:effective_max].rstrip(" ,-_")

    if not merged_name.strip():
        return "merged_output"

    return merged_name



# ── Internal helpers ─────────────────────────────────────────

def _open_pdf(source: str | bytes) -> fitz.Document:
    """Open a PDF from a file path string or raw bytes."""
    if isinstance(source, (bytes, bytearray)):
        return fitz.open(stream=source, filetype="pdf")
    return fitz.open(source)


_TEMP_DIR: str | None = None

def set_temp_dir(temp_dir: str):
    global _TEMP_DIR
    _TEMP_DIR = temp_dir


def _page_to_temp_img(page: fitz.Page, dpi: int, fmt: str = "jpeg", quality: int = 75) -> str:
    """Render a single fitz.Page, save to _TEMP_DIR, and return the relative /temp URL."""
    zoom   = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix    = page.get_pixmap(matrix=matrix, alpha=False)
    
    if fmt.lower() == "jpeg":
        img_bytes = pix.tobytes("jpeg", jpg_quality=quality)
        ext = "jpg"
    else:
        img_bytes = pix.tobytes("png")
        ext = "png"
        
    filename = f"{uuid.uuid4().hex}.{ext}"
    if _TEMP_DIR:
        filepath = os.path.join(_TEMP_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        return f"/temp/{filename}"
    else:
        # Fallback to base64 if temp dir not set (e.g. testing without webview)
        b64 = base64.b64encode(img_bytes).decode("ascii")
        mime = "image/jpeg" if ext == "jpg" else "image/png"
        return f"data:{mime};base64,{b64}"


# ── Public API ───────────────────────────────────────────────

def get_pdf_meta(source: str | bytes) -> dict:
    """
    Instantly return PDF metadata (page count) without rendering pages.
    """
    doc = _open_pdf(source)
    try:
        return {"page_count": len(doc)}
    finally:
        doc.close()


def get_page_range_thumbnails(source: str | bytes, start_page: int, count: int, dpi: int = 72) -> dict:
    """
    Render a specific range of pages (1-based index).
    Returns dict mapping 1-based page index to base64 image data URL.
    """
    doc = _open_pdf(source)
    results = {}
    total_pages = len(doc)
    try:
        end_page = min(start_page + count - 1, total_pages)
        for p_idx in range(start_page - 1, end_page):
            page = doc[p_idx]
            results[p_idx + 1] = _page_to_temp_img(page, dpi, fmt="jpeg", quality=75)
    finally:
        doc.close()
    return results

def get_page_hires(source: str | bytes, page_index: int, dpi: int = 150) -> str:
    """
    Render a single page at high resolution (150 DPI JPEG) for Lightbox inspection.
    """
    doc = _open_pdf(source)
    try:
        page = doc[page_index - 1]
        return _page_to_temp_img(page, dpi, fmt="jpeg", quality=85)
    finally:
        doc.close()


def execute_export(
    assembly_data: dict,
    source_cache: dict,
    output_path: str,
    mode: str = "split",
    progress_cb: Callable[[int], None] | None = None,
) -> dict:
    """
    Build output PDF(s) from the assembly queue.

    Args:
        assembly_data: { sol_code: [{ typeId, pageIndex }, ...], ... }
        source_cache:  { "{sol}_{typeId}_{fileIdx}": str|bytes } — file path or raw bytes.
                       Built by bridge.py before calling this function.
        output_path:   Local directory path to write output PDFs.
        mode:          "split" → one PDF per SOL  |  "merge" → one combined PDF
        progress_cb:   Optional callable(pct: int) for progress reporting.

    Returns:
        { "success": True, "files": [...output paths...] }
        or { "success": False, "error": "..." }
    """
    os.makedirs(output_path, exist_ok=True)

    # 1. Early Return for empty queue
    if not assembly_data or all(len(items) == 0 for items in assembly_data.values()):
        return {"success": False, "error": "No pages to export (Assembly Queue is empty)."}

    sol_codes   = list(dict.fromkeys(k.strip() for k, v in assembly_data.items() if len(v) > 0 and k and isinstance(k, str) and k.strip()))
    total_items = len(sol_codes)
    processed   = 0
    output_files = []
    
    import threading
    progress_lock = threading.Lock()

    def _report(extra: int = 1):
        nonlocal processed
        with progress_lock:
            processed += extra
            if progress_cb and total_items > 0:
                pct = min(100, int(processed / total_items * 100))
                progress_cb(pct)

    open_docs = {}
    
    def get_src_doc(sol: str, type_id: str, fidx: int):
        key = f"{sol}_{type_id}_{fidx}"
        src = source_cache.get(key)
        if src is None:
            return None
        
        # Deduplicate open documents by source content identity
        src_key = src if isinstance(src, str) else id(src)
        
        if src_key not in open_docs:
            open_docs[src_key] = _open_pdf(src)
        return open_docs[src_key]

    try:
        if mode == "merge":
            # ── Single combined PDF ──────────────────────────
            out_doc = fitz.open()
            for sol in sol_codes:
                items = assembly_data[sol]
                for item in items:
                    src_doc = get_src_doc(sol, item['typeId'], item.get("fileIdx", 0))
                    if not src_doc:
                        continue
                    
                    page_idx = item["pageIndex"] - 1
                    if 0 <= page_idx < src_doc.page_count:
                        out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
                        
                _report(1) # Report per SOL

            if out_doc.page_count > 0:
                merged_name = generate_merged_filename(sol_codes, output_path=output_path)
                out_path = os.path.join(output_path, f"{merged_name}.pdf")
                out_doc.save(out_path, garbage=3, deflate=True)
                output_files.append(out_path)
            out_doc.close()

        else:
            # ── Split: one PDF per SOL ───────────────────────
            import concurrent.futures
            
            def process_sol(sol: str):
                items = assembly_data[sol]
                out_doc = fitz.open()
                saved_path = None
                
                for item in items:
                    # In threads, reading from shared src_doc with insert_pdf is thread-safe
                    src_doc = get_src_doc(sol, item['typeId'], item.get("fileIdx", 0))
                    if not src_doc:
                        continue
                        
                    page_idx = item["pageIndex"] - 1
                    if 0 <= page_idx < src_doc.page_count:
                        out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
                
                if out_doc.page_count > 0:
                    safe_sol = "".join(c for c in sol if c.isalnum() or c in "-_")
                    saved_path = os.path.join(output_path, f"{safe_sol}.pdf")
                    out_doc.save(saved_path, garbage=3, deflate=True)
                    
                out_doc.close()
                _report(1) # Report per SOL
                return saved_path

            # Pre-open all documents safely in the main thread to avoid race conditions during dict insertion
            for sol in sol_codes:
                for item in assembly_data[sol]:
                    get_src_doc(sol, item['typeId'], item.get("fileIdx", 0))

            # Run PDF generation in parallel threads
            with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                futures = [executor.submit(process_sol, sol) for sol in sol_codes]
                for future in concurrent.futures.as_completed(futures):
                    path = future.result()
                    if path:
                        output_files.append(path)

        for doc in open_docs.values():
            doc.close()

        if progress_cb:
            progress_cb(100)

        return {"success": True, "files": output_files}

    except Exception as exc:
        for doc in open_docs.values():
            doc.close()
        return {"success": False, "error": str(exc)}
