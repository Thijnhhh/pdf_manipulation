"""
backend/bridge.py
─────────────────────────────────────────────────────────────
BridgeApi — exposes Python methods to the JS frontend via
pywebview's js_api mechanism.

Each public method on BridgeApi becomes callable as:
    window.pywebview.api.method_name(args)
which returns a native JS Promise.

Threading model:
  Long-running methods (pre_fetch_all_pdfs, export_assembly,
  run_auto_detect) are executed on a background thread so
  pywebview's main loop stays responsive. Progress is pushed
  to JS via window.evaluate_js().
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import concurrent.futures
import functools
import json
import os
import subprocess
import threading
import time
import traceback
from typing import Any

import pymupdf as fitz
import webview

from backend import updater
from backend.file_resolver import (
    _RESOLVED_FILES_CACHE,
    find_all_pdfs_batch,
    get_file_source,
    parse_so,
)
from backend.pdf_combiner import combine_pdfs, search_files as combiner_search
from backend.pdf_deduplicator import deduplicate_batch
from backend.pdf_detector import (
    AUTO_DETECT_RAM_CACHE,
    clear_ram_cache,
    run_auto_detect_all,
    validate_so_flags,
)
from backend.pdf_processor import (
    _page_to_temp_img,
    execute_export,
    get_page_hires,
    get_page_range_thumbnails,
    get_pdf_meta,
    set_temp_dir as set_pdf_temp_dir,
)
from backend.sheets_service import get_all_initial_data
from backend.window_utils import flash_app_window


# ── Bridge Tracing ───────────────────────────────────────────
# Set to False to silence all trace output after manual testing.
TRACING_ENABLED = False   

_MAX_ARG_LEN = 40  # max chars per argument value in trace output


def _fmt_arg(v) -> str:
    """Format a single argument value for trace output."""
    s = repr(v)
    if len(s) > _MAX_ARG_LEN:
        s = s[:_MAX_ARG_LEN - 3] + "..."
    return s


def _trace(method):
    """
    Decorator for BridgeApi public methods.
    - Async methods (that call _run_in_thread internally): logs method name +
      args + wall-clock processing time from inside the thread.
    - Sync methods (return directly): logs immediately after the call returns.
    - Does nothing when TRACING_ENABLED = False.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if not TRACING_ENABLED:
            return method(self, *args, **kwargs)

        # Build short argument string from the first 3 positional args
        import inspect
        params = list(inspect.signature(method).parameters.keys())[1:]  # skip 'self'
        sig_parts = []
        for i, val in enumerate(args[:3]):
            name = params[i] if i < len(params) else f"arg{i}"
            sig_parts.append(f"{name}={_fmt_arg(val)}")
        if len(args) > 3:
            sig_parts.append(f"... +{len(args) - 3} more")
        sig_str = ", ".join(sig_parts)

        # Track whether the method dispatched to a thread
        thread_dispatched = {"value": False}

        def patched_run_in_thread(target, *a, **kw):
            thread_dispatched["value"] = True
            original_target = target

            def timed_target():
                t0 = time.perf_counter()
                try:
                    original_target()
                finally:
                    ms = (time.perf_counter() - t0) * 1000
                    print(f"[BRIDGE] >> {method.__name__}({sig_str}) | {ms:.1f}ms",
                          flush=True)

            _run_in_thread_orig(timed_target, *a, **kw)

        global _run_in_thread
        _run_in_thread_orig = _run_in_thread
        _run_in_thread = patched_run_in_thread
        try:
            t0_sync = time.perf_counter()
            result = method(self, *args, **kwargs)
        finally:
            _run_in_thread = _run_in_thread_orig

        # If no thread was dispatched, it's a sync method — log now
        if not thread_dispatched["value"]:
            ms = (time.perf_counter() - t0_sync) * 1000
            print(f"[BRIDGE] >> {method.__name__}({sig_str}) | {ms:.1f}ms",
                  flush=True)

        return result

    return wrapper


# ── Internal thread launcher ─────────────────────────────────

def _run_in_thread(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()


# ── BridgeApi ────────────────────────────────────────────────

class BridgeApi:
    """
    All public methods are auto-exposed to JS by pywebview.
    """

    _window: webview.Window | None = None
    _auto_detect_cancel: threading.Event = threading.Event()

    def _eval(self, js: str) -> None:
        """Safely evaluate JS in the main window."""
        if self._window:
            self._window.evaluate_js(js)

    @_trace
    def set_temp_dir(self, temp_dir: str) -> None:
        """Pass the temp directory down to the pdf_processor."""
        set_pdf_temp_dir(temp_dir)

    @_trace
    def flash_window(self) -> bool:
        """
        Flash the taskbar icon to attract user attention when a task completes
        while the app window is in the background.
        """
        return flash_app_window(self._window, window_title="PDF Manipulation Tool")

    # ── 1. Initial Data ──────────────────────────────────────

    @_trace
    def get_initial_data(self, line: str = "PFL") -> dict:
        """
        Called once on app load (or module switch).
        `line` filters which presets/SOL data to return:
          "PFL"     → PFL presets and SOL lists (default).
          "RFID HT" → RFID HT presets and SOL entries with job/rb/flag columns.
        """
        try:
            local_ver = updater._read_local_version()
            data = get_all_initial_data(line=line)
            return {"success": True, "presets": data["presets"], "solMap": data["solMap"], "current_version": local_ver}
        except Exception as exc:
            return {"success": False, "error": str(exc), "presets": [], "solMap": {}}


    @_trace
    def get_local_version(self) -> str:
        try:
            return updater._read_local_version()
        except Exception:
            return "?"

    # ── 2. Fast Metadata Fetch (Instant Load) ────────────────

    @_trace
    def fetch_pdf_meta(
        self,
        sol_code:     str,
        type_id:      str,
        folder_input: str,
        is_drive:     bool,
    ) -> None:
        """
        Async: returns PDF metadata (name, total page count) for ALL files
        matching the 9-digit SO derived from sol_code.
        Pushes one _onPdfMetaReady event per found file.
        If SOL pattern is invalid, pushes a single not-found event.
        """
        def _task():
            so_code = parse_so(sol_code)
            if not so_code:
                # Invalid SOL pattern — push not-found and exit
                res = {"found": False, "sol": sol_code, "typeId": type_id,
                       "error": "Invalid SOL pattern"}
                payload  = _json_str(res)
                sol_esc  = _js_str(sol_code)
                type_esc = _js_str(type_id)
                self._eval(f"window._onPdfMetaReady({sol_esc}, {type_esc}, {payload})")
                return

            try:
                results_map = find_all_pdfs_batch(folder_input, [so_code], is_drive)
                file_list = results_map.get(so_code, [])
            except Exception as exc:
                res = {"found": False, "sol": sol_code, "typeId": type_id, "error": str(exc)}
                payload  = _json_str(res)
                sol_esc  = _js_str(sol_code)
                type_esc = _js_str(type_id)
                self._eval(f"window._onPdfMetaReady({sol_esc}, {type_esc}, {payload})")
                return

            found_files = [f for f in file_list if f.get("found")]
            if not found_files:
                res = {"found": False, "sol": sol_code, "typeId": type_id}
                payload  = _json_str(res)
                sol_esc  = _js_str(sol_code)
                type_esc = _js_str(type_id)
                self._eval(f"window._onPdfMetaReady({sol_esc}, {type_esc}, {payload})")
                return

            # Push one event per matching file
            for file_result in found_files:
                try:
                    source = file_result.get("bytes") or file_result.get("path")
                    meta   = get_pdf_meta(source)
                    f_id   = file_result.get("file_id") or file_result.get("path") or file_result.get("url") or file_result.get("name", "")
                    res    = {
                        "found":      True,
                        "sol":        sol_code,
                        "typeId":     type_id,
                        "name":       file_result.get("name", ""),
                        "fileId":     f_id,
                        "fullPath":   file_result.get("path") or file_result.get("url") or file_result.get("name", ""),
                        "pageCount":  meta["page_count"]
                    }
                except Exception as exc:
                    res = {"found": False, "sol": sol_code, "typeId": type_id, "error": str(exc)}

                payload  = _json_str(res)
                sol_esc  = _js_str(sol_code)
                type_esc = _js_str(type_id)
                self._eval(f"window._onPdfMetaReady({sol_esc}, {type_esc}, {payload})")

        _run_in_thread(_task)

    @_trace
    def pre_fetch_all_pdfs(
        self,
        sol_list_json:      str,
        folder_inputs_json: str,
        is_drive_json:      str,
        job_map_json:       str = "{}",
    ) -> None:
        """
        Async: searches and extracts metadata for ALL SOLs across ALL PDF types.
        Uses SO-based deduplication for SOview, and Job-code deduplication for Layout (RFID HT).

        Pushes progress: window._onPreFetchProgress({ current, total, so, typeId })
        Pushes per-file: window._onPdfMetaReady(sol, typeId, result) — one per found file.
        Pushes completion: window._onPreFetchDone()
        """
        def _task():
            try:
                raw_sol_list  = json.loads(sol_list_json)
                sol_list      = list(dict.fromkeys(s.strip() for s in raw_sol_list if s and isinstance(s, str) and s.strip()))
                folder_inputs = json.loads(folder_inputs_json)
                is_drive_map  = json.loads(is_drive_json)
                job_map       = json.loads(job_map_json) if job_map_json else {}

                pdf_type_ids = list(folder_inputs.keys())  # ["soview", "layout"]

                # Build SO -> SOLs map (deduplicate)
                so_to_sols = {}
                for sol in sol_list:
                    so = parse_so(sol)
                    if not so:
                        continue
                    if sol not in so_to_sols.setdefault(so, []):
                        so_to_sols[so].append(sol)

                unique_sos   = list(so_to_sols.keys())
                total_ops    = len(pdf_type_ids)
                current_op   = 0

                for type_id in pdf_type_ids:
                    current_op += 1
                    p_payload = _json_str({
                        "current": current_op,
                        "total":   total_ops,
                        "so":      "Batch Search",
                        "sol":     "Batch Search",
                        "typeId":  type_id
                    })
                    self._eval(f"window._onPreFetchProgress({p_payload})")

                    try:
                        is_layout_rfid = bool(type_id == "layout" and job_map)
                        if is_layout_rfid:
                            search_keys = list(dict.fromkeys(str(j).strip() for j in job_map.values() if str(j).strip()))
                        else:
                            search_keys = unique_sos

                        results_map = find_all_pdfs_batch(
                            folder_inputs[type_id],
                            search_keys,
                            is_drive_map.get(type_id, False),
                            match_prefix_8=is_layout_rfid,
                        )
                    except Exception as exc:
                        # Push not-found for all SOLs in this group
                        for sol in sol_list:
                            res = {"found": False, "sol": sol, "typeId": type_id, "error": str(exc)}
                            self._eval(f"window._onPdfMetaReady({_js_str(sol)}, {_js_str(type_id)}, {_json_str(res)})")
                        continue

                    if type_id == "layout" and job_map:
                        # RFID HT Layout: map by job_code
                        for sol in sol_list:
                            job_code = str(job_map.get(sol, "")).strip()
                            found_files = [f for f in results_map.get(job_code, []) if f.get("found")]
                            if not found_files:
                                res = {"found": False, "sol": sol, "typeId": type_id}
                                self._eval(f"window._onPdfMetaReady({_js_str(sol)}, {_js_str(type_id)}, {_json_str(res)})")
                                continue

                            for file_result in found_files:
                                try:
                                    source = file_result.get("bytes") or file_result.get("path")
                                    meta   = get_pdf_meta(source)
                                    f_id = file_result.get("file_id") or file_result.get("path") or file_result.get("url") or file_result.get("name", "")
                                    file_res = {
                                        "found":     True,
                                        "sol":       sol,
                                        "typeId":    type_id,
                                        "name":      file_result.get("name", ""),
                                        "fileId":    f_id,
                                        "fullPath":  file_result.get("path") or file_result.get("url") or file_result.get("name", ""),
                                        "pageCount": meta["page_count"]
                                    }
                                except Exception as exc:
                                    file_res = {"found": False, "sol": sol, "typeId": type_id, "error": str(exc)}

                                self._eval(f"window._onPdfMetaReady({_js_str(sol)}, {_js_str(type_id)}, {_json_str(file_res)})")
                    else:
                        # PFL mode or SOview: map by base SO
                        for so, sols_for_so in so_to_sols.items():
                            found_files = results_map.get(so, [])
                            found_files = [f for f in found_files if f.get("found")]

                            if not found_files:
                                for sol in sols_for_so:
                                    res = {"found": False, "sol": sol, "typeId": type_id}
                                    self._eval(f"window._onPdfMetaReady({_js_str(sol)}, {_js_str(type_id)}, {_json_str(res)})")
                                continue

                            for file_result in found_files:
                                try:
                                    source = file_result.get("bytes") or file_result.get("path")
                                    meta   = get_pdf_meta(source)
                                    f_id = file_result.get("file_id") or file_result.get("path") or file_result.get("url") or file_result.get("name", "")
                                    file_res = {
                                        "found":     True,
                                        "typeId":    type_id,
                                        "name":      file_result.get("name", ""),
                                        "fileId":    f_id,
                                        "fullPath":  file_result.get("path") or file_result.get("url") or file_result.get("name", ""),
                                        "pageCount": meta["page_count"]
                                    }
                                except Exception as exc:
                                    file_res = {"found": False, "typeId": type_id, "error": str(exc)}

                                for sol in sols_for_so:
                                    res = dict(file_res)
                                    res["sol"] = sol
                                    self._eval(f"window._onPdfMetaReady({_js_str(sol)}, {_js_str(type_id)}, {_json_str(res)})")

            except Exception:
                pass

            self._eval("window._onPreFetchDone()")

        _run_in_thread(_task)

    # ── 3. Range Thumbnail Fetch (On-Demand Scrolling) ──────

    @_trace
    def fetch_page_range(
        self,
        sol_code:     str,
        type_id:      str,
        file_id:      str,   # Unique file_id from cache (or local path / folder_input fallback)
        is_drive:     bool,
        start_page:   int,
        count:        int,
        file_idx:     int = 0,
        dpi:          int = 80,
    ) -> None:
        """
        Async: renders requested page range for the specific file identified by file_id.
        Pushes: window._onPageRangeReady(sol, typeId, startPage, pagesMap, fileIdx)
        """
        def _task():
            try:
                source = get_file_source(file_id)
                if not source:
                    # Fallback for folder paths: parse SO and find the Nth file
                    so_code = parse_so(sol_code)
                    if not so_code:
                        so_code = sol_code
                    results_map = find_all_pdfs_batch(file_id, [so_code], is_drive)
                    file_list = results_map.get(so_code, [])
                    if 0 <= file_idx < len(file_list):
                        file_result = file_list[file_idx]
                        if file_result.get("found"):
                            source = file_result.get("bytes") or file_result.get("path")

                if source:
                    res_map = get_page_range_thumbnails(source, start_page, count, dpi=dpi)
                else:
                    res_map = {}
            except Exception as exc:
                print(f"[fetch_page_range] Error: {exc}")
                res_map = {}

            payload  = _json_str(res_map)
            sol_esc  = _js_str(sol_code)
            type_esc = _js_str(type_id)
            self._eval(f"window._onPageRangeReady({sol_esc}, {type_esc}, {start_page}, {payload}, {file_idx})")

        _run_in_thread(_task)

    # ── 3b. Zero-Network Instant Auto Detect Preview ─────────

    @_trace
    def fetch_auto_detect_preview(
        self,
        sol_code:          str,
        sol_assembly_json: str,
        dpi:               int = 96,
    ) -> None:
        """
        Async: instant thumbnail rendering directly from AUTO_DETECT_RAM_CACHE in RAM
        with ZERO network I/O.
        Pushes: window._onAutoDetectPreviewReady(sol, cardsMap)
        """
        def _task():
            cards_map: dict[str, str] = {}
            try:
                assembly_types: dict[str, list[dict]] = json.loads(sol_assembly_json) if sol_assembly_json else {}

                for type_id, entries in assembly_types.items():
                    ram_key = f"{sol_code}_{type_id}"
                    cached_bytes = AUTO_DETECT_RAM_CACHE.get(ram_key)

                    if cached_bytes:
                        try:
                            doc = fitz.open(stream=cached_bytes, filetype="pdf")
                            try:
                                for seq_idx, entry in enumerate(entries):
                                    file_idx = entry.get("fileIdx", 0)
                                    orig_page_num = entry.get("pageIndex", 1)
                                    card_id = f"ad-preview-card-{type_id}-{file_idx}-{orig_page_num}"

                                    if 0 <= seq_idx < len(doc):
                                        page = doc[seq_idx]
                                        img_url = _page_to_temp_img(page, dpi=dpi, fmt="jpeg", quality=80)
                                        cards_map[card_id] = img_url
                            finally:
                                doc.close()
                        except Exception as e_render:
                            print(f"[fetch_auto_detect_preview] Render error for {ram_key}: {e_render}")
            except Exception as exc:
                print(f"[fetch_auto_detect_preview] Error: {exc}")

            payload = _json_str(cards_map)
            sol_esc = _js_str(sol_code)
            self._eval(f"window._onAutoDetectPreviewReady({sol_esc}, {payload})")

        _run_in_thread(_task)

    @_trace
    def get_page_hires_by_path(
        self,
        sol_code:   str,
        type_id:    str,
        page_index: int,
        file_id:    str,
    ) -> None:
        """
        Async: renders a single page at 100 DPI directly from resolved file source.
        Pushes: window._onHiresReady(sol, typeId, pageIndex, dataUrl)
        """
        def _task():
            data_url = ""
            try:
                source = get_file_source(file_id)
                if source:
                    data_url = get_page_hires(source, page_index, dpi=100)
            except Exception as exc:
                print(f"[get_page_hires_by_path] Error: {exc}")
                data_url = ""

            sol_esc  = _js_str(sol_code)
            type_esc = _js_str(type_id)
            url_esc  = _js_str(data_url)
            self._eval(
                f"window._onHiresReady({sol_esc}, {type_esc}, {page_index}, {url_esc})"
            )

        _run_in_thread(_task)

    # ── 4. Export Assembly ───────────────────────────────────

    @_trace
    def export_assembly(
        self,
        assembly_json: str,
        folder_inputs_json: str,
        is_drive_json: str,
        output_path: str,
        export_mode: str,
    ) -> None:
        """
        Async: compiles the selected pages into output PDF(s) and pushes:
            window._onExportProgress(pct)   — 0-100
            window._onExportDone(result)    — { success, files } or { success, error }

        assembly_json:      JSON string of { sol: [{ typeId, pageIndex, fileIdx }, ...] }
        folder_inputs_json: JSON string of { typeId: folderInput } (fallback only)
        is_drive_json:      JSON string of { typeId: bool }        (fallback only)
        """
        def _task():
            try:
                assembly      = json.loads(assembly_json)
                folder_inputs = json.loads(folder_inputs_json)
                is_drive_map  = json.loads(is_drive_json)

                # Build source_cache: { "sol_typeId_fileIdx": path_or_bytes }
                source_cache: dict[str, Any] = {}

                # ── Fast path: resolve all items that are already in _RESOLVED_FILES_CACHE ──
                missing_by_tid: dict[str, set[str]] = {}  # tid → set of so_codes needing batch resolve

                for sol, items in assembly.items():
                    for item in items:
                        tid  = item["typeId"]
                        fidx = item.get("fileIdx", 0)
                        file_id   = item.get("fileId") or ""
                        cache_key = f"{sol}_{tid}_{fidx}"

                        if cache_key in source_cache:
                            continue

                        if file_id and file_id in _RESOLVED_FILES_CACHE:
                            cached = _RESOLVED_FILES_CACHE[file_id]
                            source_cache[cache_key] = cached.get("path") or cached.get("bytes")
                            continue

                        # Track which SO codes still need a fallback scan per type_id
                        so_code = parse_so(sol) or sol
                        missing_by_tid.setdefault(tid, set()).add(so_code)

                # ── Slow fallback: one batch scan per type_id (not one per SOL) ──
                # find_all_pdfs_batch issues a single Drive API query for all SO codes.
                batch_results: dict[str, dict[str, list]] = {}  # tid → { so_code: [file_dicts] }
                for tid, so_codes in missing_by_tid.items():
                    batch_results[tid] = find_all_pdfs_batch(
                        folder_inputs[tid],
                        list(so_codes),
                        is_drive_map.get(tid, False),
                    )

                # ── Fill source_cache from batch results ──
                for sol, items in assembly.items():
                    for item in items:
                        tid  = item["typeId"]
                        fidx = item.get("fileIdx", 0)
                        cache_key = f"{sol}_{tid}_{fidx}"

                        if cache_key in source_cache:
                            continue  # already resolved in fast path

                        so_code  = parse_so(sol) or sol
                        res_list = batch_results.get(tid, {}).get(so_code, [])
                        if 0 <= fidx < len(res_list):
                            res = res_list[fidx]
                            if res.get("found"):
                                source_cache[cache_key] = res.get("path") or res.get("bytes")

                def progress_cb(pct: int):
                    self._eval(f"window._onExportProgress({pct})")

                result = execute_export(
                    assembly_data=assembly,
                    source_cache=source_cache,
                    output_path=output_path,
                    mode=export_mode,
                    progress_cb=progress_cb,
                )
            except Exception as exc:
                try:
                    import os
                    dl_folder = os.path.join(os.path.expanduser('~'), 'Downloads')
                    result = execute_export(
                        assembly_data=assembly,
                        source_cache=source_cache,
                        output_path=dl_folder,
                        mode=export_mode,
                        progress_cb=progress_cb,
                    )
                    result["fallback_used"] = True
                    result["fallback_path"] = dl_folder
                except Exception as exc2:
                    result = {"success": False, "error": str(exc)}

            payload = _json_str(result)
            self._eval(f"window._onExportDone({payload})")

        _run_in_thread(_task)

    @_trace
    def export_from_auto_detect(
        self,
        assembly_json: str,
        output_path: str,
        export_mode: str,
    ) -> None:
        """
        Async: exports using the pre-extracted sub-doc bytes stored in
        AUTO_DETECT_RAM_CACHE after run_auto_detect completes.

        assembly_json: JSON string of { sol: { typeId: [{ pageIndex, fileIdx, ... }] } }
        output_path:   destination folder
        export_mode:   'split' | 'merge'

        Strict: if cache is empty (e.g. user navigated Back), returns an error
        immediately without re-running detection.

        Pushes:
            window._onExportProgress(pct)
            window._onExportDone(result)
        """
        def _task():
            try:
                if not AUTO_DETECT_RAM_CACHE:
                    raise ValueError(
                        "Auto Detect result cache is empty. "
                        "Please re-run Auto Detect before exporting."
                    )

                assembly_raw = json.loads(assembly_json)

                # Convert adState.assembly { sol: { typeId: [entries] } }
                # → execute_export assembly_data format { sol: [{ typeId, pageIndex, fileIdx }] }
                # AND build source_cache from RAM bytes, keyed by "sol_typeId_fileIdx".
                assembly_data: dict[str, list[dict]] = {}
                source_cache:  dict[str, Any] = {}

                for sol, types_map in assembly_raw.items():
                    items_out = []
                    for type_id, entries in types_map.items():
                        ram_key = f"{sol}_{type_id}"
                        if ram_key in AUTO_DETECT_RAM_CACHE:
                            # All pages for this sol+type are already in one sub-doc.
                            # Map each entry back to its sequential position in the sub-doc.
                            for seq_idx, entry in enumerate(entries):
                                out_page_idx = seq_idx + 1  # 1-based in the sub-doc
                                flat_key = f"{sol}_{type_id}_0"
                                source_cache[flat_key] = AUTO_DETECT_RAM_CACHE[ram_key]
                                items_out.append({
                                    "typeId":    type_id,
                                    "pageIndex": out_page_idx,
                                    "fileIdx":   0,
                                })
                        else:
                            # Partial cache miss — skip this sol/type
                            print(f"[export_from_auto_detect] Cache miss for {sol}/{type_id}")

                    if items_out:
                        assembly_data[sol] = items_out

                if not assembly_data:
                    raise ValueError(
                        "No cached pages found. Please re-run Auto Detect."
                    )

                def progress_cb(pct: int):
                    self._eval(f"window._onExportProgress({pct})")

                result = execute_export(
                    assembly_data=assembly_data,
                    source_cache=source_cache,
                    output_path=output_path,
                    mode=export_mode,
                    progress_cb=progress_cb,
                )
            except Exception as exc:
                try:
                    import os
                    dl_folder = os.path.join(os.path.expanduser('~'), 'Downloads')
                    result = execute_export(
                        assembly_data=assembly_data,
                        source_cache=source_cache,
                        output_path=dl_folder,
                        mode=export_mode,
                        progress_cb=progress_cb,
                    )
                    result["fallback_used"] = True
                    result["fallback_path"] = dl_folder
                except Exception as exc2:
                    result = {"success": False, "error": str(exc)}

            payload = _json_str(result)
            self._eval(f"window._onExportDone({payload})")

        _run_in_thread(_task)

    @_trace
    def clear_auto_detect_ram_cache(self) -> None:
        """
        Release the AUTO_DETECT_RAM_CACHE. Called by JS when the user
        navigates Back from the Auto Detect results screen.
        """
        clear_ram_cache()

    # ── 5. Auto Detect ───────────────────────────────────────

    @_trace
    def run_auto_detect(
        self,
        sol_list_json:      str,
        folder_inputs_json: str,
        is_drive_json:      str,
        config_json:        str,
        exceptions_json:    str,
        ocr_flag:           bool = False,
        flags_json:         str = "{}",
        job_map_json:       str = "{}",
        line_str:           str = "PFL",
    ) -> None:
        """
        Async: runs the Auto Detect engine for all SOLs and PDF types.

        Args (new):
            flags_json:   JSON string of { sol: bool } flag map (RFID HT only).
                          Built by validate_so_flags() from the sheet entries.
                          Omit or pass "{}" for PFL (all SOLs default to False).
            job_map_json: JSON string of { sol: job_code } (RFID HT only).
                          Used for layout file matching by job code.
                          Omit or pass "{}" for PFL.
            line_str:     Line identifier (e.g. "PFL", "RFID SB"). Default "PFL".

        Pushes to JS:
          window._onAutoDetectProgress({ sol, typeId, method, page, totalPages })
          window._onAutoDetectDone({ success, assembly, errors })
          window._onAutoDetectCancelled()   — if cancel was requested
        """
        # Reset cancel flag for a fresh run
        self._auto_detect_cancel.clear()

        def _task():
            try:
                raw_sol_list  = json.loads(sol_list_json)
                sol_list      = list(dict.fromkeys(s.strip() for s in raw_sol_list if s and isinstance(s, str) and s.strip()))
                folder_inputs = json.loads(folder_inputs_json)
                is_drive_map  = json.loads(is_drive_json)
                config        = json.loads(config_json)
                exceptions    = json.loads(exceptions_json)

                # Parse RFID HT optional parameters (safe for PFL — both default to {})
                raw_flags = json.loads(flags_json) if flags_json else {}
                job_map   = json.loads(job_map_json) if job_map_json else {}

                # raw_flags may be a flat { sol: bool } map (already validate_so_flags output)
                # or a list of RFID HT entries [{sol, job, rb, flag}] — handle both.
                if isinstance(raw_flags, list):
                    sol_flags = validate_so_flags(raw_flags)
                elif isinstance(raw_flags, dict):
                    sol_flags = {k: bool(v) for k, v in raw_flags.items()}
                else:
                    sol_flags = {}

                def progress_cb(payload: dict):
                    p = _json_str(payload)
                    self._eval(f"window._onAutoDetectProgress({p})")

                result = run_auto_detect_all(
                    sol_list      = sol_list,
                    folder_inputs = folder_inputs,
                    is_drive_map  = is_drive_map,
                    config        = config,
                    exceptions    = exceptions,
                    cancel_event  = self._auto_detect_cancel,
                    progress_cb   = progress_cb,
                    ocr_flag      = bool(ocr_flag),
                    sol_flags     = sol_flags,
                    job_map       = job_map,
                    trace_enabled = TRACING_ENABLED,
                    line          = line_str,
                )

                if self._auto_detect_cancel.is_set():
                    self._eval("window._onAutoDetectCancelled()")
                    return

                payload = _json_str({"success": True, "tracing_enabled": TRACING_ENABLED, **result})
                self._eval(f"window._onAutoDetectDone({payload})")
                self.flash_window()

            except Exception as exc:
                traceback.print_exc()
                err_msg = str(exc)
                if not err_msg or err_msg.isdigit():
                    err_msg = f"{type(exc).__name__}: {exc}"
                payload = _json_str({"success": False, "error": err_msg,
                                     "assembly": {}, "errors": []})
                self._eval(f"window._onAutoDetectDone({payload})")
                self.flash_window()

        _run_in_thread(_task)

    @_trace
    def cancel_auto_detect(self) -> None:
        """Signal the running Auto Detect to stop after its current page."""
        self._auto_detect_cancel.set()

    # ── 5b. Trim Adidas Auto Detect ──────────────────────────

    @_trace
    def run_auto_detect_trim(
        self,
        sol_list_json:      str,
        folder_inputs_json: str,
        output_path:        str,
    ) -> None:
        """
        Async: Trim Adidas detection pipeline.

        Stages:
          1. BigQuery CALL get_TE → ORDERED_ITEM + list_TE per SOL.
          2. Validate BQ data → report missing_ordered_item / missing_te.
          3. Scan SOview folder (all SOLs, even missing BQ) using 3-pattern fitz.
          4. Scan TE folder (only SOLs with valid BQ data) using barcode text scan;
             files sorted newest-first by mtime.
          5. Write sub-docs to AUTO_DETECT_RAM_CACHE.
          6. Push _onAutoDetectDone with { success, assembly, errors,
                                           bq_metadata, missing_data }.

        folder_inputs_json: JSON { "soview": "<path>", "layout": "<path>" }
          (frontend uses "layout" key for the TE folder to reuse existing UI).
        """
        self._auto_detect_cancel.clear()

        def _task():
            try:
                from backend.trim_service import (
                    fetch_bigquery_te,
                    validate_data_bigquery,
                    scan_soview_pdf_3patterns,
                    scan_te_pdf_barcode,
                )
                from backend.file_resolver import (
                    find_all_pdfs_local_batch,
                    parse_so,
                )
                from backend.pdf_detector import (
                    AUTO_DETECT_RAM_CACHE,
                    clear_ram_cache,
                    AutoDetectTracer,
                )
                import pymupdf as fitz

                raw_sol_list   = json.loads(sol_list_json)
                sol_list       = list(dict.fromkeys(
                    s.strip() for s in raw_sol_list
                    if s and isinstance(s, str) and s.strip()
                ))
                folder_inputs  = json.loads(folder_inputs_json)
                soview_folder  = folder_inputs.get("soview", "")
                te_folder      = folder_inputs.get("layout", "")   # UI sends 2nd folder as "layout"

                tracer = AutoDetectTracer(
                    enabled=TRACING_ENABLED,
                    title="Trim Adidas Auto Detect Performance Breakdown",
                    prefix="TRIM",
                )

                def _progress(payload: dict):
                    p = _json_str(payload)
                    self._eval(f"window._onAutoDetectProgress({p})")

                _progress({"sol": "Batch Search", "typeId": "bigquery",
                           "method": "Fetching BigQuery data…",
                           "fileName": "", "currentFile": 0, "totalFiles": 0})

                # ── Stage 1: BigQuery ────────────────────────────────────
                pairs: list[tuple[int, int]] = []
                for sol in sol_list:
                    so = parse_so(sol)
                    if so:
                        line_part = sol.split("-", 1)[1] if "-" in sol else "1"
                        try:
                            pairs.append((int(so), int(line_part)))
                        except ValueError:
                            pass

                t_bq = time.perf_counter()
                bq_records: list[dict] = []
                bq_error: str = ""
                try:
                    bq_records = fetch_bigquery_te(pairs)
                    tracer.record(
                        phase="1. BigQuery Fetch",
                        target="Stored Procedure get_TE",
                        t0=t_bq,
                        details=f"{len(pairs)} pairs sent, {len(bq_records)} rows returned",
                    )
                except Exception as bq_exc:
                    bq_error = str(bq_exc)
                    tracer.record(
                        phase="1. BigQuery Fetch",
                        target="Stored Procedure get_TE",
                        t0=t_bq,
                        details=f"FAILED: {bq_exc}",
                    )
                    print(f"[run_auto_detect_trim] BigQuery error: {bq_exc}")

                # ── Stage 2: Validate ────────────────────────────────────
                t_val = time.perf_counter()
                sol_metadata, missing_data = validate_data_bigquery(bq_records, sol_list)
                tracer.record(
                    phase="2. BQ Validate",
                    target="validate_data_bigquery",
                    t0=t_val,
                    details=f"{len(sol_metadata)} valid, {len(missing_data.get('missing_ordered_item', []))} missing OI, {len(missing_data.get('missing_te', []))} missing TE",
                )

                # ── Stage 3: SOview batch file find ─────────────────────
                unique_sos: list[str] = list(dict.fromkeys(
                    parse_so(sol) for sol in sol_list if parse_so(sol)
                ))

                _progress({"sol": "Batch Search", "typeId": "soview",
                           "method": "Scanning SOview folder…",
                           "fileName": "", "currentFile": 0, "totalFiles": 0})

                t_sv_search = time.perf_counter()
                soview_files: dict = {}
                if soview_folder:
                    soview_files = find_all_pdfs_local_batch(
                        soview_folder, unique_sos, sort_by_mtime_desc=False
                    )
                sv_found = sum(len([f for f in flist if f.get("found")]) for flist in soview_files.values())
                tracer.record(
                    phase="3. Batch Search",
                    target="SOview (UNC/Local)",
                    t0=t_sv_search,
                    details=f"{len(unique_sos)} unique SOs, {sv_found} files found",
                )

                # ── Stage 4: TE batch file find (newest first) ───────────
                _progress({"sol": "Batch Search", "typeId": "layout",
                           "method": "Scanning TE folder…",
                           "fileName": "", "currentFile": 0, "totalFiles": 0})

                # Build ordered_item → file search map
                ordered_items_needed: list[str] = list(dict.fromkeys(
                    meta["ordered_item"]
                    for meta in sol_metadata.values()
                    if meta.get("ordered_item")
                ))

                t_te_search = time.perf_counter()
                te_files: dict = {}
                if te_folder and ordered_items_needed:
                    te_files = find_all_pdfs_local_batch(
                        te_folder, ordered_items_needed, sort_by_mtime_desc=True
                    )
                te_found = sum(len([f for f in flist if f.get("found")]) for flist in te_files.values())
                tracer.record(
                    phase="4. Batch Search",
                    target="TE (UNC/Local, Newest)",
                    t0=t_te_search,
                    details=f"{len(ordered_items_needed)} unique OIs, {te_found} files found",
                )

                # ── Stage 5: Detection loop ──────────────────────────────
                assembly: dict[str, dict] = {sol: {"soview": [], "layout": []} for sol in sol_list}
                errors:   list[dict]      = []

                # SOview: group SOLs by SO → scan each unique file once
                so_to_sols: dict[str, list[str]] = {}
                for sol in sol_list:
                    so = parse_so(sol)
                    if so:
                        so_to_sols.setdefault(so, []).append(sol)

                soview_tasks = []
                for so, sols_for_so in so_to_sols.items():
                    found_files = [f for f in soview_files.get(so, []) if f.get("found")]
                    for file_idx, file_entry in enumerate(found_files):
                        soview_tasks.append((so, sols_for_so, file_idx, file_entry))

                total_soview_files = len(soview_tasks)
                completed_sv = 0
                total_matched_sv_pages = 0
                t_sv_phase = time.perf_counter()

                def _scan_one_soview_file(task_item):
                    if self._auto_detect_cancel.is_set():
                        return None
                    so_code, sols_sub, f_idx, f_entry = task_item
                    f_name = f_entry.get("name", "")
                    f_path = f_entry.get("path", "")
                    src = f_entry.get("path") or f_entry.get("bytes")
                    t0 = time.perf_counter()
                    try:
                        # Sequential streaming read over SMB prevents MuPDF's random fseek contention
                        if isinstance(src, str) and (src.startswith(r"\\") or os.path.isfile(src)):
                            with open(src, "rb") as fp:
                                src_bytes = fp.read()
                            f_entry["bytes"] = src_bytes
                            pdf_to_scan = src_bytes
                        else:
                            pdf_to_scan = src

                        p_map = scan_soview_pdf_3patterns(
                            pdf_source=pdf_to_scan,
                            target_sols=sols_sub,
                            base_so=so_code,
                            cancel_event=self._auto_detect_cancel,
                        )
                        dur = time.perf_counter() - t0
                        return (so_code, sols_sub, f_idx, f_name, f_path, p_map, dur, None)
                    except Exception as exc:
                        dur = time.perf_counter() - t0
                        return (so_code, sols_sub, f_idx, f_name, f_path, {}, dur, exc)

                if soview_tasks:
                    max_sv_workers = min(8, max(1, total_soview_files))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_sv_workers) as executor:
                        future_to_task = {
                            executor.submit(_scan_one_soview_file, task): task
                            for task in soview_tasks
                        }
                        for future in concurrent.futures.as_completed(future_to_task):
                            if self._auto_detect_cancel.is_set():
                                executor.shutdown(wait=False, cancel_futures=True)
                                break
                            res = future.result()
                            if res is None:
                                continue
                            so_code, sols_sub, f_idx, f_name, f_path, page_map, dur, exc = res
                            completed_sv += 1

                            _progress({"sol": sols_sub[0] if sols_sub else "",
                                       "typeId": "soview",
                                       "method": "soview_3pattern",
                                       "fileName": f_name,
                                       "currentFile": completed_sv,
                                       "totalFiles": total_soview_files})

                            if exc is not None:
                                for sol in sols_sub:
                                    errors.append({"sol": sol, "typeId": "soview", "error": str(exc)})
                                if TRACING_ENABLED:
                                    print(f"[TRIM] >> 5. SOview Scan (File) | {f_name:<32} | {dur*1000:>8.1f}ms (ERROR: {exc})")
                            else:
                                matched_pages_cnt = 0
                                for sol, pages in page_map.items():
                                    seen = set((e["fileIdx"], e["pageIndex"]) for e in assembly[sol]["soview"])
                                    for p in pages:
                                        if (f_idx, p) not in seen:
                                            assembly[sol]["soview"].append({
                                                "pageIndex": p,
                                                "fileIdx":   f_idx,
                                                "fileName":  f_name,
                                                "filePath":  f_path,
                                                "fileId":    f_path or f_name,
                                                "method":    "soview_3pattern",
                                            })
                                            seen.add((f_idx, p))
                                            matched_pages_cnt += 1
                                            total_matched_sv_pages += 1
                                    assembly[sol]["soview"].sort(key=lambda e: (e["fileIdx"], e["pageIndex"]))
                                if TRACING_ENABLED:
                                    print(f"[TRIM] >> 5. SOview Scan (File) | {f_name:<32} | {dur*1000:>8.1f}ms (SO={so_code}, {matched_pages_cnt} pages)")

                    tracer.record(
                        phase="5. SOview Scan",
                        target=f"{total_soview_files} files ({max_sv_workers} workers)",
                        t0=t_sv_phase,
                        details=f"{total_matched_sv_pages} pages matched across {len(so_to_sols)} SOs",
                    )

                # ── Stage 5b: TE scan (Grouped by ordered_item) ───────────
                # Build map: ordered_item -> { te_code: [sol1, sol2, ...] }
                oi_to_te_sols: dict[str, dict[str, list[str]]] = {}
                for sol in sol_list:
                    meta = sol_metadata.get(sol, {})
                    ordered_item = meta.get("ordered_item")
                    list_te      = meta.get("list_te", [])
                    if ordered_item and list_te:
                        te_dict = oi_to_te_sols.setdefault(ordered_item, {})
                        for te in list_te:
                            te_dict.setdefault(te, []).append(sol)

                total_te_ois = len(oi_to_te_sols)
                completed_oi = 0
                total_te_matched = 0
                total_te_files_scanned = 0
                t_te_phase = time.perf_counter()

                for ordered_item, te_to_sols in oi_to_te_sols.items():
                    if self._auto_detect_cancel.is_set():
                        break

                    te_found_files = [f for f in te_files.get(ordered_item, []) if f.get("found")]
                    te_remaining = set(te_to_sols.keys())
                    for te_file_idx, te_file_entry in enumerate(te_found_files):
                        if self._auto_detect_cancel.is_set():
                            break
                        if not te_remaining:
                            break  # Early exit: all TEs for this ordered_item found!

                        te_file_name = te_file_entry.get("name", "")
                        te_file_path = te_file_entry.get("path", "")
                        te_pdf_source = te_file_entry.get("bytes") or te_file_entry.get("path")
                        if isinstance(te_pdf_source, str) and (te_pdf_source.startswith(r"\\") or os.path.isfile(te_pdf_source)):
                            try:
                                with open(te_pdf_source, "rb") as fp:
                                    te_file_entry["bytes"] = fp.read()
                                te_pdf_source = te_file_entry["bytes"]
                            except Exception:
                                pass

                        first_sol = te_to_sols[next(iter(te_remaining))][0] if te_remaining else ""
                        _progress({"sol": first_sol,
                                   "typeId": "layout",
                                   "method": "te_barcode_scan",
                                   "fileName": te_file_name,
                                   "currentFile": completed_oi + 1,
                                   "totalFiles": total_te_ois})

                        t_te_file = time.perf_counter()
                        total_te_files_scanned += 1
                        try:
                            te_page_map = scan_te_pdf_barcode(
                                pdf_source=te_pdf_source,
                                te_codes_needed=list(te_remaining),
                                cancel_event=self._auto_detect_cancel,
                            )
                            te_matched_cnt = 0
                            for te_code, page_num in te_page_map.items():
                                target_sols = te_to_sols.get(te_code, [])
                                for s in target_sols:
                                    seen_te = set((e["fileIdx"], e["pageIndex"]) for e in assembly[s]["layout"])
                                    if (te_file_idx, page_num) not in seen_te:
                                        assembly[s]["layout"].append({
                                            "pageIndex": page_num,
                                            "fileIdx":   te_file_idx,
                                            "fileName":  te_file_name,
                                            "filePath":  te_file_path,
                                            "fileId":    te_file_path or te_file_name,
                                            "method":    "te_barcode_scan",
                                            "teCode":    te_code,
                                        })
                                        seen_te.add((te_file_idx, page_num))
                                        assembly[s]["layout"].sort(key=lambda e: (e["fileIdx"], e["pageIndex"]))
                                te_matched_cnt += 1
                                total_te_matched += 1
                                te_remaining.discard(te_code)

                            if TRACING_ENABLED:
                                dur_te_ms = (time.perf_counter() - t_te_file) * 1000
                                print(f"[TRIM] >> 6. TE Scan (File)     | {te_file_name:<32} | {dur_te_ms:>8.1f}ms (OI={ordered_item}, {te_matched_cnt} TEs)")
                        except Exception as exc:
                            if TRACING_ENABLED:
                                dur_te_ms = (time.perf_counter() - t_te_file) * 1000
                                print(f"[TRIM] >> 6. TE Scan (File)     | {te_file_name:<32} | {dur_te_ms:>8.1f}ms (ERROR: {exc})")
                            for s in list(set(s for sols in te_to_sols.values() for s in sols)):
                                errors.append({"sol": s, "typeId": "layout", "error": str(exc)})

                    completed_oi += 1

                tracer.record(
                    phase="6. TE Scan",
                    target=f"{total_te_ois} OIs ({total_te_files_scanned} files)",
                    t0=t_te_phase,
                    details=f"{total_te_matched} TEs matched across {total_te_ois} ordered items",
                )

                # ── Stage 6: Extract matched pages to RAM cache ───────────
                t_ram = time.perf_counter()
                clear_ram_cache()
                soview_batched = {so: soview_files.get(so, []) for so in so_to_sols}
                te_batched     = te_files  # keyed by ordered_item; reused by extract step below

                opened_docs: dict = {}
                try:
                    for sol, types_dict in assembly.items():
                        so = parse_so(sol)
                        for type_id, items in types_dict.items():
                            if not items:
                                continue
                            ram_key = f"{sol}_{type_id}"
                            out_doc = fitz.open()
                            try:
                                if type_id == "soview":
                                    found_files_for_so = [f for f in soview_batched.get(so, []) if f.get("found")]
                                else:
                                    # layout/TE: find files by ordered_item
                                    oi = sol_metadata.get(sol, {}).get("ordered_item")
                                    found_files_for_so = [f for f in te_batched.get(oi, []) if f.get("found")] if oi else []

                                for e in items:
                                    fidx = e.get("fileIdx", 0)
                                    p    = e.get("pageIndex")
                                    f_entry = found_files_for_so[fidx] if 0 <= fidx < len(found_files_for_so) else {}
                                    # Prioritize in-memory bytes over UNC network path
                                    pdf_source = f_entry.get("bytes") or f_entry.get("path") or e.get("filePath")
                                    if not pdf_source:
                                        continue

                                    file_key = f_entry.get("path") or f_entry.get("name") or str(fidx)
                                    if file_key not in opened_docs:
                                        if isinstance(pdf_source, (bytes, bytearray)):
                                            opened_docs[file_key] = fitz.open(stream=pdf_source, filetype="pdf")
                                        else:
                                            # Fallback: sequential read into RAM before opening with fitz
                                            with open(pdf_source, "rb") as fp:
                                                b = fp.read()
                                            f_entry["bytes"] = b
                                            opened_docs[file_key] = fitz.open(stream=b, filetype="pdf")

                                    src_doc = opened_docs.get(file_key)
                                    if src_doc and p is not None:
                                        page_idx_0based = p - 1
                                        if 0 <= page_idx_0based < src_doc.page_count:
                                            out_doc.insert_pdf(src_doc, from_page=page_idx_0based, to_page=page_idx_0based)

                                if out_doc.page_count > 0:
                                    AUTO_DETECT_RAM_CACHE[ram_key] = out_doc.tobytes(deflate=False, garbage=0)

                                # -- Late Eviction: xoa bytes file goc sau khi sub-doc da duoc tao ----
                                # Sub-doc da nam trong AUTO_DETECT_RAM_CACHE -- bytes goc khong can nua.
                                for ev_e in items:
                                    ev_fidx = ev_e.get("fileIdx", 0)
                                    if 0 <= ev_fidx < len(found_files_for_so):
                                        ev_fe = found_files_for_so[ev_fidx]
                                        if "bytes" in ev_fe:
                                            del ev_fe["bytes"]

                            except Exception as exc:
                                print(f"[run_auto_detect_trim] RAM extract {sol}/{type_id}: {exc}")
                            finally:
                                out_doc.close()
                finally:
                    for d in opened_docs.values():
                        try:
                            d.close()
                        except Exception:
                            pass

                total_ram_bytes = sum(len(b) for b in AUTO_DETECT_RAM_CACHE.values())
                tracer.record(
                    phase="7. RAM Cache",
                    target=f"{len(AUTO_DETECT_RAM_CACHE)} sub-docs",
                    t0=t_ram,
                    details=f"{total_ram_bytes // 1024} KB written",
                )

                # ── Stage 7: Build bq_metadata for frontend ──────────────
                bq_metadata: dict = {}
                for sol in sol_list:
                    meta        = sol_metadata.get(sol, {})
                    sv_entries  = assembly[sol].get("soview", [])
                    te_entries  = assembly[sol].get("layout", [])

                    sv_file = sv_entries[0].get("fileName", "") if sv_entries else ""
                    sv_path = sv_entries[0].get("filePath", "") if sv_entries else ""
                    te_file = te_entries[0].get("fileName", "") if te_entries else ""
                    te_path = te_entries[0].get("filePath", "") if te_entries else ""

                    bq_metadata[sol] = {
                        "ordered_item":  meta.get("ordered_item"),
                        "list_te":       meta.get("list_te", []),
                        "soview_pages":  len(sv_entries),
                        "soview_file":   sv_file,
                        "soview_path":   sv_path,
                        "te_pages":      len(te_entries),
                        "te_file":       te_file,
                        "te_path":       te_path,
                    }

                # Print trace summary table upon completion
                tracer.print_summary()

                if self._auto_detect_cancel.is_set():
                    self._eval("window._onAutoDetectCancelled()")
                    return

                payload = _json_str({
                    "success":         True,
                    "tracing_enabled": TRACING_ENABLED,
                    "assembly":        assembly,
                    "errors":          errors,
                    "bq_metadata":     bq_metadata,
                    "missing_data":    missing_data,
                    "bq_error":        bq_error,
                })
                self._eval(f"window._onAutoDetectDone({payload})")
                self.flash_window()

            except Exception as exc:
                traceback.print_exc()
                err_msg = str(exc) or f"{type(exc).__name__}: {exc}"
                payload = _json_str({
                    "success": False, "error": err_msg,
                    "assembly": {}, "errors": [],
                    "bq_metadata": {}, "missing_data": {},
                })
                self._eval(f"window._onAutoDetectDone({payload})")
                self.flash_window()

        _run_in_thread(_task)

    # ── 6. OTA Update ────────────────────────────────────────

    @_trace
    def check_for_update(self) -> dict:
        """Synchronous version check (fast network call)."""
        return updater.check_for_update()

    @_trace
    def apply_update(self, remote_version: str = "new") -> None:
        """Download and hot-swap the new .exe, then close the app."""
        def _task():
            try:
                updater.apply_update(remote_version)
            except Exception as e:
                traceback.print_exc()
                print(f"Error during apply_update: {e}")
        _run_in_thread(_task)

    # ── 7. Open output folder in Explorer ───────────────────

    @_trace
    def open_folder(self, folder_path: str) -> None:
        """Open a directory in Windows Explorer."""
        try:
            subprocess.Popen(["explorer", folder_path])
        except Exception:
            pass

    @_trace
    def open_file(self, path: str) -> None:
        """Open a file with its default Windows application."""
        try:
            os.startfile(path)
        except Exception:
            pass

    # ── 8. File Dialog Helpers ───────────────────────────────

    @_trace
    def browse_files_dialog(self, file_types: str = "PDF files (*.pdf)") -> list:
        """
        Open a native multi-file picker dialog.
        Returns a list of selected file paths, or an empty list if cancelled.
        """
        if not self._window:
            return []
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=True,
                file_types=(file_types,),
            )
            return list(result) if result else []
        except Exception:
            return []

    @_trace
    def browse_folder_dialog(self) -> str:
        """
        Open a native folder picker dialog.
        Returns the selected folder path, or an empty string if cancelled.
        """
        if not self._window:
            return ""
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.FOLDER,
            )
            return result[0] if result else ""
        except Exception:
            return ""

    # ── 9. PDF Deduplicate ───────────────────────────────────

    @_trace
    def run_deduplicate(self, files_json: str, output_folder: str, combine: bool = False, sols_json: str = "[]") -> None:
        """
        Async: run barcode-based deduplication on a batch of PDF files.
        If combine is True, merges all output files into one PDF.

        Pushes to JS:
          window._onDedupProgress({ fileIdx, totalFiles, pageNum, totalPages, fileName })
          window._onDedupLog({ fileIdx, pageNum, barcode, action, fileName })
          window._onDedupDone({ success, results })
        """
        def _task():
            try:
                raw_file_list = json.loads(files_json)
                raw_sols_list = json.loads(sols_json)
                file_list = list(dict.fromkeys(os.path.normpath(p.strip()) for p in raw_file_list if p and isinstance(p, str) and p.strip()))
                sols_list = list(dict.fromkeys(s.strip() for s in raw_sols_list if s and isinstance(s, str) and s.strip()))

                def progress_cb(file_idx, total_files, page_num, total_pages):
                    payload = _json_str({
                        "fileIdx": file_idx,
                        "totalFiles": total_files,
                        "pageNum": page_num,
                        "totalPages": total_pages,
                        "fileName": os.path.basename(file_list[file_idx]) if file_idx < len(file_list) else "",
                    })
                    self._eval(f"window._onDedupProgress({payload})")

                # Pre-compute the combined output path (before the batch runs)
                # so we can pass it directly to deduplicate_batch — avoiding
                # the old pattern of writing per-file temp PDFs then re-opening,
                # re-inserting and deleting them in a second pass.
                combined_path = None
                if combine:
                    if sols_list:
                        base_name = f"(Dedup) {' '.join(sols_list)}"
                    else:
                        base_name = "(Dedup) Combined"

                    # Truncate to 250 chars to leave room for the .pdf extension
                    if len(base_name) > 250:
                        base_name = base_name[:250]
                    combined_name = f"{base_name}.pdf"
                    combined_path = os.path.join(output_folder, combined_name)

                    # Avoid overwriting an existing file
                    counter = 1
                    while os.path.exists(combined_path):
                        combined_name = f"{base_name} ({counter}).pdf"
                        combined_path = os.path.join(output_folder, combined_name)
                        counter += 1

                results = deduplicate_batch(
                    pdf_paths=file_list,
                    output_folder=output_folder,
                    progress_cb=progress_cb,
                    combine=combine,
                    combined_output_path=combined_path,
                )

                payload = _json_str({"success": True, "results": results, "combined_path": combined_path})
                self._eval(f"window._onDedupDone({payload})")

            except Exception as exc:
                payload = _json_str({"success": False, "error": str(exc), "results": []})
                self._eval(f"window._onDedupDone({payload})")

        _run_in_thread(_task)

    # ── 10. PDF Combine ──────────────────────────────────────

    @_trace
    def search_combine_files(self, sol_ids_json: str, folders_json: str) -> None:
        """
        Async: search input folders for PDFs matching SOL IDs.

        Pushes to JS:
          window._onCombinePreviewReady(mapping)
        where mapping = { solId: [{ path, name, source }, ...] }
        """
        def _task():
            try:
                raw_sol_ids = json.loads(sol_ids_json)
                raw_folders = json.loads(folders_json)
                sol_ids = list(dict.fromkeys(s.strip() for s in raw_sol_ids if s and isinstance(s, str) and s.strip()))
                folders = list(dict.fromkeys(os.path.normpath(f.strip()) for f in raw_folders if f and isinstance(f, str) and f.strip()))

                mapping = combiner_search(sol_ids, folders)

                payload = _json_str(mapping)
                self._eval(f"window._onCombinePreviewReady({payload})")

            except Exception as exc:
                payload = _json_str({"error": str(exc)})
                self._eval(f"window._onCombinePreviewReady({payload})")

        _run_in_thread(_task)

    @_trace
    def export_combine(self, sol_ids_json: str, preview_json: str, output_folder: str, export_mode: str = "split") -> None:
        """
        Async: merge PDF files per SOL ID according to the preview mapping.

        Pushes to JS:
          window._onCombineProgress({ solIdx, totalSols, solId })
          window._onCombineDone({ success, files, errors })
        """
        def _task():
            try:
                raw_sol_ids = json.loads(sol_ids_json)
                sol_ids = list(dict.fromkeys(s.strip() for s in raw_sol_ids if s and isinstance(s, str) and s.strip()))
                raw_preview_mapping = json.loads(preview_json)
                
                # Reconstruct dict to preserve the user's original input order
                preview_mapping = {}
                for sol in sol_ids:
                    if sol in raw_preview_mapping:
                        preview_mapping[sol] = raw_preview_mapping[sol]
                # Fallback for any missing keys not in sol_ids
                for sol, files in raw_preview_mapping.items():
                    if sol not in preview_mapping:
                        preview_mapping[sol] = files

                def progress_cb(sol_idx, total_sols, sol_id):
                    payload = _json_str({
                        "solIdx": sol_idx,
                        "totalSols": total_sols,
                        "solId": sol_id,
                    })
                    self._eval(f"window._onCombineProgress({payload})")

                result = combine_pdfs(
                    preview_mapping=preview_mapping,
                    output_folder=output_folder,
                    progress_cb=progress_cb,
                    mode=export_mode,
                )

                payload = _json_str(result)
                self._eval(f"window._onCombineDone({payload})")

            except Exception as exc:
                try:
                    import os
                    dl_folder = os.path.join(os.path.expanduser('~'), 'Downloads')
                    result = combine_pdfs(
                        preview_mapping=preview_mapping,
                        output_folder=dl_folder,
                        progress_cb=progress_cb,
                        mode=export_mode,
                    )
                    result["fallback_used"] = True
                    result["fallback_path"] = dl_folder
                    payload = _json_str(result)
                except Exception as exc2:
                    payload = _json_str({"success": False, "error": str(exc), "files": [], "errors": []})
                self._eval(f"window._onCombineDone({payload})")

        _run_in_thread(_task)


# ── JSON / JS string helpers ─────────────────────────────────

def _json_str(obj) -> str:
    """Serialize a Python object to a JSON string safe for evaluate_js()."""
    return json.dumps(obj, ensure_ascii=True)


def _js_str(s: str) -> str:
    """Wrap a string value as a JSON-quoted JS string literal."""
    return json.dumps(s, ensure_ascii=True)
