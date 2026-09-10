"""
backend/sheets_service.py
─────────────────────────────────────────────────────────────
Connects to Google Sheets via a Service Account JSON key and
reads:
  • Users_Preset  → user presets (name, soview folder, layout folder, output folder)
  • <preset name> → one sheet per preset, each containing SOL codes in column A

The credentials.json is resolved relative to the running
executable so it works both in dev and in a PyInstaller bundle.
─────────────────────────────────────────────────────────────
"""

import json
import os
import sys
import time

import gspread
from google.oauth2.service_account import Credentials

# ── Constants ───────────────────────────────────────────────
SPREADSHEET_ID   = "1dUHAsSLSa1thA9Mmy-4_B2A9sXSqsILfRVOoz_s_gAk"
USERS_SHEET_NAME = "Users_Preset"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _credentials_path() -> str:
    """
    Resolve credentials.json whether running as:
      • a plain Python script  → next to config/
      • a PyInstaller .exe     → sys._MEIPASS/config/
    """
    if getattr(sys, "frozen", False):          # PyInstaller bundle
        base = sys._MEIPASS                    # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(__file__), "..")

    path = os.path.normpath(os.path.join(base, "config", "credentials.json"))
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"credentials.json not found at: {path}\n"
            "Place your Google Service Account key in the config/ folder."
        )
    return path


_CLIENT_CACHE = None

def _get_client() -> gspread.Client:
    global _CLIENT_CACHE
    if not _CLIENT_CACHE:
        creds = Credentials.from_service_account_file(_credentials_path(), scopes=SCOPES)
        _CLIENT_CACHE = gspread.authorize(creds)
    return _CLIENT_CACHE


def _to_bool(val) -> bool:
    """
    Safely convert a Google Sheets cell value to bool for the Flag column.
    Blank / None / empty string → False (default).
    Returns True only when the value is 'true', '1', or 'yes' (case-insensitive).
    """
    if not val or not str(val).strip():
        return False
    return str(val).strip().lower() in ("true", "1", "yes")


def get_all_initial_data(line: str = "PFL") -> dict:
    """
    Returns presets and a per-preset SOL map in the minimum number of API calls.
    Filters presets by the `line` parameter (Col B of Users_Preset).

    Args:
        line: Module line identifier — "PFL" or "RFID HT".
              Only presets whose Column B matches `line` are returned.

    Strategy:
      1. Fetch Users_Preset sheet (A:E) to get all preset rows.
      2. Filter rows by Column B == line.
      3. Build one batch request for the filtered preset sheets only.
         - PFL:     reads column A only (SOL list).
         - RFID HT: reads columns A:D (SOL, Job, RB, Flag).
      4. Return:
           {
             "presets": [{ name, line, soviewFolder, layoutFolder, outputFolder }, ...],
             "solMap":  {
               "<presetName>": ["SOL-CODE", ...]        # PFL
               "<presetName>": [{ sol, job, rb, flag }] # RFID HT
             }
           }

    If a preset's sheet does not exist or is empty, its solMap entry is [].
    Uses exponential backoff for reliability.
    """
    global _CLIENT_CACHE
    target_line = line.strip().upper()

    for attempt in range(1, 4):
        try:
            client      = _get_client()
            spreadsheet = client.open_by_key(SPREADSHEET_ID)

            # ── Step 1: read Users_Preset (A:E — 5 columns) ─────────────────
            # Columns: A=User, B=Line, C=SOview_Folder_Link,
            #          D=Layout_Folder_Link, E=Output_Folder_Link
            preset_response = spreadsheet.values_get(f"{USERS_SHEET_NAME}!A:E")
            preset_rows     = preset_response.get("values", [])

            all_presets: list[dict] = []
            for row in preset_rows[1:]:          # skip header row
                if not row or not row[0].strip():
                    continue
                all_presets.append({
                    "name":         row[0].strip(),
                    "line":         row[1].strip() if len(row) > 1 else "",
                    "soviewFolder": row[2].strip() if len(row) > 2 else "",
                    "layoutFolder": row[3].strip() if len(row) > 3 else "",
                    "outputFolder": row[4].strip() if len(row) > 4 else "",
                })

            # ── Step 2: filter by line ───────────────────────────────────────
            filtered_presets = [
                p for p in all_presets
                if p["line"].strip().upper() == target_line
            ]

            # ── Step 3: batch-fetch only the filtered preset sheets ──────────
            sol_map: dict = {}
            is_rfid_ht = target_line == "RFID HT"

            if filtered_presets:
                preset_names = [p["name"] for p in filtered_presets]

                # Choose sheet range depending on module line
                if is_rfid_ht:
                    ranges = [f"'{name}'!A:D" for name in preset_names]
                else:
                    ranges = [f"'{name}'!A:A" for name in preset_names]

                # ─ Helper: parse a single valueRange for PFL (SOL list) ──────
                def _parse_sol_rows(value_range: dict) -> list:
                    rows = value_range.get("values", [])
                    raw = [
                        row[0].strip()
                        for row in rows[1:]          # skip header
                        if row and row[0].strip()
                    ]
                    return list(dict.fromkeys(raw))  # deduplicate, preserve order

                # ─ Helper: parse a single valueRange for RFID HT (A:D) ───────
                def _parse_rfid_rows(value_range: dict) -> list:
                    rows = value_range.get("values", [])
                    entries = []
                    seen_sols: set = set()
                    for row in rows[1:]:             # skip header
                        if not row or not row[0].strip():
                            continue
                        sol = row[0].strip()
                        if sol in seen_sols:
                            continue
                        seen_sols.add(sol)
                        entries.append({
                            "sol":  sol,
                            "job":  row[1].strip() if len(row) > 1 else "",
                            "rb":   row[2].strip() if len(row) > 2 else "",
                            "flag": _to_bool(row[3] if len(row) > 3 else ""),
                        })
                    return entries

                # ─ Try batch first (1 API call) ─────────────────────────────
                batch_ok = False
                try:
                    batch_response = spreadsheet.values_batch_get(ranges)
                    value_ranges   = batch_response.get("valueRanges", [])
                    for i, name in enumerate(preset_names):
                        vr = value_ranges[i] if i < len(value_ranges) else {}
                        if is_rfid_ht:
                            sol_map[name] = _parse_rfid_rows(vr)
                        else:
                            sol_map[name] = _parse_sol_rows(vr)
                    batch_ok = True
                except Exception:
                    pass  # fall through to individual fetches

                # ─ Fallback: fetch each sheet individually ───────────────────
                # Triggered when batch fails (e.g. one or more sheet names do
                # not exist in the spreadsheet).  Each missing sheet silently
                # returns [] so valid sheets are not affected.
                if not batch_ok:
                    for name in preset_names:
                        try:
                            sheet_range = f"'{name}'!A:D" if is_rfid_ht else f"'{name}'!A:A"
                            resp = spreadsheet.values_get(sheet_range)
                            if is_rfid_ht:
                                sol_map[name] = _parse_rfid_rows(resp)
                            else:
                                sol_map[name] = _parse_sol_rows(resp)
                        except Exception:
                            sol_map[name] = []  # sheet not found — skip silently

            return {"presets": filtered_presets, "solMap": sol_map}

        except Exception as exc:
            if attempt == 3:
                raise exc
            time.sleep(0.5 * attempt)
            # Clear cache to force token refresh on next attempt
            _CLIENT_CACHE = None
