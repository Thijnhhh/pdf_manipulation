"""
backend/updater.py
─────────────────────────────────────────────────────────────
OTA (Over-The-Air) update system.

Flow:
  1. check_for_update()  — reads local version.json, compares
     against a remote version.json stored on Google Drive.
     Returns { update_available, remote_version, current_version }.

  2. apply_update()  — downloads the new .exe from Drive into
     temp/, then launches update_launcher.bat as a detached
     process and signals the main window to close.

Configuration:
  Set REMOTE_VERSION_FILE_ID and REMOTE_EXE_FILE_ID below
  once you have uploaded the release files to Drive.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile  
import tempfile
from packaging.version import Version

from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload  # type: ignore
from google.oauth2.service_account import Credentials
import io

# ── Configuration ────────────────────────────────────────────
# Replace with the actual Drive File IDs after uploading.
REMOTE_VERSION_FILE_ID = "1AKQM_kuyrvqeK6foCt_1O1tRJN2MZ6Vm"
REMOTE_EXE_FILE_ID     = "1cMHbfZJNonLNssdNvJJoyazRBnxM6aOO"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


# ── Helpers ─────────────────────────────────────────────────

def _credentials_path() -> str:
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(__file__), "..")
    return os.path.normpath(os.path.join(base, "config", "credentials.json"))


def _version_json_path() -> str:
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(__file__), "..")
    return os.path.normpath(os.path.join(base, "config", "version.json"))


def _exe_dir() -> str:
    """Directory of the running .exe (or script during dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.join(os.path.dirname(__file__), "..")


def _drive_service():
    creds = Credentials.from_service_account_file(_credentials_path(), scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _read_local_version() -> str:
    try:
        with open(_version_json_path(), "r", encoding="utf-8") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


# ── Public API ───────────────────────────────────────────────

def check_for_update() -> dict:
    """
    Compare local version against remote version.json on Drive.

    Returns:
        {
            "update_available": bool,
            "current_version":  "1.0.0",
            "remote_version":   "1.1.0"
        }
        or { "update_available": False, "error": "..." }
    """
    local_ver = _read_local_version()

    # Skip if Drive IDs haven't been configured
    if REMOTE_VERSION_FILE_ID.startswith("REPLACE"):
        return {"update_available": False, "current_version": local_ver, "remote_version": local_ver}

    try:
        service = _drive_service()
        request = service.files().get_media(fileId=REMOTE_VERSION_FILE_ID)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        remote_data = json.loads(buf.read().decode("utf-8"))
        remote_ver  = remote_data.get("version", "0.0.0")

        update_available = Version(remote_ver) > Version(local_ver)
        return {
            "update_available": update_available,
            "current_version":  local_ver,
            "remote_version":   remote_ver,
        }
    except Exception as exc:
        return {"update_available": False, "error": str(exc), "current_version": local_ver}


def apply_update(remote_version: str = "new") -> None:
    """
    Download the new .exe to temp/ then launch update_launcher.bat
    as a fully detached process. The bat script waits 3 s,
    replaces the old exe, and restarts the app.
    """
    exe_dir = _exe_dir()
    temp_dir = os.path.join(tempfile.gettempdir(), "MyAppUpdate")
    os.makedirs(temp_dir, exist_ok=True)
    zip_path = os.path.join(temp_dir, "update.zip")

    service = _drive_service()
    request = service.files().get_media(fileId=REMOTE_EXE_FILE_ID)
    with open(zip_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)
    # Xóa file .zip sau khi giải nén xong
    try:
        os.remove(zip_path)
    except Exception:
        pass

    extracted_exe_name = next((f for f in os.listdir(temp_dir) if f.endswith(".exe")), None)
    if not extracted_exe_name:
        raise Exception("No .exe file found inside the downloaded zip.")

    bat_path = os.path.join(temp_dir, "update_launcher.bat")
    log_path = os.path.join(temp_dir, "update_log.txt")
    
    # Fix: trong dev mode sys.executable là python.exe (không thể xóa được)
    # → chỉ dùng đường dẫn thật khi đang chạy dạng frozen (.exe build).
    if getattr(sys, "frozen", False):
        current_exe_path = sys.executable
    else:
        # Dev mode: exe gốc không tồn tại nên bat sẽ skip bước xóa và move luôn
        current_exe_path = os.path.join(exe_dir, "PDF_Manipulation_DEV_PLACEHOLDER.exe")

    new_exe_name    = f"PDF Manipulation v{remote_version}.exe"
    target_exe_path = os.path.join(exe_dir, new_exe_name)
    
    bat_content = (
        "@echo off\n"
        f'echo === Update Log === > "{log_path}"\n'
        ":WaitLoop\n"
        "ping 127.0.0.1 -n 4 > nul\n"
        f'del /f /q "{current_exe_path}" >> "{log_path}" 2>&1\n'
        f'if exist "{current_exe_path}" goto WaitLoop\n'
        f'move /y "{os.path.join(temp_dir, extracted_exe_name)}" "{target_exe_path}" >> "{log_path}" 2>&1\n'
        f'start "" "{target_exe_path}" >> "{log_path}" 2>&1\n'
        f'rmdir /s /q "{temp_dir}"\n'
    )
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    # Fix: dùng cmd.exe /c để thực thi .bat file đúng cách trên Windows
    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    # CRITICAL: sys.exit(0) từ background thread chỉ kill thread đó, KHÔNG kill
    # toàn bộ process → pywebview vẫn chạy → exe bị lock → bat loop vô tận.
    # os._exit(0) gọi trực tiếp C _exit(), kill toàn bộ process ngay lập tức
    # từ bất kỳ thread nào → file lock được giải phóng → bat xóa được exe.
    os._exit(0)
