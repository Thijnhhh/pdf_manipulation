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

import concurrent.futures
import io
import os
import re
import sys

# Google API — only imported when Drive mode is needed
from googleapiclient.discovery import build  # type: ignore
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
import requests

from backend.constants import get_credentials_path

import atexit
import queue
import tempfile
import threading
import time
import uuid

SESSION_TEMP_DIR: str = tempfile.mkdtemp(prefix="pdf_merger_")


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

def _drive_service():
    creds = Credentials.from_service_account_file(get_credentials_path(), scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


_thread_local = threading.local()


def _get_thread_drive_service():
    """Return a thread-safe Drive service instance for the current thread."""
    if not hasattr(_thread_local, "service") or _thread_local.service is None:
        _thread_local.service = _drive_service()
    return _thread_local.service


_token_lock = threading.Lock()
_cached_drive_token: tuple[float, str] | None = None  # (expiry_timestamp, token_str)


def _get_drive_token() -> str:
    """Return a valid Bearer token for Google Drive API with automatic refresh."""
    global _cached_drive_token
    now = time.time()
    with _token_lock:
        if _cached_drive_token and _cached_drive_token[0] > now + 60:
            return _cached_drive_token[1]
        creds = Credentials.from_service_account_file(get_credentials_path(), scopes=SCOPES)
        creds.refresh(Request())
        expiry = creds.expiry.timestamp() if creds.expiry else now + 3500
        _cached_drive_token = (expiry, creds.token)
        return creds.token


def _download_single_file(file_id: str) -> str:
    """Download a single Drive file to SESSION_TEMP_DIR with direct HTTP stream."""
    cached = _RESOLVED_FILES_CACHE.get(file_id)
    if cached and cached.get("path") and os.path.exists(cached["path"]):
        return cached["path"]

    temp_path = os.path.join(SESSION_TEMP_DIR, f"{file_id}.pdf")
    token = _get_drive_token()
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=headers, timeout=45)
            if resp.status_code == 200:
                with open(temp_path, "wb") as f:
                    f.write(resp.content)
                if file_id in _RESOLVED_FILES_CACHE:
                    _RESOLVED_FILES_CACHE[file_id]["path"] = temp_path
                    _RESOLVED_FILES_CACHE[file_id]["bytes"] = resp.content
                return temp_path
        except Exception:
            if attempt == 3:
                return ""
            time.sleep(0.3 * attempt)
    return ""


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
def find_all_pdfs_local_batch(
    folder_path: str,
    so_codes: list,
    match_prefix_8: bool = False,
    sort_by_mtime_desc: bool = False,
) -> dict:
    """
    Search a local/network directory for ALL PDFs matching any of the so_codes.
    Uses multi-threaded concurrent directory crawler (ThreadPool/Queue) to maximize throughput
    over high-latency SMB network shares while preventing blocking single-threaded traversal.
    If match_prefix_8 is True, matches filename's first 8 characters exactly with the job code.
    If sort_by_mtime_desc is True, results for each code are sorted by file modification time
    (newest first) — used by PDF Merger (Trim) TE folder scanning.
    Otherwise (default), results are sorted alphabetically by filename.
    Returns: { so_code: [ {found, file_id, path, name}, ... ] }
    """
    clean_so_codes = list(dict.fromkeys(so.strip() for so in so_codes if so and isinstance(so, str) and so.strip()))
    if not clean_so_codes:
        return {}

    folder_str = str(folder_path).strip()
    if not os.path.exists(folder_str) or not os.path.isdir(folder_str):
        return {so: [{"found": False, "error": f"Folder not found: {folder_path}"}] for so in clean_so_codes}

    results = {so: [] for so in clean_so_codes}
    results_lock = threading.Lock()

    if match_prefix_8:
        lower_job_map = {str(j).strip()[:8].lower(): str(j).strip() for j in clean_so_codes if str(j).strip()}
    else:
        lower_so_map = {so.lower(): so for so in clean_so_codes}

    dirs_queue: queue.Queue[str] = queue.Queue()
    dirs_queue.put(folder_str)

    active_workers = 0
    workers_lock = threading.Lock()
    done_event = threading.Event()

    def worker():
        nonlocal active_workers
        while not done_event.is_set():
            try:
                curr_dir = dirs_queue.get(timeout=0.04)
            except queue.Empty:
                with workers_lock:
                    if active_workers == 0 and dirs_queue.empty():
                        done_event.set()
                        return
                continue

            with workers_lock:
                active_workers += 1

            try:
                subdirs = []
                with os.scandir(curr_dir) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append(entry.path)
                            elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".pdf"):
                                f_name = entry.name
                                try:
                                    f_mtime = entry.stat().st_mtime
                                except OSError:
                                    f_mtime = 0.0

                                if match_prefix_8:
                                    prefix = f_name[:8].lower()
                                    if prefix in lower_job_map:
                                        orig_so = lower_job_map[prefix]
                                        f_path = entry.path
                                        item = {"found": True, "file_id": f_path, "path": f_path, "name": f_name, "mtime": f_mtime}
                                        with results_lock:
                                            _RESOLVED_FILES_CACHE[f_path] = item
                                            results[orig_so].append(item)
                                else:
                                    name_lower = f_name.lower()
                                    for so_lower, orig_so in lower_so_map.items():
                                        if so_lower in name_lower:
                                            f_path = entry.path
                                            item = {"found": True, "file_id": f_path, "path": f_path, "name": f_name, "mtime": f_mtime}
                                            with results_lock:
                                                _RESOLVED_FILES_CACHE[f_path] = item
                                                results[orig_so].append(item)
                        except OSError:
                            pass

                for sd in subdirs:
                    dirs_queue.put(sd)
            except OSError:
                pass
            finally:
                dirs_queue.task_done()
                with workers_lock:
                    active_workers -= 1
                    if active_workers == 0 and dirs_queue.empty():
                        done_event.set()

    max_workers = 32
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for so in results:
        if sort_by_mtime_desc:
            results[so].sort(
                key=lambda x: x.get("mtime") if x.get("mtime") is not None else (os.path.getmtime(x["path"]) if x.get("path") and os.path.exists(x["path"]) else 0),
                reverse=True,
            )
        else:
            results[so].sort(key=lambda x: x.get("name", "").lower())

    return results

def find_all_pdfs_drive_batch(folder_url: str, so_codes: list, match_prefix_8: bool = False) -> dict:
    """
    Search Drive folder hierarchy and download matching PDFs with high-throughput connection pool.
    Caches downloaded bytes directly in RAM (_RESOLVED_FILES_CACHE[fid]['bytes']) and writes to temp disk.
    Returns: { so_code: [ {found, file_id, path, name, bytes, url}, ... ] }
    """
    clean_so_codes = list(dict.fromkeys(so.strip() for so in so_codes if so and isinstance(so, str) and so.strip()))
    results = {so: [] for so in clean_so_codes}

    folder_id = extract_folder_id(folder_url)
    if not folder_id:
        return {so: [{"found": False, "error": "Invalid Drive folder URL or ID"}] for so in clean_so_codes}

    service = _get_thread_drive_service()

    def _safe_list(query_str, p_token):
        nonlocal service
        for attempt in range(1, 6):
            try:
                return service.files().list(
                    q=query_str,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, parents)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=p_token,
                    pageSize=1000
                ).execute()
            except Exception:
                if attempt == 5:
                    return None
                time.sleep(1.0 * attempt)
                try:
                    _thread_local.service = _drive_service()
                    service = _thread_local.service
                except Exception:
                    pass

    # Dynamic upward folder ancestor cache to avoid scanning 1000+ folders in advance
    folder_ancestor_cache: dict[str, str | None] = {}
    folder_cache_lock = threading.Lock()

    def is_in_target(file_parents: list[str]) -> bool:
        if not file_parents:
            return False
        if folder_id in file_parents:
            return True
        for p in file_parents:
            curr = p
            visited = set()
            while curr:
                if curr == folder_id:
                    return True
                with folder_cache_lock:
                    cached_parent = folder_ancestor_cache.get(curr)
                if cached_parent is not None:
                    curr = cached_parent
                    continue
                if curr in visited:
                    break
                visited.add(curr)
                try:
                    f = service.files().get(fileId=curr, fields="parents", supportsAllDrives=True).execute()
                    parents = f.get("parents", [])
                    parent_of_curr = parents[0] if parents else None
                    with folder_cache_lock:
                        folder_ancestor_cache[curr] = parent_of_curr
                    curr = parent_of_curr
                except Exception:
                    with folder_cache_lock:
                        folder_ancestor_cache[curr] = None
                    break
        return False

    def chunker(seq, size):
        return (seq[pos:pos + size] for pos in range(0, len(seq), size))

    lower_so_map = {so.lower(): so for so in clean_so_codes}
    matched_files: dict[str, dict] = {}

    for chunk in chunker(list(lower_so_map.keys()), 30):
        kw_query = " or ".join([f"name contains '{kw}'" for kw in chunk])
        query = f"({kw_query}) and mimeType = 'application/pdf' and trashed = false"

        page_token = None
        while True:
            resp = _safe_list(query, page_token)
            if not resp:
                break

            for item in resp.get("files", []):
                fid = item["id"]
                file_name = item.get("name", "")
                parents = item.get("parents", [])

                if is_in_target(parents):
                    name_lower = file_name.lower()
                    matched_sos = []
                    for so_lower, original_so in lower_so_map.items():
                        if match_prefix_8:
                            if file_name[:8].lower() == so_lower:
                                matched_sos.append(original_so)
                        else:
                            if so_lower in chunk and so_lower in name_lower:
                                matched_sos.append(original_so)

                    if matched_sos:
                        if fid not in matched_files:
                            matched_files[fid] = {
                                "file_id": fid,
                                "name": file_name,
                                "matched_sos": set(matched_sos)
                            }
                        else:
                            matched_files[fid]["matched_sos"].update(matched_sos)

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    # High-performance parallel download with requests.Session (10 workers)
    files_to_download_items = []
    for fid, finfo in matched_files.items():
        cached = _RESOLVED_FILES_CACHE.get(fid)
        if not (cached and cached.get("bytes") and cached.get("path") and os.path.exists(cached["path"])):
            files_to_download_items.append(finfo)

    if files_to_download_items:
        token = _get_drive_token()
        sess = requests.Session()
        sess.headers.update({"Authorization": f"Bearer {token}"})

        def _dl_worker(info_item):
            fid = info_item["file_id"]
            fname = info_item["name"]
            url = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&supportsAllDrives=true"
            temp_path = os.path.join(SESSION_TEMP_DIR, f"{fid}.pdf")
            for attempt in range(1, 4):
                try:
                    resp = sess.get(url, timeout=60)
                    if resp.status_code == 200:
                        b = resp.content
                        with open(temp_path, "wb") as fp:
                            fp.write(b)
                        entry = {
                            "found": True,
                            "file_id": fid,
                            "path": temp_path,
                            "name": fname,
                            "bytes": b,
                            "url": f"https://drive.google.com/file/d/{fid}/view",
                        }
                        return fid, entry
                except Exception:
                    if attempt == 3:
                        return fid, {
                            "found": False,
                            "file_id": fid,
                            "path": "",
                            "name": fname,
                            "bytes": b"",
                            "url": f"https://drive.google.com/file/d/{fid}/view",
                        }
                    time.sleep(0.3 * attempt)
            return fid, {
                "found": False,
                "file_id": fid,
                "path": "",
                "name": fname,
                "bytes": b"",
                "url": f"https://drive.google.com/file/d/{fid}/view",
            }

        max_workers = min(len(files_to_download_items), 10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_map = {executor.submit(_dl_worker, item): item["file_id"] for item in files_to_download_items}
            for fut in concurrent.futures.as_completed(fut_map):
                fid, entry = fut.result()
                _RESOLVED_FILES_CACHE[fid] = entry

    # Populate results map for each SO code
    for fid, info in matched_files.items():
        pdf_item = _RESOLVED_FILES_CACHE.get(fid)
        if not pdf_item:
            continue
        for so in info["matched_sos"]:
            if not any(f.get("file_id") == fid for f in results[so]):
                results[so].append(pdf_item)

    for so in results:
        results[so].sort(key=lambda x: x.get("name", "").lower())

    return results




def find_all_pdfs_batch(
    folder_input: str,
    so_codes: list,
    is_drive: bool,
    match_prefix_8: bool = False,
    sort_by_mtime_desc: bool = False,
) -> dict:
    """
    Unified batch entry point.
    sort_by_mtime_desc is forwarded to the local implementation only;
    Drive mode always uses name-based sorting.
    """
    if is_drive:
        return find_all_pdfs_drive_batch(folder_input, so_codes, match_prefix_8=match_prefix_8)
    else:
        return find_all_pdfs_local_batch(
            folder_input, so_codes,
            match_prefix_8=match_prefix_8,
            sort_by_mtime_desc=sort_by_mtime_desc,
        )



