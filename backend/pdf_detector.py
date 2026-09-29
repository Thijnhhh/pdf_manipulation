"""
backend/pdf_detector.py
─────────────────────────────────────────────────────────────
Auto Detect engine — three reading methods + priority-chained dispatcher.

Public API:
  • run_auto_detect_all(sol_list, folder_inputs, is_drive_map, config, exceptions,
                        cancel_event, progress_cb)
      Top-level loop. For each SOL × type, resolves the method priority chain and
      detects matching pages. Returns assembly dict + errors list.

  • detect_pages_for_type(pdf_source, sol_code, priority_chain, ...)
      Priority-chained dispatcher. Tries each method in order, falls back if the
      method signals "no usable content found."

Internal methods:
  • detect_barcode(pdf_source, sol_code, cancel_event, progress_cb)
  • detect_digital(pdf_source, sol_code, cancel_event, progress_cb)
  • detect_scanned(pdf_source, sol_code, tesseract_cmd, ocr_lang,
                   cancel_event, progress_cb)

No-console-flash policy:
  • PDF rendering uses PyMuPDF (fitz) directly — no external Poppler subprocess.
  • Tesseract (pytesseract) is patched at import time via _suppress_subprocess_windows()
    to always set CREATE_NO_WINDOW + SW_HIDE, so no console window ever flashes.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import sys
import threading
import time

# ── Validate SO Flags ────────────────────────────────────────────────────────

def validate_so_flags(sol_entries: list | dict) -> dict[str, bool]:
    """
    Build and validate a { sol_str: bool } flag map from RFID HT sheet entries.

    Validates the '1 SO = 1 Flag' consistency rule: all SOL entries sharing the
    same 9-digit base SO (parse_so) MUST have an identical Flag value.
    If contradictory flags exist (both True and False for the same SO base),
    raises ValueError with a descriptive message.

    Args:
        sol_entries: list of dicts [{"sol": "123456789-1", "flag": True}, ...]
                     or flat dict {"123456789-1": True, ...}.

    Returns:
        { "123456789-1": True, "123456789-2": False, ... }
    """
    from backend.file_resolver import parse_so

    if not sol_entries:
        return {}

    flags: dict[str, bool] = {}
    if isinstance(sol_entries, dict):
        for sol, flg in sol_entries.items():
            if sol and isinstance(sol, str):
                flags[sol.strip()] = bool(flg)
    elif isinstance(sol_entries, list):
        for entry in sol_entries:
            if isinstance(entry, dict):
                sol = entry.get("sol", "").strip()
                if sol:
                    flags[sol] = bool(entry.get("flag", False))

    # 1 SO = 1 Flag validation rule
    so_flag_map: dict[str, tuple[bool, str]] = {}  # base_so -> (flag_val, sol_code)
    for sol, flg in flags.items():
        base_so = parse_so(sol) or sol
        if base_so in so_flag_map:
            prev_flag, prev_sol = so_flag_map[base_so]
            if prev_flag != flg:
                raise ValueError(
                    f"SO '{base_so}' has conflicting Flag values in the sheet "
                    f"({prev_sol}={prev_flag}, {sol}={flg}). "
                    f"Each SO must have a single consistent Flag."
                )
        else:
            so_flag_map[base_so] = (flg, sol)

    return flags



# ── Auto Detect RAM Cache ───────────────────────────────────────────────────
# Key: "{sol}_{type_id}"  Value: PDF bytes of matched pages (pre-extracted sub-doc)
AUTO_DETECT_RAM_CACHE: dict[str, bytes] = {}


def clear_ram_cache() -> None:
    """Release all RAM held by the Auto Detect results cache."""
    global AUTO_DETECT_RAM_CACHE
    AUTO_DETECT_RAM_CACHE.clear()

# ── Dependency helpers ────────────────────────────────────────────────────────

def find_tesseract() -> str | None:
    """Find Tesseract OCR executable. Returns path or None."""
    import shutil
    import sys
    if getattr(sys, 'frozen', False):
        p = os.path.join(sys._MEIPASS, "vendor", "tesseract", "tesseract.exe")
        if os.path.isfile(p):
            return p

    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            r"Programs\Tesseract-OCR\tesseract.exe"
        ),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    found = shutil.which("tesseract")
    return found



_PYZBAR_PATCHED = False

def _apply_pyzbar_dll_patch() -> None:
    """Add pyzbar directory to DLL search path on Windows (Python 3.8+). Ensure msvcr120.dll exists."""
    global _PYZBAR_PATCHED
    if _PYZBAR_PATCHED or sys.platform != 'win32':
        return
    try:
        import importlib.util
        import shutil
        spec = importlib.util.find_spec('pyzbar')
        if spec and spec.submodule_search_locations:
            pyzbar_dir = spec.submodule_search_locations[0]
            if os.path.exists(pyzbar_dir):
                target_msvcr = os.path.join(pyzbar_dir, 'msvcr120.dll')
                if not os.path.exists(target_msvcr):
                    candidates = [
                        r'C:\Program Files\Microsoft Office\root\Office16\msvcr120.dll',
                        r'C:\Program Files (x86)\Microsoft Office\root\Office16\msvcr120.dll',
                        r'C:\Windows\System32\msvcr120.dll',
                        r'C:\Windows\SysWOW64\msvcr120.dll',
                    ]
                    for candidate in candidates:
                        if os.path.exists(candidate):
                            try:
                                shutil.copy(candidate, target_msvcr)
                                break
                            except Exception:
                                pass
                os.add_dll_directory(pyzbar_dir)
        _PYZBAR_PATCHED = True
    except Exception:
        pass

def _suppress_subprocess_windows() -> None:
    """
    Patch pytesseract's subprocess launcher once at module load time so that
    *every* call to tesseract.exe runs with CREATE_NO_WINDOW + SW_HIDE.

    This is safe to call multiple times — it is idempotent after the first
    successful patch thanks to a module-level sentinel.
    """
    global _TESSERACT_PATCHED
    if _TESSERACT_PATCHED or sys.platform != 'win32':
        return
    try:
        import subprocess
        import pytesseract.pytesseract as _pt

        def _hidden_subprocess_args(include_stdout: bool = True) -> dict:
            """Drop-in replacement that always hides the console window."""
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            kwargs: dict = {
                'stdin':       subprocess.PIPE,
                'stderr':      subprocess.PIPE,
                'startupinfo': si,
                'env':         _pt.environ,
                # CREATE_NO_WINDOW prevents the black flash on Windows
                'creationflags': subprocess.CREATE_NO_WINDOW,
            }
            kwargs['stdout'] = subprocess.PIPE if include_stdout else subprocess.DEVNULL
            return kwargs

        _pt.subprocess_args = _hidden_subprocess_args
        _TESSERACT_PATCHED = True
    except Exception:
        pass  # non-critical — tesseract may not be installed


_TESSERACT_PATCHED: bool = False
_suppress_subprocess_windows()   # apply patch at import time


def _pdf_to_pil_images(pdf_source: str | bytes, dpi: int):
    """
    Convert a PDF (path or bytes) to a list of PIL Images.

    Uses PyMuPDF (fitz) instead of pdf2image / Poppler so that rendering runs
    entirely in-process — no external subprocess is spawned, which eliminates
    the console-window flashing that occurred with pdftoppm.exe.

    NOTE: Used by the "scanned" branch only (full upfront conversion).
    The "barcode" branch uses _page_image_stream() for streaming conversion.
    """
    import pymupdf as fitz
    from PIL import Image

    scale = dpi / 72.0  # fitz native unit is 72 DPI
    matrix = fitz.Matrix(scale, scale)

    if isinstance(pdf_source, (bytes, bytearray)):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        doc = fitz.open(pdf_source)

    images: list[Image.Image] = []
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)
    finally:
        doc.close()

    return images


def _fitz_page_to_pil(page, dpi: int):
    """
    Render a fitz page → PIL Image at the given DPI.
    """
    import pymupdf as fitz
    from PIL import Image
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _crop_top_fraction(img, fraction: float = 0.25):
    """Return the top `fraction` of a PIL Image."""
    w, h = img.size
    return img.crop((0, 0, w, int(h * fraction)))


def _crop_bottom_fraction(img, fraction: float = 0.25):
    """Return the bottom `fraction` of a PIL Image."""
    w, h = img.size
    return img.crop((0, int(h * (1 - fraction)), w, h))


def _pil_upscale(img, from_dpi: int, to_dpi: int):
    """Upscale a PIL Image proportionally from from_dpi to to_dpi."""
    from PIL import Image
    scale = to_dpi / from_dpi
    return img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)


_SOL_BARCODE_REGEX = None  # lazy-compiled


def _get_sol_regex():
    import re
    global _SOL_BARCODE_REGEX
    if _SOL_BARCODE_REGEX is None:
        _SOL_BARCODE_REGEX = re.compile(r"^\d{9}-\d{1,3}$")
    return _SOL_BARCODE_REGEX


_OCR_PAGE_REGEX = None  # lazy-compiled


def _get_ocr_regex():
    import re
    global _OCR_PAGE_REGEX
    if _OCR_PAGE_REGEX is None:
        _OCR_PAGE_REGEX = re.compile(r"Page (\d{1,3}) of (\d{1,3})")
    return _OCR_PAGE_REGEX


_2D_BARCODE_TYPES = {"QRCODE", "PDF417", "SQCODE", "DATAMATRIX"}


def _scan_1d_barcode(img, decode_fn) -> tuple[str, bool]:
    """
    Scan for a 1D SOL barcode using pyzbar decode_fn:
      1. Scans top 25% first (standard document orientation).
         If found -> returns (val, False)  (is_upsidedown = False)
      2. Fallback to bottom 25% (handles 180° upside-down scanned pages).
         If found -> returns (val, True)   (is_upsidedown = True)
      3. Strictly ignores 2D barcodes (QR codes, DataMatrix, etc.).
    Returns: (barcode_val: str, is_upsidedown: bool)
    """
    sol_regex = _get_sol_regex()

    def _extract_1d(crop_img):
        try:
            barcodes = decode_fn(crop_img)
        except Exception:
            return ""
        for bc in barcodes:
            if getattr(bc, "type", "") in _2D_BARCODE_TYPES:
                continue
            try:
                val = bc.data.decode("utf-8", errors="replace").strip()
                if sol_regex.match(val):
                    return val
            except Exception:
                pass
        return ""

    # 1. Try top 25% first (fast path for normal upright pages)
    val = _extract_1d(_crop_top_fraction(img, 0.25))
    if val:
        return val, False

    # 2. Fallback to bottom 25% (handles 180° upside-down scanned pages)
    val_bottom = _extract_1d(_crop_bottom_fraction(img, 0.25))
    if val_bottom:
        return val_bottom, True

    return "", False


# Backward compatibility alias
_scan_1d_barcode_top25 = _scan_1d_barcode


def _ocr_bottom25(img, pytesseract_mod, ocr_lang: str = "eng"):
    """
    OCR the bottom 25% of img to find "Page X of Y" pattern.
    Tries @100 DPI (native), then upscales to 150 DPI if first attempt fails.
    Returns (x, y) ints, or (None, None) as fallback → treated as Page 1 of 1.
    """
    ocr_regex = _get_ocr_regex()
    crop = _crop_bottom_fraction(img, 0.25)

    def _try(c):
        try:
            text = pytesseract_mod.image_to_string(c, lang=ocr_lang)
            for m in ocr_regex.finditer(text):
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return None, None

    x, y = _try(crop)
    if x is not None:
        return x, y
    # Upscale and retry
    crop2 = _pil_upscale(crop, 100, 150)
    x, y = _try(crop2)
    return x, y  # may still be (None, None) → caller uses fallback


def _ocr_top25_rotated180(img, pytesseract_mod, ocr_lang: str = "eng"):
    """
    OCR the top 25% of an upside-down page:
      1. Crops the top 25% (where 'Page X of Y' is physically located when inverted).
      2. Rotates the crop 180 degrees to upright orientation.
      3. Performs OCR to find 'Page X of Y'.
    """
    ocr_regex = _get_ocr_regex()
    crop = _crop_top_fraction(img, 0.25).rotate(180)

    def _try(c):
        try:
            text = pytesseract_mod.image_to_string(c, lang=ocr_lang)
            for m in ocr_regex.finditer(text):
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return None, None

    x, y = _try(crop)
    if x is not None:
        return x, y
    crop2 = _pil_upscale(crop, 100, 150)
    x, y = _try(crop2)
    return x, y


def _validate_page_xy(x, y, max_y: int = 3):
    """
    Normalize OCR "Page X of Y" output:
      • X is always forced to 1 (we only look at the first page of each SOL).
      • If Y is None or Y > max_y, treat as Y=1 (OCR misread total pages).
    Returns (1, y_validated).
    """
    y_out = 1 if (y is None or y > max_y) else y
    return 1, y_out

# ── Barcode type classification ─────────────────────────────────────────────
# pyzbar returns a symbolic type name for each decoded barcode.
# 1D linear barcodes signal the START of a SOL page range (first page).


def _sol_line_number(sol: str) -> "int | None":
    """
    Parse the line-number suffix from a SOL string (e.g. '143590905-3' → 3).
    Returns None when the string does not match the expected SOL pattern.
    """
    import re
    m = re.match(r"^\d{9}-(\d{1,3})$", sol.strip())
    return int(m.group(1)) if m else None


def _detect_barcode_soview(
    pdf_source,
    sol_list: list,
    cancel_event=None,
    use_ocr: bool = False,
    sol_flags: dict = None,
) -> tuple:
    """
    SOview-aware barcode page-range detector — unified single-pass implementation.

    Supports three execution modes, dispatched per-SOL based on `sol_flags`:

    Mode 1 — Fast Mode  (sol_flags[sol] == False, use_ocr == False):
      • Finds 1D barcode in top 25% matching target SOL → records as single page.
      • No OCR, no range accumulation.

    Mode 2 — OCR Mode   (sol_flags[sol] == False, use_ocr == True):
      • Finds 1D barcode in top 25% → OCR bottom 25% for "Page X of Y".
      • Backward scan from page_num+(Y-1) to page_num+1 to confirm range.
      • Backward scan logic is preserved exactly as-is for correctness.
      • Falls back to single page if OCR/backward scan finds no confirmation.

    Mode 3 — Barcode Range Scan  (sol_flags[sol] == True):
      • Opens a range when target SOL barcode is found.
      • Continuation pages (no barcode) accumulate into the open range.
      • Range closes when any subsequent valid 1D barcode is found (close at page-1)
        or at EOF (close at last page of document).
      • use_ocr is ignored for Flag=True SOLs.

    Single-pass design: all SOLs across all modes are resolved in one sequential
    scan of the document. Mixed-flag files (some SOLs with Flag=True, others False)
    are handled correctly within the same loop iteration.

    Args:
        pdf_source:   File path (str) or PDF bytes.
        sol_list:     List of target SOL strings to collect pages for.
        cancel_event: threading.Event; if set, scanning stops immediately.
        use_ocr:      If True, apply OCR Mode for Flag=False SOLs (default False).
        sol_flags:    { sol_str: bool } mapping. If a SOL is absent or the dict
                      is None/empty, defaults to False (Fast Mode / OCR Mode).
                      Fully backward-compatible — PFL passes nothing here.

    Returns:
        (results, status) where:
          results — {sol: [page_numbers_1based]} for every sol in sol_list.
          status  — "barcode" if at least one SOL barcode was found,
                    "none" otherwise (signals the dispatcher to fall through).
    """
    import threading
    import pymupdf as fitz

    if cancel_event is None:
        cancel_event = threading.Event()

    if sol_flags is None:
        sol_flags = {}

    _apply_pyzbar_dll_patch()
    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
    except ImportError:
        return ({sol: [] for sol in sol_list}, "none")

    # Clean target list, preserve order
    target_sols = list(dict.fromkeys(
        s.strip() for s in sol_list if s and isinstance(s, str) and s.strip()
    ))
    target_sol_set = set(target_sols)

    # Miss-Exit pre-computation
    so_prefixes = set()
    import re as _re
    for s in target_sols:
        m = _re.match(r"^(\d{9})-\d{1,3}$", s)
        if m:
            so_prefixes.add(m.group(1))
    is_single_so = len(so_prefixes) == 1
    single_so_prefix = next(iter(so_prefixes)) if is_single_so else None

    target_line_numbers = [n for n in (_sol_line_number(s) for s in target_sols) if n is not None]
    max_target_line = max(target_line_numbers) if target_line_numbers else None

    closed_ranges: dict = {}
    had_any_barcode = False

    # ── Open PDF document ─────────────────────────────────────────────────────
    if isinstance(pdf_source, (bytes, bytearray)):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        doc = fitz.open(pdf_source)

    total_pages = len(doc)
    _DPI = 100

    # ── Barcode Range Scan state (Flag = True SOLs) ───────────────────────────
    # open_sol  : the currently active SOL that has opened a range
    # open_page : the 1-based page number where the current range started
    open_sol: str | None = None
    open_page: int = 1

    # ── OCR Mode setup (Flag = False SOLs with use_ocr=True) ─────────────────
    pytesseract_mod = None
    if use_ocr and any(not sol_flags.get(s, False) for s in target_sols):
        if find_tesseract() is None:
            raise RuntimeError(
                "Tesseract OCR executable not found. "
                "Please ensure Tesseract is installed."
            )
        import pytesseract as _pytesseract
        _pytesseract.pytesseract.tesseract_cmd = find_tesseract()
        pytesseract_mod = _pytesseract

    first_sol_scanned = False

    try:
        # ════════════════════════════════════════════════════════════════════════
        # UNIFIED SINGLE-PASS LOOP
        # ════════════════════════════════════════════════════════════════════════
        i = 0
        while i < total_pages:
            if cancel_event.is_set():
                break

            # ── Complete-Match Exit ───────────────────────────────────────────
            # Only exit early if no range is currently open — an open range must
            # be closed by the next barcode or by EOF before we can stop.
            if open_sol is None and target_sol_set and all(s in closed_ranges for s in target_sol_set):
                break

            page_num = i + 1  # 1-based

            img = _fitz_page_to_pil(doc[i], _DPI)
            barcode_val, is_upsidedown = _scan_1d_barcode(img, pyzbar_decode)

            # ── No barcode on this page ───────────────────────────────────────
            if not barcode_val:
                # If a Flag=True SOL has opened a range, this page belongs to it.
                # Otherwise just skip.
                i += 1
                continue

            had_any_barcode = True

            # ── Miss-Exit (guarded for multi-SO files) ────────────────────────
            # Activate only when scanning a single SO AND the detected barcode
            # actually belongs to that SO (same 9-digit prefix).  Without the
            # prefix guard, a barcode from a different SO in the same file would
            # trigger a false early exit.
            if is_single_so and not first_sol_scanned and max_target_line is not None:
                if single_so_prefix and barcode_val.startswith(single_so_prefix):
                    sl = _sol_line_number(barcode_val)
                    if sl is not None and sl > max_target_line:
                        # Close any open range at this page - 1 before exiting
                        if open_sol is not None:
                            end = max(open_page, page_num - 1)
                            closed_ranges[open_sol] = list(range(open_page, end + 1))
                            open_sol = None
                        break

            first_sol_scanned = True

            # ── Found a valid barcode — close any currently open range ────────
            if open_sol is not None:
                # Close the active Flag=True SOL range at (current page - 1)
                end = max(open_page, page_num - 1)
                closed_ranges[open_sol] = list(range(open_page, end + 1))
                open_sol = None

            # ── Evaluate the newly detected barcode ───────────────────────────
            if barcode_val not in target_sol_set:
                # Not a target SOL — used only to close the previous range above.
                i += 1
                continue

            if barcode_val in closed_ranges:
                # Already recorded for this SOL (e.g. repeated barcode in file).
                i += 1
                continue

            # Decide mode for this SOL based on its flag
            sol_is_range_scan = sol_flags.get(barcode_val, False)

            if sol_is_range_scan:
                # ── BARCODE RANGE SCAN MODE (Flag = True) ────────────────────
                # Open a new range starting at this page.
                open_sol = barcode_val
                open_page = page_num
                i += 1
                continue

            else:
                # ── FAST MODE or OCR MODE (Flag = False) ─────────────────────
                if not use_ocr or pytesseract_mod is None:
                    # Fast Mode: record this single page immediately.
                    closed_ranges[barcode_val] = [page_num]
                    i += 1
                    continue

                else:
                    # OCR Mode: read "Page X of Y" from bottom 25% (or top 25% rotated 180° if inverted)
                    # Backward scan logic is preserved exactly from the original implementation.
                    pages_to_advance = 1

                    if is_upsidedown:
                        # Xoay 180 độ + OCR đọc Top 25%
                        ocr_x, ocr_y = _ocr_top25_rotated180(img, pytesseract_mod)
                    else:
                        # Trang xuôi chuẩn: OCR đọc Bottom 25%
                        ocr_x, ocr_y = _ocr_bottom25(img, pytesseract_mod)

                    _, y_val = _validate_page_xy(ocr_x, ocr_y, max_y=3)

                    if y_val == 1:
                        closed_ranges[barcode_val] = [page_num]
                        pages_to_advance = 1

                    else:
                        # Multi-page SOL — backward scan from page_num+(Y-1) down to page_num+1.
                        back_found = False

                        for offset in range(y_val - 1, 0, -1):
                            back_page_num = page_num + offset   # 1-based
                            back_idx = back_page_num - 1        # 0-based
                            expected_x = offset + 1

                            if back_idx >= total_pages:
                                continue  # Beyond EOF

                            back_img = _fitz_page_to_pil(doc[back_idx], _DPI)

                            # Pre-check: if page has any 1D SOL barcode,
                            # this page is the start of another SOL → skip OCR.
                            pre_bc, _ = _scan_1d_barcode(back_img, pyzbar_decode)
                            if pre_bc:
                                continue  # New SOL starts here → can't be a sub-page

                            # OCR bottom 25% to verify "Page expected_x of Y" (mặc định đọc bottom 25% không rotate thêm)
                            bx, by = _ocr_bottom25(back_img, pytesseract_mod)

                            if bx is not None and bx == expected_x:
                                sol_pages = list(range(page_num, page_num + expected_x))
                                closed_ranges[barcode_val] = sol_pages
                                pages_to_advance = expected_x
                                back_found = True
                                break

                        if not back_found:
                            # Backward scan exhausted without confirmation → fallback
                            closed_ranges[barcode_val] = [page_num]
                            pages_to_advance = 1

                    i += pages_to_advance
                    continue

            i += 1

        # ── EOF: close any still-open Barcode Range Scan range ────────────────
        if open_sol is not None:
            closed_ranges[open_sol] = list(range(open_page, total_pages + 1))
            open_sol = None

    finally:
        doc.close()

    results = {sol: closed_ranges.get(sol, []) for sol in target_sols}
    status = "barcode" if had_any_barcode else "none"
    return results, status



_DYNAMIC_SOL_CLUSTER_PATTERN = re.compile(
    r'(?<!\d)(\d{9})[ \t]*-[ \t]*(\d{1,3})(?!\d)'
    r'((?:[ \t]*[,;\-][ \t]*\d{1,3}(?![a-zA-Z\d]))*)'
)


def _extract_sols_from_text(text: str) -> set:
    """
    Extract all SOLs present in text, supporting:
      • Single SOL: 146842454-1
      • Range: 146842454-1-4 or 146842454-1 - 4 (exactly 1 '-' after first line -> lines 1..4)
      • Discrete list: 146842455-1,5,9 or 146842454-1; 2; 3; 4 or 146842454-1 - 4-5
      • Auto-excludes suffixes with attached letters: e.g. 146842454-1-61mm -> extracts 146842454-1
    """
    found_sols = set()
    for match in _DYNAMIC_SOL_CLUSTER_PATTERN.finditer(text):
        so = match.group(1)
        first_line = int(match.group(2))
        tail = match.group(3) or ""

        if not tail:
            found_sols.add(f"{so}-{first_line}")
            continue

        items = re.findall(r"([,;\-])[ \t]*(\d{1,3})(?![a-zA-Z\d])", tail)
        if not items:
            found_sols.add(f"{so}-{first_line}")
            continue

        separators = [sep for sep, _ in items]
        lines = [int(num) for _, num in items]

        # Range: exactly 1 separator after first line and it is '-'
        if len(items) == 1 and separators[0] == "-":
            end_line = lines[0]
            if first_line < end_line:
                for line_num in range(first_line, end_line + 1):
                    found_sols.add(f"{so}-{line_num}")
            else:
                found_sols.add(f"{so}-{first_line}")
                found_sols.add(f"{so}-{end_line}")
        else:
            # Discrete list: ',' or ';' or >= 2 '-'
            found_sols.add(f"{so}-{first_line}")
            for line_num in lines:
                found_sols.add(f"{so}-{line_num}")

    return found_sols


def _detect_pages_all_sols(
    pdf_source,
    sol_list,
    priority_chain,
    tesseract_cmd,
    ocr_lang="eng",
    cancel_event=None,
    progress_cb=None,
    ocr_flag: bool = False,
    sol_flags: dict = None,
):
    if cancel_event is None:
        cancel_event = threading.Event()

    clean_sol_list = list(dict.fromkeys(s.strip() for s in sol_list if s and isinstance(s, str) and s.strip()))
    results = {sol: [] for sol in clean_sol_list}

    sol_patterns = {sol: re.compile(rf"(?<!\d){re.escape(sol)}(?!\d)") for sol in clean_sol_list}
    
    # Lazy image conversion — shared between barcode and scanned branches
    cached_images = None

    def get_images():
        nonlocal cached_images
        if cached_images is None:
            cached_images = _pdf_to_pil_images(pdf_source, dpi=150)
        return cached_images

    # Define process_scanned_page once so both scanned branch and potential re-use can call it
    def process_scanned_page(i, img):
        if cancel_event and cancel_event.is_set():
            return (i, [])
        import pytesseract
        text = pytesseract.image_to_string(img, lang=ocr_lang)
        page_sols = _extract_sols_from_text(text)
        found = [sol for sol in clean_sol_list if (sol in page_sols or sol_patterns[sol].search(text))]
        return (i, found)

    for method in priority_chain:
        if cancel_event.is_set():
            break

        if method == "barcode":
            # ── SOview-aware streaming barcode detector ────────────────
            # Delegates to _detect_barcode_soview() which implements:
            #   • Page-by-page streaming (no upfront full conversion)
            #   • 1D barcode → open SOL range / 2D barcode → close SOL range
            #   • Fallback rule: new 1D while prior range open → force single page
            #   • Case A miss-exit and complete-match exit
            #   • EOF → unclosed ranges become single-page results
            if progress_cb:
                progress_cb("barcode")

            page_map, bc_status = _detect_barcode_soview(
                pdf_source   = pdf_source,
                sol_list     = clean_sol_list,
                cancel_event = cancel_event,
                use_ocr      = ocr_flag,
                sol_flags    = sol_flags or {},
            )

            if bc_status == "barcode":
                # Merge new pages into results (union, preserve order)
                for sol, pages in page_map.items():
                    existing = set(results.get(sol, []))
                    for p in pages:
                        if p not in existing:
                            results[sol].append(p)
                            existing.add(p)
                    results[sol].sort()
                return results, "barcode"

        elif method == "digital":
            import pymupdf as fitz
            if isinstance(pdf_source, (bytes, bytearray)):
                _doc = fitz.open(stream=pdf_source, filetype="pdf")
            else:
                _doc = fitz.open(pdf_source)
            total = len(_doc)
            
            if progress_cb:
                progress_cb("digital")
                
            had_any_text = False  # initialize before loop to prevent UnboundLocalError
            for page_num in range(1, total + 1):
                if cancel_event.is_set():
                    break
                    
                page = _doc[page_num - 1]
                text = page.get_text("text")
                
                if text.strip():
                    had_any_text = True
                    
                page_sols = _extract_sols_from_text(text)
                for sol in clean_sol_list:
                    if page_num not in results[sol] and (sol in page_sols or sol_patterns[sol].search(text)):
                        results[sol].append(page_num)
                        
            _doc.close()

            if had_any_text:
                return results, "digital"

        elif method == "scanned":
            import pytesseract
            tc = tesseract_cmd or find_tesseract()
            if not tc:
                raise RuntimeError("Tesseract not found.")
            pytesseract.pytesseract.tesseract_cmd = tc

            images = get_images()  # reuses cache if already converted by barcode branch
            total = len(images)
            
            if progress_cb:
                progress_cb("scanned")

            with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                futures = {executor.submit(process_scanned_page, i, img): i for i, img in enumerate(images, start=1)}
                for future in concurrent.futures.as_completed(futures):
                    if cancel_event.is_set(): break
                    i, found_sols = future.result()
                    for sol in found_sols:
                        if i not in results[sol]:
                            results[sol].append(i)
                            
            for sol in clean_sol_list:
                results[sol].sort()

            return results, "scanned"

    return results, "none"


# ── Precision Performance Tracer ─────────────────────────────────────────────

class _AutoDetectTracer:
    """
    Precision performance tracer for run_auto_detect_all and run_auto_detect_trim.
    Records duration of major phases and individual file operations.
    Prints real-time log lines and an ASCII summary breakdown table upon completion.
    """
    def __init__(self, enabled: bool = True, title: str = "Auto Detect Performance Breakdown", prefix: str = "DETECTOR"):
        self.enabled = enabled
        self.title = title
        self.prefix = prefix
        self.t_start = time.perf_counter()
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def record(self, phase: str, target: str, t0: float, details: str = "") -> float:
        duration_ms = (time.perf_counter() - t0) * 1000
        if self.enabled:
            with self._lock:
                self.records.append({
                    "phase": phase,
                    "target": target,
                    "ms": duration_ms,
                    "details": details,
                })
                detail_str = f" ({details})" if details else ""
                print(f"[{self.prefix}] >> {phase:<22} | {target:<32} | {duration_ms:>8.1f}ms{detail_str}", flush=True)
        return duration_ms

    def print_summary(self) -> None:
        if not self.enabled or not self.records:
            return

        total_elapsed_ms = (time.perf_counter() - self.t_start) * 1000
        sum_recorded_ms = sum(r["ms"] for r in self.records) or 1e-6

        line_sep = "=" * 98
        sub_sep  = "-" * 98

        lines = [
            "",
            line_sep,
            f" [{self.prefix} TRACE SUMMARY] {self.title} (Total: {total_elapsed_ms:.1f}ms)",
            sub_sep,
            f" {'Phase':<24} | {'Target / Item':<36} | {'Duration':>10} | {'Share':>7} | {'Details'}",
            sub_sep,
        ]

        for r in self.records:
            share_pct = (r["ms"] / total_elapsed_ms) * 100
            phase_str = (r["phase"][:22] + "..") if len(r["phase"]) > 24 else r["phase"]
            target_str = (r["target"][:34] + "..") if len(r["target"]) > 36 else r["target"]
            lines.append(
                f" {phase_str:<24} | {target_str:<36} | {r['ms']:>8.1f}ms | {share_pct:>6.1f}% | {r['details']}"
            )

        lines.append(sub_sep)
        unaccounted_ms = max(0.0, total_elapsed_ms - sum_recorded_ms)
        if unaccounted_ms > 5.0:
            unacc_pct = (unaccounted_ms / total_elapsed_ms) * 100
            lines.append(
                f" {'Other / Overhead':<24} | {'Python dispatch, locks & memory':<36} | {unaccounted_ms:>8.1f}ms | {unacc_pct:>6.1f}% |"
            )
            lines.append(sub_sep)

        lines.append(
            f" {'TOTAL':<24} | {f'{len(self.records)} operations recorded':<36} | {total_elapsed_ms:>8.1f}ms | 100.0% |"
        )
        lines.append(line_sep)
        lines.append("")
        print("\n".join(lines), flush=True)


AutoDetectTracer = _AutoDetectTracer


# ── Helper: Boundary Match PDF Finder for RFID SB Layout ─────────────────────

def _find_pdfs_by_sol_boundary(folder_path: str, sol_list: list, is_drive: bool) -> dict:
    """
    Find PDF files by SOL with boundary check.
    RFID SB filename pattern: '{SOL}_{...rest}.pdf' — SOL is terminated by '_'.
    Prevents false match: '144187563-1' matching '144187563-10_...pdf'.
    Normalizes excessive spaces in filenames before matching.

    Returns: { sol: [{found, path, name, file_id}] }
    Keys are full SOL strings (e.g. '144187563-1'), NOT 9-digit SO.
    """
    import os, threading, queue as _queue

    clean_sols = list(dict.fromkeys(s.strip() for s in sol_list if s and s.strip()))
    results = {sol: [] for sol in clean_sols}

    if is_drive:
        from backend.file_resolver import find_all_pdfs_batch
        return find_all_pdfs_batch(folder_path, clean_sols, is_drive=True)

    if not os.path.isdir(folder_path):
        return results

    results_lock = threading.Lock()
    lower_sol_map = {sol.lower(): sol for sol in clean_sols}

    dirs_q: _queue.Queue = _queue.Queue()
    dirs_q.put(folder_path)
    active_count = 0
    count_lock = threading.Lock()
    done_evt = threading.Event()

    def worker():
        nonlocal active_count
        while not done_evt.is_set():
            try:
                curr_dir = dirs_q.get(timeout=0.04)
            except _queue.Empty:
                with count_lock:
                    if active_count == 0 and dirs_q.empty():
                        done_evt.set()
                return
            with count_lock:
                active_count += 1
            try:
                subdirs = []
                with os.scandir(curr_dir) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append(entry.path)
                            elif (entry.is_file(follow_symlinks=False)
                                  and entry.name.lower().endswith(".pdf")):
                                fname = entry.name
                                # Chuẩn hóa khoảng trắng thừa
                                fname_norm = " ".join(fname.lower().split())
                                for sol_l, orig_sol in lower_sol_map.items():
                                    idx = fname_norm.find(sol_l)
                                    if idx == -1:
                                        continue
                                    end = idx + len(sol_l)
                                    # Chặn biên: ký tự tiếp theo phải là '_', '.', khoảng trắng hoặc hết chuỗi
                                    if end < len(fname_norm) and fname_norm[end] not in ('_', '.', ' '):
                                        continue
                                    fpath = entry.path
                                    item = {"found": True, "file_id": fpath,
                                            "path": fpath, "name": fname}
                                    with results_lock:
                                        results[orig_sol].append(item)
                        except OSError:
                            pass
                for sd in subdirs:
                    dirs_q.put(sd)
            except OSError:
                pass
            finally:
                dirs_q.task_done()
                with count_lock:
                    active_count -= 1
                    if active_count == 0 and dirs_q.empty():
                        done_evt.set()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for sol in results:
        results[sol].sort(key=lambda x: x.get("name", "").lower())
    return results


# ── Top-level runner ──────────────────────────────────────────────────────────

def run_auto_detect_all(
    sol_list,
    folder_inputs,
    is_drive_map,
    config,
    exceptions,
    cancel_event,
    progress_cb=None,
    ocr_flag: bool = False,
    sol_flags: dict = None,
    job_map: dict = None,
    trace_enabled: bool = False,
    line: str = "PFL",
):
    """
    Main entry point called from bridge.py in a background thread.
    """
    from backend.file_resolver import parse_so, find_all_pdfs_batch
    from backend.pdf_deduplicator import detect_unique_pages_dedup
    from backend.pdf_processor import is_network_path, read_pdf_bytes_smart
    import pymupdf as fitz

    tracer_title = (
        f"Auto Detect Performance Breakdown (RFID SB UQ - {len(sol_list)} SOLs)"
        if line == "RFID SB"
        else "Auto Detect Performance Breakdown"
    )
    tracer = _AutoDetectTracer(enabled=trace_enabled, title=tracer_title, prefix="DETECTOR")

    if sol_flags is None:
        sol_flags = {}
    if job_map is None:
        job_map = {}

    tesseract_cmd = find_tesseract()

    # Clean and deduplicate sol_list while preserving order
    clean_sol_list = list(dict.fromkeys(
        s.strip() for s in sol_list if s and isinstance(s, str) and s.strip()
    ))

    assembly = {}
    errors = []

    pdf_type_ids = list(folder_inputs.keys())   # e.g. ["soview", "layout"]

    # ── Step 1: Build SO -> SOLs map, initialise assembly ────────────────────
    so_to_sols: dict = {}  # { "144187563": ["144187563-1", "144187563-2"] }

    for sol in clean_sol_list:
        so = parse_so(sol)
        if not so:
            continue  # Skip invalid SOL patterns
        assembly[sol] = {}
        if sol not in so_to_sols.setdefault(so, []):
            so_to_sols[so].append(sol)

    # ── Step 2: Pre-fetch all files using Batch Search ────────────────────────
    unique_sos = list(so_to_sols.keys())
    batched_files: dict = {}

    for type_id in pdf_type_ids:
        if cancel_event.is_set():
            break

        if progress_cb:
            progress_cb({
                "sol": "Batch Search",
                "typeId": type_id,
                "method": "Searching...",
                "fileName": "Finding PDFs...",
                "currentFile": 0,
                "totalFiles": 0,
            })

        t_search = time.perf_counter()
        try:
            if type_id == "layout" and job_map:
                # RFID HT: Layout files in network folder are searched by Job number (8-digit prefix)
                unique_jobs = list(dict.fromkeys(str(j).strip() for j in job_map.values() if str(j).strip()))
                batched_files[type_id] = find_all_pdfs_batch(
                    folder_inputs[type_id],
                    unique_jobs,
                    is_drive_map.get(type_id, False),
                    match_prefix_8=True
                )
                search_desc = f"{len(unique_jobs)} unique Jobs"
            elif type_id == "layout" and line == "RFID SB":
                # RFID SB: layout files searched by full SOL with boundary check
                batched_files[type_id] = _find_pdfs_by_sol_boundary(
                    folder_inputs[type_id],
                    clean_sol_list,
                    is_drive_map.get(type_id, False)
                )
                search_desc = f"{len(clean_sol_list)} SOLs (boundary match)"
            else:
                batched_files[type_id] = find_all_pdfs_batch(
                    folder_inputs[type_id],
                    unique_sos,
                    is_drive_map.get(type_id, False)
                )
                search_desc = f"{len(unique_sos)} unique SOs"

            found_cnt = sum(len([f for f in flist if f.get("found")]) for flist in batched_files[type_id].values())
            loc_label = "Drive" if is_drive_map.get(type_id, False) else "UNC/Local"
            target_label = "layout (RFID SB Boundary)" if (type_id == "layout" and line == "RFID SB") else f"{type_id} ({loc_label})"
            tracer.record(
                phase="1. Batch Search",
                target=target_label,
                t0=t_search,
                details=f"{search_desc}, {found_cnt} files found"
            )
        except Exception as exc:
            for sol in clean_sol_list:
                errors.append({"sol": sol, "typeId": type_id, "error": str(exc)})
            batched_files[type_id] = {}
            tracer.record(
                phase="1. Batch Search",
                target=f"{type_id} (ERROR)",
                t0=t_search,
                details=str(exc)
            )

    # ── Step 3: Initialise assembly type-slots ────────────────────────────────
    for so, sols_for_so in so_to_sols.items():
        for type_id in pdf_type_ids:
            for sol in sols_for_so:
                assembly[sol][type_id] = []

    # ── Step 4: Detection Loop per PDF Type ───────────────────────────────────
    for type_id in pdf_type_ids:
        if cancel_event.is_set():
            break

        # ── Case A: RFID HT Layout Mode (job_map provided) ────────────────────
        # 1-to-1 SOL-to-Job mapping; extracts ALL pages directly from each job's layout file.
        if type_id == "layout" and job_map:
            layout_files_all = batched_files.get("layout", {})
            for sol in clean_sol_list:
                job_code = str(job_map.get(sol, "")).strip()
                if not job_code:
                    continue

                found_for_sol = [f for f in layout_files_all.get(job_code, []) if f.get("found")]
                for file_idx, file_result in enumerate(found_for_sol):
                    t_layout_file = time.perf_counter()
                    file_name  = file_result.get("name", "")
                    pdf_source = file_result.get("bytes") or file_result.get("path")
                    if not pdf_source:
                        continue

                    try:
                        if isinstance(pdf_source, (bytes, bytearray)):
                            doc = fitz.open(stream=pdf_source, filetype="pdf")
                        else:
                            doc = fitz.open(pdf_source)
                        total_pages = doc.page_count
                        doc.close()
                    except Exception as exc:
                        errors.append({"sol": sol, "typeId": "layout", "error": str(exc)})
                        continue

                    file_path = file_result.get("path") or ""
                    file_id   = file_result.get("file_id") or file_path or file_name
                    seen = set((e["fileIdx"], e["pageIndex"]) for e in assembly[sol]["layout"])
                    for p in range(1, total_pages + 1):
                        if (file_idx, p) not in seen:
                            assembly[sol]["layout"].append({
                                "pageIndex": p,
                                "fileIdx":   file_idx,
                                "fileName":  file_name,
                                "filePath":  file_path,
                                "fileId":    file_id,
                                "method":    "layout_all_pages",
                            })
                            seen.add((file_idx, p))
                    assembly[sol]["layout"].sort(key=lambda e: (e["fileIdx"], e["pageIndex"]))

                    tracer.record(
                        phase="2. Extract Layout (RFID)",
                        target=file_name,
                        t0=t_layout_file,
                        details=f"Job {job_code}, {total_pages} pages, direct"
                    )
            continue

        # ── Case C [NEW]: RFID SB Layout — Cluster-Jump Dedup per SOL ────────
        elif type_id == "layout" and line == "RFID SB":
            layout_files_all = batched_files.get("layout", {})

            # ── Stage 2: Parallel Small-First Preload + In-Flight Dedup Pipeline ──
            # Thu thập tất cả các file mạng duy nhất trên toàn bộ SOL cần preload
            unique_net_files: dict[str, dict] = {}
            for sol in clean_sol_list:
                for f in layout_files_all.get(sol, []):
                    fpath = f.get("path")
                    if f.get("found") and fpath and not f.get("bytes") and is_network_path(fpath):
                        if fpath not in unique_net_files:
                            unique_net_files[fpath] = f

            if unique_net_files and not cancel_event.is_set():
                t_pipeline_start = time.perf_counter()

                # Sắp xếp ưu tiên file nhỏ trước (Small-Files First) để thoát nhanh các file nhỏ
                sorted_net_files = sorted(
                    unique_net_files.values(),
                    key=lambda fe: os.path.getsize(fe.get("path", "")) if os.path.isfile(fe.get("path", "")) else 0
                )
                max_workers = min(2, len(sorted_net_files))

                def _pipeline_worker(f_entry: dict):
                    if cancel_event.is_set():
                        return
                    f_path = f_entry.get("path", "")
                    f_name = f_entry.get("name", "")
                    t0 = time.perf_counter()
                    try:
                        b = read_pdf_bytes_smart(f_path)
                        f_entry["bytes"] = b
                        bytes_len = len(b)
                        dt_load = max(time.perf_counter() - t0, 1e-6)
                        throughput_mbs = (bytes_len / (1024 * 1024)) / dt_load

                        # ── PIPELINE: Dedup ngay lập tức trong RAM khi bytes vừa về ──
                        t_dedup = time.perf_counter()
                        unique_pages = detect_unique_pages_dedup(b)
                        f_entry["unique_pages"] = unique_pages
                        dt_dedup_ms = (time.perf_counter() - t_dedup) * 1000

                        if tracer.enabled:
                            print(
                                f"[{tracer.prefix}] >> 2. Layout Pipeline   | {f_name[:32]:<32} | "
                                f"{dt_load*1000:>8.1f}ms load ({throughput_mbs:.1f} MB/s) + "
                                f"{dt_dedup_ms:>6.1f}ms dedup ({len(unique_pages)} pgs)",
                                flush=True
                            )
                    except Exception as _load_exc:
                        print(f"[pipeline RFID SB layout] {f_name}: {_load_exc}")

                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(_pipeline_worker, fe) for fe in sorted_net_files]
                    concurrent.futures.wait(futures)

                dt_pipeline_ms = (time.perf_counter() - t_pipeline_start) * 1000
                total_layout_kb = sum(len(fe.get("bytes", b"")) for fe in sorted_net_files) // 1024
                throughput_total = (total_layout_kb / 1024) / max((dt_pipeline_ms / 1000), 1e-6)

                tracer.record(
                    phase="2. Layout Pipeline",
                    target=f"{len(sorted_net_files)} files ({total_layout_kb // 1024} MB)",
                    t0=t_pipeline_start,
                    details=f"Parallel Small-First (4 workers) @ {throughput_total:.1f} MB/s [Load + Dedup]"
                )

            # ── Map kết quả vào assembly ──────────────────────────────────────
            for sol in clean_sol_list:
                if cancel_event.is_set():
                    break

                found_for_sol = [f for f in layout_files_all.get(sol, []) if f.get("found")]

                for file_idx, file_result in enumerate(found_for_sol):
                    file_name = file_result.get("name", "")
                    _src_path = file_result.get("path", "")

                    unique_pages = file_result.get("unique_pages")
                    if unique_pages is None:
                        # Fallback nếu file local hoặc pipeline chưa kịp chạy
                        pdf_source = file_result.get("bytes") or _src_path
                        if pdf_source:
                            try:
                                unique_pages = detect_unique_pages_dedup(pdf_source)
                                file_result["unique_pages"] = unique_pages
                            except Exception as exc:
                                errors.append({"sol": sol, "typeId": "layout", "error": str(exc)})
                                continue
                        else:
                            continue

                    file_path = file_result.get("path") or ""
                    file_id = file_result.get("file_id") or file_path or file_name
                    seen = set((e["fileIdx"], e["pageIndex"]) for e in assembly[sol]["layout"])

                    for p in unique_pages:
                        if (file_idx, p) not in seen:
                            assembly[sol]["layout"].append({
                                "pageIndex": p,
                                "fileIdx": file_idx,
                                "fileName": file_name,
                                "filePath": file_path,
                                "fileId": file_id,
                                "method": "layout_dedup",
                            })
                            seen.add((file_idx, p))
                    assembly[sol]["layout"].sort(key=lambda e: (e["fileIdx"], e["pageIndex"]))

                    # 1B: Early Eviction nếu 0 trang nào được giữ lại
                    if len(unique_pages) == 0 and "bytes" in file_result:
                        del file_result["bytes"]

            continue  # Thoát type_id, bỏ qua block sort priority không liên quan bên dưới

        # ── Case B: Standard Priority-Chained Detection (SOview, PFL Layout) ──
        # Priority chain is resolved directly from frontend config (config[type_id]).
        raw_chain = config.get(type_id)
        if isinstance(raw_chain, dict):
            chain = raw_chain.get("priority") or raw_chain.get("chain") or ["digital", "scanned"]
        elif isinstance(raw_chain, list):
            chain = raw_chain
        else:
            chain = ["digital", "scanned"]

        # Build unique file table: each physical file is processed once,
        # aggregating all SOLs that map to it across one or multiple SOs.
        unique_files: dict = {}
        for so, sols_for_so in so_to_sols.items():
            file_list = batched_files.get(type_id, {}).get(so, [])
            found_files = [f for f in file_list if f.get("found")]

            for local_idx, file_result in enumerate(found_files):
                name = file_result.get("name", "")
                path = file_result.get("path", "") or ""
                file_key = (name, path)

                if file_key not in unique_files:
                    unique_files[file_key] = {
                        "file_result":     file_result,
                        "sols":            [],
                        "global_idx":      len(unique_files),
                        "so_to_local_idx": {},
                    }
                entry = unique_files[file_key]
                for sol in sols_for_so:
                    if sol not in entry["sols"]:
                        entry["sols"].append(sol)
                entry["so_to_local_idx"][so] = local_idx

        total_unique = len(unique_files)
        completed_unique = 0

        # ── 1A: Parallel RAM Preload for Network Files in Case B (SOview) ───
        net_soviews = [
            uf["file_result"] for uf in unique_files.values()
            if not uf["file_result"].get("bytes") and uf["file_result"].get("path") and is_network_path(uf["file_result"].get("path"))
        ]
        if net_soviews and not cancel_event.is_set():
            t_preload_so = time.perf_counter()
            sorted_soviews = sorted(
                net_soviews,
                key=lambda fe: os.path.getsize(fe.get("path", "")) if os.path.isfile(fe.get("path", "")) else 0
            )
            so_workers = min(4, len(sorted_soviews))

            def _preload_so_worker(fe: dict):
                if cancel_event.is_set():
                    return
                fpath = fe.get("path", "")
                fname = fe.get("name", "")
                t0 = time.perf_counter()
                try:
                    b = read_pdf_bytes_smart(fpath)
                    fe["bytes"] = b
                    dt_s = max(time.perf_counter() - t0, 1e-6)
                    mb = len(b) / (1024 * 1024)
                    if tracer.enabled:
                        print(f"[{tracer.prefix}] >> 2A. Preload {type_id:<9} | {fname[:32]:<32} | {dt_s*1000:>8.1f}ms ({mb*1024:.1f} KB @ {mb/dt_s:.1f} MB/s)", flush=True)
                except Exception as _err:
                    print(f"[preload {type_id}] {fname}: {_err}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=so_workers) as executor:
                futures = [executor.submit(_preload_so_worker, fe) for fe in sorted_soviews]
                concurrent.futures.wait(futures)

            dt_so_preload = (time.perf_counter() - t_preload_so) * 1000
            total_so_kb = sum(len(fe.get("bytes", b"")) for fe in sorted_soviews) // 1024
            throughput_so = (total_so_kb / 1024) / max((dt_so_preload / 1000), 1e-6)
            tracer.record(
                phase=f"2A. Preload {type_id}",
                target=f"{len(sorted_soviews)} files ({total_so_kb} KB)",
                t0=t_preload_so,
                details=f"Parallel Small-First (4 workers) @ {throughput_so:.1f} MB/s"
            )

        for file_key, uf in unique_files.items():
            if cancel_event.is_set():
                break

            file_result = uf["file_result"]
            file_name   = file_result.get("name", "")
            all_sols    = uf["sols"]

            # Fallback nếu preload bị sót hoặc file local
            if not file_result.get("bytes"):
                _src_path = file_result.get("path", "")
                if _src_path and (os.path.isfile(_src_path) or _src_path.startswith(r"\\\\")):
                    try:
                        file_result["bytes"] = read_pdf_bytes_smart(_src_path)
                    except Exception as _load_exc:
                        print(f"[preload] {file_name}: {_load_exc}")

            pdf_source = file_result.get("bytes") or file_result.get("path")

            def make_progress_cb(f_name=file_name, t_id=type_id, c=completed_unique, tot=total_unique):
                def _cb(method):
                    if progress_cb:
                        progress_cb({
                            "sol":         all_sols[0] if all_sols else "",
                            "typeId":      t_id,
                            "method":      method,
                            "fileName":    f_name,
                            "currentFile": c + 1,
                            "totalFiles":  tot,
                        })
                return _cb

            # Pre-check: skip file if all target SOLs already found via barcode
            if chain and chain[0] == "barcode":
                all_found = all(
                    bool(assembly.get(sol, {}).get(type_id))
                    for sol in all_sols
                )
                if all_found:
                    completed_unique += 1
                    continue

            t_file = time.perf_counter()
            try:
                page_map, method_used = _detect_pages_all_sols(
                    pdf_source     = pdf_source,
                    sol_list       = all_sols,
                    priority_chain = chain,
                    tesseract_cmd  = tesseract_cmd,
                    cancel_event   = cancel_event,
                    progress_cb    = make_progress_cb(),
                    ocr_flag       = ocr_flag,
                    sol_flags      = sol_flags,
                )
                # Merge pages into assembly — use local_idx for this SO
                total_pages_matched = 0
                for sol in all_sols:
                    so = parse_so(sol)
                    local_idx = uf["so_to_local_idx"].get(so, 0)
                    pages = page_map.get(sol, [])
                    total_pages_matched += len(pages)
                    file_path = file_result.get("path") or ""
                    file_id   = file_result.get("file_id") or file_path or file_name

                    seen = set((e["fileIdx"], e["pageIndex"]) for e in assembly[sol][type_id])
                    for p in pages:
                        if (local_idx, p) not in seen:
                            assembly[sol][type_id].append({
                                "pageIndex": p,
                                "fileIdx":   local_idx,
                                "fileName":  file_name,
                                "filePath":  file_path,
                                "fileId":    file_id,
                                "method":    method_used,
                            })
                            seen.add((local_idx, p))
                    assembly[sol][type_id].sort(key=lambda e: (e["fileIdx"], e["pageIndex"]))

                tracer.record(
                    phase=f"2. Scan {type_id}",
                    target=file_name,
                    t0=t_file,
                    details=f"file {completed_unique + 1}/{total_unique}, method={method_used}, {total_pages_matched} pgs ({len(all_sols)} SOLs)"
                )

                # ── 1B: Early Eviction — xóa bytes ngay nếu file không có trang nào match ───
                # Bytes không cần nữa → trả lại RAM cho hệ thống.
                # Các file CÓ match giữ bytes lại để layout priority sort + cắt ghép sub-doc.
                if total_pages_matched == 0 and "bytes" in file_result:
                    del file_result["bytes"]

            except Exception as exc:
                for sol in all_sols:
                    errors.append({"sol": sol, "typeId": type_id, "error": str(exc)})
                tracer.record(
                    phase=f"2. Scan {type_id} (ERROR)",
                    target=file_name,
                    t0=t_file,
                    details=str(exc)
                )
                # Evict bytes even on error — file không dùng được
                if "bytes" in file_result:
                    del file_result["bytes"]

            completed_unique += 1

        # ── Post-process layout priority sort (PFL: multi-file front/layout) ──
        if type_id == "layout":
            t_sort = time.perf_counter()
            sorted_count = 0
            for sol, types_dict in assembly.items():
                if "layout" not in types_dict or not types_dict["layout"]:
                    continue

                layout_items = types_dict["layout"]
                unique_fidxs = set(e["fileIdx"] for e in layout_items)

                if len(unique_fidxs) >= 2:
                    sorted_count += 1
                    so = parse_so(sol)
                    priority_dict: dict = {}

                    for fidx in unique_fidxs:
                        f_name = next(e["fileName"] for e in layout_items if e["fileIdx"] == fidx)
                        name_lower = f_name.lower()

                        has_top    = "front" in name_lower or "film" in name_lower
                        has_bottom = "layout" in name_lower or "docs" in name_lower

                        if has_top and not has_bottom:
                            priority = 0
                        elif has_bottom and not has_top:
                            priority = 1
                        else:
                            # Conflict or neither — check PDF page size
                            priority = 0
                            all_layout_for_so = batched_files.get("layout", {}).get(so, []) if so else []
                            layout_found = [f for f in all_layout_for_so if f.get("found")]
                            if 0 <= fidx < len(layout_found):
                                pdf_source = layout_found[fidx].get("bytes") or layout_found[fidx].get("path")
                                selected_pages = [e["pageIndex"] for e in layout_items if e["fileIdx"] == fidx]
                                try:
                                    if isinstance(pdf_source, (bytes, bytearray)):
                                        doc = fitz.open(stream=pdf_source, filetype="pdf")
                                    else:
                                        try:
                                            with open(pdf_source, "rb") as fp:
                                                b = fp.read()
                                            layout_found[fidx]["bytes"] = b
                                            doc = fitz.open(stream=b, filetype="pdf")
                                        except Exception:
                                            doc = fitz.open(pdf_source)

                                    all_a4 = True
                                    for p_idx in selected_pages:
                                        if p_idx - 1 < len(doc):
                                            page = doc[p_idx - 1]
                                            try:
                                                box = page.trimbox
                                                if box.width == 0 or box.height == 0:
                                                    box = page.rect
                                            except AttributeError:
                                                box = page.rect
                                            w, h = float(box.width), float(box.height)
                                            A4_W, A4_H, TOL = 595.28, 841.89, 1.0
                                            is_portrait  = abs(w - A4_W) <= TOL and abs(h - A4_H) <= TOL
                                            is_landscape = abs(w - A4_H) <= TOL and abs(h - A4_W) <= TOL
                                            if not (is_portrait or is_landscape):
                                                all_a4 = False
                                                break
                                        else:
                                            all_a4 = False
                                            break
                                    doc.close()
                                    priority = 1 if all_a4 else 0
                                except Exception:
                                    pass

                        priority_dict[fidx] = priority

                    if len(set(priority_dict.values())) == 1:
                        layout_items.sort(key=lambda e: (e["fileIdx"], e["pageIndex"]))
                    else:
                        layout_items.sort(key=lambda e: (priority_dict.get(e["fileIdx"], 0), e["fileIdx"], e["pageIndex"]))

            if sorted_count > 0:
                tracer.record(
                    phase="3. Layout Priority Sort",
                    target="PFL Layout",
                    t0=t_sort,
                    details=f"{sorted_count} SOLs evaluated for keywords/A4 geometry"
                )

    # -- Step 5: Slice matched pages into AUTO_DETECT_RAM_CACHE (Zero-network I/O) --
    # Bytes da nam san trong RAM tu buoc quet (1A preload).
    # Khong can doc mang lan nao nua -- chi cat ghep thuan CPU/RAM.
    t_ram = time.perf_counter()
    import pymupdf as fitz
    AUTO_DETECT_RAM_CACHE.clear()

    _opened: dict = {}
    try:
        for sol, types_dict in assembly.items():
            so = parse_so(sol)
            for type_id in pdf_type_ids:
                items = types_dict.get(type_id, [])
                if not items:
                    continue

                if type_id == "layout" and job_map and sol in job_map:
                    job_code = str(job_map[sol]).strip()
                    found_files = [
                        f for f in batched_files.get("layout", {}).get(job_code, [])
                        if f.get("found")
                    ]
                elif type_id == "layout" and line == "RFID SB":
                    found_files = [
                        f for f in batched_files.get("layout", {}).get(sol, [])
                        if f.get("found")
                    ]
                else:
                    found_files = [
                        f for f in batched_files.get(type_id, {}).get(so, [])
                        if f.get("found")
                    ] if so else []

                ram_key = f"{sol}_{type_id}"
                out_doc = fitz.open()
                try:
                    for e in items:
                        fidx = e.get("fileIdx", 0)
                        p    = e.get("pageIndex")
                        file_entry = found_files[fidx] if 0 <= fidx < len(found_files) else {}

                        # Priority: bytes in RAM (from 1A preload) > path fallback
                        pdf_source = file_entry.get("bytes") or file_entry.get("path") or e.get("filePath")
                        if not pdf_source:
                            continue

                        file_key = file_entry.get("path") or e.get("filePath") or (type_id, so, fidx, file_entry.get("name"))
                        if file_key not in _opened:
                            if isinstance(pdf_source, (bytes, bytearray)):
                                _opened[file_key] = fitz.open(stream=pdf_source, filetype="pdf")
                            else:
                                if is_network_path(pdf_source):
                                    try:
                                        b = read_pdf_bytes_smart(pdf_source)
                                        file_entry["bytes"] = b
                                        _opened[file_key] = fitz.open(stream=b, filetype="pdf")
                                    except Exception:
                                        _opened[file_key] = fitz.open(pdf_source)
                                else:
                                    _opened[file_key] = fitz.open(pdf_source)

                        src_doc = _opened.get(file_key)
                        if src_doc and p is not None:
                            page_idx = p - 1  # convert 1-based to 0-based
                            if 0 <= page_idx < src_doc.page_count:
                                out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)

                    if out_doc.page_count > 0:
                        # Fast serialization: deflate=False, garbage=0 avoids CPU compression overhead
                        AUTO_DETECT_RAM_CACHE[ram_key] = out_doc.tobytes(deflate=False, garbage=0)

                except Exception as exc:
                    print(f"[ram_slice] {sol}/{type_id}: {exc}")
                finally:
                    out_doc.close()

                # -- Late Eviction: xoa bytes file goc sau khi sub-doc da duoc tao ------
                # Sub-doc RAM da du cho Export -- bytes file goc khong can nua.
                for ev_e in items:
                    ev_fidx = ev_e.get("fileIdx", 0)
                    if 0 <= ev_fidx < len(found_files):
                        ev_fe = found_files[ev_fidx]
                        if "bytes" in ev_fe:
                            del ev_fe["bytes"]

    finally:
        for d in _opened.values():
            try:
                d.close()
            except Exception:
                pass

    cached_count = len(AUTO_DETECT_RAM_CACHE)
    total_ram_kb = sum(len(b) for b in AUTO_DETECT_RAM_CACHE.values()) // 1024
    tracer.record(
        phase="4. RAM Slice",
        target="AUTO_DETECT_RAM_CACHE",
        t0=t_ram,
        details=f"{cached_count} sub-documents ({total_ram_kb} KB) -- zero network I/O"
    )

    tracer.print_summary()
    return {"assembly": assembly, "errors": errors}

