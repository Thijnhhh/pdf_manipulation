"""
backend/constants.py
─────────────────────────────────────────────────────────────
Centralized configuration, credentials resolution, and constants
for all modules (PFL, RFID HT, PDF Merger (Trim), PFL Thermal, RFID SB).
─────────────────────────────────────────────────────────────
"""

import os
import sys

# ── 1. Credentials Resolution ───────────────────────────────
def get_credentials_path() -> str:
    """
    Resolve credentials.json path for local development and PyInstaller bundles.
      • plain script: <repo_root>/config/credentials.json
      • PyInstaller exe: sys._MEIPASS/config/credentials.json
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

    path = os.path.normpath(os.path.join(base, "config", "credentials.json"))
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"credentials.json not found at: {path}\n"
            "Place your Google Service Account key in the config/ folder."
        )
    return path


# ── 2. Google BigQuery ──────────────────────────────────────
BQ_PROJECT_ID   = "avy-data-rfid-fru"
BQ_PROC_GET_TE  = f"`{BQ_PROJECT_ID}.raw_ingest.get_TE`"
BQ_PROC_GET_JOB = f"`{BQ_PROJECT_ID}.raw_ingest.get_Job`"


# ── 3. Google Sheets ────────────────────────────────────────
SPREADSHEET_ID   = "1dUHAsSLSa1thA9Mmy-4_B2A9sXSqsILfRVOoz_s_gAk"
USERS_SHEET_NAME = "Users_Preset"
SHEETS_SCOPES    = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


# ── 4. Business Rules & Code Patterns ───────────────────────
JOB_CODE_LENGTH = 8
JOB_CODE_REGEX  = rf"(?<!\d)(\d{{{JOB_CODE_LENGTH}}})(?!\d)"
SOL_CODE_LENGTH = 9
