# RFID HT PDF Merger Implementation Plan

## Executive Summary & System Overview

This implementation plan outlines the architectural, backend, frontend, and data ingestion changes required to add a 4th module—**PDF Merger (RFID HT)**—to the existing PDF manipulation application (which currently contains *PDF Merger (PFL)*, *PDF Deduplicate*, and *PDF Combine*).

---

## 1. System Requirements & Logic Specification

### 1.1 User Presets Configuration (Google Sheets)
* **Schema Update**: Update the preset Google Sheet schema from 4 columns to 5:
  1. `User`
  2. `Line`
  3. `SOview_Folder_Link`
  4. `Layout_Folder_Link`
  5. `Output_Folder_Link`
* **Data Ingestion**: Filter preset data by `User` and `Line` (`PFL` vs. `RFID HT`) to load the correct target paths for the selected module.

### 1.2 Core Module Logic: PDF Merger (RFID HT)
* **Folder Location**: Both `SOview` and `Layout` locations are network drive file paths (unlike PFL layout folders, which rely on Google Drive).
* **Input Data Schema**:
  * Expand the existing Google Sheets ID reader range from `A:A` to `A:D`. (Only apply for RFID HT sheet)
  * **Column Mapping**:
    * Column A (`Col A`): `SOL`
    * Column B (`Col B`): `Job`
    * Column C (`Col C`): `RB`
    * Column D (`Col D`): `Flag`
* **SOview Processing**:
  * Parse `SOL` (`Col A`) to extract the base SO.
  * Scan the `SOview` folder using barcode detection, leveraging existing backend barcode and frontend handling modules.
* **Layout File Matching**:
  * Match layout files on the network folder by searching for filenames containing the `Job` number (`Col B`) utilize def `find_all_pdfs_local_batch` in file_resolver.py.
  * Do **not** run barcode detection, OCR, or digital text searches on layout files; extract all pages from matched files directly.
* **Merging**:
  * Sequentially pair and merge each processed SOL section from `SOview` with its corresponding `Job` layout file.

### 1.3 Custom Barcode Detection Logic (`Flag` Exception)
* **Flag Evaluation**:
  * `Flag == False`: Use standard processing (*Fast Mode* or *OCR Mode*).
  * `Flag == True`: Trigger a custom barcode detection mode inside `_detect_barcode_soview` (built as an alternative variant to default Fast Mode).
* **Algorithm Workflow (`Flag == True`)**:
  1. **Page Iteration**: Loop through pages (`for page_idx in range(total_pages)` where `page_num = page_idx + 1`). (Same as current fast mode)
  2. **Interruption Checks**: (Same as current fast mode)
     * Check `cancel_event` -> `break`.
     * **Complete-Match Exit**: If all `target_sol_set` items exist in `closed_ranges` -> `break`.
  3. **Rendering**: Render the top 25% region of the page at 100 DPI using PyMuPDF (`fitz.Matrix(100/72, 100/72)`). (Same as current fast mode)
  4. **Barcode Decoding**: (Same as current fast mode)
     * Extract 1D barcodes via `pyzbar`.
     * Regex Pattern Match: `^\d{9}-\d{1,3}$`.
     * Skip page (`continue`) if no 1D barcode is found or regex pattern fails to match.
  5. **Miss-Exit Check**: (Same as current fast mode)
     * If single-SO mode, first barcode not yet scanned, and `_line_num(barcode_val) > max_target_line` -> set `first_sol_scanned = True` and `break`.
  6. **SOL Matching & Range Boundaries**: (modification here)
     * If `barcode_val` is not in `target_sol_set` and no `open_SOL` is active -> `continue`.
     * **Range Closing Condition**: If an `open_SOL` is active and ANY valid 1D barcode matching pattern `^\d{9}-\d{1,3}$` is found in the top 25% region (regardless of whether it is in `target_sol_set`), close the current `open_SOL` at `close_range = current_page - 1`. 
     * **Range Opening Condition**: Immediately evaluate if the newly detected barcode matches any pending `target_sol_set` item to initialize a new `open_SOL` at `open_range = current_page`.
  7. **Final Exit Check**: Re-evaluate **Complete-Match Exit** before advancing to the next iteration. (Same as current fast mode)

Tóm tắt thay đổi bằng tiếng Việt: thay vì đọc 1D barcode ở 1/4 top và check target SOL rồi đóng luôn để chỉ lấy 1 page, thì loop đọc tiếp barcode các trang đến khi tìm được barcode mới thì close open_SOL hiện tại và lấy page từ lúc tìm được đến trang hiện tại -1.
---

## 2. Developer-Ready Architecture & Implementation Plan

### 2.1 Database & Google Sheets Ingestion Layer Updates
1. **Preset Sheet Adapter Updates**:
   * Modify Google Sheets API parsing logic to read 5 columns (`User`, `Line`, `SOview_Folder_Link`, `Layout_Folder_Link`, `Output_Folder_Link`).
   * Update internal data mapping models (`PresetConfig`) to include `line` attribute.
   * Add filtering functions to query presets user name based on which module is selected
2. *Try to utilize current backend and frontend functions.
