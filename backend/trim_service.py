"""
backend/trim_service.py
─────────────────────────────────────────────────────────────
Dedicated processing engine for the "Trim Adidas" module.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import sys

import pymupdf as fitz


BQ_PROJECT_ID = "avy-data-rfid-fru"
BQ_PROCEDURE  = f"`{BQ_PROJECT_ID}.raw_ingest.get_TE`"

EXACT_TE_HEADER = "Ticket Express Number"

_P1_REGEX = re.compile(r"\*\d{9}\*")
_P2_REGEX = re.compile(
    r"FOR (MANUFACTURING(?:/PURCHASE)?|PURCHASE)(?:\s*[\r\n]+\s*|\s+)(\d{1,3})\s*/\s*(\d{1,3}(?:\s*[\r\n]+\s*\d+)?)",
    re.MULTILINE,
)
_P3_REGEX = re.compile(r"Page\s+(\d{1,3})\s*of\s*(\d{1,3})")


def _credentials_path() -> str:
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(__file__), "..")
    path = os.path.normpath(os.path.join(base, "config", "credentials.json"))
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"credentials.json not found at: {path}\n"
            "Place your Google Service Account key in the config/ folder."
        )
    return path


# ── 1. BigQuery Fetch ─────────────────────────────────────────

def fetch_bigquery_te(pairs: list) -> list:
    """
    Call BigQuery stored procedure get_TE with (so_int, line_int) pairs.
    Returns list of dicts: [{SOL, ORDERED_ITEM, list_TE}, ...]
    """
    from google.cloud import bigquery
    from google.oauth2 import service_account

    if not pairs:
        return []

    creds = service_account.Credentials.from_service_account_file(_credentials_path())
    client = bigquery.Client(credentials=creds, project=creds.project_id)

    structs_sql = ",\n        ".join(
        [f"STRUCT({so} AS so, {line} AS line)" for so, line in pairs]
    )
    call_sql = f"""
    CALL {BQ_PROCEDURE}([
        {structs_sql}
    ]);
    """

    try:
        query_job = client.query(call_sql)
        results = query_job.result()
        return [dict(row) for row in results]
    finally:
        client.close()


# ── 2. Data Validation ────────────────────────────────────────

def validate_data_bigquery(records: list, sol_list: list) -> tuple:
    """
    Validate BigQuery results against expected SOL list.

    Returns:
        (sol_metadata, missing_report)

        sol_metadata: {sol: {"ordered_item": str|None, "list_te": [str, ...]}}
        missing_report: {"missing_ordered_item": [...], "missing_te": [...]}
    """
    bq_by_sol: dict = {}
    for row in records:
        sol_key = str(row.get("SOL", "")).strip()
        if sol_key:
            bq_by_sol[sol_key] = row

    sol_metadata: dict = {}
    missing_ordered_item: list = []
    missing_te: list = []

    for sol in sol_list:
        row = bq_by_sol.get(sol)
        ordered_item = None
        list_te: list = []

        if row is None:
            missing_ordered_item.append(sol)
            missing_te.append(sol)
        else:
            raw_oi = row.get("ORDERED_ITEM")
            ordered_item = str(raw_oi).strip() if raw_oi else None
            if not ordered_item:
                missing_ordered_item.append(sol)

            raw_te = row.get("list_TE")
            if raw_te:
                raw_te_str = str(raw_te).strip()
                if re.fullmatch(r"\d{10}", raw_te_str):
                    list_te = [raw_te_str]
                else:
                    extracted = extract_ticket_express_numbers(raw_te_str)
                    list_te = [t.strip() for t in extracted.split(", ") if t.strip()] if extracted else []

            if not list_te:
                missing_te.append(sol)

        sol_metadata[sol] = {
            "ordered_item": ordered_item,
            "list_te":      list_te,
        }

    return sol_metadata, {
        "missing_ordered_item": missing_ordered_item,
        "missing_te":           missing_te,
    }


# ── 3. TE Text Extraction ─────────────────────────────────────

def parse_semicolon_delimited_row(row: str) -> list:
    raw_segments = row.split(";")
    header_idx = -1
    te_col = -1

    for i, seg in enumerate(raw_segments):
        if EXACT_TE_HEADER in seg:
            cols = [c.strip() for c in seg.split(":")]
            for ci, c in enumerate(cols):
                if c == EXACT_TE_HEADER:
                    te_col = ci
                    header_idx = i
                    break
            if header_idx != -1:
                break

    if header_idx == -1 or te_col == -1:
        return []

    tes = []
    for seg in raw_segments[header_idx + 1:]:
        s_clean = seg.strip()
        if not s_clean or s_clean in ("^", ".^") or s_clean.upper() == "KEM PACKING LIST CHI TIET":
            continue
        if EXACT_TE_HEADER in s_clean:
            cols = [c.strip() for c in s_clean.split(":")]
            for ci, c in enumerate(cols):
                if c == EXACT_TE_HEADER:
                    te_col = ci
            continue
        if re.match(r"^total\s*:", s_clean, re.IGNORECASE):
            continue
        parts = [p.strip() for p in s_clean.split(":")]
        if len(parts) > te_col:
            val = re.sub(r"[.\^]+$", "", parts[te_col]).strip()
            if re.fullmatch(r"\d{10}", val):
                tes.append(val)
    return tes


def parse_merged_total_row(row: str) -> list:
    fixed_row = re.sub(r"\b(Total)\s+([^\s:;]+\s*:)", r"\1 ; \2", row, flags=re.IGNORECASE)
    return parse_semicolon_delimited_row(fixed_row)


def parse_space_delimited_row(row: str) -> list:
    if EXACT_TE_HEADER not in row:
        return []
    start_pos = row.find(EXACT_TE_HEADER) + len(EXACT_TE_HEADER)
    m_total = re.search(r"\bTotal\s*", row[start_pos:], re.IGNORECASE)
    if not m_total:
        return []
    data_start = start_pos + m_total.end()
    data_part = row[data_start:]
    matches = re.findall(r"(?<=\s)(\d{10})\s+(\d{1,6})(?=\s+[^\d]|\s+\d{7,}|\s*\^)", data_part)
    if not matches:
        matches = re.findall(r"(?<=\s)(\d{10})\s+(\d{1,6})\b", data_part)
        matches = [m for m in matches if len(m[1]) <= 6]
    return [m[0] for m in matches]


def extract_ticket_express_numbers(row: str) -> str:
    """
    Top-level dispatcher: extract all 10-digit TE codes from a raw text row.
    Returns comma-separated string or empty string.
    """
    if not row or not row.strip():
        return ""
    if EXACT_TE_HEADER not in row:
        return ""
    if ";" not in row and ":" not in row:
        tes = parse_space_delimited_row(row)
        return ", ".join(tes) if tes else ""
    if re.search(r"\bTotal\s+([^\s:;]+\s*:)", row, re.IGNORECASE):
        tes = parse_merged_total_row(row)
        return ", ".join(tes) if tes else ""
    tes = parse_semicolon_delimited_row(row)
    return ", ".join(tes) if tes else ""


# ── 4. SOview 3-Pattern PDF Scan ─────────────────────────────

def _extract_page_patterns(page_text: str) -> tuple:
    raw_p1 = _P1_REGEX.findall(page_text)
    p1_str = "; ".join(list(dict.fromkeys(raw_p1)))

    p2_matches = []
    for m in _P2_REGEX.finditer(page_text):
        form_type = m.group(1)
        n1 = re.sub(r"\s+", "", m.group(2))
        n2 = re.sub(r"\s+", "", m.group(3))
        if int(n1) <= int(n2):
            p2_matches.append(f"FOR {form_type} {n1}/{n2}")
    p2_str = "; ".join(list(dict.fromkeys(p2_matches)))

    p3_matches = []
    for m in _P3_REGEX.finditer(page_text):
        n1, n2 = m.group(1), m.group(2)
        if int(n1) <= int(n2):
            p3_matches.append(f"Page {n1} of {n2}")
    p3_str = "; ".join(list(dict.fromkeys(p3_matches)))

    return p1_str, p2_str, p3_str


def scan_soview_pdf_3patterns(pdf_source, target_sols: list, base_so: str, cancel_event=None, progress_cb=None) -> dict:
    """
    Scan a SOview PDF for pages belonging to each target SOL using 3-pattern algorithm.
    Returns {sol: [1-based page numbers]}.
    """
    target_sol_set = set(target_sols)
    results = {sol: [] for sol in target_sols}

    try:
        if isinstance(pdf_source, (bytes, bytearray)):
            doc = fitz.open(stream=pdf_source, filetype="pdf")
        else:
            doc = fitz.open(pdf_source)
    except Exception as exc:
        print(f"[trim_service] scan_soview: open failed — {exc}")
        return results

    seen_pages = {sol: set() for sol in target_sols}
    current_sol = None
    range_expected_end = None

    if progress_cb:
        progress_cb("soview_3pattern")

    try:
        for page_idx in range(doc.page_count):
            if cancel_event and cancel_event.is_set():
                break

            page_num = page_idx + 1
            try:
                text = doc[page_idx].get_text() or ""
            except Exception:
                text = ""

            p1_str, p2_str, p3_str = _extract_page_patterns(text)

            # ── 1. Pattern 1: Check SO Barcode (*SO*) ──
            if p1_str:
                range_expected_end = None
                barcodes = [b.strip("*") for b in p1_str.split("; ")]

                # Điều kiện 1: Barcode phải khớp base_so mục tiêu
                if base_so in barcodes:
                    sol_hit = None
                    # Điều kiện 2: Kiểm tra tiếp số Line (FOR MFG/PURCHASE X/Y)
                    if p2_str:
                        for match_str in p2_str.split("; "):
                            m = re.search(r"(\d{1,3})/(\d{1,3})$", match_str)
                            if m:
                                x_val = m.group(1)
                                candidate = f"{base_so}-{x_val}"
                                if candidate in target_sol_set:
                                    sol_hit = candidate
                                    break

                    current_sol = sol_hit
                    if sol_hit and sol_hit in target_sol_set:
                        if page_num not in seen_pages[sol_hit]:
                            results[sol_hit].append(page_num)
                            seen_pages[sol_hit].add(page_num)

                        # Kiểm tra Pattern 3 ngay trên cùng trang để xác định dải trang
                        if p3_str:
                            m3 = re.search(r"Page\s+(\d{1,3})\s*of\s*(\d{1,3})", p3_str)
                            if m3:
                                x_val, y_val = int(m3.group(1)), int(m3.group(2))
                                if x_val < y_val:
                                    range_expected_end = y_val
                                else:
                                    range_expected_end = None
                    else:
                        # Khác Line cần tìm trong SO này
                        current_sol = None
                        range_expected_end = None
                else:
                    # Barcode thuộc SO khác
                    current_sol = None
                    range_expected_end = None
                continue

            # Fallback nếu trang không có Barcode P1 nhưng có P2 (hiếm gặp)
            if p2_str:
                range_expected_end = None
                sol_hit = None
                for match_str in p2_str.split("; "):
                    m = re.search(r"(\d{1,3})/(\d{1,3})$", match_str)
                    if m:
                        x_val = m.group(1)
                        candidate = f"{base_so}-{x_val}"
                        if candidate in target_sol_set:
                            sol_hit = candidate
                            break
                current_sol = sol_hit
                if sol_hit and sol_hit in target_sol_set:
                    if page_num not in seen_pages[sol_hit]:
                        results[sol_hit].append(page_num)
                        seen_pages[sol_hit].add(page_num)
                    if p3_str:
                        m3 = re.search(r"Page\s+(\d{1,3})\s*of\s*(\d{1,3})", p3_str)
                        if m3:
                            x_val, y_val = int(m3.group(1)), int(m3.group(2))
                            if x_val < y_val:
                                range_expected_end = y_val
                            else:
                                range_expected_end = None
                else:
                    current_sol = None
                    range_expected_end = None
                continue

            # Pattern 3: Page X of Y (Trang tiếp nối của SOL đa trang)
            if p3_str and current_sol and range_expected_end is not None:
                m3 = re.search(r"Page\s+(\d{1,3})\s*of\s*(\d{1,3})", p3_str)
                if m3:
                    x_val, y_val = int(m3.group(1)), int(m3.group(2))
                    if current_sol in target_sol_set:
                        if page_num not in seen_pages[current_sol]:
                            results[current_sol].append(page_num)
                            seen_pages[current_sol].add(page_num)
                    if x_val >= y_val or x_val >= range_expected_end:
                        range_expected_end = None
                        current_sol = None
                continue

            # No pattern: continuation under open range
            if current_sol and range_expected_end is not None:
                if current_sol in target_sol_set:
                    if page_num not in seen_pages[current_sol]:
                        results[current_sol].append(page_num)
                        seen_pages[current_sol].add(page_num)

            # Early exit: Đã tìm đủ tất cả target_sols và không còn range nào đang mở
            if range_expected_end is None and all(len(results[s]) > 0 for s in target_sols):
                break

    finally:
        doc.close()

    for sol in results:
        results[sol].sort()

    return results


# ── 5. TE Barcode Scan ────────────────────────────────────────

def scan_te_pdf_barcode(pdf_source, te_codes_needed: list, cancel_event=None) -> dict:
    """
    Scan a TE PDF for *<TE>* barcode text.
    Each TE code maps to exactly 1 page. Stops when all needed TEs are found.
    Returns {te_code: 1-based page number}.
    """
    if not te_codes_needed:
        return {}

    remaining = set(te_codes_needed)
    found = {}

    try:
        if isinstance(pdf_source, (bytes, bytearray)):
            doc = fitz.open(stream=pdf_source, filetype="pdf")
        else:
            doc = fitz.open(pdf_source)
    except Exception as exc:
        print(f"[trim_service] scan_te_pdf_barcode: open failed — {exc}")
        return {}

    try:
        for page_idx in range(doc.page_count):
            if cancel_event and cancel_event.is_set():
                break
            if not remaining:
                break
            page_num = page_idx + 1
            try:
                text = doc[page_idx].get_text() or ""
            except Exception:
                continue
            for te in list(remaining):
                if f"*{te}*" in text:
                    found[te] = page_num
                    remaining.discard(te)
    finally:
        doc.close()

    return found
