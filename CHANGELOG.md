# Changelog

All notable changes to this project will be documented in this file.


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
