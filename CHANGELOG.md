# Changelog

All notable changes to this project will be documented in this file.


## [1.2.2] - 2026-09-23
### Added
- **`save_pdf_safely()` — Safe Output Writer (`pdf_processor.py`):** New utility replacing all raw `doc.save()` calls throughout the export pipeline. Automatically detects file-lock conflicts (e.g. PDF open in Adobe Acrobat / Foxit Reader) and appends an auto-incrementing numeric suffix `(1)`, `(2)`... up to 100 attempts before raising `PermissionError`. Strictly enforces Windows MAX_PATH (259 characters) by mathematically truncating the base name before appending the suffix. Adopted by: `execute_export` (merge & split), `deduplicate_pdf`, and `deduplicate_batch`.
- **`_is_file_locked()` helper (`pdf_processor.py`):** Internal helper used by `save_pdf_safely` to probe whether an existing file is exclusively locked by another process via a non-destructive `r+b` open attempt.

### Changed
- **`_extract_to_ram` — Selective Parallel Pre-fetch (`pdf_detector.py`):** Migrated from serial network I/O to a **3-phase** architecture:
  - *Phase 1 — Collect:* Traverses `assembly` to build a deduplicated `needed_files` dict containing only files with matched pages. Skips entries already buffered in memory (e.g. Google Drive temp files).
  - *Phase 2 — Parallel Load:* All required network/local PDFs are loaded **concurrently** via `ThreadPoolExecutor(max_workers=min(16, cpu*2))`. Each worker writes the file bytes directly into the shared `file_entry["bytes"]` so subsequent SOLs reuse the buffer without a second I/O call.
  - *Phase 3 — In-RAM Slicing:* The existing `fitz.open(stream=bytes) → insert_pdf → tobytes` loop runs entirely from memory with zero network I/O. Reduces the `4. RAM Extraction` stage from ~37s down to ~3–5s for RFID HT batches.
- **`run_auto_detect_all` — Layout File Byte-Caching (`pdf_detector.py`):** When verifying PFL Layout page geometry (A4 size check), file source priority is now `bytes → path` (was `path → bytes`), and the file is read into `file_entry["bytes"]` via binary stream read to avoid repeated UNC round-trips. Tracer log now reports RAM Cache size in KB.
- **`run_auto_detect_trim` — SOview Scan Parallelization (`bridge.py`):** Refactored SOview scan stage to use `ThreadPoolExecutor(max_workers=8)`, scanning all SOview files concurrently. Result futures are collected via `as_completed` with proper cancel handling.
- **`run_auto_detect_trim` — TE Scan Restructuring (`bridge.py`):** TE scan loop now groups SOLs by `ordered_item` into `oi_to_te_sols` map, processing each `ordered_item` group once with early-exit when all TE codes for that group are found. Fixes duplicate file reads and improves TE scan throughput for multi-SOL orders.
- **`find_all_pdfs_local_batch` — Inline mtime Caching (`file_resolver.py`):** `st_mtime` is now read from `entry.stat()` (already available in `scandir` cache) and stored in `item["mtime"]`. The `sort_by_mtime_desc` sort lambda reads from the cached field instead of calling `os.path.getmtime()` a second time, eliminating one additional SMB network syscall per file.
- **`deduplicate_pdf` & `deduplicate_batch` (`pdf_deduplicator.py`):** Migrated from `doc.save()` to `save_pdf_safely()` to handle file-lock edge cases and enforce MAX_PATH compliance.
- **`TRACING_ENABLED = False` (`bridge.py`):** Disabled bridge function execution tracing for production release.

## [1.2.1] - 2026-09-19
### Fixed
- **PDF Deduplication — SOL Search Order (Layout UQ RFID HT):** Resolved ordering issue where files returned via SOL Search were displayed and processed in incorrect order (JavaScript V8 engine auto-sorted numeric Object keys ascending instead of preserving user input order). Files are now strictly flattened and rendered in the exact top-to-bottom order that SOL IDs were entered in the Textarea input, ensuring combined output pages match the intended sequence.

## [1.2.0] - 2026-09-16
### Added
- **New Module: Trim Adidas:** Automated pipeline for Adidas trims integrating Google BigQuery to retrieve TE codes, locate SOview PDFs via flexible naming patterns (`SO.pdf`, `SO-line.pdf`, `SO-*-*.pdf`), detect `*TE*` barcodes, match layout files directly by TE code, and merge outputs.
- **New Module: PDF Merger (PFL Thermal):** Added dedicated workflow, presets, and UI tab for PFL Thermal production lines.
- **1D Barcode Detection & Upside-Down Fallback:** Upgraded SOview barcode detector to exclusively recognize 1D barcodes while filtering out 2D barcodes (QR codes, DataMatrix, PDF417). Introduced an automatic bottom-25% fallback mechanism to accurately detect 180° inverted/upside-down scanned pages with zero rotation overhead.
- **Dynamic Composite SOL Parser:** Implemented `_extract_sols_from_text` supporting dash ranges (`146842454-1-4`), comma/semicolon-separated discrete lines (`146842454-1, 2, 3; 4`), and automatic stripping of attached measurement units (e.g. `146842454-1-61mm`).
- **Fast Batch Local Search:** Added high-performance directory scanner (`find_all_pdfs_local_batch`) leveraging `os.scandir` to index local directories with tens of thousands of PDFs in under 1 second.
- **BigQuery PyInstaller Packaging:** Enhanced `build.spec` with `collect_submodules('google.cloud.bigquery')` and runtime package metadata extraction (`copy_metadata`) to ensure standalone executable compatibility without missing distribution metadata.

### Changed
- **Resolved KeyError in SOL Detection:** Replaced raw list iterations with sanitized `clean_sol_list` in `_detect_pages_all_sols`, preventing runtime crashes caused by leading/trailing whitespace.
- **Backend Clean-up & Optimization:** Removed redundant imports, unused modules, and duplicate declarations across `pdf_detector.py`, `file_resolver.py`, `pdf_processor.py`, `sheets_service.py`, `trim_service.py`, and `updater.py`.
- **Synchronized Technical Documentation:** Updated `Documents.md` to v1.2.0 incorporating BigQuery architecture, updated system diagrams, and configuration workflows for all 4 production lines.

## [1.1.0] - 2026-09-10
### Added
- **New Module: PDF Merger (RFID HT):** Dedicated module tailored for the RFID Heat Transfer manufacturing line.
- **5-Column Users_Preset Support:** Upgraded master preset schema (`User`, `Line`, `SOview_Folder_Link`, `Layout_Folder_Link`, `Output_Folder_Link`) with automatic filtering by `Line` (`PFL` vs `RFID HT`).
- **4-Column RFID HT Sheet Ingestion:** Added ingestion of RFID HT preset sheets reading range `A:D` (`SOL`, `Job`, `RB`, `Flag`).
- **Mode 3 Barcode Range Scan:** Added boundary-driven barcode range scanning for `Flag=True` items to accumulate continuation pages without barcodes until the next barcode or EOF.
- **RFID HT Layout Direct Extraction:** Implemented 1-to-1 matching by `Job` code, extracting all pages directly (`layout_all_pages`) without OCR/regex overhead.
- **SO Flag Consistency Validation:** Enforced the "1 SO = 1 Flag" consistency rule via `validate_so_flags()` with client and server error alerts for contradictory sheet entries.
- **Copyable Auto Detect Results Table:** Enabled text selection (`user-select: text`) on the Auto Detect summary grid to allow direct highlight and copying of SOL codes and page counts.
- **Continuous Vertical Preview Modal:** Upgraded preview modal to display all detected pages in a continuous scrollable layout with dynamic zoom scaling (40% - 300%).

### Changed
- **Refactored Auto Detect Architecture:** Unified `run_auto_detect_all` loop across `pdf_type_ids`, consuming priority chains directly from frontend config and eliminating duplicate code.
- **Layout Logic Isolation:** Separated RFID HT layout all-pages extraction from PFL layout priority-chained digital/OCR scanning.
- **Fixed Layout Priority Sort Bug:** Resolved indentation error in `pdf_detector.py` preventing `KeyError: 0` during multi-file layout priority sorting.

## [1.0.8] - 2026-09-07
### Changed
- Fix PDF merger (PFL) export logic (def _extract_to_ram )

## [1.0.7] - 2026-09-07
### Changed
- Fix error max length file path at auto detect PDF merger module
- Modify how to input SOL and Add UI show SOL grid table at config screen PDF merger module

## [1.0.6] - 2026-09-04
### Changed
- Refactor PDF combiner: add deep dive into subfolder
- PDF merger/Auto detect: optimize barcode reading of PFL SOview folder

## [1.0.5] - 2026-08-31
### Changed
- Refactor PDF combine module

## [1.0.4] - 2026-08-27
### Changed
- Refactor PDF combine

## [1.0.3] - 2026-08-26
### Changed
- Refactored the PDF Merger and PDF Deduplicate modules

## [1.0.2] - 2026-08-20
### Added
- Add "Combine" pdf logic in Deduplicate module

## [1.0.1] - 2026-08-19
### Changed
- Refactored the deduplicate PDF logic to optimize performance and stability
- Implemented Auto-Updater mechanism with Google Drive integration

## [1.0.0] - 2026-08-10
### Added
- Initial release of the PDF Manipulation Tool (Desktop Edition)
- Core features including PDF Merger, Deduplicate, and Combine modules
