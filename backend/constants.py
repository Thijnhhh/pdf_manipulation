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
def has_credentials() -> bool:
    """Kiểm tra file credentials.json có tồn tại không mà không ném lỗi."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.normpath(os.path.join(base, "config", "credentials.json"))
    return os.path.exists(path)


def get_credentials_path(strict: bool = True) -> str | None:
    """
    Resolve credentials.json path for local development and PyInstaller bundles.
      • plain script: <repo_root>/config/credentials.json
      • PyInstaller exe: sys._MEIPASS/config/credentials.json
      • strict=False: trả về None thay vì raise FileNotFoundError
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

    path = os.path.normpath(os.path.join(base, "config", "credentials.json"))
    if not os.path.exists(path):
        if not strict:
            return None
        raise FileNotFoundError(
            f"credentials.json not found at: {path}\n"
            "Place your Google Service Account key in the config/ folder."
        )
    return path


_SAMPLE_MODULE_SUBDIRS = ("pfl", "trim", "rfid_ht", "pfl_thermal", "rfid_sb_uq")
_SAMPLE_DIR_CACHE: str | None = None


def _is_valid_sample_dir(path: str) -> bool:
    """Thư mục hợp lệ nếu chứa ít nhất 1 thư mục con module có metadata.json."""
    if not path or not os.path.isdir(path):
        return False
    return any(
        os.path.isfile(os.path.join(path, sub, "metadata.json"))
        for sub in _SAMPLE_MODULE_SUBDIRS
    )


def _ask_user_for_sample_dir() -> str | None:
    """Hiện hộp thoại chọn thư mục sample_data qua pywebview. Trả về None nếu huỷ."""
    try:
        import webview
        if not webview.windows:
            return None
        result = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=os.path.expanduser("~"),
        )
    except Exception as exc:
        print(f"[constants] Folder dialog error: {exc}")
        return None
    if not result:
        return None
    chosen = os.path.normpath(result[0] if isinstance(result, (list, tuple)) else result)
    # Người dùng lỡ chọn thư mục module con (vd: sample_data/pfl) → lấy thư mục cha
    if not _is_valid_sample_dir(chosen) and _is_valid_sample_dir(os.path.dirname(chosen)):
        chosen = os.path.dirname(chosen)
    return chosen


def get_sample_data_dir() -> str:
    """
    Đường dẫn thư mục sample_data gốc. Thứ tự tìm:
      1. Đường dẫn người dùng đã chọn trước đó (cache trong phiên)
      2. Cạnh file .exe (frozen) / gốc repo (dev)
      3. sys._MEIPASS (frozen)
      4. Không tìm thấy → hiện hộp thoại cho người dùng chọn thư mục
    """
    global _SAMPLE_DIR_CACHE
    if _SAMPLE_DIR_CACHE and _is_valid_sample_dir(_SAMPLE_DIR_CACHE):
        return _SAMPLE_DIR_CACHE

    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "sample_data"))
        candidates.append(os.path.join(sys._MEIPASS, "sample_data"))  # type: ignore[attr-defined]
    else:
        candidates.append(os.path.join(os.path.dirname(__file__), "..", "sample_data"))

    for c in candidates:
        c = os.path.normpath(c)
        if _is_valid_sample_dir(c):
            _SAMPLE_DIR_CACHE = c
            return c

    # Không tìm thấy → hỏi người dùng (lặp lại tới khi hợp lệ hoặc huỷ)
    while True:
        chosen = _ask_user_for_sample_dir()
        if chosen is None:
            break
        if _is_valid_sample_dir(chosen):
            _SAMPLE_DIR_CACHE = chosen
            return chosen
        print(f"[constants] Thư mục không hợp lệ (thiếu <module>/metadata.json): {chosen}")

    # Huỷ chọn → trả về đường dẫn mặc định; sample_service sẽ báo lỗi metadata.json
    return os.path.normpath(candidates[0])



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
