# Changelog

All notable changes to this project will be documented in this file.


## [1.3.3] - 2026-10-09
### Added
- **Parallel Multi-Branch Batch Search (`backend/pdf_detector.py`):**
  - Refactored Step 2 (Batch Search) in `run_auto_detect_all` into a concurrent multi-threaded orchestrator using `concurrent.futures.ThreadPoolExecutor` (up to 4 workers).
  - Searches and resolves file candidate maps across multiple PDF types (`soview`, `layout`, etc.) simultaneously instead of sequentially, drastically reducing discovery latency across multiple drives and folders.
- **Concurrent Network Preload for SMB Shares (`backend/pdf_detector.py`):**
  - Integrated asynchronous I/O preloading for network UNC paths (e.g. `soview` shares): preloads discovered PDF bytes into memory using up to 12 parallel threads while concurrent branches (such as Google Drive layout downloads) are actively downloading.
  - Sorts candidate files by size (`os.path.getsize`) to optimize transfer concurrency and pipeline flow.
- **Parallelized BigQuery Stored Procedure Resolution (`backend/bridge.py` & `backend/pdf_detector.py`):**
  - **PFL Module:** Spawns a background worker thread (`_bg_bq`) to execute BigQuery `get_Job` query and validation in parallel with file discovery, synchronizing via `bq_resolver` before page detection begins.
  - **Trim Adidas Module:** Executes Stage 1 (BigQuery `get_TE` stored procedure query) and Stage 3 (SOview batch file search) concurrently using a background daemon thread, eliminating the wait time previously spent searching local/SMB directories after querying BigQuery.
- **Dynamic Upward Ancestor Hierarchy Cache for Google Drive (`backend/file_resolver.py`):**
  - Replaced the exhaustive, upfront global folder tree scan (`_safe_list("mimeType = 'application/vnd.google-apps.folder'")` traversing 1,000+ folders) with a thread-safe, on-demand upward ancestor traversal (`folder_ancestor_cache` guarded by `threading.Lock`).
  - Resolves folder lineage lazily from matched file parents upward to the target `folder_id`, completely avoiding multi-page API queries and memory overhead for large Google Drive trees.
- **High-Performance Parallel Google Drive Downloads (`backend/file_resolver.py`):**
  - Re-architected Google Drive file downloading to utilize persistent `requests.Session` with authorization headers, connection reuse, and increased concurrency to 10 worker threads (`ThreadPoolExecutor`).
  - Added automatic retry logic (up to 3 attempts with 0.3s backoff).
  - Directly streams PDF content into memory cache (`_RESOLVED_FILES_CACHE`) and writes to session temporary files.

### Changed
- **Centralized Credentials Resolution & Cleanups (`backend/updater.py`, `backend/sheets_service.py`, `backend/trim_service.py`):**
  - Standardized `backend/updater.py` to use `get_credentials_path()` from `backend.constants`, removing duplicate `_credentials_path()` helper.
  - Removed redundant backward-compatibility aliases in `backend/sheets_service.py` and `backend/trim_service.py`.
- **Frontend Event Binding & Cleanup (`assets/app.js`):**
  - Removed outdated OCR Auto Detect placeholder stub and dead helper `setFetchLoading`.
  - Cleaned up duplicate DOM clone/replacement logic (`dom.btnAutoDetect.replaceWith(...)`) during `pywebviewready` lifecycle initialization.
  - Decoupled execution benchmark toast from backend `tracing_enabled` flag, ensuring elapsed time is always displayed upon completion of Auto Detect across all modules.
- **Synchronized PE Metadata & Configurations:**
  - Upgraded application version to `1.3.3` across `config/version.json`, `version_info.txt`, `assets/index.html`, `CHANGELOG.md`, and `Documents.md`.

## [1.3.2] - 2026-10-03
### Added
- **PFL Customer Job Layout Matching & BigQuery Integration (`backend/pdf_detector.py`, `backend/bigquery_service.py`, `backend/bridge.py`):**
  - **2-Column Sheet & Input Ingestion:** Enabled reading both `SOL` (Column A) and Customer `Job` code (Column B) in the PFL module via Google Sheets (`A:B`) or manual entry.
  - **BigQuery Automatic Job Retrieval:** Integrated Google BigQuery stored procedure `raw_ingest.get_Job(filters ARRAY<STRUCT<so INT64, line INT64>>)` using Service Account credentials to automatically query missing 8-digit Customer Job numbers for PFL SOLs.
  - **Dual-Strategy Layout Page Detection with Fallback:** When `job_flag` is enabled in PFL, candidate layout pages are first matched by 8-digit Customer Job number (`JOB_CODE_REGEX = re.compile(r'\b\d{8}\b')`). If no page matches the Job code, the engine automatically falls back to matching by SOL pattern (`\b{sol}\b`).
  - **Auto Detect Summary Modal Reporting:** Added explicit detection status indicators in the PFL Auto Detect Summary modal (`Found by Job` vs. `Found by SOL Pattern Fallback`), clearly communicating the matching strategy used per SOL while keeping the layout results review grid intact.
  - **SO Prefix Layout Discovery Optimization:** Preserved 9-digit SO prefix filtering (`{so}_*`) during directory scans to keep candidate layout file discovery instantaneous over local and network shares.
- **Centralized Constants & Path Configuration (`backend/constants.py`):**
  - Consolidated application-wide constants, regex definitions (`JOB_CODE_REGEX`, `SOL_REGEX`, `SO_REGEX`), GCP configuration (`BIGQUERY_PROJECT`, stored procedure names `PROCEDURE_GET_TE` and `PROCEDURE_GET_JOB`), and credentials resolution logic (`get_credentials_path()`) into a single source of truth.
- **Dedicated BigQuery Service Module (`backend/bigquery_service.py`):**
  - Created a modularized BigQuery client service with generic stored procedure execution (`fetch_bigquery_procedure`), dedicated procedure helpers (`fetch_bigquery_te`, `fetch_bigquery_job`), and validation logic (`validate_pfl_job_data`).

### Changed
- **Modular Backend Architecture:**
  - Decoupled BigQuery procedures from `backend/trim_service.py`, leaving `trim_service.py` focused exclusively on Trim Adidas domain logic (SOview 3-pattern parsing and TE barcode matching) while re-exporting BigQuery utilities for backward compatibility.
  - Updated `backend/sheets_service.py`, `backend/bridge.py`, and `backend/pdf_detector.py` to consume centralized constants and BigQuery services.
- **Strict Module Isolation & Tracing Control:**
  - Enforced strict scoping so that PFL Job flag logic, BigQuery Job querying, and layout fallback detection are active solely within the PFL module, preserving untouched behavior for RFID HT, Trim Adidas, PFL Thermal, RFID SB, and deduplication modules.
  - Unified the `TRACING_ENABLED` configuration in `backend/bridge.py` to toggle BigQuery query result terminal logging for both Trim Adidas and PFL.
- **PyInstaller Build Configuration (`build.spec`):**
  - Updated `hiddenimports` to explicitly include all backend modules (`backend.constants`, `backend.bigquery_service`, `backend.sheets_service`, `backend.file_resolver`, `backend.pdf_processor`, `backend.pdf_detector`, `backend.pdf_deduplicator`, `backend.pdf_combiner`, `backend.trim_service`, `backend.updater`, `backend.window_utils`).
- **Synchronized PE Metadata & Configurations:**
  - Upgraded application version to `1.3.2` across `config/version.json`, `version_info.txt`, `assets/index.html`, `CHANGELOG.md`, and `Documents.md`.

## [1.3.1] - 2026-10-01
### Added
- **Native Windows Taskbar Flash Notification (`backend/window_utils.py` & `bridge.py`):**
  - Added dedicated Win32 integration module using `ctypes` (`FlashWindowEx`, `GetForegroundWindow`, `FindWindowW`).
  - Added `flash_window()` to `BridgeApi` using flag `FLASHW_TIMERNOFG` to flash the taskbar icon when background operations (`run_auto_detect`, `run_auto_detect_trim`) complete while the app window is in the background.
  - Included `backend.window_utils` in `build.spec` PyInstaller hidden imports.
- **RFID SB Dedicated Review Grid (`assets/app.js`):**
  - Implemented custom tabular summary for RFID SB workflow displaying per-SOL resolution of SOview files, Layout files, raw page counts vs. deduplicated page counts (`raw -> kept`), total assembly pages, and direct preview/file opening triggers.
  - Highlights incomplete SOLs or missing files with clear red indicator tags.
- **Dual-Metric Page Retrieval in Deduplicator (`backend/pdf_deduplicator.py`):**
  - Added optional `return_total=True` parameter to `detect_unique_pages_dedup()`, returning `(kept_pages, total_pages)` to capture raw page counts without an extra PDF open cycle.

### Fixed
- **Pattern 2 Wrapped Page Number Parsing in Trim Adidas (`backend/trim_service.py`):**
  - Resolved an issue where 3-digit page counts (e.g. `100/126` to `126/126`) caused the total page number to wrap onto a new line (e.g. `104/12\n6`) due to narrow form text boxes in PDFs.
  - Updated `_P2_REGEX` to allow optional wrapped digits in the denominator: `(\d{1,3}(?:\s*[\r\n]+\s*\d+)?)`.
  - Added whitespace sanitization (`re.sub(r"\s+", "", ...)`) for both numerator and denominator in `_extract_page_patterns` prior to `int()` casting, preventing `ValueError` and achieving 100% detection accuracy (126/126 pages) on multi-page orders.

### Changed
- **Optimized Network Preload Concurrency (`backend/pdf_detector.py`):**
  - Increased SOview network preload workers from 4 to 12 (`so_workers = min(12, len(sorted_soviews))`) based on SMB round-trip latency benchmarks on `\\fppdvn03`, reducing fixed connection overhead from ~559ms to ~255ms.
  - Enhanced layout pipeline trace logging to display raw vs. deduplicated page conversion (`{raw_pages} -> {unique_pages} pgs`).
- **Production Performance Tuning (`backend/bridge.py`):**
  - Disabled `TRACING_ENABLED = False` to eliminate console I/O overhead during high-volume production operations.
- **PE Metadata & Configuration Synchronization:**
  - Upgraded application version to `1.3.1` across `config/version.json`, `version_info.txt`, `Documents.md`, and `assets/index.html`.

## [1.3.0] - 2026-09-29
### Added
- **New Production Line Module — PDF Merger (RFID SB UQ):**
  - Added dedicated dashboard module card and workflow `RFID SB` (`PDF Merger (RFID SB UQ)`) with custom icon `🔖`.
  - Config modal automatically locks the layout row to `🔒 Deduplicate (13-digit ID / Barcode)` since layout pages are deduplicated automatically.
  - Automatically hides Google Drive/Sheet fetch button for standalone local/network operation.
- **Boundary-Matched PDF Finder (`_find_pdfs_by_sol_boundary` in `pdf_detector.py`):**
  - Added multithreaded (32 worker threads) directory crawler specifically tailored for RFID SB layout naming pattern `{SOL}_{...rest}.pdf`.
  - Implements boundary matching: verifies that characters immediately following `{SOL}` are in `('_', '.', ' ')` or end-of-string. Prevents critical false-positive collisions where e.g. `144187563-1` falsely matched `144187563-10_...pdf`.
  - Normalizes excessive whitespace in filenames prior to matching.
- **In-Flight Cluster-Jump Deduplication Pipeline (`Case C` in `pdf_detector.py`):**
  - Added high-performance layout detection pipeline for RFID SB combining parallel small-files-first preloading and instant in-flight deduplication via `detect_unique_pages_dedup()`.
  - Exposes `detect_unique_pages_dedup(pdf_source)` from `pdf_deduplicator.py` to run exponential jump + binary search clustering with zero disk round-trips.
  - Dynamically tuned worker concurrency (`max_workers = min(2, len(sorted_net_files))`) based on real-world benchmark to eliminate SMB contention and avoid throughput saturation over high-latency WAN/LAN shares.
- **Zero-Network Instant Auto Detect Preview (`fetch_auto_detect_preview` in `bridge.py` & `app.js`):**
  - Renders continuous preview page thumbnails directly from pre-extracted sub-documents in `AUTO_DETECT_RAM_CACHE`.
  - Completely eliminates network/disk re-reads during preview inspection, delivering instantaneous preview popups.
  - Pushes `window._onAutoDetectPreviewReady(sol, cardsMap)` to frontend with fallback to network path if needed.
- **Smart Adaptive I/O Engine (`open_pdf_smart`, `read_pdf_bytes_smart`, `is_network_path` in `pdf_processor.py`):**
  - Added Win32 API network path detection identifying both UNC paths (`\\server\share`) and remote mapped drives (`GetDriveTypeW == DRIVE_REMOTE`).
  - Automatically loads network files sequentially into RAM in a single pass before streaming into PyMuPDF, eliminating thousands of small random SMB `fseek` round-trips (yielding up to 30–570x speedup).
  - Preserves native zero-copy `fitz.open(path)` for local SSD/HDD files to maximize OS page cache benefits.

### Changed
- **Unified Network I/O Across Deduplication & Detection:**
  - Standardized `pdf_deduplicator.py` and `pdf_detector.py` to use `open_pdf_smart()` and `read_pdf_bytes_smart()`.
  - Added parallel small-first preloading for SOview network files (`Case B`) using ThreadPoolExecutor (`max_workers = min(4, len(sorted_soviews))`).
  - Thread-safe tracer: Added `threading.Lock()` to `_AutoDetectTracer.record` to prevent race conditions during concurrent worker phase logging.
- **Temporary Image Directory Safeguard:**
  - Added `os.makedirs(_TEMP_DIR, exist_ok=True)` in `_page_to_temp_img` to prevent transient file creation errors.
- **Synchronized PE Metadata & Configurations:**
  - Upgraded application version to `1.3.0` across `config/version.json`, `version_info.txt`, `Documents.md`, and `assets/index.html`.

## [1.2.3] - 2026-09-24
### Changed
- **Single-Pass I/O & Preload Architecture (`pdf_detector.py`):** Candidate PDFs in the detection loop are now preloaded directly into RAM (`file_result["bytes"] = fp.read()`) in a single pass upon entering the scan loop. Subsequent page detection runs entirely from in-memory byte streams, eliminating redundant network round-trips over SMB/LAN shares.
- **Double Eviction Memory Management (`pdf_detector.py` & `bridge.py`):**
  - *Early Eviction:* PDFs with zero matched pages across all target SOLs immediately delete their in-memory bytes buffer (`del file_result["bytes"]`), returning memory to the OS during the scan phase.
  - *Late Eviction:* After matched pages are sliced into sub-documents and cached into `AUTO_DETECT_RAM_CACHE`, source PDF bytes are immediately cleared (`del file_entry["bytes"]`), eliminating duplicate in-memory document peaks across both PDF Merger and Trim Adidas.
- **Inline Zero-Network Sub-Document Slicing (`pdf_detector.py`):** Removed the standalone `_extract_to_ram()` function (−3,617 characters). Sub-document page slicing now executes inline using in-memory bytes with zero network I/O, dramatically reducing RAM extraction latency.
- **Synchronized Documentation & PE Version Metadata:** Updated `Documents.md`, `version_info.txt`, and `config/version.json` to version 1.2.3.

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
