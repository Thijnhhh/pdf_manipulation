"""
backend/pdf_combiner.py
─────────────────────────────────────────────────────────────
SOL-based PDF search and merge engine.

Public functions:
  • search_files(sol_ids, input_folders)
      Searches input folders (in order) for PDFs whose filenames
      contain each SOL ID. Returns a mapping dict.

  • combine_pdfs(preview_mapping, output_folder, progress_cb)
      For each SOL ID, merges the mapped PDF files into one
      output file: output_folder/SOL_ID.pdf
      Follows the file order as given in the preview mapping.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import concurrent.futures
import datetime
import os
import threading
from typing import Callable, Optional

import pymupdf as fitz


# ── Type alias for progress callback ────────────────────────

# progress_cb(sol_idx, total_sols, sol_id)
ProgressCb = Callable[[int, int, str], None]


# ── Search ──────────────────────────────────────────────────

def search_files(
    sol_ids: list[str],
    input_folders: list[str],
) -> dict[str, list[dict]]:
    """
    Search input folders for PDF files whose filenames contain
    each provided SOL ID.

    Folders are searched in the order they were provided.
    Within each folder, files are sorted alphabetically.

    Args:
        sol_ids:       List of SOL ID strings to search for.
        input_folders: Ordered list of folder paths to search in.

    Returns:
        {
            "SOL1": [
                { "path": "C:\\...\\file.pdf", "name": "file.pdf", "source": "auto" },
                ...
            ],
            "SOL2": [ ... ],
            ...
        }
    """
    # Clean and deduplicate input lists while preserving order
    clean_sol_ids = list(dict.fromkeys(s.strip() for s in sol_ids if s and isinstance(s, str) and s.strip()))
    clean_folders = list(dict.fromkeys(os.path.normpath(f.strip()) for f in input_folders if f and isinstance(f, str) and f.strip()))

    # Pre-scan all folders (including subfolders) once and cache their PDF file lists
    folder_pdfs: list[list[tuple[str, str]]] = []  # [(full_path, filename), ...]
    for folder in clean_folders:
        if not os.path.isdir(folder):
            folder_pdfs.append([])
            continue
        files = []
        for root, _, filenames in os.walk(folder):
            for filename in filenames:
                if filename.lower().endswith('.pdf'):
                    files.append((os.path.join(root, filename), filename))
        # Sort alphabetically within each folder tree
        files.sort(key=lambda x: x[1].lower())
        folder_pdfs.append(files)

    # Match SOL IDs against filenames
    result: dict[str, list[dict]] = {}

    for sol_id in clean_sol_ids:
        matched: list[dict] = []
        sol_lower = sol_id.lower()

        # Search folders in order
        for files in folder_pdfs:
            for full_path, filename in files:
                if sol_lower in filename.lower():
                    # Avoid duplicate paths
                    if not any(m["path"] == full_path for m in matched):
                        matched.append({
                            "path": full_path,
                            "name": filename,
                            "source": "auto",
                        })

        result[sol_id] = matched

    return result


# ── Combine / Merge ─────────────────────────────────────────

def combine_pdfs(
    preview_mapping: dict[str, list[dict]],
    output_folder: str,
    progress_cb: Optional[ProgressCb] = None,
    mode: str = "split",
) -> dict:
    """
    Merge PDF files according to the preview mapping.

    mode = "split": Output one PDF per SOL ID → output_folder/SOL_ID.pdf
    mode = "merge": Output one combined PDF for all SOLs → output_folder/Combined_Output_TIMESTAMP.pdf

    Performance optimisations applied:
      • merge mode — all source PDFs are pre-loaded in parallel (ThreadPoolExecutor)
        before the single-threaded fitz insert loop, eliminating serial network
        round-trips when files live on a UNC/NAS share.
      • split mode — each SOL ID is processed in a separate thread
        (ThreadPoolExecutor), making full use of available CPU cores.
      • Both modes save with garbage=3, deflate=True for maximum output compression.
      • merge mode wraps out_doc in a try/finally so it is always closed even if
        an exception is raised mid-way, preventing a RAM leak.

    Args:
        preview_mapping: { sol_id: [{ path, name, source }, ...] }
        output_folder:   Directory to save merged output files.
        progress_cb:     Called per SOL with (sol_idx, total_sols, sol_id).
        mode:            "split" or "merge".

    Returns:
        {
            "success": True/False,
            "files": [ { "sol": str, "output_path": str, "page_count": int }, ... ],
            "errors": [ { "sol": str, "error": str }, ... ]
        }
    """
    os.makedirs(output_folder, exist_ok=True)

    sol_ids = list(dict.fromkeys(k.strip() for k in preview_mapping.keys() if k and isinstance(k, str) and k.strip()))
    total_sols = len(sol_ids)
    output_files: list[dict] = []
    errors: list[dict] = []

    if mode == "merge":
        # ── Step 1: Collect all unique (sol_id, pdf_path) pairs in order ────
        # Preserve SOL order then file order within each SOL.
        ordered_entries: list[tuple[str, str]] = []  # [(sol_id, pdf_path), ...]
        seen_global: set[str] = set()
        for sol_id in sol_ids:
            for entry in preview_mapping.get(sol_id, []):
                p = entry.get("path")
                if p and p not in seen_global:
                    seen_global.add(p)
                    ordered_entries.append((sol_id, p))

        # ── Step 2: Pre-load all PDF files in parallel (network I/O) ────────
        # Reading many files over a UNC share serially incurs latency × N.
        # ThreadPoolExecutor lets OS overlap multiple network reads concurrently.
        def _load_file(item: tuple[str, str]) -> tuple[str, str, bytes | None, str | None]:
            """Read one PDF from disk/network. Returns (sol_id, path, data, error)."""
            sol_id, pdf_path = item
            if not os.path.isfile(pdf_path):
                return sol_id, pdf_path, None, f"File not found: {pdf_path}"
            try:
                with open(pdf_path, "rb") as fh:
                    return sol_id, pdf_path, fh.read(), None
            except Exception as exc:
                return sol_id, pdf_path, None, str(exc)

        max_io_workers = min(16, os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_io_workers) as executor:
            loaded: list[tuple[str, str, bytes | None, str | None]] = list(
                executor.map(_load_file, ordered_entries)
            )

        # ── Step 3: Build an in-order buffer map from pre-loaded bytes ───────
        # Key: sol_id → ordered list of (bytes, original_path) for insert loop.
        loaded_map: dict[str, list[tuple[bytes, str]]] = {}
        for sol_id, pdf_path, data, err in loaded:
            if err:
                errors.append({"sol": sol_id, "error": err})
            else:
                loaded_map.setdefault(sol_id, []).append((data, pdf_path))

        # ── Step 4: Ghép từ RAM buffer — không còn I/O network ──────────────
        out_doc = fitz.open()
        try:
            for idx, sol_id in enumerate(sol_ids):
                if progress_cb:
                    progress_cb(idx, total_sols, sol_id)

                buffers = loaded_map.get(sol_id)
                if not buffers:
                    # Only add to errors if there were no I/O errors already logged
                    already_errored = any(e["sol"] == sol_id for e in errors)
                    if not already_errored:
                        errors.append({"sol": sol_id, "error": "No files mapped"})
                    continue

                for data, _ in buffers:
                    src_doc = fitz.open(stream=data, filetype="pdf")
                    out_doc.insert_pdf(src_doc)
                    src_doc.close()

            if len(out_doc) > 0:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                merged_name = f"Combined_Output_{timestamp}.pdf"
                output_path = os.path.join(output_folder, merged_name)

                out_doc.save(output_path, garbage=3, deflate=True)
                output_files.append({
                    "sol": "ALL_SOLS",
                    "output_path": output_path,
                    "page_count": len(out_doc),
                })

        except Exception as exc:
            errors.append({"sol": "ALL_SOLS", "error": str(exc)})
        finally:
            # Always release fitz document resources regardless of exceptions.
            out_doc.close()

    else:
        # ── Split mode: process each SOL in a parallel thread ────────────────
        # fitz.insert_pdf() operates on independent out_doc instances per thread,
        # so there are no shared-state conflicts between workers.
        errors_lock = threading.Lock()
        output_files_lock = threading.Lock()
        progress_lock = threading.Lock()
        progress_counter = [0]  # mutable container for nonlocal-style update

        def _process_sol(sol_id: str) -> None:
            """Build and save one output PDF for a single SOL ID."""
            # ── Deduplicate entries for this SOL ─────────────────────────────
            seen_paths: set[str] = set()
            file_entries: list[dict] = []
            for entry in preview_mapping.get(sol_id, []):
                p = entry.get("path")
                if p and p not in seen_paths:
                    seen_paths.add(p)
                    file_entries.append(entry)

            if not file_entries:
                with errors_lock:
                    errors.append({"sol": sol_id, "error": "No files mapped"})
                _tick_progress(sol_id)
                return

            out_doc = fitz.open()
            try:
                for entry in file_entries:
                    pdf_path = entry["path"]
                    if not os.path.isfile(pdf_path):
                        with errors_lock:
                            errors.append({
                                "sol": sol_id,
                                "error": f"File not found: {pdf_path}",
                            })
                        continue

                    src_doc = fitz.open(pdf_path)
                    out_doc.insert_pdf(src_doc)
                    src_doc.close()

                if len(out_doc) > 0:
                    output_path = os.path.join(output_folder, f"{sol_id}.pdf")
                    # garbage=3, deflate=True: remove duplicate objects + compress streams
                    out_doc.save(output_path, garbage=3, deflate=True)
                    with output_files_lock:
                        output_files.append({
                            "sol": sol_id,
                            "output_path": output_path,
                            "page_count": len(out_doc),
                        })

            except Exception as exc:
                with errors_lock:
                    errors.append({"sol": sol_id, "error": str(exc)})
            finally:
                out_doc.close()
                _tick_progress(sol_id)

        def _tick_progress(sol_id: str) -> None:
            """Thread-safe progress reporting."""
            if not progress_cb:
                return
            with progress_lock:
                progress_counter[0] += 1
                progress_cb(progress_counter[0], total_sols, sol_id)

        max_workers = min(16, os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all SOLs and wait for completion; exceptions propagate via futures.
            futures = {executor.submit(_process_sol, sol_id): sol_id for sol_id in sol_ids}
            for future in concurrent.futures.as_completed(futures):
                # Re-raise any unexpected exception that escaped _process_sol's own handler.
                future.result()

    return {
        "success": len(errors) == 0,
        "files": output_files,
        "errors": errors,
    }
