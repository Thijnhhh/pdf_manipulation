"""
backend/file_resolver.py
─────────────────────────────────────────────────────────────
Dual-mode PDF file finder.

Mode A — Local / Network Drive (is_drive=False)
  • Accepts a UNC path or local directory path.
  • Uses pathlib.Path.rglob() to find ALL PDFs whose filename
    contains the SO code (case-insensitive).
  • Returns list of { "found": True, "path": str, "name": str }

Mode B — Google Drive (is_drive=True)
  • Accepts a Google Drive folder URL or raw folder ID.
  • Uses Drive API files.list() with a name-contains query.
  • Downloads each matched file into an in-memory BytesIO buffer.
  • Returns list of { "found": True, "bytes": bytes, "name": str, "url": str }

SO vs SOL:
  • SOL  — full Sales Order Line string, e.g. "144187563-1"
  • SO   — 9-digit base prefix, e.g. "144187563"
  • parse_so() extracts SO from SOL. Pattern: ^\\d{9}-\\d{1,3}$
    If the SOL does not match, parse_so() returns None → caller skips.

Backward compatibility:
  • find_pdf(folder_input, sol_code, is_drive) still works —
    it parses SO from SOL and returns the FIRST match (or {found:False}).
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

# Google API — only imported when Drive mode is needed
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload  # type: ignore
from google.oauth2.service_account import Credentials

import atexit
import tempfile
import uuid

SESSION_TEMP_DIR: str = tempfile.mkdtemp(prefix="pdf_merger_")


def get_session_temp_dir() -> str:
    """Return the shared session temp directory path."""
    return SESSION_TEMP_DIR


def _cleanup_session_temp():
    """Remove the session temp directory on application exit."""
    import shutil
    try:
        shutil.rmtree(SESSION_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


atexit.register(_cleanup_session_temp)


SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Regex for valid SOL: exactly 9 digits, hyphen, 1-3 digits
_SOL_PATTERN = re.compile(r"^(\d{9})-\d{1,3}$")

# ── Resolved Files In-Memory Cache ─────────────────────────
_RESOLVED_FILES_CACHE: dict[str, dict] = {}


def get_file_source(file_id: str) -> "bytes | str | None":
    """
    Retrieve PDF source (file path string or bytes buffer) by file_id.
    First checks _RESOLVED_FILES_CACHE.
    If not in cache and file_id is a valid local file path on disk, returns file_id.
    """
    if not file_id:
        return None
    if file_id in _RESOLVED_FILES_CACHE:
        entry = _RESOLVED_FILES_CACHE[file_id]
        return entry.get("bytes") or entry.get("path")

    # If file_id is a Drive URL like https://drive.google.com/file/d/FILE_ID/view
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", file_id)
    if m:
        extracted_id = m.group(1)
        if extracted_id in _RESOLVED_FILES_CACHE:
            entry = _RESOLVED_FILES_CACHE[extracted_id]
            return entry.get("bytes") or entry.get("path")

    import os
    if os.path.isfile(file_id):
        return file_id

    return None


# ── SO Parsing ───────────────────────────────────────────────

def parse_so(sol_code: str) -> "str | None":
    """
    Extract the 9-digit SO base from a SOL string.

    Valid pattern: ^\\d{9}-\\d{1,3}$
    Examples:
        "144187563-1"  → "144187563"
        "144187563-12" → "144187563"
        "ABCDEF-1"     → None  (ignored)
        "144187563"    → None  (no hyphen, ignored)

    Returns:
        str: 9-digit SO on match
        None: SOL does not match expected pattern — caller should skip
    """
    m = _SOL_PATTERN.match(sol_code.strip())
    return m.group(1) if m else None


# ── Helpers ─────────────────────────────────────────────────

def _credentials_path() -> str:
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(__file__), "..")
    return os.path.normpath(os.path.join(base, "config", "credentials.json"))


import httplib2
import google_auth_httplib2

def _drive_service():
    creds = Credentials.from_service_account_file(_credentials_path(), scopes=SCOPES)
    # Create fresh HTTP transport per service instance with timeout to avoid WinError 10054 socket re-use crashes
    http_transport = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build("drive", "v3", http=http_transport, cache_discovery=False)


def extract_folder_id(url_or_id: str) -> "str | None":
    """
    Extract a Drive folder ID from a URL like:
      https://drive.google.com/drive/folders/FOLDER_ID
    or return the raw ID if it already looks like one.
    """
    s = url_or_id.strip()
    if re.match(r"^[a-zA-Z0-9_-]{10,}$", s):
        return s
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", s)
    return m.group(1) if m else None


# ── Public API ───────────────────────────────────────────────
def find_all_pdfs_local_batch(folder_path: str, so_codes: list) -> dict:
    """
    Search a local/network directory for ALL PDFs matching any of the so_codes.
    Returns: { so_code: [ {found, file_id, path, name}, ... ] }
    """
    clean_so_codes = list(dict.fromkeys(so.strip() for so in so_codes if so and isinstance(so, str) and so.strip()))
    base = Path(folder_path)
    if not base.exists() or not base.is_dir():
        return {so: [{"found": False, "error": f"Folder not found: {folder_path}"}] for so in clean_so_codes}

    results = {so: [] for so in clean_so_codes}
    lower_so_map = {so.lower(): so for so in clean_so_codes}

    for f in base.rglob("*.pdf"):
        name_lower = f.name.lower()
        for so_lower, original_so in lower_so_map.items():
            if so_lower in name_lower:
                f_path = str(f)
                item = {"found": True, "file_id": f_path, "path": f_path, "name": f.name}
                _RESOLVED_FILES_CACHE[f_path] = item
                results[original_so].append(item)

    for so in results:
        results[so].sort(key=lambda x: x.get("name", "").lower())

    return results

def find_all_pdfs_drive_batch(folder_url: str, so_codes: list) -> dict:
    """
    Global Search + In-Memory Tree for Drive.
    Returns: { so_code: [ {found, bytes, name, url}, ... ] }
    """
    import time
    
    clean_so_codes = list(dict.fromkeys(so.strip() for so in so_codes if so and isinstance(so, str) and so.strip()))
    results = {so: [] for so in clean_so_codes}
    
    folder_id = extract_folder_id(folder_url)
    if not folder_id:
        return {so: [{"found": False, "error": "Invalid Drive folder URL or ID"}] for so in clean_so_codes}

    service = _drive_service()
    
    # 1. Build In-Memory Tree of all folders
    folder_parents = {}
    page_token = None
    
    def _safe_list(query_str, p_token):
        nonlocal service
        for attempt in range(1, 6):
            try:
                return service.files().list(
                    q=query_str,
                    spaces='drive',
                    fields="nextPageToken, files(id, name, parents)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=p_token,
                    pageSize=1000
                ).execute()
            except Exception as exc:
                if attempt == 5: return None
                time.sleep(1.0 * attempt)
                try:
                    service = _drive_service()
                except:
                    pass

    while True:
        resp = _safe_list("mimeType = 'application/vnd.google-apps.folder' and trashed = false", page_token)
        if not resp: break
        for item in resp.get('files', []):
            folder_parents[item.get('id')] = item.get('parents', [])
        page_token = resp.get('nextPageToken')
        if not page_token: break

    def is_in_target(file_parents):
        if not file_parents: return False
        visited = set()
        queue = list(file_parents)
        while queue:
            curr = queue.pop(0)
            if curr == folder_id:
                return True
            if curr in visited:
                continue
            visited.add(curr)
            if curr in folder_parents:
                queue.extend(folder_parents[curr])
        return False

    def _safe_download(file_id):
        nonlocal service
        for attempt in range(1, 4):
            try:
                request = service.files().get_media(fileId=file_id)
                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, request, chunksize=5 * 1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                buf.seek(0)
                temp_path = os.path.join(SESSION_TEMP_DIR, f"{uuid.uuid4().hex}.pdf")
                with open(temp_path, "wb") as f:
                    f.write(buf.read())
                return temp_path
            except Exception as exc:
                if attempt == 3: return ""
                time.sleep(0.5 * attempt)
                try:
                    service = _drive_service()
                except: pass
        return ""

    # 2. Chunk SO codes and query Drive
    def chunker(seq, size):
        return (seq[pos:pos + size] for pos in range(0, len(seq), size))
        
    lower_so_map = {so.lower(): so for so in clean_so_codes}

    for chunk in chunker(list(lower_so_map.keys()), 30):
        kw_query = " or ".join([f"name contains '{kw}'" for kw in chunk])
        query = f"({kw_query}) and mimeType = 'application/pdf' and trashed = false"
        
        page_token = None
        while True:
            resp = _safe_list(query, page_token)
            if not resp: break
            
            for item in resp.get('files', []):
                file_id = item['id']
                file_name = item.get('name', '')
                parents = item.get('parents', [])
                
                if is_in_target(parents):
                    name_lower = file_name.lower()
                    matched_sos = []
                    for so_lower, original_so in lower_so_map.items():
                        if so_lower in chunk and so_lower in name_lower:
                            matched_sos.append(original_so)
                    
                    if matched_sos:
                        if file_id in _RESOLVED_FILES_CACHE:
                            pdf_item = _RESOLVED_FILES_CACHE[file_id]
                        else:
                            temp_path = _safe_download(file_id)
                            drive_url = f"https://drive.google.com/file/d/{file_id}/view"
                            pdf_item = {
                                "found": True,
                                "file_id": file_id,
                                "path": temp_path,
                                "name": file_name,
                                "url": drive_url
                            }
                            _RESOLVED_FILES_CACHE[file_id] = pdf_item
                        
                        for so in matched_sos:
                            if not any(f.get('file_id') == file_id for f in results[so]):
                                results[so].append(pdf_item)
                                
            page_token = resp.get('nextPageToken')
            if not page_token: break

    for so in results:
        results[so].sort(key=lambda x: x.get("name", "").lower())
        
    return results


def find_all_pdfs_batch(folder_input: str, so_codes: list, is_drive: bool) -> dict:
    """
    Unified batch entry point.
    """
    if is_drive:
        return find_all_pdfs_drive_batch(folder_input, so_codes)
    else:
        return find_all_pdfs_local_batch(folder_input, so_codes)



