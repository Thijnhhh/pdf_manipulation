# PDF Manipulation Desktop Application — System Architecture & Technical Specifications

---

## Document Information
* **Document Version:** 1.3.3
* **Target Application:** PDF Manipulation Tool Desktop Edition
* **Target Operating System:** Microsoft Windows 10 / 11 (64-bit)
* **Author / Ownership:** Thinh Phung / Avery Dennison (AD) RFID FRU Team
* **Last Updated:** 2026-10-09

---

## Table of Contents
1. [Overview](#1-overview)
   - [1.1 Purpose & High-Level Scope](#11-purpose--high-level-scope)
   - [1.2 Tech Stack & Primary Language](#12-tech-stack--primary-language)
   - [1.3 System Architecture & Key Modules](#13-system-architecture--key-modules)
   - [1.4 Project Structure & Module Responsibilities](#14-project-structure--module-responsibilities)
   - [1.5 Critical Execution & Runtime Flags](#15-critical-execution--runtime-flags)
   - [1.6 Comprehensive Data Flow Trajectory](#16-comprehensive-data-flow-trajectory)
2. [Prerequisites & Environment Setup](#2-prerequisites--environment-setup)
   - [2.1 Hardware & OS Requirements](#21-hardware--os-requirements)
   - [2.2 Google Cloud Platform (GCP) Configuration](#22-google-cloud-platform-gcp-configuration)
   - [2.3 Local Development Setup](#23-local-development-setup)
   - [2.4 Enterprise Security Policy](#24-enterprise-security-policy)
3. [Execution & Build Pipeline](#3-execution--build-pipeline)
   - [3.1 Local Development Execution](#31-local-development-execution)
   - [3.2 PyInstaller Packaging Pipeline](#32-pyinstaller-packaging-pipeline)
   - [3.3 Compilation & Build Troubleshooting](#33-compilation--build-troubleshooting)
4. [Detailed Module & Logic Specifications](#4-detailed-module--logic-specifications)
   - [4.1 Constants & Global Configurations](#41-constants--global-configurations)
   - [4.2 Core Logic & Algorithmic Analysis](#42-core-logic--algorithmic-analysis)
   - [4.3 Comprehensive Logic-to-UI Mapping](#43-comprehensive-logic-to-ui-mapping)
   - [4.4 Storage & Resource Management](#44-storage--resource-management)

---

# 1. Overview

### 1.1 Purpose & High-Level Scope
The **PDF Manipulation Desktop Application** is an enterprise-grade utility developed for the Avery Dennison (AD) / Fast React Utility (FRU) division. The software automates the extraction, sorting, deduplication, merging, and assembly of complex manufacturing and sales documentation PDFs (Packaging Front Layouts / PFL, Sales Order Lines / SOL, engineering film drawings, production dockets, and Trim Adidas Ticket Express bundles).

#### Core Problems Solved:
1. **Google Apps Script (GAS) Execution Limits:** The legacy cloud-scripted solution suffered from Google's strict 6-minute maximum execution timeout, Drive API rate limits, and slow web roundtrips when parsing multi-hundred-page PDFs.
2. **High-Latency Network Storage (UNC / NAS):** Manufacturing environments host multi-gigabyte document trees across on-premise Windows Network Shares (`\\server\share`). Serial I/O over remote shares led to application freezing and memory exhaustion.
3. **Automated Barcode Page-Range Isolation:** Production order bundles contain multiple Sales Orders (SO) and Sales Order Lines (SOL) concatenated within single files. The desktop tool provides automated 1D barcode boundary scanning with upside-down (180°) bottom-fallback detection to extract specific page subsets without human error.
4. **Desktop Autonomy & BigQuery Integration:** Operates as a self-contained, high-performance Windows desktop application utilizing multi-core hardware threading, native PyMuPDF vector extraction, Microsoft Edge Chromium WebView2 rendering, and direct Google Cloud BigQuery Stored Procedure execution (`raw_ingest.get_TE` and `raw_ingest.get_Job`).

#### Primary Functional Modules:
* **PDF Merger (PFL):** Automated multi-folder barcode matching, layout sorting, front-drawing prioritization, and an interactive 3-panel manual assembly workspace. Presets read single-column or 2-column input (`SOL`, `Job`). Supports optional Customer Job Layout page detection with automatic BigQuery `raw_ingest.get_Job` query and SOL pattern fallback. Layout folders support Google Drive and local UNC.
* **PDF Merger (RFID HT):** Dedicated module for the RFID Heat Transfer line. Presets read 4 columns (`SOL`, `Job`, `RB`, `Flag`). SOview scanning executes single-pass Barcode Range Scan for `Flag=True` items while maintaining Fast Mode / OCR Mode for `Flag=False`. Layout folder is local UNC, matched 1-to-1 by `Job` code, extracting all pages directly without barcode/OCR scanning.
* **PDF Merger (PFL Thermal):** Specialized module for PFL Thermal production. Treats all SOLs uniformly under `Flag=True` (Barcode Range Scan mode), auto-disables the OCR toggle in the UI, and accurately isolates multi-page continuation runs.
* **PDF Merger (RFID SB UQ):** Specialized workflow for Uniqlo RFID SB manufacturing lines. Automatically crawls network layout repositories via boundary-checked matching (`_find_pdfs_by_sol_boundary`) to avoid false-positive sub-string SOL collisions, preloads layout PDFs concurrently, and performs in-flight Cluster-Jump deduplication (`detect_unique_pages_dedup`) directly in memory.
* **Trim Adidas:** Specialized workflow integrating BigQuery stored procedure `get_TE` with Service Account authentication. Fetches `Ticket Express Number` and `Ordered Item`, validates missing records, parses SOview PDFs using a 3-Pattern regex engine (`*SO*`, `FOR MFG/PURCHASE X/Y`, `Page X of Y`), locates matching Ticket Express (TE) pages via `*TE*` text barcodes, and automatically pairs SOview and TE pages into ordered exports.
* **PDF Deduplicate:** High-speed removal of identical repeated pages in production docket batches using fast-path text regex and an $O(K \log(N/K))$ exponential cluster-jump algorithm.
* **PDF Combine:** Recursive multi-folder search for SOL matching, parallel network pre-fetching, and bulk split/merge assembly.
* **Over-The-Air (OTA) Updater:** Silent remote version checking against Google Drive with detached self-updating process replacement.

---

### 1.2 Tech Stack & Primary Language

| Component Layer | Technology | Exact Version | Justification / Role |
| :--- | :--- | :--- | :--- |
| **Runtime Language** | Python (CPython 64-bit) | **3.14.6** | Core business logic, threading, process orchestration |
| **Desktop Host GUI** | `pywebview` | **6.2.1** | Native Windows WinForms host embedding Microsoft Edge Chromium WebView2 |
| **Internal WSGI Server** | `bottle` | **0.13.4** | Ultra-lightweight local microserver serving static assets and session temp images |
| **PDF Rendering & Assembly** | `pymupdf` (fitz) | **1.28.2** | High-performance C-based MuPDF engine for page extraction, rendering, and vector assembly |
| **Barcode Recognition** | `pyzbar` | **0.1.9** | C-library wrapper (libzbar-64) for decoding 1D linear barcodes (Code 128/39) with 2D exclusion |
| **Optical Character Recognition** | `pytesseract` | **0.3.13** | Python wrapper for Google Tesseract OCR v5 engine (scanned PDF fallback) |
| **Image Processing** | `Pillow` (PIL) | **12.3.0** | Pixel-level manipulation, color space conversion, and ROI image clipping |
| **Google Cloud BigQuery** | `google-cloud-bigquery` | **3.45.1** | Invoking stored procedures `raw_ingest.get_TE` (Trim Adidas) and `raw_ingest.get_Job` (PFL) |
| **Google Sheets API** | `gspread` | **6.2.1** | Reading user presets and SOL lists via Google Sheets v4 API |
| **Google Drive API** | `google-api-python-client` | **2.198.0** | Recursive folder traversal and chunked stream download via Drive v3 API |
| **Authentication** | `google-auth`, `google-auth-httplib2` | **2.56.3**, **0.4.1** | GCP Service Account JWT authentication and token lifecycle |
| **Frontend Framework** | Vanilla HTML5 / ES6+ JavaScript | Modern Web Standards | Zero-dependency, lightweight, high-performance UI |
| **Drag & Drop Reordering** | `SortableJS` | **1.15.2** (`vendor/sortable.min.js`) | Touch-friendly drag-and-drop page assembly reordering |
| **Resizable Panes** | `Split.js` | **1.6.5** (`vendor/split.min.js`) | Smooth gutter resizing for the 3-panel manual workspace |
| **Executable Packager** | `pyinstaller` | **6.22.0** | Single-file Windows binary compilation with dynamic bundling |

---

### 1.3 System Architecture & Key Modules

The application adopts a decoupled **Hybrid Desktop-Web Architecture**:

```mermaid
graph TD
    subgraph UI_Presentation_Layer ["Presentation Layer (Edge Chromium WebView2)"]
        UI_HTML["assets/index.html (Single Page App)"]
        UI_JS["assets/app.js (UI State, Virtual DOM, Events)"]
        UI_CSS["assets/styles.css (Glassmorphism & Responsive Design)"]
        UI_VEND["assets/vendor/ (SortableJS, SplitJS)"]
    end

    subgraph IPC_Bridge_Layer ["IPC & Web Server Layer"]
        BOTTLE["bottle.Bottle WSGI Server (Routes: /, /temp/, assets)"]
        BRIDGE["backend/bridge.py (BridgeApi Class)"]
        TRACING["Function Execution Tracing & Profiling"]
        WIN_UTILS["backend/window_utils.py (Taskbar Flash Notification)"]
    end

    subgraph Core_Backend_Engine ["Core Backend Engine (Python 3.14.6)"]
        CONSTANTS["backend/constants.py (Centralized Constants & Settings)"]
        BQ_SERVICE["backend/bigquery_service.py (BigQuery get_TE & get_Job)"]
        DETECTOR["backend/pdf_detector.py (1D Barcode, Digital Text, Layout Job/SOL Fallback)"]
        DEDUPE["backend/pdf_deduplicator.py (Cluster-Jump Binary Search)"]
        COMBINER["backend/pdf_combiner.py (Recursive Search & Parallel Pre-fetch)"]
        PROCESSOR["backend/pdf_processor.py (PyMuPDF fitz Slicing & Merging)"]
        TRIM["backend/trim_service.py (Trim Adidas SOview 3-Pattern & TE Barcodes)"]
        UPDATER["backend/updater.py (OTA Background Update Engine)"]
    end

    subgraph Data_Storage_Layer ["Data, Cloud & File Access Layer"]
        RESOLVER["backend/file_resolver.py (Fast Local Batch os.scandir & Drive API)"]
        SHEETS["backend/sheets_service.py (Batch Sheet Ingestion)"]
        RAM_CACHE["AUTO_DETECT_RAM_CACHE & In-Memory Doc Buffers"]
        TEMP_DISK["%TEMP%/pdfmerger_* & %TEMP%/pdf_merger_*"]
        LOCAL_UNC["Local File System / Windows UNC Network Shares"]
        GCP_CLOUD["Google Cloud Platform (Drive API v3, Sheets API v4)"]
        GCP_BQ["Google BigQuery (raw_ingest.get_TE & raw_ingest.get_Job)"]
    end

    UI_JS <-->|"window.pywebview.api (Promises)"| BRIDGE
    BRIDGE -->|"window.evaluate_js (Async Progress)"| UI_JS
    BOTTLE -->|"Static Assets & /temp/ Images"| UI_HTML
    BRIDGE --> DETECTOR & DEDUPE & COMBINER & PROCESSOR & TRIM & UPDATER & WIN_UTILS
    BRIDGE --> SHEETS
    DETECTOR & DEDUPE & COMBINER & TRIM --> RESOLVER
    TRIM --> BQ_SERVICE
    BRIDGE --> BQ_SERVICE
    BQ_SERVICE --> GCP_BQ
    RESOLVER <--> GCP_CLOUD
    RESOLVER <--> LOCAL_UNC
    DETECTOR & PROCESSOR <--> RAM_CACHE
    PROCESSOR <--> TEMP_DISK
```

#### Layer Responsibilities:
1. **Presentation Layer (`assets/`):** Runs inside an embedded WebView2 runtime. Renders 6 dedicated screens (`dashboard-screen`, `input-screen`, `workspace-screen`, `auto-detect-results-screen`, `deduplicate-screen`, `combine-screen`). Employs virtualized DOM rendering to render large page grids without memory bloat.
2. **IPC & Web Server Layer (`main.py`, `backend/bridge.py`):** Exposes Python methods as asynchronous JavaScript Promises via `window.pywebview.api`. Dispatches background worker tasks (`threading.Thread`) so the UI never freezes. Concurrently hosts an in-memory `bottle` WSGI HTTP server on localhost to serve cached thumbnail JPEGs from `%TEMP%`.
3. **Core Backend Engine (`backend/`):** Houses the multi-threaded algorithms for barcode detection (1D/2D), streaming PDF generators, OCR fallback workers, vector-level PDF page concatenation using PyMuPDF, centralized constants (`backend/constants.py`), and BigQuery stored procedure integration (`backend/bigquery_service.py`).
4. **Data Access & Cloud Integration:** Connects seamlessly to both on-premise Windows network shares (`\\server\share\...`) and Google Cloud APIs (Sheets v4, Drive v3, BigQuery) using authenticated Service Account credentials.

---

### 1.4 Project Structure & Module Responsibilities

```
pdf_manipulation_desktop/
│
├── main.py                             # Application entry point: initializes WebView2, Bottle server, and temp directories
├── build.spec                          # PyInstaller packaging configuration (bundling, hiddenimports, exclusions)
├── version_info.txt                    # Windows PE binary metadata (Version, Copyright, Company, Author)
├── CHANGELOG.md                        # Version release history and change tracking
├── Document requirement.md             # Project documentation specifications and criteria
│
├── backend/                            # Core Python Application Engine
│   ├── __init__.py                     # Package initializer
│   ├── constants.py                    # Centralized constants, regex patterns, BigQuery IDs, credentials path
│   ├── bigquery_service.py             # Dedicated BigQuery service: get_TE (Trim Adidas) & get_Job (PFL)
│   ├── bridge.py                       # BridgeApi: Public JS-Python API methods, thread dispatch, execution tracing
│   ├── file_resolver.py                # Dual-mode PDF resolver: local UNC rglob search and Google Drive v3 API query
│   ├── pdf_detector.py                 # Auto-detect engine: dual-mode SOview barcode, digital text, OCR, layout Job/SOL fallback
│   ├── pdf_deduplicator.py             # Deduplication engine: cluster-jump exponential + binary search algorithm
│   ├── pdf_combiner.py                 # PDF combiner engine: recursive folder scanning and parallel UNC pre-fetching
│   ├── pdf_processor.py                # Low-level PyMuPDF processor: thumbnail generation, hires rendering, export assembly
│   ├── sheets_service.py               # Google Sheets API connector: batch ingestion of Presets, 1-col/2-col SOLs, 4-col RFID HT
│   ├── trim_service.py                 # Trim Adidas specialized workflow: SOview 3-pattern scanning and TE barcode pairing
│   ├── updater.py                      # Over-The-Air (OTA) updater: checks remote version on Drive and launches detached updater
│   └── window_utils.py                 # Native Win32 API taskbar flash notifications via ctypes
│
├── config/                             # Application Configuration
│   ├── credentials.json                # GCP Service Account Private Key (Strictly restricted, git-ignored)
│   └── version.json                    # Local semantic application version indicator (e.g. {"version": "1.3.3"})
│
├── assets/                             # Frontend Presentation Files (Embedded in WebView2)
│   ├── index.html                      # Single-page HTML application structure hosting 6 modular screens
│   ├── styles.css                      # Modern CSS design system (HSL color variables, glassmorphism, responsive grids)
│   ├── app.js                          # Frontend client logic (DOM controller, state management, event listeners)
│   ├── icon.ico                        # Application desktop icon (256x256 multi-resolution Windows ICO)
│   └── vendor/                         # Third-Party Frontend Dependencies
│       ├── sortable.min.js             # Drag-and-drop page reordering library
│       └── split.min.js                # Resizable split-pane gutter library
│
└── vendor/                             # Third-Party Native Binaries
    └── tesseract/                      # Standalone Tesseract OCR binary and trained language data files
```

---

### 1.5 Critical Execution & Runtime Flags

| Flag / Variable | Location | Type | Default Value | Dev vs Prod Behavior & Architectural Justification |
| :--- | :--- | :--- | :--- | :--- |
| `TRACING_ENABLED` | `backend/bridge.py:59` | `bool` | `True` (Dev) / `False` (Prod) | **Execution Profiling Decorator:** When `True`, every call to `BridgeApi` logs method names, truncated arguments, thread dispatch state, and execution wall-clock time in milliseconds to stdout. Set to `False` in production builds to eliminate logging overhead. |
| `_MAX_ARG_LEN` | `backend/bridge.py:61` | `int` | `40` | **Log Sanitization & Truncation:** Caps argument string representations in stdout logs to 40 characters to prevent massive JSON payloads or file buffers from flooding terminal logs. |
| `debug` | `main.py:90` | `bool` | `False` | **Chromium DevTools:** Passed into `webview.start(debug=False)`. When `True`, right-clicking the window enables Inspect Element and Chromium Developer Console. Disabled in production. |
| `CREATE_NO_WINDOW` | `backend/pdf_detector.py:134` | `int` | `0x08000000` | **Console Flash Suppression:** Injected into `pytesseract.pytesseract.subprocess_args` alongside `STARTUPINFO.wShowWindow = SW_HIDE`. Guarantees that invoking `tesseract.exe` will never pop up a black terminal window on Windows. |
| `upx` | `build.spec:96` | `bool` | `True` | **Binary Compression:** Directs PyInstaller to compress embedded binaries via Ultimate Packer for eXecutables (UPX), reducing the final standalone `.exe` footprint from ~110 MB to ~45 MB. |

---

### 1.6 Comprehensive Data Flow Trajectory

The following trajectory details the lifecycle of documents from cloud/network storage to RAM, local disk, UI rendering, and final assembly:

```
[1. Google Sheets / Drive / UNC Share]
      │
      │ HTTPS Batch Request (Sheets API v4) / UNC File Probe
      ▼
[2. Network Ingestion]
      │
      ├── Google Sheets: JSON Data -> Presets & SOL lists parsed in RAM
      ├── Google Drive: 5 MB chunked stream via MediaIoBaseDownload -> RAM BytesIO
      └── Local UNC Share: Binary read via open() into memory buffer
      │
      ▼
[3. Session Cache / Local Temp Disk]
      │
      ├── PDF files cached to %TEMP%\pdf_merger_*\<uuid>.pdf
      └── File metadata indexed into _RESOLVED_FILES_CACHE in RAM
      │
      ▼
[4. Processing & Algorithmic Detection]
      │
      ├── Page rasterization via PyMuPDF (fitz) at 72 / 150 DPI
      ├── Barcode recognition (pyzbar) / Text extraction (fitz) / OCR (Tesseract)
      └── Matched page sequences serialized to AUTO_DETECT_RAM_CACHE as fitz bytes
      │
      ▼
[5. Dynamic UI Rendering via Bottle WSGI]
      │
      ├── Page images written to %TEMP%\pdfmerger_*\<uuid>.jpg
      ├── Served by Bottle HTTP server at http://127.0.0.1/temp/<uuid>.jpg
      └── WebView2 DOM renders <img> tags in virtualized grid (app.js)
      │
      ▼
[6. Export Assembly & Final Output]
      │
      ├── User organizes Assembly Queue (SortableJS reordering)
      ├── PyMuPDF executes insert_pdf() combining vector streams in RAM
      ├── Saved directly to destination path with garbage=3, deflate=True
      └── Zero intermediate temporary PDF files created on disk
      │
      ▼
[7. Lifecycle Termination & Auto-Cleanup]
      │
      └── atexit hooks execute shutil.rmtree() clearing all %TEMP% directories
```

---

# 2. Prerequisites & Environment Setup

### 2.1 Hardware & OS Requirements
* **Operating System:** Microsoft Windows 10 (Build 19041+) or Windows 11 (64-bit).
* **System Architecture:** x86_64 (AMD64). ARM64 is supported via Windows x64 emulation.
* **WebView2 Runtime:** Microsoft Edge WebView2 Evergreen Runtime (pre-installed on Windows 11 and modern Windows 10).
* **Display & DPI Settings:**
  * Minimum Resolution: $960 \times 640$ pixels.
  * Recommended Resolution: $1440 \times 900$ pixels or higher.
  * DPI Scaling: Fully compatible with Per-Monitor DPI V2 ($100\%, 125\%, 150\%, 200\%$). PyWebView automatically negotiates coordinate scaling with WebView2.
* **Hardware Compute:**
  * CPU: Dual-core 2.0 GHz minimum; Quad-core or higher recommended (parallel OCR and export auto-scale to `os.cpu_count()`).
  * RAM: Minimum 4 GB; 8 GB+ recommended when processing multi-hundred-page engineering PDFs.

---

### 2.2 Google Cloud Platform (GCP) Configuration

#### 1. Cloud Project & IAM Identity
* **GCP Project ID:** `avy-data-rfid-fru`
* **Service Account Email:** `rfid-fru-ngocnguyen@avy-data-rfid-fru.iam.gserviceaccount.com`
* **Service Account Key Type:** JSON Private Key (`config/credentials.json`).

#### 2. Required GCP APIs
The following APIs must be enabled within the GCP Console:
1. **Google Sheets API v4** (`sheets.googleapis.com`)
2. **Google Drive API v3** (`drive.googleapis.com`)

#### 3. OAuth 2.0 Scopes
The application enforces strict **least-privilege read-only** scopes:
```python
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
```

#### 4. Google Sheets Database Configuration
* **Master Spreadsheet ID:** `1dUHAsSLSa1thA9Mmy-4_B2A9sXSqsILfRVOoz_s_gAk`
* **Required Sheets & Schema Definitions:**
  1. `Users_Preset`: Global user directory tab containing 5 columns:
     - `Col A: User` (Preset Name / Tab Name reference, e.g. "User_1")
     - `Col B: Line` (Target module line: `"PFL"` or `"RFID HT"`)
     - `Col C: SOview_Folder_Link` (Google Drive URL or Local UNC Path)
     - `Col D: Layout_Folder_Link` (Google Drive URL or Local UNC Path)
     - `Col E: Output_Folder_Link` (Local UNC / Directory Destination Path)
  2. **Per-Preset Data Sheets** (named identically to `User` in `Users_Preset`):
     - **PFL Line (`Line == "PFL"`):** Fetched via range `A:A`
       - `Col A: SOL Code` (Header in row 1, followed by 9-digit SOL strings e.g. `144187563-1`).
     - **RFID HT Line (`Line == "RFID HT"`):** Fetched via range `A:D`
       - `Col A: SOL Code` (9-digit base SO + line suffix, e.g. `144187563-1`)
       - `Col B: Job` (Alphanumeric Job ID used to match layout files, e.g. `J12345`)
       - `Col C: RB` (Ribbon / Roll Batch specification)
       - `Col D: Flag` (Boolean flag: `"true"`, `"1"`, `"yes"` for Barcode Range Scan; empty / `"false"` for Fast/OCR Mode).

---

### 2.3 Local Development Setup

#### Step 1: Python Environment Installation
Ensure Python 3.14 (64-bit) is installed and registered on the system `PATH`. Verify via terminal:
```powershell
python --version
# Expected: Python 3.14.x (or 3.11 - 3.14)
```

#### Step 2: Clone and Create Virtual Environment
```powershell
cd "c:\Users\1228411\Desktop\Merger Tool\pdf_manipulation_desktop"
python -m venv venv
.\venv\Scripts\activate
```

#### Step 3: Install Required Dependencies
Create or verify `requirements.txt` containing the pinned versions, then install:
```text
bottle==0.13.4
cryptography==50.0.0
google-api-python-client==2.198.0
google-auth==2.56.3
google-auth-httplib2==0.4.1
google-auth-oauthlib==1.4.0
gspread==6.2.1
httplib2==0.32.0
packaging==26.3
pdfminer.six==20260107
pillow==12.3.0
psutil==7.2.2
pyinstaller==6.22.0
pyinstaller-hooks-contrib==2026.6
pymupdf==1.28.2
pytesseract==0.3.13
pywebview==6.2.1
pyzbar==0.1.9
requests==2.34.2
```
Execute installation:
```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 4: External Native Binaries Setup
1. **PyZbar DLL Dependencies:** `pyzbar` requires `libiconv.dll`, `libzbar-64.dll`, and `msvcr120.dll`. Ensure Microsoft Visual C++ 2013 Redistributable (x64) is installed, or verify `msvcr120.dll` exists in `System32` or the `pyzbar` package folder.
2. **Tesseract OCR:** Install Tesseract OCR to `C:\Program Files\Tesseract-OCR\` or place portable binaries into `vendor/tesseract/`.

---

### 2.4 Enterprise Security Policy

1. **Credentials Isolation:**
   * The file `config/credentials.json` contains a high-privilege Service Account private key.
   * **Mandatory Rule:** `credentials.json` must **NEVER** be committed to Git or pushed to any public/remote repository.
2. **Mandatory `.gitignore` Rules:**
   ```gitignore
   # GCP Credentials & Secrets
   config/credentials.json
   *.pem
   *.key
   
   # Python Environment & Bytecode
   venv/
   __pycache__/
   *.pyc
   
   # PyInstaller Build Artifacts
   build/
   dist/
   *.spec.bak
   
   # Temporary Run Data
   *.log
   %TEMP%/
   ```
3. **Read-Only Scope Enforcement:** Application code never requests `drive` (write/delete) or `spreadsheets` (write) permissions. All operations are read-only, preventing accidental data alteration in enterprise repositories.

---

# 3. Execution & Build Pipeline

### 3.1 Local Development Execution
To launch the application in development mode with live tracing:
```powershell
# Activate virtual environment
.\venv\Scripts\activate

# Launch application
python main.py
```
*To enable Chromium Developer Tools during development, open `main.py` and set `debug=True` in `webview.start(debug=True)` at line 90.*

---

### 3.2 PyInstaller Packaging Pipeline

The application is compiled into a standalone, single-file Windows executable via `build.spec`.

#### Build Command:
```powershell
# Run from the root project directory:
pyinstaller --noconfirm build.spec
```
The output binary will be compiled to:
`dist/PDF Manipulation v1.1.0.exe`

#### Architectural Breakdown of `build.spec`:
1. **Dynamic Version Ingestion:**
   Reads `config/version.json` at build time to dynamically label the output binary `PDF Manipulation v{app_version}.exe`.
2. **Native DLL Bundling (`pyzbar_binaries`):**
   Explicitly extracts `libiconv.dll`, `libzbar-64.dll`, and `msvcr120.dll` from the active Python environment and packages them directly into the root `pyzbar` folder of the bundle.
3. **Data Bundles (`datas`):**
   * `('assets', 'assets')`: Bundles all HTML, CSS, JavaScript, and vendor UI libraries.
   * `('config', 'config')`: Bundles default configs and `version.json`.
   * `('vendor/tesseract', 'vendor/tesseract')`: Bundles standalone OCR binaries.
4. **Dynamic Path Resolution (`sys._MEIPASS`):**
   In dev mode, assets resolve relative to `__file__`. In compiled mode, PyInstaller unpacks files into Windows temporary storage (`sys._MEIPASS`). All backend modules resolve paths via:
   ```python
   if getattr(sys, "frozen", False):
       base = sys._MEIPASS
   else:
       base = os.path.dirname(os.path.abspath(__file__))
   ```
5. **Package Exclusions for Size Minimization:**
   Heavy unused scientific libraries are strictly excluded:
   ```python
   excludes=['matplotlib', 'numpy', 'pandas', 'PyQt5', 'tkinter', 'pdf2image']
   ```
   *Impact:* Reduces binary payload by over 180 MB.
6. **PE Version Information:**
   Compiles `version_info.txt` to inject official Windows File Properties (File Version, Product Version, Legal Copyright, Company Name).

---

### 3.3 Compilation & Build Troubleshooting

#### Issue 1: `pyzbar` Fails with `FileNotFoundError: Could not find module 'libzbar-64.dll'`
* **Root Cause:** Missing `msvcr120.dll` (Microsoft Visual C++ 2013 runtime dependency).
* **Automated Solution:** `backend/pdf_detector.py` implements `_apply_pyzbar_dll_patch()`, which dynamically scans candidate system paths (e.g., Office16, System32, SysWOW64) and copies `msvcr120.dll` into the `pyzbar` package folder before invoking `os.add_dll_directory()`.

#### Issue 2: Terminal Window Flashes When Running Tesseract OCR
* **Root Cause:** Standard `pytesseract` calls invoke `subprocess.Popen` without hiding the Windows console window.
* **Automated Solution:** `backend/pdf_detector.py` executes `_suppress_subprocess_windows()` at import time, patching `pytesseract.pytesseract.subprocess_args` with `creationflags = subprocess.CREATE_NO_WINDOW` and `STARTF_USESHOWWINDOW` with `SW_HIDE`.

#### Issue 3: Google Drive Download Drops Connection (`WinError 10054`)
* **Root Cause:** Windows socket recycling when reusing `httplib2` transport over keep-alive connections.
* **Automated Solution:** `backend/file_resolver.py` constructs a fresh `AuthorizedHttp(creds, http=httplib2.Http(timeout=30))` per service instance and applies exponential backoff retry loops.

---

# 4. Detailed Module & Logic Specifications

### 4.1 Constants & Global Configurations

As of v1.3.2, core system constants, regular expressions, and service parameters are centralized in `backend/constants.py`:

| Constant / Symbol | File Location | Value / Type | Description & Technical Significance |
| :--- | :--- | :--- | :--- |
| `SPREADSHEET_ID` | `backend/constants.py:20` | `"1dUHAsSLSa1thA9Mmy-4_B2A9sXSqsILfRVOoz_s_gAk"` | Master Google Spreadsheet containing Presets and SOL lists across all manufacturing lines |
| `USERS_SHEET_NAME` | `backend/constants.py:21` | `"Users_Preset"` | Master directory tab containing 5 columns: `User`, `Line`, `SOview_Folder_Link`, `Layout_Folder_Link`, `Output_Folder_Link` |
| `PFL_PRESET_RANGE` | `backend/constants.py:22` | `'<Preset>'!A:B` | Batch read range for PFL line sheets; extracts 2 columns: `SOL` and optional `Job` |
| `RFID_HT_PRESET_RANGE` | `backend/constants.py:23` | `'<Preset>'!A:D` | Batch read range for RFID HT line sheets; extracts 4 columns: `SOL`, `Job`, `RB`, `Flag` |
| `BIGQUERY_PROJECT` | `backend/constants.py:26` | `"navy-data-rfid-fru"` | Default GCP Project ID for BigQuery stored procedure execution |
| `PROCEDURE_GET_TE` | `backend/constants.py:27` | `"raw_ingest.get_TE"` | Stored procedure for Trim Adidas Ticket Express retrieval |
| `PROCEDURE_GET_JOB` | `backend/constants.py:28` | `"raw_ingest.get_Job"` | Stored procedure for PFL Customer Job number retrieval |
| `JOB_CODE_REGEX` | `backend/constants.py:34` | `re.compile(r'\b\d{8}\b')` | Matches standard 8-digit Customer Job codes for PFL Layout page detection |
| `SOL_REGEX` | `backend/constants.py:32` | `re.compile(r"^(\d{9})-(\d{1,3})$")` | Validates standard SOL strings (9-digit SO prefix + 1 to 3 digit line suffix) |
| `SO_REGEX` | `backend/constants.py:33` | `re.compile(r"^\d{9}$")` | Validates standard 9-digit base Sales Order (SO) codes |
| `get_credentials_path()` | `backend/constants.py:37` | `Callable[[], str]` | Resolves runtime path to `config/credentials.json` supporting frozen PyInstaller and dev mode |
| `validate_so_flags()` | `backend/pdf_detector.py:40` | `Callable` | Validates the "1 SO = 1 Flag" consistency rule; raises `ValueError` if contradictory flags exist for the same 9-digit base SO |
| `_to_bool()` | `backend/sheets_service.py:32` | `Callable` | Safely converts Google Sheets cell strings to bool: returns `True` for `"true"`, `"1"`, `"yes"`; `False` for empty or other values |
| `AUTO_DETECT_RAM_CACHE`| `backend/pdf_detector.py:94` | `dict[str, bytes]` | Global in-memory cache mapping `"{sol}_{typeId}"` to serialized PyMuPDF `fitz.Document` bytes for zero-disk Auto Detect export |
| `REMOTE_VERSION_FILE_ID` | `backend/updater.py:39` | `"1AKQM_kuyrvqeK6foCt_1O1tRJN2MZ6Vm"` | Google Drive File ID hosting the remote release `version.json` |
| `REMOTE_EXE_FILE_ID` | `backend/updater.py:40` | `"1cMHbfZJNonLNssdNvJJoyazRBnxM6aOO"` | Google Drive File ID hosting the compiled update `.zip` bundle |
| `_ID_PATTERN` | `backend/pdf_deduplicator.py:63` | `re.compile(r'(?<!\d)(?<!\d )(?:\d{13}\|\d(?: \d){12})(?! ?\d)')` | Matches 13-digit continuous or single-space separated numeric IDs for docket deduplication |
| `A4_W`, `A4_H`, `TOL` | `backend/pdf_detector.py:1100` | `595.28`, `841.89`, `1.0` | Standard A4 dimensions in PDF points ($72\text{ pt/inch}$) with $\pm 1.0\text{ pt}$ tolerance for layout file sorting |
| `TRACING_ENABLED` | `backend/bridge.py:60` | `False` (Prod) | Global switch for execution tracing and BigQuery terminal query logging |
| `_MAX_ARG_LEN` | `backend/bridge.py:62` | `40` | Maximum character length for arguments printed in trace logs |

---

### 4.2 Core Logic & Algorithmic Analysis

#### 1. SOview Barcode Detection — Three-Mode Unified Single-Pass Engine (`backend/pdf_detector.py`)

Production SOview files combine multiple Sales Orders (SO). SOLs within each SO appear in **strictly ascending line-number order** (e.g., `-1`, `-2`, `-3`). Depending on the manufacturing line and sheet configuration, `_detect_barcode_soview` executes a **Unified Single-Pass Stream Engine** supporting three operational modes:

1. **Fast Mode (`Flag=False, use_ocr=False`):** Single-page assumption.
2. **V2 OCR Mode (`Flag=False, use_ocr=True`):** Multi-page SOL range detection via Tesseract OCR and backward verification.
3. **Barcode Range Scan Mode (`Flag=True`):** Boundary-driven range accumulation for continuation pages lacking barcode markers (RFID HT line).

> **UI Entry Point:** The `⚡ OCR Mode` toggle button in the *Auto Detect Configuration* modal footer controls `use_ocr` (defaults to **OFF**). The `Flag` parameter is ingested directly from Column D of the Google Sheet preset when running in the **RFID HT** line.

---

##### The "1 SO = 1 Flag" Validation Rule (`validate_so_flags()`)

Before barcode detection or pre-fetching begins, `validate_so_flags()` inspects all target SOL entries:
* **Domain Invariant:** All SOL lines belonging to the same 9-digit base Sales Order (`parse_so(sol)`) **MUST** share an identical `Flag` boolean.
* **Integrity Enforcement:** If contradictory flags exist in the Google Sheet (e.g., `144187563-1` has `Flag=True` while `144187563-2` has `Flag=False`), the system aborts immediately with a `ValueError`:
  ```python
  "SO '144187563' has conflicting Flag values in the sheet (144187563-1=True, 144187563-2=False). Each SO must have a single consistent Flag."
  ```
* **Frontend Handling:** `app.js` runs `validateRfidFlags(state.rfidEntries)` on both "Fetch & Open" and "Run Auto Detect", displaying an error toast alert and preventing execution if inconsistent rows exist.

---

##### 1D-Only Barcode Scanning & Upside-Down (180°) Bottom Fallback
Across all barcode-scanning modes in SOview (`_detect_barcode_soview`), recognition is strictly bound to **1D linear barcodes** (e.g. Code 128 / Code 39) matching the SOL regex `^\d{9}-\d{1,3}$`:
* **2D Barcode Exclusion:** 2D symbologies (`QRCODE`, `PDF417`, `SQCODE`, `DATAMATRIX`) are explicitly ignored to prevent false-positive range closures from noisy, small square codes.
* **Top 25% + Bottom 25% Fallback:**
  1. *Primary Pass (Top 25%):* Scans the top quarter of the page at native 100 DPI (fast path for standard upright pages, taking ~18ms).
  2. *Secondary Fallback (Bottom 25%):* If the top quarter does not contain a valid 1D SOL barcode, the engine immediately crops and scans the bottom 25% of the page. This guarantees 100% detection accuracy on documents scanned upside down (180° rotated), where the large 1D Code 128 barcode is positioned at the footer.

---

##### Mode 1 — Fast Mode (`Flag=False, use_ocr=False`, default)

Assumes every SOview page containing a 1D SOL barcode is a **single-page** document. No OCR and no secondary barcode verification are performed. Optimized for maximum throughput.
* Renders at native 100 DPI.
* Scans for 1D barcodes via `pyzbar` with Top 25% + Bottom 25% fallback.
* When a target SOL is matched, records `[page_num]` and continues.

---

##### Mode 2 — V2 OCR Mode (`Flag=False, use_ocr=True`)

Designed for PFL production runs where multi-page SOL documents occur and contain "Page X of Y" textual footer notations.
1. **Step 1 (Gate Check):** Scans page $i$ at 100 DPI for a 1D barcode (Top 25% with Bottom 25% fallback). If no barcode or not in `target_sol_set`, advances $i \mathrel{+}= 1$ without OCR.
2. **Step 2 (OCR Page X of Y):** If matched, renders bottom 25% crop and runs Tesseract OCR (with fallback upscaling to 150 DPI). If $Y=1$, records `[page_num]` and advances 1 page.
3. **Step 3 (Backward Scan):** If $Y > 1$, backward scans offsets $Y-1$ down to 1 (`expected_x = offset + 1`):
   - **Boundary Pre-check:** Checks top/bottom 25% of `back_page` for any 1D barcode. If found, this page starts a *different* SOL $\rightarrow$ skips OCR to avoid invalid boundary crossing.
   - **OCR Verification:** Confirms bottom 25% OCR matches `expected_x == offset + 1`.
   - **Range Close:** On confirmation (`bx == expected_x`), records `[page_num .. page_num + expected_x - 1]` (total `expected_x` pages) and skips ahead $i \mathrel{+}= \text{expected\_x}$.
   - **Fallback:** If backward scan exhausts without confirmation (`not back_found`), records single page `[page_num]` and advances $i \mathrel{+}= 1$.

---

##### Mode 3 — Barcode Range Scan Mode (`Flag=True` or PFL Thermal)

Engineered for the **RFID Heat Transfer (RFID HT)** manufacturing workflow and the **PFL Thermal** module. In these workflows, an order starts with a page bearing a 1D SOL barcode, followed by an arbitrary number of continuation pages that contain **no barcode markers**.
* In **PFL Thermal**, all SOLs are automatically assigned `Flag=True` (OCR toggle is hidden in the UI).
* In **RFID HT**, `Flag=True` is read dynamically per SOL from the Google Sheet preset.

```mermaid
flowchart TD
    Start([Scan Page i @ 100 DPI Top 25% + Bottom Fallback]) --> HasBC{1D Barcode\nFound?}
    
    HasBC -- No --> InRange{Is a Flag=True\nRange Open?}
    InRange -- Yes --> Accumulate["Accumulate page i into open_sol\n(Advance i += 1)"] --> NextPage([Next Page])
    InRange -- No --> SkipPage["Skip page i\n(Advance i += 1)"] --> NextPage

    HasBC -- Yes --> CheckOpen{Is a Flag=True\nRange Open?}
    CheckOpen -- Yes --> ClosePrev["Close previous open_sol at (i - 1)\nclosed_ranges[open_sol] = [open_page .. i-1]"] --> EvalBC
    CheckOpen -- No --> EvalBC["Evaluate Detected 1D Barcode"]

    EvalBC --> InTarget{barcode_val in\ntarget_sol_set?}
    InTarget -- No --> SkipBC["Non-target SOL\n(Advance i += 1)"] --> NextPage
    InTarget -- Yes --> AlreadyDone{Already recorded\nin closed_ranges?}
    AlreadyDone -- Yes --> SkipBC

    CheckFlag{sol_flags[barcode_val]\n== True or PFL Thermal?}
    AlreadyDone -- No --> CheckFlag
    CheckFlag -- Yes --> OpenRange["OPEN NEW RANGE:\nopen_sol = barcode_val\nopen_page = i"] --> Advance1["i += 1"] --> NextPage
    CheckFlag -- No --> FastOrOCR["Execute Mode 1 (Fast) or Mode 2 (OCR)\nbased on use_ocr"] --> NextPage
```

**Range Delimitation Rules:**
1. **Open Boundary:** Opened at `current_page` when a 1D barcode matching a target SOL with `Flag=True` is decoded (`open_sol = barcode_val, open_page = page_num`).
2. **Continuation Accumulation:** Every subsequent page where no 1D barcode is detected is automatically absorbed into `open_sol`.
3. **Close Boundary Trigger 1 (Subsequent Barcode):** The moment **ANY** valid 1D barcode is encountered on a subsequent page (whether another target SOL or an unselected SOL), the active range is closed at `current_page - 1`:
   $$\text{closed\_ranges}[open\_sol] = [\text{open\_page} \dots \text{page\_num} - 1]$$
4. **Close Boundary Trigger 2 (End of File):** When the scanning loop reaches `total_pages`, any open range is closed at the file boundary:
   $$\text{closed\_ranges}[open\_sol] = [\text{open\_page} \dots \text{total\_pages}]$$
5. **Close Boundary Trigger 3 (Early Miss-Exit):** In single-SO files, if the first scanned line exceeds `max_target_line`, the open range is closed at `current_page - 1` immediately before triggering early exit.

---

##### Unified Single-Pass Sequential Architecture

A single production SOview PDF file frequently bundles multiple Sales Orders with mixed requirements (some orders have `Flag=False`, others have `Flag=True`).
* The engine **NEVER** re-opens the file or performs multiple passes.
* A single sequential loop (`while i < total_pages:`) traverses the document from beginning to end.
* Dynamic state branching evaluates each page in $O(1)$ overhead, seamlessly switching between Fast Mode, OCR verification, and Barcode Range accumulation in real time.

---

##### Common Early-Exit Rules (All Modes)

| Rule | Trigger Condition | Algorithmic Action |
|---|---|---|
| **Complete-Match Exit** | All target SOLs have a closed range recorded | Terminate document processing immediately; release PyMuPDF handle. |
| **Miss-Exit** | Single-SO file: first detected SOL line number $> \text{max\_target\_line}$ | Close any active `open_sol` at `page_num - 1` and terminate immediately (ascending order guarantees targets cannot appear further). |

##### Operational Mode Comparison

| Feature / Metric | Old Algorithm | Mode 1 (Fast) | Mode 2 (V2 OCR) | Mode 3 (Barcode Range Scan) |
| :--- | :--- | :--- | :--- | :--- |
| **Target Manufacturing Line** | Legacy PFL | PFL (Default) | PFL (Multi-Page) | **RFID HT** (`Flag=True`) & **PFL Thermal** |
| **Render Resolution** | 150 DPI (Full page) | 100 DPI (Top 25% + Bot Fallback) | 100 DPI (Full page) | 100 DPI (Top 25% + Bot Fallback) |
| **Barcode Scan Area** | Full page pyzbar | Top 25% (Fallback Bot 25%) | Top 25% (Gate check) | Top 25% (Fallback Bot 25%) |
| **Barcode Type Filter** | 1D & 2D mixed | **1D Only** (Excludes 2D QR/DM) | **1D Only** | **1D Only** |
| **Multi-page Delimitation** | 1D $\rightarrow$ wait for 2D barcode | ❌ None (1 page only) | ✅ OCR "Page X of Y" + Backward Scan | ✅ **Range accumulation until next barcode or EOF** |
| **OCR Dependency** | ❌ None | ❌ None | ✅ Tesseract (Bottom 25%) | ❌ None |
| **Page Skip-Ahead** | ❌ No | ❌ No | ✅ $i \mathrel{+}= \text{expected\_x}$ upon confirmation | ❌ Sequential single-pass |
| **Relative Throughput** | Baseline | **Fastest (~10x)** | Moderate | **Fastest (~10x)** |

---

#### 2. Deduplication Cluster-Jump Optimization (`backend/pdf_deduplicator.py`)
Manufacturing dockets print identical copies of pages in contiguous blocks ($A A A \dots B B B \dots C C C \dots$). Naive scanning requires $O(N)$ barcode renders. The Cluster-Jump algorithm optimizes this to $O(K \log(N/K))$:

```
Cluster A (Pages 1-10)          Cluster B (Pages 11-25)
[A1] -> Jump 1 -> [A2]
     -> Jump 2 -> [A4]
     -> Jump 4 -> [A8]
     -> Jump 8 -> [B16] (Boundary Overstepped!)
                  │
                  ▼ Binary Search between Page 8 and 16
                  Pinpoints Page 11 as exact start of Cluster B
```

1. **Step 1: Text Fast-Path:** Page 1 is scanned via `fitz.get_text()`. If the 13-digit `_ID_PATTERN` is found in text, the entire document is processed in text mode (sub-second execution). If no text exists, fallback to pyzbar barcode mode.
2. **Step 2: Exponential Jump:** From the current known page, step forward by $2^0, 2^1, 2^2, 2^3\dots$ pages.
3. **Step 3: Binary Search:** As soon as an ID mismatch or change is detected, binary search between the last match and the overstepped boundary to find the exact first page of the new cluster.
4. **Performance Gain:** Reduces barcode rasterization from 1,000 pages to ~40 page inspections (a 25x–50x speedup).

---

#### 3. Layout Detection & Matching — PFL vs RFID HT Isolation (`backend/pdf_detector.py`)

Layout processing varies fundamentally between manufacturing lines. The application strictly isolates their discovery, extraction, and sorting rules:

##### A. PFL Manufacturing Line (Packaging Front Layout)
* **File Discovery:** Layout PDFs are named after or matched against the 9-digit base Sales Order (`parse_so(sol)`). Files can reside on local UNC network shares or in Google Drive folders.
* **Extraction Logic:** Executes priority-chained detection (`config["layout"]`, defaulting to `["digital", "scanned"]`). PyMuPDF extracts digital text from each page to find the specific SOL pattern; if no digital text is found, Tesseract OCR fallback scans the pages. Only pages containing matching SOL text are extracted.
* **Multi-File Layout Priority Sorting:** When multiple layout files match a single SO (e.g., separate front film and documentation PDFs), the system applies a dual-heuristic sorting pipeline:
  1. **Keyword Analysis:** Filenames containing `front` or `film` (case-insensitive) receive **Priority 0 (Top)**. Filenames containing `layout` or `docs` receive **Priority 1 (Bottom)**.
  2. **Geometry Heuristic Fallback:** If filename keywords are ambiguous, contradictory, or absent, PyMuPDF inspects the page `trimbox` / `rect` dimensions against standard A4 points:
     $$\text{A4 Width} = 595.28\text{ pt}, \quad \text{A4 Height} = 841.89\text{ pt} \quad (\pm 1.0\text{ pt})$$
     - If all pages in the document match A4 (portrait or landscape), the file is classified as documentation $\rightarrow$ **Priority 1 (Bottom)**.
     - If any page deviates from A4 (e.g., custom wide engineering film drawings), it is classified as engineering layout $\rightarrow$ **Priority 0 (Top)**.
  3. **Ordering Guarantee:** Items are sorted with Priority 0 before Priority 1 (`key = lambda e: (priority_dict.get(e["fileIdx"], 0), e["fileIdx"], e["pageIndex"])`).

##### B. RFID HT Manufacturing Line (RFID Heat Transfer)
* **File Discovery:** Layout files are located on local UNC network shares (Google Drive toggle is hidden in UI). Files are matched **1-to-1 by Job Code** (`job_code = job_map[sol]` from Column B of the Google Sheet preset) rather than by base SO.
* **Extraction Logic:** Directly extracts **ALL pages** (`1` to `doc.page_count`) from every matching job layout file into the assembly.
* **Zero OCR / Text Overhead:** No text regex matching or OCR scanning is performed on RFID HT layout files. Every page is recorded with `method: "layout_all_pages"`, maximizing extraction throughput and eliminating detection misses.

##### C. Dynamic SOL Cluster Parser (`_extract_sols_from_text`)
Digital text and OCR scanned text frequently contain composite SOL cluster notations where multiple line items share a single 9-digit base Sales Order. The regex engine evaluates text blocks via `_DYNAMIC_SOL_CLUSTER_PATTERN`:
* **Syntax Grammar:**
  $$\text{Prefix (not digit)} + \text{SO (9 digits)} + \text{"-"} + \text{First Line (1-3 digits)} + \left[ \text{delimiter} + \text{Line (1-3 digits)} \right]^*$$
  Delimiters support commas (`,`), semicolons (`;`), and hyphens (`-`).
* **Format Disambiguation:**
  - *Continuous Range:* If followed by exactly one hyphen (`-`) and a larger line number, expands all intermediate lines (e.g. `146842454-1-4` $\rightarrow$ `lines 1, 2, 3, 4`).
  - *Discrete List:* If separated by commas or semicolons, or multiple hyphens, extracts exact discrete lines (e.g. `146842455-1,5,9` $\rightarrow$ `lines 1, 5, 9` or `146842454-1; 2; 3; 4` $\rightarrow$ `lines 1, 2, 3, 4`).
  - *Suffix Protection:* Delimited lines followed by letters (e.g. `146842454-1-61mm`) are automatically excluded from line expansion, safely extracting only `146842454-1`.

---

#### 4. Architecture of Unified Detection Loop (`run_auto_detect_all`)

To maintain clean code separation and eliminate duplicate logic, `backend/pdf_detector.py` orchestrates detection through a unified `for type_id in pdf_type_ids:` loop:

```
run_auto_detect_all(sols, folder_inputs, is_drive_map, config, ocr_flag, sol_flags, job_map)
   │
   ├── Step 1 & 2: Batch find files across all folders (SO-based or Job-based)
   │
   ├── Step 3: Initialize assembly slots { sol: { soview: [], layout: [] } }
   │
   ├── Step 4: Unified Type Loop: for type_id in pdf_type_ids:
   │     │
   │     ├── Case A: RFID HT Layout (type_id == "layout" and job_map exists)
   │     │     └── Iterate clean_sol_list -> job_code = job_map[sol]
   │     │         └── Extract ALL pages (1 .. total_pages) with method="layout_all_pages"
   │     │
   │     └── Case B: Standard Priority-Chained Detection (SOview, PFL Layout)
   │           ├── Build unique_files table: deduplicate physical PDFs across multiple SOs
   │           ├── For each unique file -> run _detect_pages_all_sols(..., chain=config[type_id])
   │           ├── Merge matched pages into assembly[sol][type_id] using local_idx
   │           └── Post-process multi-file layout priority sorting (PFL layout only)
   │
   └── Step 5: _extract_to_ram(assembly, batched_files, pdf_type_ids, job_map)
         └── Extract matched page vector streams into AUTO_DETECT_RAM_CACHE["{sol}_{type_id}"]
```

**Key Architectural Guarantees:**
1. **Frontend-Driven Priority Chains:** Priority chains are consumed directly from `config[type_id]` passed by the frontend (`app.js`), avoiding hardcoded defaults or duplicate overrides.
2. **Physical File Deduplication:** `unique_files` maps `(fileName, filePath)` so each physical document on disk/network is inspected exactly once, even when referenced by dozens of different Sales Orders.
3. **RAM Cache Pre-Extraction (`_extract_to_ram`):** As soon as detection completes, sub-documents containing only the matched pages are sliced and saved as serialized `bytes` in `AUTO_DETECT_RAM_CACHE`. When the user clicks "Export", the files are written directly from memory without re-reading the source PDFs.

---

#### 5. Combiner Parallel Pre-Fetching (`backend/pdf_combiner.py`)
Reading dozens of multi-megabyte PDFs serially across a Windows UNC share (`\\server\share`) incurs severe network latency.
* `combine_pdfs` uses a `ThreadPoolExecutor(max_workers=min(16, os.cpu_count()))` to load all binary streams concurrently into memory.
* The assembly loop then performs pure in-memory `fitz.insert_pdf()` operations with zero disk or network I/O during concatenation.

---

#### 6. Trim Adidas Workflow & BigQuery Engine (`backend/trim_service.py`)

The **Trim Adidas** module implements a dedicated dual-document pairing pipeline integrating Google BigQuery:

```mermaid
flowchart TD
    InputSOLs([Target SOL List]) --> Step1[Call BigQuery Stored Procedure\navy-data-rfid-fru.raw_ingest.get_TE]
    Step1 --> ValidateBQ{Validate Ordered Item\n& Ticket Express}
    
    ValidateBQ -->|Missing TE/Item| ModalWarn[Display Warning Modal in UI\nOption to Proceed or Abort]
    ValidateBQ -->|Valid| Step2[Scan SOview PDFs\n3-Pattern Engine]
    ModalWarn -- User Continues --> Step2
    
    Step2 --> P1["Pattern 1: Check *SO* Barcode text"]
    P1 --> P2["Pattern 2: FOR MANUFACTURING X/Y"]
    P2 --> P3["Pattern 3: Page X of Y Continuation"]
    
    Step3[Scan TE Layout PDFs] --> TEFind["Match *TE* Barcode text (Exact 1 page per TE)"]
    
    Step2 & Step3 --> MergePairs["Pair SOview pages + TE pages per SOL"]
    MergePairs --> AutoExport["Export Merged Pairs to Output Folder"]
```

1. **BigQuery Procedure (`raw_ingest.get_TE`):** Evaluates `STRUCT(so AS so, line AS line)` arrays in a single cloud query over BigQuery Client, retrieving `Ticket Express Number` and `Ordered Item`.
2. **Missing Data Pre-check:** If any target SOL is missing `Ordered Item` or `Ticket Express Number`, a non-blocking modal highlights the missing keys and warns the operator before scanning.
3. **SOview 3-Pattern Scanning:**
   - *Pattern 1 (`*SO*`):* Identifies the base Sales Order.
   - *Pattern 2 (`FOR MANUFACTURING X/Y`):* Matches line item $X$ against target SOL.
   - *Pattern 3 (`Page X of Y`):* Continues capturing multi-page SOview sheets until $X \ge Y$.
4. **TE Barcode Fast-Path (`*TE*`):** Scans the Ticket Express PDF using vector text extraction for `*{te}*`, immediately recording the single matching page per TE code.

---

#### 7. Fast Batch Local Search (`backend/file_resolver.py: find_all_pdfs_local_batch`)

To bypass Windows Network Share (UNC) SMB latency caused by deep recursive `rglob` traversal:
* Uses non-recursive `os.scandir` iteration on root/sub-folders.
* Pre-filters directory names against target SO prefixes (e.g. matching date/month folders).
* Eliminates redundant network roundtrips, reducing multi-folder discovery time from 15–20s down to <1s.
* **v1.2.2 — Inline mtime Caching:** `st_mtime` is now read from `entry.stat()` directly during the `scandir` walk (already in the OS directory cache) and stored in `item["mtime"]`. The `sort_by_mtime_desc` sort lambda uses this cached field instead of re-issuing `os.path.getmtime()`, eliminating one additional SMB syscall per discovered file.

---

#### 8. Single-Pass RAM Preload & Inline Sub-Document Slicing (`backend/pdf_detector.py`)

As of v1.2.3, the previous two-pass I/O and separate `_extract_to_ram` pre-fetch architecture has been replaced with a **Single-Pass I/O + Double Eviction** strategy across the detection and RAM caching pipeline:

**1. Single-Pass RAM Preload (Step 2 Scan Loop):**
Each candidate PDF file is loaded into RAM (`file_result["bytes"] = fp.read()`) immediately upon entering the sequential scan loop before page detection begins (reusing pre-downloaded buffers for Google Drive files). Detection runs entirely from in-memory bytes, eliminating repetitive SMB/network reads:
```python
if not file_result.get("bytes"):
    _src_path = file_result.get("path", "")
    if _src_path and (os.path.isfile(_src_path) or _src_path.startswith(r"\\")):
        try:
            with open(_src_path, "rb") as _fp:
                file_result["bytes"] = _fp.read()
        except Exception as _load_exc:
            print(f"[preload] {file_name}: {_load_exc}")

pdf_source = file_result.get("bytes") or file_result.get("path")
```

**2. Early Eviction:**
If a file yields zero matched pages across all target SOLs (`total_pages_matched == 0`), `file_result["bytes"]` is immediately deleted (`del file_result["bytes"]`) to reclaim heap memory, avoiding unnecessary memory retention during subsequent scans. Files with matched pages retain their in-memory bytes for the layout priority sort (PFL) and subsequent sub-document slicing.

**3. Inline In-RAM Slicing & Late Eviction (Step 5):**
The standalone `_extract_to_ram()` function has been removed. Matched pages are sliced directly in-memory via `fitz.open(stream=bytes) → insert_pdf → tobytes(deflate=False, garbage=0)` with zero network I/O. Once a sub-document is serialized to `AUTO_DETECT_RAM_CACHE`, source file bytes are evicted (`del file_entry["bytes"]`) immediately, completely preventing "double memory" peaks.

**4. Double Eviction in Trim Adidas (`backend/bridge.py` Stage 6):**
Similarly, Trim Adidas Stage 6 executes late eviction (`del ev_fe["bytes"]`) immediately after each sub-document is written to `AUTO_DETECT_RAM_CACHE`, maintaining minimal memory usage throughout large order batches.

| Metric | Before (v1.2.2 2-Pass I/O) | After (v1.2.3 Single-Pass + Double Eviction) |
| :--- | :--- | :--- |
| Network File Reads | 2 passes (Scan pass + Extraction pass) | **1 single pass** (loaded during scan, zero I/O for extraction) |
| Memory Footprint | Kept all loaded source PDFs + sub-documents | **Eagerly freed** via Early Eviction (0 matches) & Late Eviction (after slicing) |
| RAM Extraction Latency | ~3,000–5,000 ms (Parallel prefetch) | **Sub-second** (Pure CPU/RAM slicing, 0 network syscalls) |

---

#### 9. `save_pdf_safely()` — Safe Output Writer (`backend/pdf_processor.py`)

Replaces all raw `doc.save(path, ...)` calls in the export pipeline with a safe wrapper that handles two common production failure modes:

1. **File-Lock Conflict:** If the target `.pdf` is open in Adobe Acrobat, Foxit Reader, or a browser, `doc.save()` raises `PermissionError`. `save_pdf_safely` detects the lock via `_is_file_locked()` (a non-destructive `r+b` probe) and auto-increments the output name: `base.pdf` → `base (1).pdf` → `base (2).pdf` ... up to 100 attempts before re-raising.

2. **Windows MAX_PATH Violation:** Windows enforces a 259-character absolute path limit. When an auto-increment suffix `(i)` is appended, the function mathematically truncates the base name so the total path never exceeds 259 characters:
```python
allowed_base_len = max(5, effective_max - len(suffix))
truncated_base = clean_base[:allowed_base_len].rstrip(" ,-_")
```

Adoption scope:
* `execute_export` — merge mode and split-per-SOL mode
* `deduplicate_pdf` (single file deduplication)
* `deduplicate_batch` (combined output file)

---

#### 10. RFID SB UQ Workflow & Boundary-Matched File Resolution (`backend/pdf_detector.py`)

The **PDF Merger (RFID SB UQ)** module introduces an automated dual-document assembly pipeline tailored for Uniqlo RFID SB lines:
1. **Filename Boundary Checking (`_find_pdfs_by_sol_boundary`):**
   Uniqlo layout PDFs are structured as `{SOL}_{...rest}.pdf` (e.g. `132337478-1_16489676_4-249177-333-00_...pdf`). Standard sub-string matching often leads to severe false-positive collisions (e.g. `144187563-1` falsely capturing `144187563-10_...pdf`). The dedicated crawler crawls network folders via 32 concurrent threads and strictly requires the terminating character immediately after the SOL code to be in `('_', '.', ' ')` or at string boundary:
   ```python
   end = idx + len(sol_l)
   if end < len(fname_norm) and fname_norm[end] not in ('_', '.', ' '):
       continue
   ```
2. **Parallel Small-First Preload + In-Flight Cluster-Jump Deduplication (`Case C`):**
   Layout files for all matched SOLs are sorted by file size (`Small-First`) to maximize pipeline responsiveness. Files are preloaded into memory and immediately deduplicated in-flight using `detect_unique_pages_dedup()` from `pdf_deduplicator.py`.
3. **Adaptive Worker Tuning for High-Latency Network Shares:**
   Benchmarking across enterprise SMB shares revealed severe WAN bandwidth saturation when streaming heavy PDFs (6MB–27MB) over 4 concurrent workers on servers like `FSPDVN06` (~2.95 MB/s ceiling). The pipeline dynamically tunes layout concurrency to `max_workers = min(2, len(sorted_net_files))` for RFID SB to avoid TCP/SMB socket contention.

---

#### 11. Smart Adaptive I/O Engine & Remote Path Detection (`backend/pdf_processor.py`)

Introduced in v1.3.0 to replace ad-hoc file reading and solve SMB network latency contention across all modules:
1. **Network Location Detection (`is_network_path`):**
   Inspects file path representations on Windows:
   - UNC Shares: Evaluates prefix `\\` (e.g. `\\server\share\...`).
   - Mapped Remote Drives: Uses Win32 API `ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') == 4` (`DRIVE_REMOTE`).
2. **Adaptive Document Loader (`open_pdf_smart`):**
   ```python
   def open_pdf_smart(source: str | bytes | bytearray) -> fitz.Document:
       if isinstance(source, (bytes, bytearray)):
           return fitz.open(stream=source, filetype="pdf")
       if is_network_path(source):
           pdf_bytes = read_pdf_bytes_smart(source)
           return fitz.open(stream=pdf_bytes, filetype="pdf")
       else:
           return fitz.open(source)
   ```
   - For network locations, executes a single sequential read (`read_pdf_bytes_smart`) into RAM before handing the byte stream to PyMuPDF. This completely eliminates thousands of small, random `fseek` round-trips over SMB, producing a **30x–570x speedup** on multi-page files.
   - For local SSD/HDD drives, opens directly via `fitz.open(path)` to leverage Windows OS page cache and zero memory duplication.

---

#### 12. Zero-Network Instant Auto Detect Preview (`backend/bridge.py` & `assets/app.js`)

In previous versions, opening the Continuous Vertical Viewer modal (`ad-preview-modal`) triggered repetitive network reads to fetch page thumbnails via `fetch_page_range`. In v1.3.0+:
* **In-RAM Sub-Document Rendering:** `fetch_auto_detect_preview(sol, sol_assembly_json)` accesses pre-extracted PDF byte buffers already present in `AUTO_DETECT_RAM_CACHE`.
* **Instantaneous Response:** Renders thumbnails at 96 DPI JPEG directly from RAM buffers with zero network I/O, delivering instant preview rendering.
* **Graceful Network Fallback:** If any page card is missing from the cache, `window._onAutoDetectPreviewReady` automatically falls back to `fetch_page_range`.

---

#### 13. Multiline Wrapped Page Number Recovery (`backend/trim_service.py`)

Introduced in v1.3.1 to solve a critical page detection drop in multi-page Trim Adidas orders ($\ge 100$ pages):
1. **Root Cause Analysis:**
   In standard Oracle/SAP Trim SOview forms, the page counter box is bounded to ~6 characters. For single- or double-digit pages (`1/126` to `99/126`), the counter fits on a single text line. When reaching three-digit pages (`100/126` to `126/126`), the 7-character string overflows, causing PyMuPDF to extract text with the final digit wrapped onto the next line (e.g. `104/12\n6`).
2. **Regex Expansion (`_P2_REGEX`):**
   ```python
   _P2_REGEX = re.compile(
       r"FOR (MANUFACTURING(?:/PURCHASE)?|PURCHASE)(?:\s*[\r\n]+\s*|\s+)(\d{1,3})\s*/\s*(\d{1,3}(?:\s*[\r\n]+\s*\d+)?)",
       re.MULTILINE,
   )
   ```
3. **Whitespace Sanitization & Safe Integer Conversion:**
   In `_extract_page_patterns()`, captures for both `n1` and `n2` are sanitized via `re.sub(r"\s+", "", ...)` before `int()` conversion. This eliminates `ValueError` exceptions caused by embedded linebreaks (`"12\n6"` $\rightarrow$ `"126"`) and ensures correct numeric evaluation (`104 <= 126`), restoring 100% page attribution across all 126 pages.

---

#### 14. Native Windows Taskbar Attention Notification (`backend/window_utils.py` & `backend/bridge.py`)

Added in v1.3.1 to alert operators when extensive background operations finish while the application window is minimized or inactive:
1. **Direct Win32 API Integration (`ctypes`):**
   Calls `user32.FlashWindowEx` using `FLASHWINFO` struct with flag `FLASHW_TIMERNOFG` (`0x0000000C`), which continuously flashes the application icon on the Windows Taskbar until the operator brings the window to the foreground.
2. **Safe HWND Resolution (`get_window_hwnd`):**
   Resolves the native window handle by probing `window.native.Handle` (WinForms / WebView2) with fallback to `user32.FindWindowW(None, window_title)`.
3. **Automated Notification Hook:**
   `BridgeApi.flash_window()` is invoked automatically upon completion of long-running operations: `run_auto_detect` (PDF Merger) and `run_auto_detect_trim` (Trim Adidas).

---

#### 15. RFID SB Latency-Optimized Worker Scaling & Granular Inspection Grid

Introduced in v1.3.1 for the RFID SB workflow:
1. **High-Latency SMB Worker Tuning:**
   Empirical testing across remote UNC shares (`\\fppdvn03`) demonstrated that per-file connection handshakes cost ~100ms fixed RTT overhead. Scaling SOview preloading concurrency from 4 to 12 workers reduced total preload latency from ~559ms to ~255ms.
2. **Dual Page Metric Extraction:**
   `pdf_deduplicator.detect_unique_pages_dedup()` supports `return_total=True`, returning `(kept_pages, total_pages)` to capture both raw and deduplicated counts in a single pass.
3. **Granular Review Grid (`assets/app.js`):**
   Provides a comprehensive breakdown per SOL: SOview file name & page count, Layout file name & deduplication ratio (`raw -> kept`), total combined pages, warning tags for missing files / zero pages, and direct preview/file open actions.

---

#### 16. PFL Customer Job Layout Matching & BigQuery Integration (`backend/pdf_detector.py` & `backend/bigquery_service.py`)

Introduced in v1.3.2 to enhance Packaging Front Layout (PFL) automation with Customer Job identification:

1. **Two-Column Input Ingestion (`SOL`, `Job`):**
   - The PFL module supports both 1-column (`SOL`) and 2-column (`SOL`, `Job`) inputs from Google Sheets (`A:B` range via `backend/sheets_service.py`) and manual textarea entry.
   - When the user selects a preset or pastes multi-column data, `SOL` and `Job` are mapped into the execution state.

2. **Automated BigQuery Stored Procedure Resolution (`raw_ingest.get_Job`):**
   - If `job_flag=True` in PFL and any SOL is missing a Customer Job number, `BridgeApi.run_auto_detect()` invokes `fetch_bigquery_job()` from `backend/bigquery_service.py`.
   - Constructs a structured query invoking `navy-data-rfid-fru.raw_ingest.get_Job`:
     ```sql
     CALL `navy-data-rfid-fru.raw_ingest.get_Job`([STRUCT(so AS so, line AS line), ...])
     ```
   - Automatically parses `job_code` (8 digits) and maps it to the respective SOL.
   - If BigQuery returns empty records or encounters an error, a non-blocking confirmation modal (`#bigquery-error-modal`) alerts the operator with the list of unresolved SOLs, allowing them to proceed with fallback matching or cancel.

3. **High-Speed Layout File Discovery by SO Prefix:**
   - Candidate layout files in the layout directory (UNC or Google Drive) continue to be filtered by the 9-digit Sales Order prefix (`{so}_*`). This avoids scanning millions of unrelated files across network shares, keeping directory discovery instantaneous.

4. **Dual-Strategy Layout Page Detection with Automatic Fallback:**
   - Inside candidate layout PDF files, page text is inspected using vector text extraction:
     - **Primary Tier (Customer Job Code):** Matches standard 8-digit Customer Job codes (`JOB_CODE_REGEX = re.compile(r'\b\d{8}\b')`).
     - **Fallback Tier (SOL Pattern):** If the Job code is not found on any page or is missing, the engine automatically falls back to regex matching by SOL pattern (`\b{sol}\b`).
   - If neither matches, the SOL is marked as missing layout pages.

5. **Auto Detect Summary Modal Reporting:**
   - The Auto Detect Summary modal explicitly annotates the matching method used per SOL:
     - `Found by Job`: Matched via 8-digit Customer Job code.
     - `Found by SOL Pattern Fallback`: Matched via fallback SOL pattern.
   - The Layout Results review grid remains clean and uncluttered, preserving the standard operator workflow while providing full auditability in the final completion summary.
   - All Job detection and BigQuery `get_Job` routines are strictly scoped to the PFL module, ensuring zero side-effects on RFID HT, Trim Adidas, PFL Thermal, RFID SB, or Deduplication modules.

---

#### 17. Parallel Multi-Branch Batch Search & Async I/O Orchestrator (`backend/pdf_detector.py`, `backend/bridge.py`, `backend/file_resolver.py`)

Introduced in v1.3.3 to accelerate file discovery and eliminate serial latency across heterogeneous storage systems (Google Drive API, SMB Windows Network Shares, and BigQuery):

1. **Parallel Multi-Branch Batch Search Engine (`_fetch_type_worker`):**
   - Step 2 (Pre-fetch all files using Batch Search) in `run_auto_detect_all` previously executed sequentially across each entry in `pdf_type_ids` (e.g. `soview`, `layout`).
   - Refactored into a concurrent multi-worker executor using `concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(len(pdf_type_ids), 4)))`.
   - Each target type runs independently in its own thread, dispatching Drive API queries and local/UNC directory scans in parallel, cutting batch search latency by up to 50–70%.

2. **Concurrent SMB Network Preload Pipeline:**
   - In heterogeneous pipelines (e.g. `soview` on UNC network share `\\server\share` and `layout` on Google Drive), reading network files sequentially used to stall page detection while waiting for Drive downloads.
   - For `soview` UNC/local paths, `_fetch_type_worker` identifies found files not yet in RAM and concurrently pre-fetches their raw binary content (`read_pdf_bytes_smart`) into `fe["bytes"]` using up to 12 threads (`workers = min(12, len(sorted_net))`).
   - Candidate files are pre-sorted ascending by byte size (`os.path.getsize`) to maximize pipeline saturation.
   - Network files arrive in RAM in advance while Drive layouts are being downloaded, eliminating SMB I/O wait times during subsequent detection loops.

3. **Background Asynchronous BigQuery Resolution:**
   - **PFL Workflow (`bridge.py`):** BigQuery Customer Job retrieval (`fetch_bigquery_job` calling `raw_ingest.get_Job`) is dispatched to a background daemon thread (`bq_thread`) immediately when auto-detect starts. It executes concurrently with Step 2 Batch Search. The detection loop joins `bq_resolver()` right before Step 4 (Detection Loop), preventing BigQuery API latency from blocking directory scans.
   - **Trim Adidas Workflow (`auto_detect_trim_pdfs`):** BigQuery Stage 1 (`fetch_bigquery_data` calling `raw_ingest.get_TE`) and Stage 3 SOview batch file discovery (`find_all_pdfs_local_batch`) execute simultaneously via `_bg_soview_search`. The wait time previously spent scanning large SOview directories after querying BigQuery is completely eliminated.

4. **Dynamic Upward Ancestor Hierarchy Resolution for Google Drive (`backend/file_resolver.py`):**
   - Previously, Google Drive searches ran an exhaustive upfront folder query (`mimeType = 'application/vnd.google-apps.folder'`), scanning 1,000+ folders into memory on each run.
   - Replaced by a thread-safe, on-demand upward ancestor crawler (`folder_ancestor_cache` guarded by `threading.Lock`).
   - When candidate files matching SO/Job codes are discovered, their parent folders are crawled upwards toward `folder_id` with memoization. Large unreferenced directory trees are never queried, saving dozens of Drive API round-trips.

5. **High-Throughput Parallel Google Drive Downloader:**
   - Google Drive downloads now utilize a persistent HTTP session (`requests.Session`) with reusable connection pools and Bearer token headers.
   - Concurrency is expanded to 10 parallel download workers (`ThreadPoolExecutor(max_workers=10)`).
   - Integrated retry logic (up to 3 attempts with 0.3s linear backoff) to handle transient network hiccups gracefully.
   - Streamed bytes are saved directly to session temp files and populated into `_RESOLVED_FILES_CACHE`.

---

### 4.3 Comprehensive Logic-to-UI Mapping

| User Interaction / Event | UI Component & Screen | BridgeApi Method | Threading Model | Backend Action & UI State Update |
| :--- | :--- | :--- | :--- | :--- |
| Application Launch | `#app-loading-overlay` | `get_initial_data("PFL")`<br>`get_local_version()` | Synchronous / Async Init | Ingests Google Sheets presets for default line; updates local version badge; fades out loading overlay. |
| Select Module Card (Dashboard) | `.module-card[data-module="merger-pfl"]`<br>`.module-card[data-module="merger-rfid"]`<br>`.module-card[data-module="merger-thermal"]`<br>`.module-card[data-module="merger_rfid_sb"]`<br>`.module-card[data-module="merger-trim"]`<br>`#dashboard-screen` | `get_initial_data(line)` | Main Thread / Worker | Switches `.screen.active` to `input-screen`; sets `state.currentLine` (`"PFL"`, `"RFID HT"`, `"PFL THERMAL"`, `"RFID SB"`, or `"TRIM ADIDAS"`); adapts UI (hides Drive toggles for UNC-only lines; hides OCR toggle for Thermal/Trim/RFID SB; locks RFID SB layout to Deduplicate); fetches presets filtered by `Line`. |
| Run Auto Detect (Trim Adidas) | `#btn-auto-detect`<br>`#input-screen` | `run_auto_detect_trim(...)` | Background Thread (`_run_in_thread`) | Calls BigQuery `get_TE`; performs 3-Pattern SOview & TE barcode matching; opens `trim-results-screen`. |
| Click "Fetch & Open" | `#btn-fetch`<br>`#input-screen` | `pre_fetch_all_pdfs(...)` | Background Thread (`_run_in_thread`) | Validates 1 SO = 1 Flag consistency (`validateRfidFlags`); resolves PDFs via `file_resolver`; pushes progress to `#prefetch-progress-modal`; navigates to `workspace-screen`. |
| Select Preset Dropdown | `#preset-select`<br>`#input-screen` | *Pure UI State* | Main Thread | Populates folder inputs; renders Preset SOL preview grid: 2 columns for PFL (`#`, `SOL Code`); 5 columns for RFID HT (`#`, `SOL Code`, `Job`, `RB`, `Flag`). |
| Click "Fetch & Open" | `#btn-fetch`<br>`#input-screen` | `pre_fetch_all_pdfs(...)` | Background Thread (`_run_in_thread`) | Validates 1 SO = 1 Flag consistency (`validateRfidFlags`); resolves PDFs via `file_resolver`; pushes progress to `#prefetch-progress-modal`; navigates to `workspace-screen`. |
| Click "Run Auto Detect" | `#btn-auto-detect`<br>`#input-screen` | `run_auto_detect(...)` | Background Thread (`_run_in_thread`) | Validates 1 SO = 1 Flag consistency; if PFL with job_flag, queries BigQuery `get_Job`; executes Three-Mode barcode stream & layout extraction (Customer Job with SOL fallback); populates `AUTO_DETECT_RAM_CACHE`; opens `auto-detect-results-screen`. |
| View Auto Detect Summary | `#ad-results-table`<br>`#auto-detect-results-screen` | *Pure UI State* | Main Thread | Renders summary grid with full text selection enabled (`user-select: text`) for direct copying of SOL codes and page counts; calculates total detected pages. |
| Click Preview Button | `.ad-preview-btn`<br>`#auto-detect-results-screen` | `fetch_auto_detect_preview(...)`<br>`fetch_page_range(...)` (fallback) | Background Thread / Async UI | Zero-Network Instant Preview: directly renders page thumbnails from `AUTO_DETECT_RAM_CACHE` with zero network I/O; opens Continuous Vertical Viewer modal; enables zoom scaling ($40\%$ to $300\%$). |
| Click "Export Auto Detect" | `#btn-export-auto`<br>`#auto-detect-results-screen`| `export_from_auto_detect(...)`| Background Thread (`_run_in_thread`) | Directly concatenates pre-extracted sub-documents from `AUTO_DETECT_RAM_CACHE` with zero disk reads; saves output PDFs to destination directory. |
| Select SOL in Workspace | `#sol-list-items`<br>`#workspace-screen` | `fetch_page_range(...)` | Background Thread | Loads first page thumbnail via `/temp/{uuid}.jpg`; initializes virtual grid in Panel 2. |
| Scroll Thumbnail Grid | `#grid-viewport`<br>`#workspace-screen` | `fetch_page_range(...)` | Background Thread | Windowed rendering: loads page thumbnails on-demand as they scroll into view. |
| Click Thumbnail | `.thumb-card`<br>`#workspace-screen` | `get_page_hires_by_path(...)` | Background Thread | Renders 150 DPI high-res image; opens Lightbox modal with zoom and pan. |
| Drag & Drop Page | `#assembly-items`<br>`#workspace-screen` | *Pure UI (SortableJS)* | Main Thread | Mutates `state.assembly[sol]` page array order in frontend memory. |
| Click "Export Assembly" | `#btn-export`<br>`#workspace-screen` | `export_assembly(...)` | Background Thread (`_run_in_thread`) | Concatenates pages via `fitz.insert_pdf(garbage=3, deflate=True)`; saves final combined document to output directory. |
| Click "Run Deduplicate" | `#btn-dedup-run`<br>`#deduplicate-screen` | `run_deduplicate(...)` | Background Thread (`_run_in_thread`) | Executes Cluster-Jump exponential + binary search; streams live page log table to UI; writes unique output PDF. |
| Click "Search Files" | `#btn-combine-search`<br>`#combine-screen` | `search_combine_files(...)` | Background Thread (`_run_in_thread`) | Recursively searches folders for matching SOLs; populates reorderable file cards. |
| Click "Export Combine" | `#btn-combine-export`<br>`#combine-screen` | `export_combine(...)` | Background Thread (`_run_in_thread`) | Pre-loads UNC files in parallel; merges or splits PDFs; pushes percent progress to UI. |
| Click "Install Update" | `#btn-apply-update`<br>`#update-banner` | `apply_update()` | Background Thread | Downloads release `.zip`; spawns detached `update_launcher.bat`; terminates app via `os._exit(0)`. |

---

### 4.4 Storage & Resource Management

#### 1. In-Memory RAM Caches
* `AUTO_DETECT_RAM_CACHE`: A global dictionary mapping `"{sol}_{typeId}"` to serialized `bytes` of pre-extracted PyMuPDF documents. Populated by inline in-RAM sub-document slicing (PDF Merger) and Stage 6 (Trim Adidas) using **Single-Pass I/O + Double Eviction** (v1.2.3+). Serves as the high-speed backend for both zero-disk exports and Zero-Network Instant Previews (`fetch_auto_detect_preview`).
* `_RESOLVED_FILES_CACHE`: Global dictionary storing file descriptors, paths, and downloaded byte buffers to prevent duplicate network roundtrips.
* `file_entry["bytes"]`: Per-file in-memory buffer stored inside `batched_files` entries. Populated once during the single-pass preload scan loop, used for in-RAM detection, layout priority sorting, and sub-document slicing, then eagerly freed via early/late eviction to prevent memory accumulation.
* Explicit Deallocation: Calling `clear_auto_detect_ram_cache()` purges all serialized document buffers from memory. All PyMuPDF document instances are managed using `try ... finally: doc.close()` blocks to guarantee immediate native memory release.

#### 2. Temporary Directory Lifecycle (`%TEMP%`)
* **Session Directory Isolation:** On application startup, `main.py` provisions a unique session directory:
  `tempfile.mkdtemp(prefix="pdfmerger_")`
  `backend/file_resolver.py` provisions a secondary cache directory:
  `tempfile.mkdtemp(prefix="pdf_merger_")`
* **Static Image Serving:** Rendered thumbnail JPEGs are written with UUID filenames (`/temp/{uuid}.jpg`) and served directly by Bottle WSGI.
* **Guaranteed Auto-Cleanup:**
  Registered `atexit` hooks ensure that when the application terminates (normally or unexpectedly), `shutil.rmtree()` wipes all session temporary directories, leaving zero disk artifacts.

#### 3. Over-The-Air (OTA) Detached Process Lifecycle
When applying an update:
1. The new executable is downloaded to `%TEMP%\MyAppUpdate\`.
2. A detached batch launcher `update_launcher.bat` is created.
3. `subprocess.Popen` launches the script with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`.
4. The batch script pings localhost (`ping 127.0.0.1 -n 4 > nul`) to wait for the host process to terminate.
5. **CRITICAL:** The application executes `os._exit(0)` instead of `sys.exit(0)`. Because `apply_update()` runs in a background thread, `sys.exit(0)` would only terminate the worker thread while pywebview remained open, locking the `.exe` file. `os._exit(0)` terminates the entire C-level process instantly, releasing the Windows file lock and allowing `update_launcher.bat` to overwrite the executable cleanly and relaunch the application.
