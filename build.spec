# build.spec — PyInstaller Packaging Configuration
# PDF Merger Tool Desktop Edition
# ─────────────────────────────────────────────────────────────
# Build command (run from pdf_manipulation_desktop/ directory):
#   pyinstaller --noconfirm build.spec
#
# Output: dist/PDF_Manipulation.exe
# ─────────────────────────────────────────────────────────────

import os
import json
import pyzbar

# Read version
version_file = os.path.join('config', 'version.json')
with open(version_file, 'r', encoding='utf-8') as f:
    version_data = json.load(f)
app_version = version_data.get('version', '1.0.0')

pyzbar_dir = os.path.dirname(pyzbar.__file__)
pyzbar_binaries = [
    (os.path.join(pyzbar_dir, f), 'pyzbar')
    for f in ['libiconv.dll', 'libzbar-64.dll', 'msvcr120.dll']
    if os.path.isfile(os.path.join(pyzbar_dir, f))
]

# Collect metadata and submodules for BigQuery & Google Cloud dependencies
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

bigquery_datas = []
for pkg in ['google-cloud-bigquery', 'google-cloud-core', 'google-api-core']:
    try:
        bigquery_datas += copy_metadata(pkg)
    except Exception:
        pass

bigquery_hidden = collect_submodules('google.cloud.bigquery')

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=pyzbar_binaries,
    datas=[
        # Bundle the entire assets/ and config/ folders into the .exe
        ('assets',  'assets'),
        ('config',  'config'),
        ('vendor/tesseract', 'vendor/tesseract'),
    ] + bigquery_datas,
    hiddenimports=[
        # PyMuPDF (new package name in v1.24+)
        'pymupdf',
        'pymupdf.mupdf',
        # gspread + google-auth
        'gspread',
        'google.auth',
        'google.auth.transport.requests',
        'google.oauth2.service_account',
        # Drive API
        'googleapiclient',
        'googleapiclient.discovery',
        'googleapiclient.http',
        # pywebview
        'webview',
        'webview.platforms.winforms',
        # packaging (used by updater.py for version comparison)
        'packaging.version',
        # Auto Detect & PDF processing
        'backend.constants',
        'backend.bigquery_service',
        'backend.sheets_service',
        'backend.file_resolver',
        'backend.pdf_processor',
        'backend.pdf_detector',
        'backend.pdf_deduplicator',
        'backend.pdf_combiner',
        'backend.trim_service',
        'backend.updater',
        'backend.window_utils',
        'pyzbar',
        'pyzbar.pyzbar',
        'pdfminer',
        'pdfminer.high_level',
        'pdfminer.layout',
        'pytesseract',
        'PIL',
        'PIL.Image',
    ] + bigquery_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy unused packages to keep .exe size down
        'matplotlib', 'numpy', 'pandas', 'PyQt5', 'PySide2', 'PySide6',
        'tkinter', 'pdf2image', 'torch', 'torchvision', 'scipy', 'cv2', 'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f'PDF Manipulation v{app_version}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # No terminal window shown to the user
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version='version_info.txt',
)
