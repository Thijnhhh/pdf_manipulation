/**
 * ============================================================
 * app.js — Frontend Application Controller (Desktop Edition)
 * PDF Merger Tool — pywebview port of GAS Scripts.html
 * ============================================================
 *
 * Key differences from GAS version:
 *  • google.script.run  →  window.pywebview.api.*  (Promises)
 *  • Chunked Download Manager  →  REMOVED
 *    Python batch-renders all page thumbnails at once,
 *    returned as Base64 PNG data URLs via _onThumbnailsReady()
 *  • pdf.js (pdfjsLib)  →  REMOVED
 *    Grid uses <img> tags; Lightbox uses high-res img via _onHiresReady()
 *  • Export output  →  local/network path (no Drive upload)
 *  • Folder inputs each have a "Drive Link" checkbox toggle
 * ============================================================
 */

(function () {
    'use strict';

    // ── Configuration ──────────────────────────────────────
    const pdfTypes = [
        { id: 'soview', label: 'SOview' },
        { id: 'layout', label: 'Layout' }
    ];

    // ── SO Parsing Helper ─────────────────────────────────
    const _SOL_RE = /^(\d{9})-\d{1,3}$/;
    function getSoFromSol(sol) {
        const m = _SOL_RE.exec(sol);
        return m ? m[1] : null;
    }

    // ── Global Application State ───────────────────────────
    const state = {
        currentLine: 'PFL', // 'PFL' | 'RFID HT'
        rfidEntries: [],    // [{ sol, job, rb, flag }] for RFID HT
        inputMode: 'sheets',
        presets: [],
        solList: [],   // SOL list for the currently selected preset
        solMap: {},   // { presetName: [solCode, ...] } — full map loaded at startup
        folderInputs: {},   // { typeId: string (URL or path) }
        isDrive: {},   // { typeId: boolean }
        outputPath: '',

        activeSolCode: '',
        activeTab: pdfTypes[0].id,
        viewMode: 'grid',

        // Thumbnail cache: { "sol_typeId": { name, pages: [dataUrl,...] } }
        thumbnailCache: {},
        // Loading status: { "sol_typeId": 'idle'|'loading'|'ready'|'not-found'|'error' }
        loadStatus: {},

        assemblyState: {},   // { solCode: [{ typeId, pageIndex, label }] }
        exportMode: 'split',
        isExporting: false,

        lightbox: { solCode: '', typeId: '', pageIndex: 1, scale: 1.0, rotation: 0 }
    };

    // ── DOM References ─────────────────────────────────────
    const dom = {};

    function cacheDom() {
        dom.loadingOverlay = document.getElementById('app-loading-overlay');
        dom.loadingMsg = document.getElementById('loading-msg');
        dom.updateBanner = document.getElementById('update-banner');
        dom.updateVersionText = document.getElementById('update-version-text');
        dom.btnApplyUpdate = document.getElementById('btn-apply-update');
        dom.btnDismissUpdate = document.getElementById('btn-dismiss-update');

        dom.inputScreen = document.getElementById('input-screen');
        dom.workspaceScreen = document.getElementById('workspace-screen');
        dom.presetSelect = document.getElementById('preset-select');
        dom.manualSolTextarea = document.getElementById('manual-sol-textarea');
        dom.folderInputsContainer = document.getElementById('folder-inputs-container');
        dom.outputFolderInput = document.getElementById('output-folder-input');
        dom.btnFetch = document.getElementById('btn-fetch');
        dom.fetchSpinner = document.getElementById('fetch-spinner');
        dom.fetchLabel = document.getElementById('fetch-label');
        dom.inputError = document.getElementById('input-error');
        dom.btnAutoDetect = document.getElementById('btn-auto-detect');

        dom.btnMergerBack = document.getElementById('btn-merger-back');
        dom.btnBack = document.getElementById('btn-back');
        dom.toolbarSolLabel = document.getElementById('toolbar-sol-label');
        dom.toolbarFileCount = document.getElementById('toolbar-file-count');
        dom.solListBody = document.getElementById('sol-list-body');
        dom.solCount = document.getElementById('sol-count');
        dom.solSearchInput = document.getElementById('sol-search-input');

        // Preset SOL preview panel
        dom.presetSolPreview = document.getElementById('preset-sol-preview');
        dom.presetSolThead = document.getElementById('preset-sol-thead');
        dom.presetSolCount = document.getElementById('preset-sol-count');
        dom.presetSolTbody = document.getElementById('preset-sol-tbody');
        dom.mergerToolbarTitle = document.getElementById('merger-toolbar-title');
        dom.mergerToolbarIcon = document.getElementById('merger-toolbar-icon');
        dom.mergerModuleSubtitle = document.getElementById('merger-module-subtitle');
        dom.solEntrySourceGroup = document.getElementById('sol-entry-source-group');
        dom.sourceTabs = document.getElementById('source-tabs');
        dom.sourceContent = document.getElementById('source-content');
        dom.btnAddAll = document.getElementById('btn-add-all');
        dom.btnAddRange = document.getElementById('btn-add-range');
        dom.assemblyBody = document.getElementById('assembly-body');
        dom.assemblyEmpty = document.getElementById('assembly-empty');
        dom.assemblyGroupedContainer = document.getElementById('assembly-grouped-container');
        dom.assemblyPageCount = document.getElementById('assembly-page-count');
        dom.btnClearAssembly = document.getElementById('btn-clear-assembly');
        dom.btnExport = document.getElementById('btn-export');
        dom.exportProgress = document.getElementById('export-progress');
        dom.exportProgressFill = document.getElementById('export-progress-fill');

        dom.lightbox = document.getElementById('lightbox');
        dom.lightboxClose = document.getElementById('lightbox-close');
        dom.lightboxZoomIn = document.getElementById('lightbox-zoom-in');
        dom.lightboxZoomOut = document.getElementById('lightbox-zoom-out');
        dom.lightboxRotate = document.getElementById('lightbox-rotate');
        dom.lightboxReset = document.getElementById('lightbox-reset');
        dom.lightboxZoomLevel = document.getElementById('lightbox-zoom-level');
        dom.lightboxContainer = document.getElementById('lightbox-container');
        dom.lightboxCanvasWrapper = document.getElementById('lightbox-canvas-wrapper');
        dom.lightboxImg = document.getElementById('lightbox-img');
        dom.lightboxInfo = document.getElementById('lightbox-info');
        dom.toastContainer = document.getElementById('toast-container');

        dom.preFetchModal = document.getElementById('pre-fetch-modal');
        dom.prefetchProgSol = document.getElementById('prefetch-prog-sol');
        dom.prefetchProgType = document.getElementById('prefetch-prog-type');
        dom.prefetchProgCount = document.getElementById('prefetch-prog-count');
        dom.prefetchProgFill = document.getElementById('prefetch-prog-fill');

        dom.toolbarPathBox = document.getElementById('toolbar-path-box');
        dom.toolbarFilePath = document.getElementById('toolbar-file-path');
        dom.btnCopyPath = document.getElementById('btn-copy-path');
    }

    // ============================================================
    // pywebview API CALLBACKS (called by Python via evaluate_js)
    // These must be on window so Python can find them.
    // ============================================================

    /**
     * Called by bridge.py when fast PDF metadata is ready.
     * result = { found, sol, typeId, name, pageCount }
     */
    window._onPdfMetaReady = function (sol, typeId, result) {
        const key = `${sol}_${typeId}`;

        if (!result.found) {
            // Only mark not-found if no files have been cached yet for this key
            if (!state.thumbnailCache[key] || state.thumbnailCache[key].length === 0) {
                state.loadStatus[key] = 'not-found';
                updateSolItemStatus(sol);
                if (state.activeSolCode === sol && state.activeTab === typeId) {
                    showSourceNotFound(sol, typeId);
                }
            }
            return;
        }

        if (result.error) {
            state.loadStatus[key] = 'error';
            updateSolItemStatus(sol);
            if (state.activeSolCode === sol && state.activeTab === typeId) {
                showSourceError(result.error);
            }
            return;
        }

        // Multi-file support: thumbnailCache[key] is now an array of file entries
        if (!state.thumbnailCache[key]) {
            state.thumbnailCache[key] = [];
        }
        if (!state.renderedPagesCache) state.renderedPagesCache = {};
        const fId = result.fileId || result.fullPath || '';
        if (fId && !state.renderedPagesCache[fId]) {
            state.renderedPagesCache[fId] = {};
        }

        // Add new file entry (avoid duplicates by name)
        const alreadyExists = state.thumbnailCache[key].some(f => f.name === result.name);
        if (!alreadyExists) {
            state.thumbnailCache[key].push({
                fileId: fId,
                name: result.name,
                fullPath: result.fullPath || result.name || '',
                pageCount: result.pageCount,
                pages: fId ? state.renderedPagesCache[fId] : {} // Shared cache across SOLs mapped to same file
            });
            // Ensure thumbnailCache array is sorted predictably by file name (e.g. File 1, File 2)
            state.thumbnailCache[key].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
        }
        state.loadStatus[key] = 'ready';
        updateSolItemStatus(sol);

        if (state.activeSolCode === sol && state.activeTab === typeId) {
            renderSourceViewer(sol, typeId);
            const first = state.thumbnailCache[key][0];
            if (dom.toolbarFileCount) dom.toolbarFileCount.textContent = `${state.thumbnailCache[key].length} file(s) • ${first.name}`;
            if (dom.toolbarFilePath && first.fullPath) {
                dom.toolbarFilePath.value = first.fullPath;
                if (dom.toolbarPathBox) dom.toolbarPathBox.style.display = 'flex';
            }
        }
    };

    /**
     * Called by bridge.py when requested page range thumbnails arrive.
     * pagesMap = { "1": "data:image...", "2": "data:image..." }
     */
    window._onPageRangeReady = function (sol, typeId, startPage, pagesMap, fileIdx) {
        // fileIdx is passed from bridge so we update the correct file's pages
        fileIdx = (fileIdx !== undefined && fileIdx !== null) ? parseInt(fileIdx) : 0;
        const key = `${sol}_${typeId}`;
        const fileList = state.thumbnailCache[key];
        if (!fileList || fileList.length === 0) return;

        const cached = fileList[fileIdx];
        if (!cached) return;

        Object.keys(pagesMap).forEach(pNum => {
            const num = parseInt(pNum);
            cached.pages[num] = pagesMap[pNum];

            const card = document.getElementById(`page-card-${key}-${fileIdx}-${num}`);
            if (card) {
                const img = card.querySelector('img');
                const ph = card.querySelector('.pdf-page-placeholder');
                if (img) {
                    img.src = pagesMap[pNum];
                    img.classList.remove('hidden');
                }
                if (ph) {
                    ph.classList.add('hidden');
                }
            }
        });
    };

    /**
     * Called by bridge.py when high-res lightbox image is ready.
     */
    window._onHiresReady = function (sol, typeId, pageIndex, dataUrl) {
        if (state.lightbox.solCode !== sol ||
            state.lightbox.typeId !== typeId ||
            state.lightbox.pageIndex !== pageIndex) return;
        // Store hires in the correct file entry's pages map
        const key = `${sol}_${typeId}`;
        const fileList = state.thumbnailCache[key];
        const fIdx = state.lightbox.fileIdx || 0;
        if (fileList && fileList[fIdx]) {
            fileList[fIdx].pages[pageIndex] = dataUrl; // Cache the hires too
        }

        if (dataUrl) {
            dom.lightboxImg.src = dataUrl;
            // Also update the thumbnail in the main panel grid immediately
            const card = document.getElementById(`page-card-${key}-${fIdx}-${pageIndex}`);
            if (card) {
                const img = card.querySelector('img');
                const ph = card.querySelector('.pdf-page-placeholder');
                if (img) {
                    img.src = dataUrl;
                    img.classList.remove('hidden');
                }
                if (ph) {
                    ph.classList.add('hidden');
                }
            }
        }
    };

    /**
     * Called by bridge.py with export progress 0-100.
     */
    window._onExportProgress = function (pct) {
        dom.exportProgressFill.style.width = pct + '%';
    };

    /**
     * Called by bridge.py when export finishes.
     * result = { success, files? } or { success: false, error }
     */
    window._onExportDone = function (result) {
        state.isExporting = false;
        dom.exportProgress.classList.add('hidden');
        dom.btnExport.disabled = false;

        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0]; // hh:mm:ss format

        if (result.success) {
            if (result.fallback_used) {
                showToastWithAction(
                    `⚠️ Export failed, try to export to download folder. ✅ ${result.files.length} file(s)`,
                    'warning',
                    'Open Folder',
                    () => { window.pywebview.api.open_folder(result.fallback_path); },
                    timeStr
                );
            } else {
                showToastWithAction(
                    `✅ Export complete — ${result.files.length} file(s)`,
                    'success',
                    'Open Folder',
                    () => { window.pywebview.api.open_folder(state.outputPath); },
                    timeStr
                );
            }
        } else {
            showToast(`❌ Export failed [${timeStr}]: ` + (result.error || 'Unknown error'), 'error', 0);
        }
    };

    // ============================================================
    // INITIALIZATION
    // ============================================================

    let isAppInitialized = false;

    function init() {
        if (isAppInitialized) return;
        isAppInitialized = true;
        cacheDom();
        generateFolderInputs();
        generateSourceTabs();
        bindEvents();
        initDashboard();
        initDeduplicate();
        initCombine();
        // Hide loading overlay immediately (dashboard doesn't need Sheets data)
        dom.loadingOverlay.classList.add('fade-out');
        setTimeout(() => { dom.loadingOverlay.style.display = 'none'; }, 450);
    }

    function enterMergerModule(line = 'PFL') {
        state.currentLine = line;
        const isRfid = line === 'RFID HT';
        const isTrim = line === 'Trim';
        const isPflThermal = line === 'PFL THERMAL';
        const isRfidSb = line === 'RFID SB';
        // Modules that never use Google Drive (UNC/local only)
        const isUncOnly = isRfid || isTrim || isPflThermal || isRfidSb;

        // ── Toolbar ──────────────────────────────────────────────
        const titles = {
            'PFL': 'PDF Merger (PFL)',
            'RFID HT': 'PDF Merger (RFID HT)',
            'Trim': 'PDF Merger (Trim)',
            'PFL THERMAL': 'PDF Merger (PFL Thermal)',
            'RFID SB': 'PDF Merger (RFID SB UQ)',
        };
        const icons = {
            'PFL': '📑',
            'RFID HT': '🏷️',
            'Trim': '✂️',
            'PFL THERMAL': '🌡️',
            'RFID SB': '🔖',
        };
        if (dom.mergerToolbarTitle) dom.mergerToolbarTitle.textContent = titles[line] || line;
        if (dom.mergerToolbarIcon) dom.mergerToolbarIcon.textContent = icons[line] || '📄';
        if (dom.mergerModuleSubtitle) dom.mergerModuleSubtitle.textContent = line;

        // ── Folder label rename for Trim (Layout → TE Folder) ───
        pdfTypes.forEach(t => {
            const lbl = document.querySelector(`label[for='folder-${t.id}']`);
            if (!lbl) return;
            if (isTrim && t.id === 'layout') {
                lbl.textContent = 'TE Folder';
            } else {
                lbl.textContent = t.label + ' Folder';
            }
        });

        // ── Manual SOL input visibility ──────────────────────────
        if (dom.solEntrySourceGroup) {
            // Only hide manual entry for RFID HT
            dom.solEntrySourceGroup.style.display = isRfid ? 'none' : '';
        }
        if (isRfid) onInputModeTabSwitch('sheets');

        // ── Drive toggles (hidden for UNC-only modules) ──────────
        document.querySelectorAll('.drive-toggle').forEach(el => {
            el.style.display = isUncOnly ? 'none' : '';
        });
        if (isUncOnly) {
            pdfTypes.forEach(t => {
                const cb = document.getElementById(`is-drive-${t.id}`);
                if (cb) {
                    cb.checked = false;
                    const lbl = document.getElementById(`drive-toggle-${t.id}`);
                    if (lbl) lbl.classList.remove('active');
                }
                state.isDrive[t.id] = false;
            });
        }

        // ── Fetch button visibility (PDF Merger (Trim) & RFID SB hide it) ───────
        if (dom.btnFetch) {
            dom.btnFetch.style.display = (isTrim || isRfidSb) ? 'none' : '';
        }

        showScreen('input-screen');
        dom.loadingOverlay.style.display = 'flex';
        dom.loadingOverlay.classList.remove('fade-out');
        loadInitialData(line);
    }

    function loadInitialData(line = 'PFL') {
        dom.loadingMsg.textContent = `Connecting to Google Sheets (${line})…`;

        window.pywebview.api.get_initial_data(line).then(result => {
            if (result && result.success) {
                state.presets = result.presets || [];
                state.solMap = result.solMap || {};
                state.solList = [];  // cleared; will be set when a preset is chosen
                state.rfidEntries = [];
                hideError();

                dom.presetSelect.innerHTML =
                    '<option value="">— Select a user —</option>' +
                    state.presets.map((p, i) =>
                        `<option value="${i}">${escHtml(p.name)}</option>`
                    ).join('');

                // Force a fresh start
                dom.presetSelect.value = '';
                pdfTypes.forEach(t => {
                    const inp = document.getElementById(`folder-${t.id}`);
                    if (inp) inp.value = '';
                });
                if (dom.outputFolderInput) dom.outputFolderInput.value = '';
                if (dom.manualSolTextarea) dom.manualSolTextarea.value = '';
                if (dom.presetSolPreview) dom.presetSolPreview.classList.add('hidden');
                validateInputs();

                dom.loadingOverlay.classList.add('fade-out');
                setTimeout(() => { dom.loadingOverlay.style.display = 'none'; }, 450);

                if (result.current_version) {
                    const btnUpdate = document.getElementById('btn-dashboard-update');
                    if (btnUpdate) {
                        btnUpdate.textContent = 'v' + result.current_version + ' (Check for updates)';
                    }
                    updateChangelogVersion(result.current_version);
                }

                checkForUpdateSilent();
            } else {
                dom.loadingMsg.innerHTML = '❌ ' + ((result && result.error) || 'Failed to connect to Google Sheets.') +
                    '<br><br><button onclick="document.getElementById(\'app-loading-overlay\').style.display=\'none\'" class="btn btn--sm btn--primary" style="margin-top:8px;">Continue with Manual Entry</button>';
            }
        }).catch(err => {
            dom.loadingMsg.innerHTML = '❌ Connection error: ' + (err.message || err) +
                '<br><br><button onclick="document.getElementById(\'app-loading-overlay\').style.display=\'none\'" class="btn btn--sm btn--primary" style="margin-top:8px;">Continue with Manual Entry</button>';
        });
    }

    function checkForUpdateSilent() {
        window.pywebview.api.check_for_update().then(result => {
            if (result.update_available) {
                dom.updateVersionText.textContent = `v${result.current_version} → v${result.remote_version}`;
                dom.updateBanner.classList.add('visible');
            }
        }).catch(() => {/* silent fail */ });
    }

    // ── Generate folder inputs with Drive toggle ───────────
    function generateFolderInputs() {
        dom.folderInputsContainer.innerHTML = pdfTypes.map(t => `
            <div class="form-group">
                <label for="folder-${t.id}">${t.label} Folder</label>
                <div class="folder-input-row">
                    <input type="text" id="folder-${t.id}"
                        placeholder="C:\\Source\\${t.label} or \\\\server\\share\\${t.label}" />
                    <label class="drive-toggle" id="drive-toggle-${t.id}" title="Enable Google Drive mode for this folder">
                        <input type="checkbox" id="is-drive-${t.id}" data-type="${t.id}">
                        ☁ Drive
                    </label>
                </div>
            </div>
        `).join('');

        // Bind drive toggle visual feedback
        pdfTypes.forEach(t => {
            const cb = document.getElementById(`is-drive-${t.id}`);
            const lbl = document.getElementById(`drive-toggle-${t.id}`);
            const inp = document.getElementById(`folder-${t.id}`);
            cb.addEventListener('change', () => {
                lbl.classList.toggle('active', cb.checked);
                inp.placeholder = cb.checked
                    ? `https://drive.google.com/drive/folders/...`
                    : `C:\\Source\\${t.label} or \\\\server\\share\\${t.label}`;
                validateInputs();
            });
        });
    }

    function generateSourceTabs() {
        dom.sourceTabs.innerHTML = pdfTypes.map((t, i) =>
            `<button class="source-tab ${i === 0 ? 'active' : ''}" data-type="${t.id}">${t.label}</button>`
        ).join('');
    }

    // ── Event Bindings ─────────────────────────────────────
    function bindEvents() {
        // Input mode tabs
        document.querySelectorAll('.input-mode-tab').forEach(tab =>
            tab.addEventListener('click', () => onInputModeTabSwitch(tab.dataset.mode))
        );

        // Auto Detect stub
        dom.btnAutoDetect.addEventListener('click', () => {
            showToast('OCR Auto Detect is coming soon 🚀', 'info');
        });

        // Search
        dom.solSearchInput.addEventListener('input', () => renderSolList());

        // Preset change
        dom.presetSelect.addEventListener('change', onPresetChange);

        // Input validation triggers
        dom.manualSolTextarea.addEventListener('input', validateInputs);
        dom.folderInputsContainer.addEventListener('input', validateInputs);
        dom.outputFolderInput.addEventListener('input', validateInputs);

        // Main actions
        dom.btnFetch.addEventListener('click', onFetch);
        if (dom.btnMergerBack) dom.btnMergerBack.addEventListener('click', onBackToDashboard);
        dom.btnBack.addEventListener('click', onBackToInput);

        // Source tabs
        dom.sourceTabs.addEventListener('click', e => {
            if (e.target.classList.contains('source-tab')) onTabSwitch(e.target.dataset.type);
        });

        // View toggle
        document.querySelectorAll('.view-toggle__btn').forEach(btn =>
            btn.addEventListener('click', () => onViewModeChange(btn.dataset.view))
        );

        // Assembly
        dom.btnAddAll.addEventListener('click', onAddAll);
        if (dom.btnAddRange) dom.btnAddRange.addEventListener('click', onAddRange);
        dom.btnClearAssembly.addEventListener('click', onClearAssembly);

        // Export mode radio
        document.querySelectorAll('input[name="export-mode"]').forEach(radio =>
            radio.addEventListener('change', e => {
                state.exportMode = e.target.value;
                document.querySelectorAll('.export-option').forEach(o => o.classList.remove('selected'));
                e.target.closest('.export-option').classList.add('selected');
            })
        );

        dom.btnExport.addEventListener('click', onExport);

        // Lightbox
        dom.lightboxClose.addEventListener('click', closeLightbox);
        dom.lightboxRotate.addEventListener('click', rotateLightbox);
        dom.lightboxReset.addEventListener('click', resetLightboxTransform);
        dom.lightboxZoomIn.addEventListener('click', () => zoomLightbox(0.2));
        dom.lightboxZoomOut.addEventListener('click', () => zoomLightbox(-0.2));

        // Lightbox drag-to-pan
        let isDragging = false, startX, startY, scrollLeft, scrollTop;

        dom.lightboxContainer.addEventListener('mousedown', e => {
            if (e.target.closest('button')) return;
            isDragging = true;
            dom.lightboxContainer.classList.add('grabbing');
            startX = e.pageX - dom.lightboxContainer.offsetLeft;
            startY = e.pageY - dom.lightboxContainer.offsetTop;
            scrollLeft = dom.lightboxContainer.scrollLeft;
            scrollTop = dom.lightboxContainer.scrollTop;
        });
        dom.lightboxContainer.addEventListener('mouseleave', () => { isDragging = false; dom.lightboxContainer.classList.remove('grabbing'); });
        dom.lightboxContainer.addEventListener('mouseup', () => { isDragging = false; dom.lightboxContainer.classList.remove('grabbing'); });
        dom.lightboxContainer.addEventListener('mousemove', e => {
            if (!isDragging) return;
            e.preventDefault();
            const x = e.pageX - dom.lightboxContainer.offsetLeft;
            const y = e.pageY - dom.lightboxContainer.offsetTop;
            dom.lightboxContainer.scrollLeft = scrollLeft - (x - startX) * 1.2;
            dom.lightboxContainer.scrollTop = scrollTop - (y - startY) * 1.2;
        });
        dom.lightboxContainer.addEventListener('wheel', e => {
            if (e.ctrlKey) { e.preventDefault(); zoomLightbox(e.deltaY > 0 ? -0.15 : 0.15); }
        }, { passive: false });

        dom.lightbox.addEventListener('click', e => { if (e.target === dom.lightbox) closeLightbox(); });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

        // Update banner
        dom.btnApplyUpdate.addEventListener('click', () => {
            dom.btnApplyUpdate.textContent = 'Downloading…';
            dom.btnApplyUpdate.disabled = true;
            window.pywebview.api.apply_update();
        });
        dom.btnDismissUpdate.addEventListener('click', () => {
            dom.updateBanner.classList.remove('visible');
        });

        if (dom.btnCopyPath) {
            dom.btnCopyPath.addEventListener('click', () => {
                const pathVal = dom.toolbarFilePath ? dom.toolbarFilePath.value : '';
                if (pathVal) {
                    navigator.clipboard.writeText(pathVal);
                    showToast('📋 File path copied to clipboard!', 'info', 3000);
                }
            });
        }
    }

    // ── Input Mode Tabs ────────────────────────────────────
    function onInputModeTabSwitch(mode) {
        state.inputMode = mode;
        document.querySelectorAll('.input-mode-tab').forEach(tab =>
            tab.classList.toggle('active', tab.dataset.mode === mode)
        );
        document.getElementById('tab-sheets-pane').classList.toggle('active', mode === 'sheets');
        document.getElementById('tab-manual-pane').classList.toggle('active', mode === 'manual');
        validateInputs();
    }

    // ── Preset & Flag Validation ───────────────────────────
    function validateRfidFlags(entries) {
        if (!entries || !entries.length) {
            hideError();
            return true;
        }
        const soMap = {};
        for (const e of entries) {
            const solStr = String(e.sol || '').trim();
            const baseSo = getSoFromSol(solStr) || solStr;
            const flag = Boolean(e.flag);
            if (baseSo in soMap) {
                if (soMap[baseSo].flag !== flag) {
                    showError(`❌ Conflicting Flags for SO '${baseSo}': row '${soMap[baseSo].sol}' is ${soMap[baseSo].flag ? 'TRUE' : 'FALSE'}, but '${solStr}' is ${flag ? 'TRUE' : 'FALSE'}. Each SO must have a single consistent Flag.`);
                    return false;
                }
            } else {
                soMap[baseSo] = { flag, sol: solStr };
            }
        }
        hideError();
        return true;
    }

    function renderPresetSolPreview() {
        if (!dom.presetSolPreview || !dom.presetSolTbody || !dom.presetSolCount) return;
        const isRfid = state.currentLine === 'RFID HT';
        const isPfl = state.currentLine === 'PFL';

        // Update thead
        if (dom.presetSolThead) {
            if (isRfid) {
                dom.presetSolThead.innerHTML = `
                    <tr>
                        <th style="width:36px;text-align:center;">#</th>
                        <th>SOL Code</th>
                        <th>Job</th>
                        <th>RB</th>
                        <th style="text-align:center;width:68px;">Flag</th>
                    </tr>
                `;
            } else if (isPfl) {
                dom.presetSolThead.innerHTML = `
                    <tr>
                        <th style="width:36px;text-align:center;">#</th>
                        <th>SOL Code</th>
                        <th style="text-align:center;width:88px;">Job Flag</th>
                    </tr>
                `;
            } else {
                dom.presetSolThead.innerHTML = `
                    <tr>
                        <th style="width:36px;text-align:center;">#</th>
                        <th>SOL Code</th>
                    </tr>
                `;
            }
        }

        if (isRfid) {
            const entries = state.rfidEntries;
            dom.presetSolCount.textContent = entries.length;
            dom.presetSolTbody.innerHTML = entries.length === 0
                ? `<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-secondary);font-style:italic;">No entries found</td></tr>`
                : entries.map((e, i) => `
                    <tr>
                        <td>${i + 1}</td>
                        <td class="sol-cell">${escHtml(e.sol)}</td>
                        <td class="code-cell">${escHtml(e.job || '—')}</td>
                        <td class="code-cell">${escHtml(e.rb || '—')}</td>
                        <td style="text-align:center;">
                            <span class="flag-badge ${e.flag ? 'flag-badge--true' : 'flag-badge--false'}">
                                ${e.flag ? 'TRUE' : 'FALSE'}
                            </span>
                        </td>
                    </tr>
                `).join('');
        } else if (isPfl) {
            const entries = state.pflEntries || [];
            dom.presetSolCount.textContent = entries.length;
            dom.presetSolTbody.innerHTML = entries.length === 0
                ? `<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-secondary);font-style:italic;">No entries found</td></tr>`
                : entries.map((e, i) => `
                    <tr>
                        <td style="text-align:center;">${i + 1}</td>
                        <td class="sol-cell">${escHtml(typeof e === 'object' && e ? e.sol : e)}</td>
                        <td style="text-align:center;">
                            <span class="flag-badge ${(e && typeof e === 'object' && e.job_flag) ? 'flag-badge--true' : 'flag-badge--false'}">
                                ${(e && typeof e === 'object' && e.job_flag) ? 'TRUE' : 'FALSE'}
                            </span>
                        </td>
                    </tr>
                `).join('');
        } else {
            const sols = state.solList;
            dom.presetSolCount.textContent = sols.length;
            dom.presetSolTbody.innerHTML = sols.length === 0
                ? `<tr><td colspan="2" style="text-align:center;padding:20px;color:var(--text-secondary);font-style:italic;">No SOL codes</td></tr>`
                : sols.map((sol, i) => `
                    <tr>
                        <td>${i + 1}</td>
                        <td class="sol-cell">${escHtml(sol)}</td>
                    </tr>
                `).join('');
        }

        dom.presetSolPreview.classList.remove('hidden');
    }

    function onPresetChange() {
        const idx = dom.presetSelect.value;

        // Hide panel and clear solList when no preset is selected
        if (idx === '') {
            if (dom.presetSolPreview) dom.presetSolPreview.classList.add('hidden');
            state.solList = [];
            state.rfidEntries = [];
            state.pflEntries = [];
            hideError();
            renderSolList();
            return;
        }

        const preset = state.presets[parseInt(idx)];
        pdfTypes.forEach(t => {
            const inp = document.getElementById(`folder-${t.id}`);
            if (inp) {
                const presetKey = t.id + 'Folder'; // 'soviewFolder' | 'layoutFolder'
                inp.value = preset[presetKey] || '';
            }
        });
        dom.outputFolderInput.value = preset.outputFolder || '';

        const isRfid = state.currentLine === 'RFID HT';
        const isPfl = state.currentLine === 'PFL';
        const rawPresetData = state.solMap[preset.name] || [];

        if (isRfid) {
            // rawPresetData is [{ sol, job, rb, flag }, ...]
            state.rfidEntries = Array.isArray(rawPresetData) ? rawPresetData : [];
            state.pflEntries = [];
            state.solList = state.rfidEntries.map(e => String(e.sol || '').trim()).filter(s => s.length > 0);
            validateRfidFlags(state.rfidEntries);
        } else if (isPfl) {
            // rawPresetData is [{ sol, job_flag }, ...] or legacy string[]
            state.rfidEntries = [];
            state.pflEntries = Array.isArray(rawPresetData) ? rawPresetData : [];
            state.solList = state.pflEntries.map(e => (typeof e === 'object' && e) ? String(e.sol || '').trim() : String(e).trim()).filter(s => s.length > 0);
            hideError();
        } else {
            state.rfidEntries = [];
            state.pflEntries = [];
            state.solList = Array.from(new Set(rawPresetData.map(s => String(s).trim()).filter(s => s.length > 0)));
            hideError();
        }

        renderSolList();
        renderPresetSolPreview();
        validateInputs();
    }

    // ── Validation ─────────────────────────────────────────
    function validateInputs() {
        const allFolders = pdfTypes.every(t => {
            const inp = document.getElementById(`folder-${t.id}`);
            return inp && inp.value.trim().length > 0;
        });
        const outputOk = dom.outputFolderInput.value.trim().length > 0;
        const solOk = state.inputMode === 'sheets' ? true : parseManualSolCodes().length > 0;
        dom.btnFetch.disabled = !(allFolders && outputOk && solOk);
    }

    function parseManualSolCodes() {
        const raw = dom.manualSolTextarea ? dom.manualSolTextarea.value : '';
        const set = new Set();
        raw.split(/[\n,;\s]+/).forEach(l => { const t = l.trim(); if (t) set.add(t); });
        return Array.from(set);
    }

    // ── Fetch & Workspace Transition ───────────────────────
    function onFetch() {
        // Collect folder inputs + drive flags
        pdfTypes.forEach(t => {
            const inp = document.getElementById(`folder-${t.id}`);
            const cb = document.getElementById(`is-drive-${t.id}`);
            state.folderInputs[t.id] = inp ? inp.value.trim() : '';
            state.isDrive[t.id] = cb ? cb.checked : false;
        });
        state.outputPath = dom.outputFolderInput.value.trim();

        const solList = state.inputMode === 'manual'
            ? parseManualSolCodes()
            : Array.from(new Set((state.solList || []).map(s => String(s).trim()).filter(s => s.length > 0)));

        if (!solList.length) {
            showError('Please enter or fetch at least one SOL code.');
            return;
        }

        state.solList = solList;
        if (state.currentLine === 'RFID HT') {
            if (!validateRfidFlags(state.rfidEntries)) {
                return;
            }
        }

        // Show blocking pre-fetch progress modal
        if (dom.preFetchModal) dom.preFetchModal.classList.add('active');
        if (dom.prefetchProgFill) dom.prefetchProgFill.style.width = '0%';
        if (dom.prefetchProgSol) dom.prefetchProgSol.textContent = '—';
        if (dom.prefetchProgType) dom.prefetchProgType.textContent = '—';
        if (dom.prefetchProgCount) dom.prefetchProgCount.textContent = `0 / ${solList.length * pdfTypes.length}`;

        let jobMapJson = "{}";
        if (state.currentLine === 'RFID HT') {
            const jobMap = {};
            state.rfidEntries.forEach(e => {
                if (e.sol && e.job) jobMap[e.sol] = String(e.job).trim();
            });
            jobMapJson = JSON.stringify(jobMap);
        }

        window.pywebview.api.pre_fetch_all_pdfs(
            JSON.stringify(solList),
            JSON.stringify(state.folderInputs),
            JSON.stringify(state.isDrive),
            jobMapJson
        );
    }

    window._onPreFetchProgress = function (payload) {
        if (dom.prefetchProgSol) dom.prefetchProgSol.textContent = payload.sol || '—';
        if (dom.prefetchProgType) dom.prefetchProgType.textContent = pdfTypes.find(t => t.id === payload.typeId)?.label || payload.typeId || '—';
        if (dom.prefetchProgCount) dom.prefetchProgCount.textContent = `${payload.current} / ${payload.total}`;

        const pct = payload.total > 0 ? Math.round((payload.current / payload.total) * 100) : 0;
        if (dom.prefetchProgFill) dom.prefetchProgFill.style.width = pct + '%';
    };

    window._onPreFetchDone = function () {
        if (dom.preFetchModal) dom.preFetchModal.classList.remove('active');
        switchToWorkspace();
    };

    function setFetchLoading(on) {
        dom.btnFetch.disabled = on;
        dom.fetchSpinner.classList.toggle('active', on);
        dom.fetchLabel.textContent = on ? 'Loading…' : 'Fetch PDFs';
    }

    function switchToWorkspace() {
        // Reset per-session state
        state.thumbnailCache = {};
        state.renderedPagesCache = {};
        state.loadStatus = {};
        state.activeSolCode = '';
        state.assemblyState = {};

        showScreen('workspace-screen');

        renderSolList();
        initSplitPanels();
        updateAssemblyUI();

        // Auto-select first SOL
        if (state.solList.length > 0) {
            selectSol(state.solList[0]);
        }
    }

    function onBackToDashboard() {
        window.location.reload();
    }

    function onBackToInput() {
        showScreen('input-screen');
        state.activeSolCode = '';
        state.assemblyState = {};
        state.thumbnailCache = {};
        state.loadStatus = {};
        updateAssemblyUI();
    }

    function initSplitPanels() {
        if (typeof Split === 'function') {
            try {
                Split(['#panel-sol', '#panel-source', '#panel-builder'], {
                    sizes: [15, 60, 25],
                    minSize: [140, 300, 200],
                    gutterSize: 4,
                    snapOffset: 0,
                    direction: 'horizontal'
                });
            } catch (err) {
                console.warn('Split initialization error:', err);
            }
        }
    }

    // ── SOL List ───────────────────────────────────────────
    function renderSolList() {
        const query = dom.solSearchInput ? dom.solSearchInput.value.toLowerCase().trim() : '';
        const filteredList = state.solList.filter(sol => sol.toLowerCase().includes(query));

        dom.solListBody.innerHTML = filteredList.map(sol => {
            const count = (state.assemblyState[sol] || []).length;
            const badge = count > 0 ? `<span class="sol-item__badge">${count}</span>` : '';
            const isActive = sol === state.activeSolCode;
            return `
                <div class="sol-item ${isActive ? 'active' : ''}" data-sol="${escHtml(sol)}">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span class="sol-item__status"></span>
                        <span>${escHtml(sol)}</span>
                    </div>
                    ${badge}
                </div>
            `;
        }).join('');

        dom.solCount.textContent = query
            ? `${filteredList.length}/${state.solList.length}`
            : state.solList.length;

        dom.solListBody.querySelectorAll('.sol-item').forEach(item =>
            item.addEventListener('click', () => selectSol(item.dataset.sol))
        );

        filteredList.forEach(sol => updateSolItemStatus(sol));
    }

    // ── SOL Status Indicator ───────────────────────────────

    function getSolAggregateStatus(solCode) {
        const statuses = pdfTypes.map(t => state.loadStatus[`${solCode}_${t.id}`] || 'idle');
        const allDone = statuses.every(s => s === 'ready' || s === 'not-found' || s === 'error');
        const anyLoading = statuses.some(s => s === 'loading');
        if (allDone) return 'ready';
        if (anyLoading) return 'loading';
        return 'partial';
    }

    function updateSolItemStatus(solCode) {
        const item = dom.solListBody.querySelector(`[data-sol="${CSS.escape(solCode)}"]`);
        if (!item) return;
        const indicator = item.querySelector('.sol-item__status');
        if (!indicator) return;

        const agg = getSolAggregateStatus(solCode);
        if (agg === 'loading') {
            indicator.innerHTML = '<span class="sol-status-spinner"></span>';
            indicator.title = 'Loading…';
            indicator.style.cssText = '';
        } else if (agg === 'ready') {
            indicator.innerHTML = '✓';
            indicator.style.cssText = 'color:var(--accent-success);font-weight:700;font-size:0.75rem;';
            indicator.title = 'All files loaded';
        } else {
            indicator.innerHTML = '';
            indicator.style.cssText = '';
        }
    }

    // ── SOL Selection ──────────────────────────────────────
    function selectSol(solCode) {
        state.activeSolCode = solCode;

        dom.solListBody.querySelectorAll('.sol-item').forEach(item =>
            item.classList.toggle('active', item.dataset.sol === solCode)
        );
        dom.toolbarSolLabel.textContent = solCode;

        // Pre-fetch / load BOTH soview and layout tabs immediately
        pdfTypes.forEach(t => {
            loadAndRenderTab(solCode, t.id);
        });

        // Render active tab view
        renderSourceViewer(solCode, state.activeTab);
    }

    // ── Tab Switching ──────────────────────────────────────
    function onTabSwitch(typeId) {
        state.activeTab = typeId;
        dom.sourceTabs.querySelectorAll('.source-tab').forEach(tab =>
            tab.classList.toggle('active', tab.dataset.type === typeId)
        );
        if (state.activeSolCode) {
            loadAndRenderTab(state.activeSolCode, typeId);
        }
    }

    /**
     * Core load-and-render logic using fast metadata fetch.
     */
    function loadAndRenderTab(solCode, typeId) {
        const key = `${solCode}_${typeId}`;
        const status = state.loadStatus[key] || 'idle';

        if (status === 'ready') {
            if (solCode === state.activeSolCode && typeId === state.activeTab) {
                renderSourceViewer(solCode, typeId);
                const fileList = state.thumbnailCache[key];
                if (fileList && fileList.length > 0) {
                    const first = fileList[0];
                    if (dom.toolbarFileCount) dom.toolbarFileCount.textContent = fileList.length > 1
                        ? `${fileList.length} files • ${first.name} (+${fileList.length - 1} more)`
                        : `${first.pageCount} pages • ${first.name}`;
                    if (dom.toolbarFilePath && first.fullPath) {
                        dom.toolbarFilePath.value = first.fullPath;
                        if (dom.toolbarPathBox) dom.toolbarPathBox.style.display = 'flex';
                    }
                }
            }
            return;
        }

        if (status === 'not-found') {
            showSourceNotFound(solCode, typeId);
            return;
        }

        if (status === 'loading') {
            showSourceLoading(solCode, typeId);
            return;
        }

        // Request fast metadata (< 10ms)
        state.thumbnailCache[key] = []; // Clear any stale cache before fresh fetch
        state.loadStatus[key] = 'loading';
        updateSolItemStatus(solCode);
        showSourceLoading(solCode, typeId);

        window.pywebview.api.fetch_pdf_meta(
            solCode,
            typeId,
            state.folderInputs[typeId],
            state.isDrive[typeId] === true
        );
    }

    function onViewModeChange(mode) {
        state.viewMode = mode;
        document.querySelectorAll('.view-toggle__btn').forEach(btn =>
            btn.classList.toggle('active', btn.dataset.view === mode)
        );
        if (state.activeSolCode) renderSourceViewer(state.activeSolCode, state.activeTab);
    }

    // IntersectionObserver for requesting page range thumbnails as user scrolls
    let rangeObserver = null;
    const requestedRanges = new Set();

    // ── Source Viewer Rendering ────────────────────────────
    function renderSourceViewer(solCode, typeId) {
        const key = `${solCode}_${typeId}`;
        // thumbnailCache[key] is now an array of file entries
        const fileList = state.thumbnailCache[key];
        if (!fileList || fileList.length === 0) return;

        // Use first file for toolbar display
        const firstFile = fileList[0];
        const totalFiles = fileList.length;
        if (dom.toolbarFileCount) dom.toolbarFileCount.textContent =
            totalFiles > 1
                ? `${totalFiles} files • ${firstFile.name} (+${totalFiles - 1} more)`
                : `${firstFile.pageCount} pages • ${firstFile.name}`;
        if (dom.toolbarFilePath) {
            dom.toolbarFilePath.value = firstFile.fullPath || firstFile.name || '';
            if (dom.toolbarPathBox) dom.toolbarPathBox.style.display = firstFile.fullPath ? 'flex' : 'none';
        }

        const isFocus = state.viewMode === 'focus';
        const gridClass = isFocus ? 'pdf-grid focus-view' : 'pdf-grid';
        const solQueue = state.assemblyState[solCode] || [];

        if (rangeObserver) {
            rangeObserver.disconnect();
        }

        // Render all files sequentially — one grid section per file
        let html = '';
        fileList.forEach((cached, fileIdx) => {
            const pageCount = cached.pageCount;
            // File header when multiple files
            if (totalFiles > 1) {
                html += `<div class="source-file-header" style="padding:6px 10px;background:var(--bg-secondary);border-radius:var(--radius-sm);margin-bottom:6px;font-size:0.78rem;color:var(--text-secondary);font-weight:600;">📄 ${escHtml(cached.name)}</div>`;
            }
            html += `<div class="${gridClass}" id="pdf-grid-${fileIdx}">`;
            for (let i = 1; i <= pageCount; i++) {
                const selected = solQueue.some(it => it.typeId === typeId && it.pageIndex === i && it.fileIndex === fileIdx);
                const dataUrl = cached.pages[i];
                const hasImg = !!dataUrl;

                html += `
                    <div class="pdf-page-card ${selected ? 'selected' : ''}"
                         data-page="${i}" data-sol="${escHtml(solCode)}" data-type="${typeId}" data-file-idx="${fileIdx}"
                         id="page-card-${key}-${fileIdx}-${i}">
                        <div class="pdf-page-placeholder ${hasImg ? 'hidden' : ''}" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:var(--bg-tertiary);color:var(--text-tertiary);font-size:0.75rem;border-radius:var(--radius-sm);">Pg ${i}</div>
                        <img src="${dataUrl || ''}" alt="Page ${i}" class="${hasImg ? '' : 'hidden'}" loading="lazy">
                        <div class="pdf-page-card__overlay">
                            <span class="pdf-page-card__label">Pg ${i}</span>
                            <div style="display:flex;gap:4px;">
                                <button class="pdf-page-card__add" title="Add to assembly" data-action="add">+</button>
                                <button class="pdf-page-card__zoom" title="Zoom" data-action="zoom">🔍</button>
                            </div>
                        </div>
                    </div>
                `;
            }
            html += '</div>';
        });
        dom.sourceContent.innerHTML = html;

        dom.sourceContent.querySelectorAll('.pdf-page-card').forEach(card =>
            card.addEventListener('click', e => {
                const page = parseInt(card.dataset.page);
                const sol = card.dataset.sol;
                const type = card.dataset.type;
                const fileIdx = parseInt(card.dataset.fileIdx || '0');
                const zoomBtn = e.target.closest('[data-action="zoom"]');

                if (zoomBtn) {
                    openLightbox(sol, type, page, fileIdx);
                } else {
                    addPageToAssembly(sol, type, page, e.shiftKey, fileIdx);
                }
            })
        );

        // Setup IntersectionObserver to fetch pages in chunks of 20 as they scroll into view
        requestedRanges.clear(); // Clear stale entries from previous render
        rangeObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const card = entry.target;
                    const pageNum = parseInt(card.dataset.page);
                    const cardFileIdx = parseInt(card.dataset.fileIdx || '0');
                    // Look up the correct file entry from the list (fixes stale `cached` reference)
                    const fileEntry = fileList[cardFileIdx];
                    if (!fileEntry || fileEntry.pages[pageNum]) return;

                    // Request chunk around this page (chunk size = 20)
                    const chunkSize = 20;
                    const chunkStart = Math.floor((pageNum - 1) / chunkSize) * chunkSize + 1;
                    const rangeKey = `${key}_${cardFileIdx}_${chunkStart}`;

                    if (!requestedRanges.has(rangeKey)) {
                        requestedRanges.add(rangeKey);
                        // Pass fullPath directly so bridge renders the correct file (not re-resolve)
                        window.pywebview.api.fetch_page_range(
                            solCode,
                            typeId,
                            fileEntry.fileId || fileEntry.fullPath || state.folderInputs[typeId],
                            state.isDrive[typeId] === true,
                            chunkStart,
                            chunkSize,
                            cardFileIdx
                        );
                    }
                }
            });
        }, {
            root: dom.sourceContent,
            rootMargin: '300px', // Preload pages 300px before scrolling to them
            threshold: 0.01
        });

        dom.sourceContent.querySelectorAll('.pdf-page-card').forEach(card => {
            rangeObserver.observe(card);
        });

        // Auto-preload chunk 1 (pages 1-20) for EVERY file in fileList so all files render initial thumbnails immediately
        fileList.forEach((fileEntry, cardFileIdx) => {
            if (!fileEntry || fileEntry.pages[1]) return;
            const rangeKey = `${key}_${cardFileIdx}_1`;
            if (!requestedRanges.has(rangeKey)) {
                requestedRanges.add(rangeKey);
                window.pywebview.api.fetch_page_range(
                    solCode,
                    typeId,
                    fileEntry.fileId || fileEntry.fullPath || state.folderInputs[typeId],
                    state.isDrive[typeId] === true,
                    1,
                    20,
                    cardFileIdx
                );
            }
        });
    }

    // ── Source Viewer State Messages ───────────────────────
    function showSourceLoading(solCode, typeId) {
        dom.sourceContent.innerHTML = `
            <div class="source-loading">
                <div class="spinner active"></div>
                <span>Loading ${escHtml(solCode)} (${typeId})…</span>
                <span style="font-size:0.75rem;color:var(--text-tertiary);">Python is rendering thumbnails…</span>
            </div>
        `;
    }

    function showSourceNotFound(solCode, typeId) {
        if (dom.toolbarFileCount) dom.toolbarFileCount.textContent = 'File not found';
        if (dom.toolbarPathBox) dom.toolbarPathBox.style.display = 'none';
        dom.sourceContent.innerHTML = `
            <div class="source-empty">
                <span style="font-size:2rem;opacity:0.5;">📭</span>
                <span>No PDF found for "${escHtml(solCode)}" in ${typeId} folder</span>
            </div>
        `;
    }

    function showSourceError(msg) {
        dom.sourceContent.innerHTML = `
            <div class="source-empty">
                <span>❌ ${escHtml(msg)}</span>
            </div>
        `;
    }

    // ── Assembly State Management ──────────────────────────
    function addPageRangeToAssembly(solCode, typeId, startPage, endPage, fileIdx = 0) {
        if (!state.assemblyState[solCode]) state.assemblyState[solCode] = [];
        const q = state.assemblyState[solCode];
        const typeLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;
        const fileList = state.thumbnailCache[`${solCode}_${typeId}`] || [];
        const fileName = fileList[fileIdx] ? fileList[fileIdx].name : `File ${fileIdx + 1}`;
        let added = 0;

        const from = Math.min(startPage, endPage);
        const to = Math.max(startPage, endPage);

        for (let i = from; i <= to; i++) {
            if (!q.some(it => it.typeId === typeId && it.pageIndex === i && it.fileIdx === fileIdx)) {
                q.push({ solCode, typeId, fileIdx, pageIndex: i, label: `${typeLabel} - ${fileName} - Pg ${i}` });
                const card = document.getElementById(`page-card-${solCode}_${typeId}-${fileIdx}-${i}`);
                if (card) card.classList.add('selected');
                added++;
            }
        }

        updateAssemblyUI();
        renderSolList();
        showToast(
            added > 0 ? `Added ${added} pages (Pg ${from}..${to}) to ${solCode}` : `Pages ${from}..${to} already added`,
            added > 0 ? 'success' : 'info'
        );
    }

    function addPageToAssembly(solCode, typeId, pageIndex, isShift = false, fileIdx = 0) {
        if (isShift && state.lastSelectedPage &&
            state.lastSelectedPage.solCode === solCode &&
            state.lastSelectedPage.typeId === typeId &&
            state.lastSelectedPage.fileIdx === fileIdx) {
            addPageRangeToAssembly(solCode, typeId, state.lastSelectedPage.pageIndex, pageIndex, fileIdx);
            state.lastSelectedPage = { solCode, typeId, pageIndex, fileIdx };
            return;
        }

        state.lastSelectedPage = { solCode, typeId, pageIndex, fileIdx };

        if (!state.assemblyState[solCode]) state.assemblyState[solCode] = [];
        const q = state.assemblyState[solCode];
        if (q.some(it => it.typeId === typeId && it.pageIndex === pageIndex && it.fileIdx === fileIdx)) {
            showToast(`Page already added to ${solCode}`, 'warning');
            return;
        }
        const typeLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;
        const fileList = state.thumbnailCache[`${solCode}_${typeId}`] || [];
        const fileName = fileList[fileIdx] ? fileList[fileIdx].name : `File ${fileIdx + 1}`;
        const label = `${typeLabel} - ${fileName} - Pg ${pageIndex}`;
        q.push({ solCode, typeId, fileIdx, pageIndex, label });
        updateAssemblyUI();
        renderSolList();
        const card = document.getElementById(`page-card-${solCode}_${typeId}-${fileIdx}-${pageIndex}`);
        if (card) card.classList.add('selected');
        showToast(`Added ${solCode} (${label}) — Shift+Click another page to add range`, 'success');
    }

    function onAddAll() {
        const { activeSolCode: sol, activeTab: typeId } = state;
        const fileList = state.thumbnailCache[`${sol}_${typeId}`];
        if (!fileList || fileList.length === 0) { showToast('No PDF loaded to add', 'warning'); return; }

        if (!state.assemblyState[sol]) state.assemblyState[sol] = [];
        const q = state.assemblyState[sol];
        const typeLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;
        let added = 0;

        // Add all pages across all files
        fileList.forEach((fileEntry, fileIdx) => {
            const pageCount = fileEntry.pageCount || 0;
            const fileName = fileEntry.name || `File ${fileIdx + 1}`;
            for (let i = 1; i <= pageCount; i++) {
                if (!q.some(it => it.typeId === typeId && it.pageIndex === i && it.fileIdx === fileIdx)) {
                    q.push({ solCode: sol, typeId, fileIdx, pageIndex: i, label: `${typeLabel} - ${fileName} - Pg ${i}` });
                    added++;
                }
            }
        });
        updateAssemblyUI();
        renderSolList();
        renderSourceViewer(sol, typeId);
        showToast(
            added > 0 ? `Added ${added} pages to ${sol}` : 'All pages already added',
            added > 0 ? 'success' : 'info'
        );
    }

    function showCustomPrompt(title, message, placeholder = 'e.g. 5-15', defaultVal = '') {
        return new Promise((resolve) => {
            const modal = document.getElementById('custom-prompt-modal');
            const titleEl = document.getElementById('custom-prompt-title');
            const msgEl = document.getElementById('custom-prompt-msg');
            const inputEl = document.getElementById('custom-prompt-input');
            const btnConfirm = document.getElementById('custom-prompt-confirm');
            const btnCancel = document.getElementById('custom-prompt-cancel');

            titleEl.textContent = title;
            msgEl.textContent = message;
            inputEl.placeholder = placeholder;
            inputEl.value = defaultVal;
            modal.classList.add('active');

            setTimeout(() => { inputEl.focus(); inputEl.select(); }, 50);

            function cleanup() {
                modal.classList.remove('active');
                btnConfirm.removeEventListener('click', onConfirm);
                btnCancel.removeEventListener('click', onCancel);
                inputEl.removeEventListener('keydown', onKeydown);
            }

            function onConfirm() {
                const val = inputEl.value.trim();
                cleanup();
                resolve(val);
            }

            function onCancel() {
                cleanup();
                resolve(null);
            }

            function onKeydown(e) {
                if (e.key === 'Enter') onConfirm();
                else if (e.key === 'Escape') onCancel();
            }

            btnConfirm.addEventListener('click', onConfirm);
            btnCancel.addEventListener('click', onCancel);
            inputEl.addEventListener('keydown', onKeydown);
        });
    }

    function onAddRange() {
        const { activeSolCode: sol, activeTab: typeId } = state;
        const cached = state.thumbnailCache[`${sol}_${typeId}`];
        if (!cached || cached.length === 0) { showToast('No PDF loaded to add', 'warning'); return; }

        const typeLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;

        const promptForRange = (fileIdx) => {
            const fileEntry = cached[fileIdx];
            if (!fileEntry) return;
            const maxPage = fileEntry.pageCount || (fileEntry.pages ? Object.keys(fileEntry.pages).length : 0);

            showCustomPrompt(
                `Add Page Range (${sol} - File ${fileIdx + 1})`,
                `Enter page range from 1 to ${maxPage} (Example: 5-15 or 5 to 15):`,
                'e.g. 5-15'
            ).then(input => {
                if (!input) return;

                const match = input.match(/(\d+)\s*(?:-|to|\s)\s*(\d+)/i);
                if (!match) {
                    showToast('Invalid range format. Use e.g., 5-15 or 5 to 15.', 'error');
                    return;
                }

                const start = parseInt(match[1]);
                const end = parseInt(match[2]);

                if (isNaN(start) || isNaN(end) || start < 1 || end < 1 || start > maxPage || end > maxPage) {
                    showToast(`Page range must be between 1 and ${maxPage}.`, 'error');
                    return;
                }

                addPageRangeToAssembly(sol, typeId, start, end, fileIdx);
            });
        };

        if (cached.length > 1) {
            showCustomPrompt(
                `Select File for ${sol}`,
                `This source has ${cached.length} files. Enter the file number (1-${cached.length}):`,
                'e.g. 1',
                '1'
            ).then(fInput => {
                if (!fInput) return;
                const fNum = parseInt(fInput);
                if (isNaN(fNum) || fNum < 1 || fNum > cached.length) {
                    showToast(`Invalid file number. Must be between 1 and ${cached.length}.`, 'error');
                    return;
                }
                promptForRange(fNum - 1);
            });
        } else {
            promptForRange(0);
        }
    }

    function onClearAssembly() {
        state.assemblyState = {};
        updateAssemblyUI();
        renderSolList();
        if (state.activeSolCode) renderSourceViewer(state.activeSolCode, state.activeTab);
        showToast('Assembly queue cleared', 'info');
    }

    function updateAssemblyUI() {
        const solKeys = Object.keys(state.assemblyState).filter(k => state.assemblyState[k].length > 0);
        let total = 0;

        if (!solKeys.length) {
            dom.assemblyEmpty.classList.remove('hidden');
            dom.assemblyGroupedContainer.innerHTML = '';
            dom.btnExport.disabled = true;
            dom.assemblyPageCount.textContent = '0';
            return;
        }

        dom.assemblyEmpty.classList.add('hidden');
        let html = '';

        solKeys.forEach(sol => {
            const items = state.assemblyState[sol];
            total += items.length;
            html += `
                <div class="sol-assembly-group" data-sol-group="${escHtml(sol)}">
                    <div class="sol-assembly-header">
                        <span>📋 ${escHtml(sol)} (${items.length} pgs)</span>
                        <button class="assembly-item__remove" data-remove-sol="${escHtml(sol)}" title="Remove whole SOL">✕</button>
                    </div>
                    <ul class="assembly-list" data-sol-list="${escHtml(sol)}">
                        ${items.map((item, idx) => `
                            <li class="assembly-item" data-sol="${escHtml(sol)}" data-idx="${idx}">
                                <span class="assembly-item__grip">⠿</span>
                                <span class="assembly-item__index">${idx + 1}</span>
                                <div class="assembly-item__info">${escHtml(item.label)}</div>
                                <button class="assembly-item__remove" data-remove-item="${idx}" data-sol="${escHtml(sol)}">✕</button>
                            </li>
                        `).join('')}
                    </ul>
                </div>
            `;
        });

        dom.assemblyGroupedContainer.innerHTML = html;
        dom.assemblyPageCount.textContent = total;
        dom.btnExport.disabled = total === 0 || state.isExporting;

        // Remove-SOL buttons
        dom.assemblyGroupedContainer.querySelectorAll('[data-remove-sol]').forEach(btn =>
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const sol = btn.dataset.removeSol;
                delete state.assemblyState[sol];
                updateAssemblyUI(); renderSolList();
                if (state.activeSolCode === sol) renderSourceViewer(sol, state.activeTab);
            })
        );

        // Remove-item buttons
        dom.assemblyGroupedContainer.querySelectorAll('[data-remove-item]').forEach(btn =>
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const sol = btn.dataset.sol;
                const idx = parseInt(btn.dataset.removeItem);
                state.assemblyState[sol].splice(idx, 1);
                if (!state.assemblyState[sol].length) delete state.assemblyState[sol];
                updateAssemblyUI(); renderSolList();
                if (state.activeSolCode === sol) renderSourceViewer(sol, state.activeTab);
            })
        );

        // Drag-to-reorder within each SOL group
        solKeys.forEach(sol => {
            const listEl = dom.assemblyGroupedContainer.querySelector(`[data-sol-list="${CSS.escape(sol)}"]`);
            if (!listEl) return;
            Sortable.create(listEl, {
                animation: 150,
                filter: '.assembly-item__remove',
                preventOnFilter: false,
                ghostClass: 'sortable-ghost',
                onEnd() {
                    const newItems = Array.from(listEl.querySelectorAll('.assembly-item'))
                        .map(el => state.assemblyState[sol][parseInt(el.dataset.idx)]);
                    state.assemblyState[sol] = newItems;
                    updateAssemblyUI();
                }
            });
        });
    }

    // ── Export ─────────────────────────────────────────────
    function onExport() {
        if (state.isExporting) return;
        state.isExporting = true;

        dom.btnExport.disabled = true;
        dom.exportProgress.classList.remove('hidden');
        dom.exportProgressFill.style.width = '0%';

        const assemblyJson = JSON.stringify(state.assemblyState);
        const folderInputsJson = JSON.stringify(state.folderInputs);
        const isDriveJson = JSON.stringify(state.isDrive);

        window.pywebview.api.export_assembly(
            assemblyJson,
            folderInputsJson,
            isDriveJson,
            state.outputPath,
            state.exportMode
        );
        // Result arrives via window._onExportDone()
    }

    // ── Lightbox ──────────────────────────────────────────
    function openLightbox(solCode, typeId, pageIndex, fileIdx) {
        fileIdx = fileIdx || 0;
        const key = `${solCode}_${typeId}`;
        const fileList = state.thumbnailCache[key];
        if (!fileList || fileList.length === 0) return;
        const fileEntry = fileList[fileIdx] || fileList[0];
        if (!fileEntry) return;

        state.lightbox = { solCode, typeId, pageIndex, fileIdx, scale: 1.0, rotation: 0 };
        dom.lightbox.classList.add('active');

        // Show low-res thumbnail immediately (from per-file pages map)
        const thumbUrl = fileEntry.pages[pageIndex] || '';
        dom.lightboxImg.src = thumbUrl;
        updateLightboxTransform();

        const tLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;
        const fileSuffix = fileList.length > 1 ? ` [${fileIdx + 1}/${fileList.length}]` : '';
        dom.lightboxInfo.textContent = `${solCode} • ${tLabel}${fileSuffix} • Page ${pageIndex} of ${fileEntry.pageCount}`;

        // Upgrade to high-res asynchronously — pass the file's source path directly
        // to avoid bridge re-resolving to the wrong file
        window.pywebview.api.get_page_hires_by_path(
            solCode, typeId, pageIndex,
            fileEntry.fileId || fileEntry.fullPath || ''
        );
    }

    function closeLightbox() {
        dom.lightbox.classList.remove('active');
        dom.lightboxImg.src = '';
    }

    function rotateLightbox() {
        state.lightbox.rotation = (state.lightbox.rotation + 90) % 360;
        updateLightboxTransform();
    }

    function zoomLightbox(d) {
        state.lightbox.scale = Math.min(Math.max(state.lightbox.scale + d, 0.4), 5.0);
        updateLightboxTransform();
    }

    function resetLightboxTransform() {
        state.lightbox.scale = 1.0;
        state.lightbox.rotation = 0;
        updateLightboxTransform();
    }

    function updateLightboxTransform() {
        const { scale, rotation } = state.lightbox;
        dom.lightboxImg.style.transform = `scale(${scale}) rotate(${rotation}deg)`;
        dom.lightboxZoomLevel.textContent = Math.round(scale * 100) + '%';
    }

    // ── Error / Toast helpers ──────────────────────────────
    function showError(msg) {
        dom.inputError.textContent = msg;
        dom.inputError.classList.remove('hidden');
    }

    function hideError() {
        dom.inputError.classList.add('hidden');
    }

    function showToast(msg, type = 'info', duration = 4000) {
        const el = document.createElement('div');
        el.className = `toast toast--${type}`;
        el.innerHTML = `
            <div style="flex:1;">${escHtml(msg)}</div>
            <button class="toast-close-btn" style="background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:0.9rem;padding:0 4px;" title="Close">✕</button>
        `;
        el.querySelector('.toast-close-btn').addEventListener('click', () => el.remove());
        dom.toastContainer.appendChild(el);
        if (duration > 0) {
            setTimeout(() => { el.remove(); }, duration);
        }
    }

    function showToastWithAction(msg, type, actionLabel, actionFn, timestampStr = '') {
        const el = document.createElement('div');
        el.className = `toast toast--${type}`;

        let headerHtml = '';
        if (timestampStr) {
            headerHtml = `<div style="font-size:0.7rem;color:var(--text-tertiary);margin-bottom:2px;font-weight:600;">🕒 ${escHtml(timestampStr)}</div>`;
        }

        el.innerHTML = `
            <div style="flex:1;">
                ${headerHtml}
                <div>${escHtml(msg)}</div>
                <div class="toast-actions" style="margin-top:6px;">
                    <button id="_toast-action">${escHtml(actionLabel)}</button>
                </div>
            </div>
            <button class="toast-close-btn" style="background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:0.9rem;padding:0 4px;align-self:flex-start;" title="Close">✕</button>
        `;
        el.querySelector('#_toast-action').addEventListener('click', () => {
            actionFn();
        });
        el.querySelector('.toast-close-btn').addEventListener('click', () => {
            el.remove();
        });
        dom.toastContainer.appendChild(el);
    }

    function showTracingBenchmarkToast(startTime, endTime) {
        if (!dom.toastContainer || !startTime || !endTime) return;

        const pad = n => String(n).padStart(2, '0');
        const formatTime = d => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

        const startStr = formatTime(startTime);
        const endStr = formatTime(endTime);
        const diffMs = Math.max(0, endTime.getTime() - startTime.getTime());

        let durationStr = `${(diffMs / 1000).toFixed(2)}s`;
        if (diffMs >= 60000) {
            const mins = Math.floor(diffMs / 60000);
            const secs = ((diffMs % 60000) / 1000).toFixed(1);
            durationStr = `${mins}m ${secs}s (${(diffMs / 1000).toFixed(2)}s)`;
        }

        const el = document.createElement('div');
        el.className = 'toast toast--info';
        el.style.cssText = 'min-width:280px;display:flex;align-items:flex-start;justify-content:space-between;gap:8px;';
        el.innerHTML = `
            <div style="flex:1;">
                <div style="font-weight:700;margin-bottom:4px;display:flex;align-items:center;gap:6px;">
                    <span>⏱️</span> Tracing Benchmark
                </div>
                <div style="font-size:0.82rem;line-height:1.4;">
                    <div><b>Start Time:</b> <span style="font-family:monospace;">${escHtml(startStr)}</span></div>
                    <div><b>End Time:</b> <span style="font-family:monospace;">${escHtml(endStr)}</span></div>
                    <div><b>Duration:</b> <span style="font-family:monospace;font-weight:700;color:var(--accent-primary);">${escHtml(durationStr)}</span></div>
                </div>
            </div>
            <button class="toast-close-btn" style="background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:0.9rem;padding:0 4px;align-self:flex-start;" title="Close">✕</button>
        `;

        el.querySelector('.toast-close-btn').addEventListener('click', () => {
            el.remove();
        });

        dom.toastContainer.appendChild(el);
    }


    function escHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ============================================================
    // AUTO DETECT — State & DOM
    // ============================================================

    const adState = {
        exportMode: 'split',
        isExporting: false,
        assembly: {},   // { sol: { typeId: [pageNums] } }
        // Progress tracking for bar calculation
        totalPages: {},  // { "sol_typeId": total } — populated during progress
        progressLog: [], // recent progress events
        ocrEnabled: false,  // OCR Mode toggle (reset to false on each modal open)
    };

    const METHOD_LABELS = { barcode: '📊 Barcode', digital: '📄 Digital', scanned: '🔍 OCR Scan' };
    const DEFAULT_CONFIG = {
        soview: ['barcode'],
        layout: ['digital', 'scanned'],
    };

    function cacheAdDom() {
        dom.adModal = document.getElementById('auto-detect-modal');
        dom.adConfigTbody = document.getElementById('ad-config-tbody');
        dom.adExceptBtn = document.getElementById('ad-except-btn');
        dom.adExceptContainer = document.getElementById('ad-exceptions-container');
        dom.adModalCancelBtn = document.getElementById('ad-modal-cancel-btn');
        dom.adRunBtn = document.getElementById('ad-run-btn');
        dom.adOcrToggleBtn = document.getElementById('ad-ocr-toggle-btn');
        dom.adOcrIcon = document.getElementById('ad-ocr-icon');
        dom.adOcrLabel = document.getElementById('ad-ocr-label');
        dom.adOcrDesc = document.getElementById('ad-ocr-desc');

        dom.adProgressModal = document.getElementById('auto-detect-progress-modal');
        dom.adProgSol = document.getElementById('ad-prog-sol');
        dom.adProgType = document.getElementById('ad-prog-type');
        dom.adProgMethod = document.getElementById('ad-prog-method');
        dom.adProgFilename = document.getElementById('ad-prog-filename');
        dom.adProgFileCount = document.getElementById('ad-prog-file-count');
        dom.adProgFill = document.getElementById('ad-prog-fill');
        dom.adProgressCancelBtn = document.getElementById('ad-progress-cancel-btn');

        dom.adResultsScreen = document.getElementById('auto-detect-results-screen');
        dom.adResultsTbody = document.getElementById('ad-results-tbody');
        dom.adResultsSubtitle = document.getElementById('ad-results-subtitle');
        dom.btnAdBack = document.getElementById('btn-ad-back');
        dom.btnAdExport = document.getElementById('btn-ad-export');
        dom.adExportProgress = document.getElementById('ad-export-progress');
        dom.adExportProgressFill = document.getElementById('ad-export-progress-fill');

        dom.adPreviewModal = document.getElementById('auto-detect-preview-modal');
        dom.adPreviewTitle = document.getElementById('ad-preview-title');
        dom.adPreviewWrapper = document.getElementById('ad-preview-wrapper');
        dom.adPreviewScrollArea = document.getElementById('ad-preview-scroll-area');
        dom.adPreviewCloseBtn = document.getElementById('ad-preview-close-btn');
        dom.adPreviewZoomIn = document.getElementById('ad-preview-zoom-in');
        dom.adPreviewZoomOut = document.getElementById('ad-preview-zoom-out');
        dom.adPreviewZoomReset = document.getElementById('ad-preview-zoom-reset');
        dom.adPreviewZoomLevel = document.getElementById('ad-preview-zoom-level');
    }

    // ── Build Config Grid ──────────────────────────────────────────────────────

    const METHOD_OPTIONS = [
        { value: '', label: '— none —' },
        { value: 'barcode', label: '📊 Barcode' },
        { value: 'digital', label: '📄 Digital' },
        { value: 'scanned', label: '🔍 OCR Scan' },
    ];

    function makeMethodSelect(id, defaultVal) {
        const opts = METHOD_OPTIONS.map(o =>
            `<option value="${o.value}" ${o.value === defaultVal ? 'selected' : ''}>${escHtml(o.label)}</option>`
        ).join('');
        return `<select id="${id}" style="width:100%;padding:5px 8px;font-size:0.82rem;border:1px solid var(--border-color);border-radius:var(--radius-sm);background:var(--bg-primary);color:var(--text-primary);">${opts}</select>`;
    }

    function buildConfigGrid() {
        const isRfidSb = state.currentLine === 'RFID SB';
        const rows = pdfTypes.map(t => {
            if (isRfidSb && t.id === 'layout') {
                return `
                    <tr>
                        <td style="padding:8px 12px;font-weight:600;">${escHtml(t.label)}</td>
                        <td colspan="3" style="padding:6px 8px;">
                            <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;font-size:0.82rem;font-weight:600;background:rgba(79,70,229,0.08);color:#4f46e5;border-radius:var(--radius-sm);border:1px solid rgba(79,70,229,0.2);">
                                🔒 Deduplicate (13-digit ID / Barcode)
                            </span>
                        </td>
                    </tr>
                `;
            }
            const defaults = DEFAULT_CONFIG[t.id] || [];
            const p1 = defaults[0] || '';
            const p2 = defaults[1] || '';
            const p3 = defaults[2] || '';
            return `
                <tr>
                    <td style="padding:8px 12px;font-weight:600;">${escHtml(t.label)}</td>
                    <td style="padding:6px 8px;">${makeMethodSelect(`ad-cfg-${t.id}-p1`, p1)}</td>
                    <td style="padding:6px 8px;">${makeMethodSelect(`ad-cfg-${t.id}-p2`, p2)}</td>
                    <td style="padding:6px 8px;">${makeMethodSelect(`ad-cfg-${t.id}-p3`, p3)}</td>
                </tr>
            `;
        }).join('');
        dom.adConfigTbody.innerHTML = rows;
    }

    // ── Exception Rows ─────────────────────────────────────────────────────────

    let adExceptionCount = 0;

    function addExceptionRow() {
        adExceptionCount++;
        const id = adExceptionCount;
        const solOptions = state.solList.map(s =>
            `<option value="${escHtml(s)}">${escHtml(s)}</option>`
        ).join('');
        const typeOptions = pdfTypes.map(t =>
            `<option value="${t.id}">${escHtml(t.label)}</option>`
        ).join('');

        const rowHtml = `
            <div id="ad-except-row-${id}" style="display:flex;align-items:center;gap:6px;padding:8px;background:var(--bg-primary);border-radius:var(--radius-sm);border:1px solid var(--border-color);flex-wrap:wrap;">
                <select id="ad-exc-sol-${id}" style="font-size:0.8rem;padding:4px 6px;border:1px solid var(--border-color);border-radius:var(--radius-sm);background:var(--bg-secondary);color:var(--text-primary);">${solOptions}</select>
                <select id="ad-exc-type-${id}" style="font-size:0.8rem;padding:4px 6px;border:1px solid var(--border-color);border-radius:var(--radius-sm);background:var(--bg-secondary);color:var(--text-primary);">${typeOptions}</select>
                <span style="font-size:0.75rem;color:var(--text-secondary);">P1:</span>
                ${makeMethodSelect(`ad-exc-p1-${id}`, '')}
                <span style="font-size:0.75rem;color:var(--text-secondary);">P2:</span>
                ${makeMethodSelect(`ad-exc-p2-${id}`, '')}
                <span style="font-size:0.75rem;color:var(--text-secondary);">P3:</span>
                ${makeMethodSelect(`ad-exc-p3-${id}`, '')}
                <button onclick="document.getElementById('ad-except-row-${id}').remove()" class="btn btn--sm" style="background:rgba(225,29,72,0.08);color:var(--accent-danger);border:1px solid rgba(225,29,72,0.2);padding:3px 8px;">✕</button>
            </div>
        `;
        dom.adExceptContainer.insertAdjacentHTML('beforeend', rowHtml);
    }

    // ── Collect Config from DOM ────────────────────────────────────────────────

    function collectAutoDetectConfig() {
        const config = {};
        pdfTypes.forEach(t => {
            const chain = [];
            ['p1', 'p2', 'p3'].forEach(p => {
                const sel = document.getElementById(`ad-cfg-${t.id}-${p}`);
                if (sel && sel.value) chain.push(sel.value);
            });
            config[t.id] = chain;
        });

        const exceptions = {};
        dom.adExceptContainer.querySelectorAll('[id^="ad-except-row-"]').forEach(row => {
            const rowId = row.id.replace('ad-except-row-', '');
            const sol = document.getElementById(`ad-exc-sol-${rowId}`)?.value;
            const type = document.getElementById(`ad-exc-type-${rowId}`)?.value;
            if (!sol || !type) return;
            const chain = [];
            ['p1', 'p2', 'p3'].forEach(p => {
                const sel = document.getElementById(`ad-exc-${p}-${rowId}`);
                if (sel && sel.value) chain.push(sel.value);
            });
            if (!exceptions[sol]) exceptions[sol] = {};
            exceptions[sol][type] = chain;
        });

        return { config, exceptions };
    }

    // ── OCR Mode Toggle ────────────────────────────────────────────────────────

    function setOcrMode(enabled) {
        adState.ocrEnabled = enabled;
        if (!dom.adOcrToggleBtn) return;
        if (enabled) {
            dom.adOcrIcon.textContent = '🔍';
            dom.adOcrLabel.textContent = 'OCR Mode: ON';
            dom.adOcrDesc.textContent = 'Multi-page OCR (Tesseract V2)';
            dom.adOcrToggleBtn.style.background = 'rgba(79,70,229,0.12)';
            dom.adOcrToggleBtn.style.borderColor = '#4f46e5';
            dom.adOcrToggleBtn.style.color = '#4f46e5';
        } else {
            dom.adOcrIcon.textContent = '⚡';
            dom.adOcrLabel.textContent = 'OCR Mode: OFF';
            dom.adOcrDesc.textContent = 'Fast 1D barcode only';
            dom.adOcrToggleBtn.style.background = 'var(--bg-primary)';
            dom.adOcrToggleBtn.style.borderColor = 'var(--border-color)';
            dom.adOcrToggleBtn.style.color = 'var(--text-secondary)';
        }
    }

    // ── Open / Close Modal ─────────────────────────────────────────────────────

    function openAutoDetectModal() {
        const isTrim = state.currentLine === 'Trim';

        // PDF Merger (Trim): skip the config modal entirely — call backend directly
        if (isTrim) {
            _startTrimAutoDetect();
            return;
        }

        // Ensure solList is deduplicated for exception rows
        if (state.inputMode === 'manual') {
            state.solList = parseManualSolCodes();
        } else {
            state.solList = Array.from(new Set((state.solList || []).map(s => String(s).trim()).filter(s => s.length > 0)));
        }
        // Reset exceptions
        adExceptionCount = 0;
        dom.adExceptContainer.innerHTML = '';
        // Reset OCR Mode to OFF
        setOcrMode(false);
        // Rebuild grid with defaults
        buildConfigGrid();

        // PFL Thermal: hide the OCR toggle in the modal
        const isPflThermal = state.currentLine === 'PFL THERMAL';
        if (dom.adOcrToggleBtn) dom.adOcrToggleBtn.style.display = isPflThermal ? 'none' : '';
        if (dom.adOcrDesc) dom.adOcrDesc.style.display = isPflThermal ? 'none' : '';

        // Show modal
        dom.adModal.classList.add('active');
    }

    // PDF Merger (Trim): directly launch backend pipeline (no config modal)
    function _startTrimAutoDetect() {
        const solList = state.inputMode === 'manual'
            ? parseManualSolCodes()
            : Array.from(new Set((state.solList || []).map(s => String(s).trim()).filter(s => s.length > 0)));

        if (!solList.length) {
            showToast('No SOL codes available. Please select a preset or enter SOLs first.', 'error');
            return;
        }
        state.solList = solList;

        // Collect folder inputs
        pdfTypes.forEach(t => {
            const inp = document.getElementById(`folder-${t.id}`);
            state.folderInputs[t.id] = inp ? inp.value.trim() : '';
            state.isDrive[t.id] = false;  // Trim always local/UNC
        });
        state.outputPath = dom.outputFolderInput ? dom.outputFolderInput.value.trim() : '';

        const hasFolders = pdfTypes.every(t => state.folderInputs[t.id] && state.folderInputs[t.id].length > 0);
        if (!hasFolders) {
            showToast('Please enter folder paths for SOview and TE folders first.', 'error');
            return;
        }

        // Show progress modal with checklist
        dom.adProgressModal.classList.add('active');
        adState.progressLog = [];
        if (dom.adProgFill) dom.adProgFill.style.width = '0%';
        const solListEl = document.getElementById('ad-sol-list');
        if (solListEl) {
            solListEl.innerHTML = '';
            solList.forEach(sol => {
                const li = document.createElement('li');
                li.id = `ad-li-${sol}`;
                li.style.cssText = 'padding:8px 12px;border-bottom:1px solid var(--border-color);display:flex;align-items:center;gap:10px;';
                li.innerHTML = `<span class="icon" style="color:var(--text-secondary);width:16px;">⏳</span> <span style="font-weight:600;font-family:monospace;">${sol}</span>`;
                solListEl.appendChild(li);
            });
        }
        window._adActiveSol = null;
        window._adStartTime = new Date();

        window.pywebview.api.run_auto_detect_trim(
            JSON.stringify(solList),
            JSON.stringify(state.folderInputs),
            state.outputPath
        );
    }

    function closeAutoDetectModal() {
        dom.adModal.classList.remove('active');
    }

    // ── Run Auto Detect ────────────────────────────────────────────────────────

    function runAutoDetect() {
        // Collect current folder inputs & SOL list from input screen state
        pdfTypes.forEach(t => {
            const inp = document.getElementById(`folder-${t.id}`);
            const cb = document.getElementById(`is-drive-${t.id}`);
            state.folderInputs[t.id] = inp ? inp.value.trim() : '';
            state.isDrive[t.id] = cb ? cb.checked : false;
        });
        state.outputPath = dom.outputFolderInput.value.trim();

        const solList = state.inputMode === 'manual'
            ? parseManualSolCodes()
            : Array.from(new Set((state.solList || []).map(s => String(s).trim()).filter(s => s.length > 0)));

        if (!solList.length) {
            showToast('No SOL codes available. Please select a user preset or enter SOLs first.', 'error');
            return;
        }

        state.solList = solList;

        const hasFolders = pdfTypes.every(t => state.folderInputs[t.id] && state.folderInputs[t.id].length > 0);
        if (!hasFolders) {
            showToast('Please enter folder paths for both SOview and Layout first.', 'error');
            return;
        }

        const { config, exceptions } = collectAutoDetectConfig();

        // Hide config modal, show progress modal
        closeAutoDetectModal();
        dom.adProgressModal.classList.add('active');

        // Reset progress state
        adState.totalPages = {};
        adState.progressLog = [];
        if (dom.adProgFill) dom.adProgFill.style.width = '0%';

        // Populate the clean checklist UI
        const solListEl = document.getElementById('ad-sol-list');
        if (solListEl) {
            solListEl.innerHTML = '';
            solList.forEach(sol => {
                const li = document.createElement('li');
                li.id = `ad-li-${sol}`;
                li.style.padding = '8px 12px';
                li.style.borderBottom = '1px solid var(--border-color)';
                li.style.display = 'flex';
                li.style.alignItems = 'center';
                li.style.gap = '10px';
                li.innerHTML = `<span class="icon" style="color:var(--text-secondary); width:16px;">⏳</span> <span style="font-weight:600;font-family:monospace;">${sol}</span>`;
                solListEl.appendChild(li);
            });
        }
        window._adActiveSol = null;

        let flagsJson = "{}";
        let jobMapJson = "{}";
        if (state.currentLine === 'RFID HT') {
            if (!validateRfidFlags(state.rfidEntries)) {
                closeAutoDetectModal();
                showToast('Conflicting Flag values detected for the same SO. Please fix in sheet.', 'error', 5000);
                return;
            }
            const flagsMap = {};
            const jobMap = {};
            state.rfidEntries.forEach(e => {
                if (e.sol) {
                    flagsMap[e.sol] = Boolean(e.flag);
                    if (e.job) jobMap[e.sol] = String(e.job).trim();
                }
            });
            flagsJson = JSON.stringify(flagsMap);
            jobMapJson = JSON.stringify(jobMap);
        } else if (state.currentLine === 'PFL THERMAL') {
            // PFL Thermal: treat all SOLs as Flag=True (Barcode Range Scan)
            const flagsMap = {};
            solList.forEach(sol => { flagsMap[sol] = true; });
            flagsJson = JSON.stringify(flagsMap);
            jobMapJson = "{}";
        } else if (state.currentLine === 'PFL') {
            // PFL: pass job_flag per SOL
            const flagsMap = {};
            (state.pflEntries || []).forEach(e => {
                if (e && typeof e === 'object' && e.sol) {
                    flagsMap[e.sol] = Boolean(e.job_flag);
                }
            });
            flagsJson = JSON.stringify(flagsMap);
            jobMapJson = "{}";
        }

        window._adStartTime = new Date();

        window.pywebview.api.run_auto_detect(
            JSON.stringify(solList),
            JSON.stringify(state.folderInputs),
            JSON.stringify(state.isDrive),
            JSON.stringify(config),
            JSON.stringify(exceptions),
            Boolean(adState.ocrEnabled),
            flagsJson,
            jobMapJson,
            state.currentLine || 'PFL'
        );
    }

    // ── pywebview callbacks ────────────────────────────────────────────────────

    window._onAutoDetectProgress = function (payload) {
        if (payload.sol === "Batch Search") {
            // Keep the spinner running, do not update checklist for batch search phase
            return;
        }

        if (window._adActiveSol !== payload.sol) {
            // Mark previous as complete
            if (window._adActiveSol) {
                const prevLi = document.getElementById(`ad-li-${window._adActiveSol}`);
                if (prevLi) {
                    prevLi.querySelector('.icon').innerHTML = '✅';
                    prevLi.querySelector('.icon').style.color = 'var(--accent-success)';
                }
            }
            // Mark new as active
            window._adActiveSol = payload.sol;
            const newLi = document.getElementById(`ad-li-${window._adActiveSol}`);
            if (newLi) {
                newLi.querySelector('.icon').innerHTML = '🔄';
                newLi.querySelector('.icon').style.color = 'var(--accent-primary)';
                newLi.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        }

        // Update progress bar based on file fraction
        const pct = payload.totalFiles > 0
            ? Math.round((payload.currentFile / payload.totalFiles) * 100)
            : 0;
        if (dom.adProgFill) dom.adProgFill.style.width = pct + '%';
    };

    window._onAutoDetectDone = function (result) {
        dom.adProgressModal.classList.remove('active');

        if (!result.success) {
            window._adStartTime = null;
            showToast('❌ Auto Detect failed: ' + (result.error || 'Unknown error'), 'error', 0);
            return;
        }

        // Mark the final active SOL as complete
        if (window._adActiveSol) {
            const prevLi = document.getElementById(`ad-li-${window._adActiveSol}`);
            if (prevLi) {
                prevLi.querySelector('.icon').innerHTML = '✅';
                prevLi.querySelector('.icon').style.color = 'var(--accent-success)';
            }
        }

        const resModal = document.getElementById('auto-detect-results-modal');
        const summaryText = document.getElementById('ad-results-summary-text');
        const grid = document.getElementById('ad-results-grid');

        // Store globally so the apply button can access it
        window._adFinalResult = result;

        // ── PDF Merger (Trim): rich BQ summary ─────────────────────────
        if (state.currentLine === 'Trim') {
            const bqMeta = result.bq_metadata || {};
            const missingD = result.missing_data || {};
            const missingOI = missingD.missing_ordered_item || [];
            const missingTE = missingD.missing_te || [];
            const allMissing = Array.from(new Set([...missingOI, ...missingTE]));

            // Warning banner for SOLs with missing BQ data
            let warnHtml = '';
            if (allMissing.length > 0) {
                warnHtml = `
                  <div style="background:rgba(255,180,0,0.12);border:1px solid var(--accent-warning,#f59e0b);
                              border-radius:6px;padding:10px 14px;margin-bottom:12px;">
                    <div style="font-weight:700;color:var(--accent-warning,#f59e0b);margin-bottom:4px;">⚠️ Missing BigQuery Data</div>
                    ${missingOI.length ? `<div style="font-size:0.82rem;margin-bottom:2px;"><b>Missing Ordered Item:</b> ${missingOI.map(s => `<code style="font-family:monospace;">${escHtml(s)}</code>`).join(', ')}</div>` : ''}
                    ${missingTE.length ? `<div style="font-size:0.82rem;"><b>Missing TE list:</b> ${missingTE.map(s => `<code style="font-family:monospace;">${escHtml(s)}</code>`).join(', ')}</div>` : ''}
                  </div>`;
            }
            if (result.bq_error) {
                warnHtml += `<div style="background:rgba(239,68,68,0.1);border:1px solid var(--accent-danger,#ef4444);
                                          border-radius:6px;padding:8px 12px;margin-bottom:12px;font-size:0.82rem;color:var(--accent-danger,#ef4444);">
                    ⚠️ BigQuery error: ${escHtml(result.bq_error)}</div>`;
            }

            // Build detail table for each SOL
            const solList = Object.keys(result.assembly || {});
            let tableRows = solList.map(sol => {
                const meta = bqMeta[sol] || {};
                const svPages = meta.soview_pages || 0;
                const tePages = meta.te_pages || 0;
                const totalP = svPages + tePages;
                const targetTE = (meta.list_te || []).length;
                const svFile = meta.soview_file || '—';
                const svPath = meta.soview_path || '';
                const teFile = meta.te_file || '—';
                const tePath = meta.te_path || '';
                const hasPages = totalP > 0;
                const previewBtn = hasPages
                    ? `<button class="btn btn--sm btn--secondary ad-preview-btn" data-sol="${escHtml(sol)}"
                              style="font-size:0.75rem;padding:4px 10px;">👁 Preview</button>`
                    : `<span style="color:var(--text-tertiary);font-size:0.75rem;">—</span>`;
                return `
                  <tr style="${totalP === 0 ? 'opacity:0.5;' : ''}">
                    <td style="padding:8px 10px;font-weight:600;font-family:monospace;font-size:0.82rem;">${escHtml(sol)}</td>
                    <td style="padding:8px 10px;text-align:center;">
                      ${svPages > 0 ? `<span style="color:var(--accent-success);font-weight:600;">${svPages}</span>` : '<span style="color:var(--text-tertiary);">0</span>'}
                      <br><span title="${escHtml(svPath)}" style="font-size:0.72rem;color:var(--text-secondary);cursor:default;" title="${escHtml(svPath)}">${escHtml(svFile)}</span>
                    </td>
                    <td style="padding:8px 10px;text-align:center;">
                      ${tePages > 0 ? `<span style="color:var(--accent-success);font-weight:600;">${tePages}</span>` : '<span style="color:var(--text-tertiary);">0</span>'}
                      <br><span style="font-size:0.72rem;color:var(--text-secondary);cursor:default;" title="${escHtml(tePath)}">${escHtml(teFile)}</span>
                    </td>
                    <td style="padding:8px 10px;text-align:center;font-weight:600;">${targetTE || '—'}</td>
                    <td style="padding:8px 10px;text-align:center;font-weight:700;">${totalP}</td>
                    <td style="padding:8px 10px;text-align:center;">${previewBtn}</td>
                  </tr>`;
            }).join('');

            grid.innerHTML = `
              ${warnHtml}
              <table style="width:100%;border-collapse:collapse;font-size:0.83rem;">
                <thead>
                  <tr style="background:var(--bg-tertiary);">
                    <th style="padding:8px 10px;text-align:left;">SOL Code</th>
                    <th style="padding:8px 10px;text-align:center;">SOview Pages &amp; File</th>
                    <th style="padding:8px 10px;text-align:center;">TE Pages &amp; File</th>
                    <th style="padding:8px 10px;text-align:center;">Target TEs (BQ)</th>
                    <th style="padding:8px 10px;text-align:center;">Total Pages</th>
                    <th style="padding:8px 10px;text-align:center;">Preview</th>
                  </tr>
                </thead>
                <tbody>${tableRows}</tbody>
              </table>`;

            const foundCount = solList.filter(s => {
                const m = bqMeta[s] || {};
                return (m.soview_pages || 0) + (m.te_pages || 0) > 0;
            }).length;
            summaryText.innerHTML = `PDF Merger (Trim) detection complete. Found pages for <strong>${foundCount} / ${solList.length}</strong> SOLs.`;

            if (result.errors && result.errors.length > 0) {
                const errMsgs = result.errors.map(e => `${e.sol} (${e.typeId}): ${e.error}`).join('; ');
                showToast('⚠️ Scan issues: ' + errMsgs, 'warning', 6000);
            }

            resModal.classList.add('active');
            if (result.tracing_enabled && window._adStartTime) {
                showTracingBenchmarkToast(window._adStartTime, new Date());
                window._adStartTime = null;
            }
            // Wire preview buttons inside the new table
            grid.querySelectorAll('.ad-preview-btn').forEach(btn => {
                btn.addEventListener('click', () => openPreviewModal(btn.dataset.sol));
            });
            return;
        }
        // ── End PDF Merger (Trim) branch ────────────────────────────────

        // ── RFID SB: dedicated rich summary table ─────────────────
        if (state.currentLine === 'RFID SB') {
            const cardEl = resModal.querySelector('.config-card');
            if (cardEl) cardEl.style.maxWidth = '880px';

            const detailsEl = grid.closest('details');
            if (detailsEl) detailsEl.open = true;
            grid.style.display = 'block';

            const sbMeta = result.rfid_sb_metadata || {};
            const solList = Object.keys(result.assembly || {});

            function formatFileLink(filePath, fileName) {
                if (!filePath && !fileName) {
                    return `<span style="color:var(--accent-danger,#ef4444);font-size:0.75rem;font-weight:500;">❌ Không tìm thấy file</span>`;
                }
                const name = fileName || (filePath ? filePath.split(/[\\/]/).pop() : '') || 'file.pdf';
                const p = filePath || '';
                let shortcut = p;
                if (shortcut.length > 38) {
                    shortcut = shortcut.substring(0, 16) + '...' + shortcut.substring(shortcut.length - 18);
                }
                return `
                    <div style="margin-top:3px;line-height:1.3;">
                        <span style="font-size:0.76rem;font-weight:600;color:var(--text-primary);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px;margin:0 auto;" title="${escHtml(name)}">${escHtml(name)}</span>
                        ${p ? `<a href="#" class="ad-file-link" data-path="${escHtml(p)}" title="${escHtml(p)} (Click để mở file)" style="font-size:0.72rem;color:var(--accent-primary,#3b82f6);text-decoration:underline;cursor:pointer;display:inline-block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:250px;">🔗 ${escHtml(shortcut)}</a>` : ''}
                    </div>
                `;
            }

            let tableRows = solList.map(sol => {
                const meta = sbMeta[sol] || {};
                const svPages = meta.soview_pages || 0;
                const svFile = meta.soview_file || '';
                const svPath = meta.soview_path || '';

                const lyRaw = meta.layout_raw_pages || 0;
                const lyDedup = meta.layout_dedup_pages || 0;
                const lyFile = meta.layout_file || '';
                const lyPath = meta.layout_path || '';

                const totalP = svPages + lyDedup;
                const isComplete = svPages > 0 && lyDedup > 0;
                const hasAnyPage = totalP > 0;

                // Dedup string: user requested "VÍ DỤ : 48 --> 12 TRANG"
                let dedupDisplay = '';
                if (!lyFile && !lyPath) {
                    dedupDisplay = `<span style="color:var(--accent-danger,#ef4444);font-size:0.75rem;font-weight:600;">❌ Không tìm thấy</span>`;
                } else if (lyRaw > 0) {
                    dedupDisplay = `<span style="font-weight:700;color:var(--accent-success,#10b981);font-size:0.85rem;">${lyRaw} → ${lyDedup} trang</span>`;
                } else if (lyDedup > 0) {
                    dedupDisplay = `<span style="font-weight:700;color:var(--accent-success,#10b981);font-size:0.85rem;">${lyDedup} trang</span>`;
                } else {
                    dedupDisplay = `<span style="color:var(--accent-warning,#f59e0b);font-size:0.75rem;font-weight:600;">⚠️ 0 trang</span>`;
                }

                // SOview display
                let soviewDisplay = '';
                if (!svFile && !svPath) {
                    soviewDisplay = `<span style="color:var(--accent-danger,#ef4444);font-size:0.75rem;font-weight:600;">❌ Không tìm thấy</span>`;
                } else if (svPages > 0) {
                    soviewDisplay = `<span style="font-weight:700;color:var(--accent-success,#10b981);font-size:0.85rem;">${svPages} trang</span>`;
                } else {
                    soviewDisplay = `<span style="color:var(--accent-warning,#f59e0b);font-size:0.75rem;font-weight:600;">⚠️ 0 trang</span>`;
                }

                const previewBtn = hasAnyPage
                    ? `<button class="btn btn--sm btn--secondary ad-preview-btn" data-sol="${escHtml(sol)}"
                              style="font-size:0.75rem;padding:4px 10px;">👁 Preview</button>`
                    : `<span style="color:var(--text-tertiary);font-size:0.75rem;">—</span>`;

                return `
                  <tr style="${!isComplete ? 'opacity:0.65;background:rgba(239,68,68,0.04);' : ''}">
                    <td style="padding:10px 10px;font-weight:600;font-family:monospace;font-size:0.82rem;white-space:nowrap;vertical-align:middle;">
                        ${escHtml(sol)}
                        ${!isComplete ? '<span style="display:block;font-size:0.7rem;color:var(--accent-danger,#ef4444);font-weight:500;">Thiếu file / 0 trang</span>' : ''}
                    </td>
                    <td style="padding:10px 10px;text-align:center;vertical-align:middle;">
                      ${soviewDisplay}
                      ${formatFileLink(svPath, svFile)}
                    </td>
                    <td style="padding:10px 10px;text-align:center;vertical-align:middle;">
                      ${dedupDisplay}
                      ${formatFileLink(lyPath, lyFile)}
                    </td>
                    <td style="padding:10px 10px;text-align:center;font-weight:700;font-size:0.92rem;vertical-align:middle;">
                      ${totalP > 0 ? totalP : '<span style="color:var(--text-tertiary);">0</span>'}
                    </td>
                    <td style="padding:10px 10px;text-align:center;vertical-align:middle;">${previewBtn}</td>
                  </tr>`;
            }).join('');

            grid.innerHTML = `
              <table style="width:100%;border-collapse:collapse;font-size:0.83rem;">
                <thead>
                  <tr style="background:var(--bg-tertiary);">
                    <th style="padding:10px;text-align:left;width:16%;">SOL Code</th>
                    <th style="padding:10px;text-align:center;width:34%;">SOview (File &amp; Số trang)</th>
                    <th style="padding:10px;text-align:center;width:34%;">Layout (File &amp; Dedup)</th>
                    <th style="padding:10px;text-align:center;width:8%;">Tổng</th>
                    <th style="padding:10px;text-align:center;width:8%;">Preview</th>
                  </tr>
                </thead>
                <tbody>${tableRows}</tbody>
              </table>`;

            const completeCount = solList.filter(s => {
                const m = sbMeta[s] || {};
                return (m.soview_pages || 0) > 0 && (m.layout_dedup_pages || 0) > 0;
            }).length;
            summaryText.innerHTML = `RFID SB detection complete. Found full pages for <strong>${completeCount} / ${solList.length}</strong> SOLs.`;

            if (result.errors && result.errors.length > 0) {
                const errMsgs = result.errors.map(e => `${e.sol} (${e.typeId}): ${e.error}`).join('; ');
                showToast('⚠️ Scan issues: ' + errMsgs, 'warning', 6000);
            }

            resModal.classList.add('active');
            if (result.tracing_enabled && window._adStartTime) {
                showTracingBenchmarkToast(window._adStartTime, new Date());
                window._adStartTime = null;
            }

            // Wire preview buttons
            grid.querySelectorAll('.ad-preview-btn').forEach(btn => {
                btn.addEventListener('click', () => openPreviewModal(btn.dataset.sol));
            });

            // Wire file open links
            grid.querySelectorAll('.ad-file-link').forEach(link => {
                link.addEventListener('click', (e) => {
                    e.preventDefault();
                    const p = link.dataset.path;
                    if (p && window.pywebview && window.pywebview.api && window.pywebview.api.open_file) {
                        window.pywebview.api.open_file(p);
                    }
                });
            });
            return;
        }
        // ── End RFID SB branch ────────────────────────────────────

        // Standard PFL / RFID HT / PFL Thermal results display
        const isPfl = state.currentLine === 'PFL';
        let foundCount = 0;
        grid.innerHTML = '';

        if (isPfl && result.bq_warnings && result.bq_warnings.length > 0) {
            const warnBanner = document.createElement('div');
            warnBanner.style.cssText = 'background:rgba(255,180,0,0.12);border:1px solid var(--accent-warning,#f59e0b);border-radius:6px;padding:10px 14px;margin-bottom:12px;width:100%;grid-column:1/-1;';
            warnBanner.innerHTML = `
                <div style="font-weight:700;color:var(--accent-warning,#f59e0b);margin-bottom:4px;">⚠️ Cảnh báo số Job (BigQuery)</div>
                <div style="font-size:0.82rem;line-height:1.4;">
                    ${result.bq_warnings.map(w => `<div>• ${escHtml(w)}</div>`).join('')}
                </div>
            `;
            grid.appendChild(warnBanner);
        }

        let jobMatchPages = 0;
        let fallbackMatchPages = 0;

        Object.keys(result.assembly || {}).forEach(sol => {
            let solFound = false;
            Object.keys(result.assembly[sol]).forEach(typeId => {
                const pages = result.assembly[sol][typeId];
                if (pages && pages.length > 0) solFound = true;

                pages.forEach(p => {
                    const card = document.createElement('div');
                    card.style.background = 'var(--bg-secondary)';
                    card.style.border = '1px solid var(--border-color)';
                    card.style.borderRadius = '4px';
                    card.style.padding = '10px';

                    const typeLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;

                    if (isPfl) {
                        if (p.matchBy === 'job') {
                            jobMatchPages++;
                        } else if (p.matchBy === 'sol_fallback') {
                            fallbackMatchPages++;
                        }

                        let matchBadge = '';
                        if (p.matchBy === 'job') {
                            const jobDisplay = p.jobCode ? `Số Job (${p.jobCode})` : 'Số Job';
                            matchBadge = `<span style="background:rgba(16,185,129,0.15);color:var(--accent-success,#10b981);font-weight:600;padding:2px 6px;border-radius:4px;font-size:0.75rem;">🎯 ${escHtml(jobDisplay)}</span>`;
                        } else if (p.matchBy === 'sol_fallback') {
                            matchBadge = `<span style="background:rgba(245,158,11,0.15);color:var(--accent-warning,#f59e0b);font-weight:600;padding:2px 6px;border-radius:4px;font-size:0.75rem;">🔄 SOL pattern (Fallback)</span>`;
                        } else {
                            matchBadge = `<span style="color:var(--text-primary);font-weight:500;">${escHtml(p.method || 'digital')} (SOL pattern)</span>`;
                        }

                        card.innerHTML = `
                            <div style="font-weight:bold;font-family:monospace;margin-bottom:6px;font-size:0.95rem;">${escHtml(sol)}</div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;margin-bottom:3px;">Type: <span style="color:var(--text-primary);font-weight:500;">${typeLabel}</span></div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;margin-bottom:3px;display:flex;align-items:center;gap:6px;">
                                <span>Nguồn:</span> ${matchBadge}
                            </div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;margin-bottom:3px;">Page: <span style="color:var(--text-primary);font-weight:500;">${p.pageIndex}</span></div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${escHtml(p.fileName)}">File: ${escHtml(p.fileName)}</div>
                        `;
                    } else {
                        // Original unmodified display for all other modules
                        card.innerHTML = `
                            <div style="font-weight:bold;font-family:monospace;margin-bottom:6px;font-size:0.95rem;">${escHtml(sol)}</div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;margin-bottom:3px;">Type: <span style="color:var(--text-primary);font-weight:500;">${typeLabel}</span></div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;margin-bottom:3px;">Method: <span style="color:var(--text-primary);font-weight:500;">${escHtml(p.method || 'unknown')}</span></div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;margin-bottom:3px;">Page: <span style="color:var(--text-primary);font-weight:500;">${p.pageIndex}</span></div>
                            <div style="color:var(--text-secondary);font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${escHtml(p.fileName)}">File: ${escHtml(p.fileName)}</div>
                        `;
                    }

                    grid.appendChild(card);
                });
            });
            if (solFound) foundCount++;
        });

        const totalSols = Object.keys(result.assembly || {}).length;
        let statsBannerHtml = '';
        if (isPfl && (jobMatchPages > 0 || fallbackMatchPages > 0)) {
            statsBannerHtml = `
                <div style="margin-top:8px;display:flex;gap:10px;font-size:0.82rem;flex-wrap:wrap;">
                    <span style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);color:var(--accent-success,#10b981);padding:4px 10px;border-radius:4px;font-weight:600;">
                        🎯 Tìm thấy theo <strong>Số Job</strong>: <b>${jobMatchPages}</b> trang
                    </span>
                    <span style="background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);color:var(--accent-warning,#f59e0b);padding:4px 10px;border-radius:4px;font-weight:600;">
                        🔄 Fallback theo <strong>SOL pattern</strong>: <b>${fallbackMatchPages}</b> trang
                    </span>
                </div>
            `;
        }
        summaryText.innerHTML = `Detection complete. Found pages for <strong>${foundCount} / ${totalSols}</strong> SOLs.${statsBannerHtml}`;

        if (result.errors && result.errors.length > 0) {
            const errMsgs = result.errors.map(e => `${e.sol} (${e.typeId}): ${e.error}`).join('; ');
            showToast('⚠️ Scan issues detected: ' + errMsgs, 'warning', 6000);
        }

        // Show Results Modal
        resModal.classList.add('active');
        if (result.tracing_enabled && window._adStartTime) {
            showTracingBenchmarkToast(window._adStartTime, new Date());
            window._adStartTime = null;
        }
    };

    // Bind Auto Detect Results Modal Action Buttons
    const btnDiscard = document.getElementById('ad-results-discard-btn');
    if (btnDiscard) {
        btnDiscard.addEventListener('click', () => {
            document.getElementById('auto-detect-results-modal').classList.remove('active');
        });
    }

    const btnApply = document.getElementById('ad-results-apply-btn');
    if (btnApply) {
        btnApply.addEventListener('click', () => {
            document.getElementById('auto-detect-results-modal').classList.remove('active');
            const result = window._adFinalResult;
            if (result) {
                adState.assembly = result.assembly || {};
                renderResultsScreen();
            }
        });
    }

    window._onAutoDetectCancelled = function () {
        dom.adProgressModal.classList.remove('active');
        window._adStartTime = null;
        showToast('Auto Detect cancelled.', 'info');
    };

    // ── Results Screen ─────────────────────────────────────────────────────────

    function renderResultsScreen() {
        // Hide input screen, show results screen
        showScreen('auto-detect-results-screen');

        const assembly = adState.assembly;
        const solCodes = Object.keys(assembly);
        const totalDetected = solCodes.reduce((sum, s) => {
            return sum + pdfTypes.reduce((s2, t) => s2 + (assembly[s][t.id] || []).length, 0);
        }, 0);

        dom.adResultsSubtitle.textContent =
            `${solCodes.length} SOLs · ${totalDetected} pages detected`;

        // Render table rows
        dom.adResultsTbody.innerHTML = solCodes.map(sol => {
            const soviewPages = (assembly[sol]?.soview || []).length;
            const layoutPages = (assembly[sol]?.layout || []).length;
            const total = soviewPages + layoutPages;
            const rowStyle = total === 0 ? 'opacity:0.5;' : '';
            const previewBtn = total > 0
                ? `<button class="btn btn--sm btn--secondary ad-preview-btn" data-sol="${escHtml(sol)}" style="font-size:0.75rem;padding:4px 10px;">👁 Preview</button>`
                : `<span style="color:var(--text-tertiary);font-size:0.75rem;">—</span>`;
            return `
                <tr style="${rowStyle}">
                    <td style="padding:10px 12px;font-weight:600;font-family:monospace;font-size:0.82rem;">${escHtml(sol)}</td>
                    <td style="text-align:center;padding:10px 12px;">
                        ${soviewPages > 0 ? `<span style="color:var(--accent-success);font-weight:600;">${soviewPages}</span>` : '<span style="color:var(--text-tertiary);">0</span>'}
                    </td>
                    <td style="text-align:center;padding:10px 12px;">
                        ${layoutPages > 0 ? `<span style="color:var(--accent-success);font-weight:600;">${layoutPages}</span>` : '<span style="color:var(--text-tertiary);">0</span>'}
                    </td>
                    <td style="text-align:center;padding:10px 12px;font-weight:700;">${total}</td>
                    <td style="text-align:center;padding:10px 12px;">${previewBtn}</td>
                </tr>
            `;
        }).join('');

        // Wire preview buttons
        dom.adResultsTbody.querySelectorAll('.ad-preview-btn').forEach(btn => {
            btn.addEventListener('click', () => openPreviewModal(btn.dataset.sol));
        });

        // Enable/disable export button
        dom.btnAdExport.disabled = totalDetected === 0;
    }

    function backFromResults() {
        showScreen('input-screen');
        adState.assembly = {};
        // Release RAM sub-doc cache — forces re-detect if user comes back
        window.pywebview.api.clear_auto_detect_ram_cache();
    }

    // ── Preview Modal (Continuous Vertical Viewer + Zoom) ──────────────────────

    const adPreviewState = {
        zoom: 1.0,
    };

    function setAdPreviewZoom(newZoom) {
        adPreviewState.zoom = Math.min(Math.max(newZoom, 0.4), 3.0);
        if (dom.adPreviewWrapper) {
            dom.adPreviewWrapper.style.transform = `scale(${adPreviewState.zoom})`;
        }
        if (dom.adPreviewZoomLevel) {
            dom.adPreviewZoomLevel.textContent = Math.round(adPreviewState.zoom * 100) + '%';
        }
    }

    function openPreviewModal(sol) {
        const solAssembly = adState.assembly[sol] || {};
        dom.adPreviewTitle.textContent = `Preview — ${sol}`;
        setAdPreviewZoom(1.0);
        dom.adPreviewWrapper.innerHTML = '<div style="color:var(--text-secondary);font-size:0.85rem;padding:30px;">Loading document pages…</div>';
        dom.adPreviewModal.classList.add('active');

        // Collect all pages for this SOL across all types
        const pageRequests = [];
        pdfTypes.forEach(t => {
            const pages = solAssembly[t.id] || [];
            pages.forEach(entry => {
                pageRequests.push({
                    typeId: t.id,
                    pageNum: entry.pageIndex,
                    fileIdx: entry.fileIdx,
                    fileName: entry.fileName || 'Unknown File',
                    fileId: entry.fileId || entry.filePath || ''
                });
            });
        });

        if (pageRequests.length === 0) {
            dom.adPreviewWrapper.innerHTML = '<div style="color:var(--text-secondary);font-size:0.85rem;padding:30px;">No pages detected for this SOL.</div>';
            return;
        }

        // Create continuous vertical page cards (large document layout)
        dom.adPreviewWrapper.innerHTML = pageRequests.map(({ typeId, pageNum, fileIdx, fileName }) => {
            const typeLabel = pdfTypes.find(t => t.id === typeId)?.label || typeId;
            const titleString = `📄 ${escHtml(typeLabel)} - ${escHtml(fileName)} - Pg ${pageNum}`;
            return `
                <div id="ad-preview-card-${typeId}-${fileIdx}-${pageNum}" style="width:100%;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:var(--radius-md);box-shadow:0 6px 20px rgba(0,0,0,0.18);overflow:hidden;display:flex;flex-direction:column;">
                    <div style="width:100%;padding:10px 16px;background:var(--bg-tertiary);border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-size:0.82rem;font-weight:600;color:var(--accent-primary);">${titleString}</span>
                        <span></span>
                    </div>
                    <div class="ad-preview-card-body" style="width:100%;min-height:380px;display:flex;align-items:center;justify-content:center;background:var(--bg-primary);padding:12px;">
                        <div class="spinner active" style="width:28px;height:28px;border-width:3px;border-color:rgba(79,70,229,0.2);border-top-color:#4f46e5;"></div>
                    </div>
                </div>
            `;
        }).join('');

        // Fetch thumbnails directly from in-memory sub-docs (Zero-Network Instant Preview)
        if (window.pywebview && window.pywebview.api && window.pywebview.api.fetch_auto_detect_preview) {
            window.pywebview.api.fetch_auto_detect_preview(
                sol,
                JSON.stringify(solAssembly)
            );
        } else {
            // Fallback: fetch thumbnails one by one via disk/network
            pageRequests.forEach(({ typeId, pageNum, fileIdx, fileId }) => {
                window.pywebview.api.fetch_page_range(
                    sol, typeId,
                    fileId || state.folderInputs[typeId],
                    state.isDrive[typeId] === true,
                    pageNum, 1, fileIdx
                );
            });
        }
    }

    // Zero-Network Instant Auto Detect Preview Handler
    window._onAutoDetectPreviewReady = function (sol, cardsMap) {
        if (!dom.adPreviewModal || !dom.adPreviewModal.classList.contains('active')) return;
        cardsMap = cardsMap || {};
        Object.keys(cardsMap).forEach(cardId => {
            const card = document.getElementById(cardId);
            if (card) {
                const body = card.querySelector('.ad-preview-card-body');
                if (body) {
                    body.innerHTML = `
                        <img src="${cardsMap[cardId]}" style="width:100%;height:auto;display:block;box-shadow:0 4px 14px rgba(0,0,0,0.15);border-radius:4px;" alt="Page preview" />
                    `;
                }
            }
        });

        // Fallback for any cards not rendered from RAM cache
        const solAssembly = adState.assembly[sol] || {};
        pdfTypes.forEach(t => {
            const pages = solAssembly[t.id] || [];
            pages.forEach(entry => {
                const cardId = `ad-preview-card-${t.id}-${entry.fileIdx}-${entry.pageIndex}`;
                if (!cardsMap[cardId]) {
                    window.pywebview.api.fetch_page_range(
                        sol, t.id,
                        entry.fileId || entry.filePath || state.folderInputs[t.id],
                        state.isDrive[t.id] === true,
                        entry.pageIndex, 1, entry.fileIdx
                    );
                }
            });
        });
    };

    // Override _onPageRangeReady to also update continuous preview cards
    const _origOnPageRangeReady = window._onPageRangeReady;
    window._onPageRangeReady = function (sol, typeId, startPage, pagesMap, fileIdx) {
        // Call original handler
        _origOnPageRangeReady(sol, typeId, startPage, pagesMap, fileIdx);

        // Also update preview cards if preview modal is open
        if (!dom.adPreviewModal || !dom.adPreviewModal.classList.contains('active')) return;
        Object.keys(pagesMap).forEach(pNum => {
            const pageNum = parseInt(pNum);
            const card = document.getElementById(`ad-preview-card-${typeId}-${fileIdx}-${pageNum}`);
            if (card) {
                const body = card.querySelector('.ad-preview-card-body');
                if (body) {
                    body.innerHTML = `
                        <img src="${pagesMap[pNum]}" style="width:100%;height:auto;display:block;box-shadow:0 4px 14px rgba(0,0,0,0.15);border-radius:4px;" alt="Page ${pageNum}" />
                    `;
                }
            }
        });
    };

    function closePreviewModal() {
        dom.adPreviewModal.classList.remove('active');
    }

    // ── Export from Results Screen ─────────────────────────────────────────────

    function exportAutoDetectResult() {
        if (!state.outputPath) {
            showToast('Please set an output folder on the input screen first.', 'error');
            return;
        }

        if (Object.keys(adState.assembly).length === 0) {
            showToast('No pages detected — nothing to export.', 'error');
            return;
        }

        adState.isExporting = true;
        dom.btnAdExport.disabled = true;
        dom.adExportProgress.classList.remove('hidden');
        dom.adExportProgressFill.style.width = '0%';

        // Pass adState.assembly in its native {sol:{typeId:[...]}} shape —
        // bridge.export_from_auto_detect reads from AUTO_DETECT_RAM_CACHE directly.
        window.pywebview.api.export_from_auto_detect(
            JSON.stringify(adState.assembly),
            state.outputPath,
            adState.exportMode
        );
    }

    // Reuse _onExportProgress for the results screen bar too
    const _origOnExportProgress = window._onExportProgress;
    window._onExportProgress = function (pct) {
        _origOnExportProgress(pct);
        if (dom.adExportProgressFill) {
            dom.adExportProgressFill.style.width = pct + '%';
        }
    };

    // Reuse _onExportDone for results screen too
    const _origOnExportDone = window._onExportDone;
    window._onExportDone = function (result) {
        _origOnExportDone(result);
        // Re-enable results screen export button if we're on results screen
        if (adState.isExporting) {
            adState.isExporting = false;
            if (dom.btnAdExport) dom.btnAdExport.disabled = false;
            if (dom.adExportProgress) dom.adExportProgress.classList.add('hidden');
        }
    };

    // ── Bind Auto Detect Events ────────────────────────────────────────────────

    function bindAutoDetectEvents() {
        // Open config modal
        dom.btnAutoDetect.addEventListener('click', openAutoDetectModal);

        // Close config modal
        dom.adModalCancelBtn.addEventListener('click', closeAutoDetectModal);
        dom.adModal.addEventListener('click', e => { if (e.target === dom.adModal) closeAutoDetectModal(); });

        // Add exception row
        dom.adExceptBtn.addEventListener('click', addExceptionRow);

        // Run button
        dom.adRunBtn.addEventListener('click', runAutoDetect);

        // OCR Mode toggle
        if (dom.adOcrToggleBtn) {
            dom.adOcrToggleBtn.addEventListener('click', () => {
                setOcrMode(!adState.ocrEnabled);
            });
        }

        // Cancel detection
        dom.adProgressCancelBtn.addEventListener('click', () => {
            window.pywebview.api.cancel_auto_detect();
            dom.adProgressCancelBtn.disabled = true;
            dom.adProgressCancelBtn.textContent = 'Cancelling…';
        });

        // Back from results
        dom.btnAdBack.addEventListener('click', backFromResults);

        // Export mode radio on results screen
        document.querySelectorAll('input[name="ad-export-mode"]').forEach(radio => {
            radio.addEventListener('change', e => {
                adState.exportMode = e.target.value;
                document.querySelectorAll('#ad-export-options .export-option').forEach(o => o.classList.remove('selected'));
                e.target.closest('.export-option').classList.add('selected');
            });
        });

        // Export button on results screen
        dom.btnAdExport.addEventListener('click', exportAutoDetectResult);

        // Preview Zoom Controls
        if (dom.adPreviewZoomIn) dom.adPreviewZoomIn.addEventListener('click', () => setAdPreviewZoom(adPreviewState.zoom + 0.15));
        if (dom.adPreviewZoomOut) dom.adPreviewZoomOut.addEventListener('click', () => setAdPreviewZoom(adPreviewState.zoom - 0.15));
        if (dom.adPreviewZoomReset) dom.adPreviewZoomReset.addEventListener('click', () => setAdPreviewZoom(1.0));

        // Ctrl + Mouse Wheel zoom on scroll area
        if (dom.adPreviewScrollArea) {
            dom.adPreviewScrollArea.addEventListener('wheel', e => {
                if (e.ctrlKey) {
                    e.preventDefault();
                    dom.loadingMsg.textContent = 'Ready';
                    setTimeout(() => {
                        dom.loadingOverlay.classList.remove('active');
                    }, 500);

                    if (result.current_version) {
                        const btnUpdate = document.getElementById('btn-dashboard-update');
                        if (btnUpdate) {
                            btnUpdate.textContent = 'v' + result.current_version + ' (Check for updates)';
                        }
                    }
                    const delta = e.deltaY > 0 ? -0.15 : 0.15;
                    setAdPreviewZoom(adPreviewState.zoom + delta);
                }
            }, { passive: false });
        }

        // Close preview modal
        dom.adPreviewCloseBtn.addEventListener('click', closePreviewModal);
        dom.adPreviewModal.addEventListener('click', e => { if (e.target === dom.adPreviewModal) closePreviewModal(); });

        // Escape key closes any AD modal
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                closePreviewModal();
                closeAutoDetectModal();
            }
        });
    }

    // ── Boot ───────────────────────────────────────────────
    // pywebview injects window.pywebview when the WebView2 bridge
    // is ready. We wait for the custom event it fires.
    window.addEventListener('pywebviewready', () => {
        init();
        cacheAdDom();
        // Remove old stub listener — replaced by bindAutoDetectEvents below
        // (btnAutoDetect was bound in bindEvents() as a stub, we re-bind here)
        dom.btnAutoDetect.replaceWith(dom.btnAutoDetect.cloneNode(true));
        dom.btnAutoDetect = document.getElementById('btn-auto-detect');
        bindAutoDetectEvents();
    });

    // Fallback: if already ready (shouldn't happen but just in case)
    if (window.pywebview) {
        init();
        cacheAdDom();
        dom.btnAutoDetect.replaceWith(dom.btnAutoDetect.cloneNode(true));
        dom.btnAutoDetect = document.getElementById('btn-auto-detect');
        bindAutoDetectEvents();
    }

    // ════════════════════════════════════════════════════════════
    // SCREEN NAVIGATION — Shared across all modules
    // ════════════════════════════════════════════════════════════

    function showScreen(screenId) {
        document.querySelectorAll('#app-container > .screen').forEach(s => s.classList.remove('active'));
        const target = document.getElementById(screenId);
        if (target) target.classList.add('active');
    }

    // ════════════════════════════════════════════════════════════
    // DASHBOARD MODULE
    // ════════════════════════════════════════════════════════════

    let mergerDataLoaded = false;

    function updateChangelogVersion(ver) {
        if (!ver || ver === '?') return;
        const changelogVer = document.getElementById('changelog-version-text');
        if (changelogVer) {
            changelogVer.textContent = 'v' + ver + ':';
        }
        const changelogPillVer = document.getElementById('changelog-pill-version-text');
        if (changelogPillVer) {
            changelogPillVer.textContent = 'v' + ver;
        }
    }

    function initDashboard() {
        window.pywebview.api.get_local_version().then(ver => {
            const btnUpdate = document.getElementById('btn-dashboard-update');
            if (btnUpdate && ver !== '?') {
                btnUpdate.textContent = 'v' + ver + ' (Check for updates)';
            }
            updateChangelogVersion(ver);
        });

        // Module card clicks
        document.getElementById('card-merger').addEventListener('click', () => {
            enterMergerModule('PFL');
        });
        const cardMergerRfid = document.getElementById('card-merger-rfid');
        if (cardMergerRfid) {
            cardMergerRfid.addEventListener('click', () => {
                enterMergerModule('RFID HT');
            });
        }
        const cardTrim = document.getElementById('card-merger-trim');
        if (cardTrim) {
            cardTrim.addEventListener('click', () => {
                enterMergerModule('Trim');
            });
        }
        const cardPflThermal = document.getElementById('card-merger-pfl-thermal');
        if (cardPflThermal) {
            cardPflThermal.addEventListener('click', () => {
                enterMergerModule('PFL THERMAL');
            });
        }
        document.getElementById('card-deduplicate').addEventListener('click', () => {
            showScreen('deduplicate-screen');
        });
        document.getElementById('card-combine').addEventListener('click', () => {
            showScreen('combine-screen');
        });
        const cardMergerRfidSb = document.getElementById('card-merger-rfid-sb');
        if (cardMergerRfidSb) {
            cardMergerRfidSb.addEventListener('click', () => {
                enterMergerModule('RFID SB');
            });
        }

        // Changelog box collapse / reopen
        const changelogBox = document.getElementById('dashboard-changelog-box');
        const btnCloseChangelog = document.getElementById('btn-close-changelog');
        const btnReopenChangelog = document.getElementById('btn-reopen-changelog');

        if (btnCloseChangelog && changelogBox && btnReopenChangelog) {
            btnCloseChangelog.addEventListener('click', (e) => {
                e.stopPropagation();
                changelogBox.style.display = 'none';
                btnReopenChangelog.style.display = 'inline-flex';
            });

            btnReopenChangelog.addEventListener('click', (e) => {
                e.stopPropagation();
                btnReopenChangelog.style.display = 'none';
                changelogBox.style.display = 'block';
            });
        }

        // Update button
        const btnUpdate = document.getElementById('btn-dashboard-update');
        btnUpdate.addEventListener('click', () => {
            const originalText = btnUpdate.textContent;
            btnUpdate.textContent = 'Checking…';
            btnUpdate.disabled = true;
            window.pywebview.api.check_for_update().then(result => {
                if (result.update_available) {
                    btnUpdate.textContent = '⬇ Downloading v' + result.remote_version + '…';
                    window.pywebview.api.apply_update(result.remote_version);
                } else {
                    btnUpdate.textContent = '✅ Up to date (v' + (result.current_version || '?') + ')';
                    setTimeout(() => { btnUpdate.textContent = originalText; btnUpdate.disabled = false; }, 3000);
                }
            }).catch(() => {
                btnUpdate.textContent = '❌ Check failed';
                setTimeout(() => { btnUpdate.textContent = originalText; btnUpdate.disabled = false; }, 3000);
            });
        });
    }

    // ════════════════════════════════════════════════════════════
    // DEDUPLICATE MODULE
    // ════════════════════════════════════════════════════════════

    const dedupState = {
        inputMode: 'browse', // 'browse' or 'sol'
        files: [],           // [{ path, name }]  — from browse mode
        solFiles: [],        // [{ path, name }]  — from SOL search mode
        solFolder: '',
        outputFolder: '',
        isRunning: false,
    };

    const dedupDom = {};

    function initDeduplicate() {
        dedupDom.btnBack = document.getElementById('btn-dedup-back');
        dedupDom.btnBrowse = document.getElementById('btn-dedup-browse');
        dedupDom.fileList = document.getElementById('dedup-file-list');
        dedupDom.outputFolder = document.getElementById('dedup-output-folder');
        dedupDom.btnOutputBrowse = document.getElementById('btn-dedup-output-browse');
        dedupDom.btnRun = document.getElementById('btn-dedup-run');
        dedupDom.progress = document.getElementById('dedup-progress');
        dedupDom.progressText = document.getElementById('dedup-progress-text');
        dedupDom.progressFill = document.getElementById('dedup-progress-fill');
        dedupDom.summary = document.getElementById('dedup-summary');

        // SOL Search pane elements
        dedupDom.paneBrowse = document.getElementById('dedup-pane-browse');
        dedupDom.paneSol = document.getElementById('dedup-pane-sol');
        dedupDom.solTextarea = document.getElementById('dedup-sol-textarea');
        dedupDom.solFolder = document.getElementById('dedup-sol-folder');
        dedupDom.btnSolFolderBrowse = document.getElementById('btn-dedup-sol-folder-browse');
        dedupDom.btnSolSearch = document.getElementById('btn-dedup-sol-search');
        dedupDom.solFileList = document.getElementById('dedup-sol-file-list');

        // Back to dashboard
        dedupDom.btnBack.addEventListener('click', () => window.location.reload());

        // ── Tab switching ──────────────────────────────────
        document.querySelectorAll('.dedup-input-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const mode = tab.dataset.dedupMode;
                dedupState.inputMode = mode;
                document.querySelectorAll('.dedup-input-tab').forEach(t => {
                    const isActive = t.dataset.dedupMode === mode;
                    t.classList.toggle('active', isActive);
                    t.style.background = isActive ? 'var(--accent-primary)' : 'var(--bg-tertiary)';
                    t.style.color = isActive ? '#fff' : 'var(--text-secondary)';
                });
                dedupDom.paneBrowse.style.display = mode === 'browse' ? '' : 'none';
                dedupDom.paneSol.style.display = mode === 'sol' ? '' : 'none';
                validateDedupInputs();
            });
        });

        // ── Browse Files mode ──────────────────────────────
        dedupDom.btnBrowse.addEventListener('click', async () => {
            const paths = await window.pywebview.api.browse_files_dialog('PDF files (*.pdf)');
            if (paths && paths.length > 0) {
                for (const p of paths) {
                    if (!dedupState.files.some(f => f.path === p)) {
                        const parts = p.replace(/\\/g, '/').split('/');
                        dedupState.files.push({ path: p, name: parts[parts.length - 1] });
                    }
                }
                renderDedupFileList();
                validateDedupInputs();
            }
        });

        // ── SOL Search mode ────────────────────────────────
        dedupDom.btnSolFolderBrowse.addEventListener('click', async () => {
            const folder = await window.pywebview.api.browse_folder_dialog();
            if (folder) {
                dedupState.solFolder = folder;
                dedupDom.solFolder.value = folder;
                validateDedupSolSearch();
                validateDedupInputs();
            }
        });

        dedupDom.solTextarea.addEventListener('input', () => {
            validateDedupSolSearch();
            validateDedupInputs();
        });

        dedupDom.btnSolSearch.addEventListener('click', () => {
            const solIds = parseDedupSolIds();
            if (solIds.length === 0 || !dedupState.solFolder) return;

            dedupDom.btnSolSearch.disabled = true;
            dedupDom.btnSolSearch.textContent = '🔍 Searching…';

            window.pywebview.api.search_combine_files(
                JSON.stringify(solIds),
                JSON.stringify([dedupState.solFolder])
            );
        });

        // Browse output folder
        dedupDom.btnOutputBrowse.addEventListener('click', async () => {
            const folder = await window.pywebview.api.browse_folder_dialog();
            if (folder) {
                dedupState.outputFolder = folder;
                dedupDom.outputFolder.value = folder;
                validateDedupInputs();
            }
        });

        // Run
        dedupDom.btnRun.addEventListener('click', () => {
            if (dedupState.isRunning) return;
            dedupState.isRunning = true;
            dedupDom.btnRun.disabled = true;
            dedupDom.btnRun.textContent = 'Processing…';
            dedupDom.progress.classList.remove('hidden');
            dedupDom.summary.classList.add('hidden');

            // Pick file list based on active mode
            const activeFiles = dedupState.inputMode === 'sol'
                ? dedupState.solFiles
                : dedupState.files;

            const filePaths = activeFiles.map(f => f.path);

            const isCombine = document.getElementById('dedup-combine-checkbox') ? document.getElementById('dedup-combine-checkbox').checked : false;
            let solList = [];
            if (dedupState.inputMode === 'sol') {
                const inputSols = parseDedupSolIds();
                solList = inputSols.filter(sol => dedupState.solMapping[sol] && dedupState.solMapping[sol].length > 0);
            }

            window.pywebview.api.run_deduplicate(
                JSON.stringify(filePaths),
                dedupState.outputFolder,
                isCombine,
                JSON.stringify(solList)
            );
        });

    }

    function parseDedupSolIds() {
        const raw = dedupDom.solTextarea ? dedupDom.solTextarea.value : '';
        const set = new Set();
        raw.split(/[\n,;\s]+/).forEach(l => { const t = l.trim(); if (t) set.add(t); });
        return Array.from(set);
    }

    function validateDedupSolSearch() {
        const hasSols = parseDedupSolIds().length > 0;
        const hasFolder = dedupState.solFolder.length > 0;
        if (dedupDom.btnSolSearch) {
            dedupDom.btnSolSearch.disabled = !(hasSols && hasFolder);
            dedupDom.btnSolSearch.textContent = '🔍 Search Files';
        }
    }

    function renderDedupFileList() {
        if (dedupState.files.length === 0) {
            dedupDom.fileList.innerHTML = '<div class="dedup-file-list__empty">No files selected. Click "Browse" to add PDFs.</div>';
            return;
        }
        dedupDom.fileList.innerHTML = dedupState.files.map((f, i) =>
            '<div class="dedup-file-item">' +
            '<span class="dedup-file-item__name" title="' + escHtml(f.path) + '">' + escHtml(f.name) + '</span>' +
            '<div style="display:flex;gap:4px;">' +
            '<button class="btn btn--secondary dedup-file-item__open" data-path="' + escHtml(f.path) + '" title="Open File" style="padding:2px 8px;font-size:0.8rem;">📂 Open</button>' +
            '<button class="dedup-file-item__remove" data-idx="' + i + '" title="Remove">✕</button>' +
            '</div>' +
            '</div>'
        ).join('');

        dedupDom.fileList.querySelectorAll('.dedup-file-item__remove').forEach(btn => {
            btn.addEventListener('click', () => {
                dedupState.files.splice(parseInt(btn.dataset.idx), 1);
                renderDedupFileList();
                validateDedupInputs();
            });
        });

        dedupDom.fileList.querySelectorAll('.dedup-file-item__open').forEach(btn => {
            btn.addEventListener('click', () => {
                window.pywebview.api.open_file(btn.dataset.path);
            });
        });
    }

    function renderDedupSolFileList() {
        if (!dedupState.solMapping || Object.keys(dedupState.solMapping).length === 0) {
            dedupDom.solFileList.innerHTML = '<div class="dedup-file-list__empty">No matching files found.</div>';
            return;
        }

        let html = '';
        const inputSols = parseDedupSolIds();
        const orderedSols = [
            ...inputSols.filter(sol => dedupState.solMapping[sol]),
            ...Object.keys(dedupState.solMapping).filter(sol => !inputSols.includes(sol))
        ];

        orderedSols.forEach(sol => {
            const files = dedupState.solMapping[sol] || [];
            if (files.length === 0) return;

            html += '<div class="dedup-sol-group" style="margin-bottom:8px;">';
            html += '<div style="font-size:0.85rem;font-weight:700;color:var(--text-secondary);padding:4px 8px;background:var(--bg-tertiary);border-radius:4px;margin-bottom:4px;">' +
                'SOL: ' + escHtml(sol) + ' (' + files.length + ' file' + (files.length > 1 ? 's' : '') + ')' +
                '</div>';

            files.forEach(f => {
                // Find index in flat list for removal
                const flatIdx = dedupState.solFiles.findIndex(sf => sf.path === f.path);

                html += '<div class="dedup-file-item" style="margin-left:8px;">' +
                    '<span class="dedup-file-item__name" title="' + escHtml(f.path) + '">📄 ' + escHtml(f.name) + '</span>' +
                    '<div style="display:flex;gap:4px;">' +
                    '<button class="btn btn--secondary dedup-file-item__open" data-path="' + escHtml(f.path) + '" title="Open File" style="padding:2px 8px;font-size:0.8rem;">📂 Open</button>' +
                    (flatIdx !== -1 ? '<button class="dedup-file-item__remove" data-idx="' + flatIdx + '" data-sol="' + escHtml(sol) + '" data-path="' + escHtml(f.path) + '" title="Remove">✕</button>' : '') +
                    '</div>' +
                    '</div>';
            });
            html += '</div>';
        });

        if (html === '') {
            dedupDom.solFileList.innerHTML = '<div class="dedup-file-list__empty">No matching files found.</div>';
            return;
        }

        dedupDom.solFileList.innerHTML = html;

        dedupDom.solFileList.querySelectorAll('.dedup-file-item__remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const flatIdx = parseInt(btn.dataset.idx);
                const path = btn.dataset.path;
                const sol = btn.dataset.sol;

                // Remove from flat list
                if (flatIdx >= 0 && flatIdx < dedupState.solFiles.length) {
                    dedupState.solFiles.splice(flatIdx, 1);
                }

                // Remove from mapping
                if (dedupState.solMapping[sol]) {
                    dedupState.solMapping[sol] = dedupState.solMapping[sol].filter(f => f.path !== path);
                }

                renderDedupSolFileList();
                validateDedupInputs();
            });
        });

        dedupDom.solFileList.querySelectorAll('.dedup-file-item__open').forEach(btn => {
            btn.addEventListener('click', () => {
                window.pywebview.api.open_file(btn.dataset.path);
            });
        });
    }

    function validateDedupInputs() {
        const hasOutput = dedupState.outputFolder.length > 0;
        let hasFiles = false;
        if (dedupState.inputMode === 'browse') {
            hasFiles = dedupState.files.length > 0;
        } else {
            hasFiles = dedupState.solFiles.length > 0;
        }
        dedupDom.btnRun.disabled = !(hasFiles && hasOutput) || dedupState.isRunning;
    }

    // Handle SOL search results for deduplicate module
    window._handleDedupSolSearchReady = function (mapping) {
        dedupDom.btnSolSearch.disabled = false;
        dedupDom.btnSolSearch.textContent = '🔍 Search Files';

        if (mapping.error) {
            showToast('❌ Search error: ' + mapping.error, 'error');
            return;
        }

        dedupState.solMapping = mapping;

        // Flatten all found files across SOL IDs in the exact order entered by the user
        const inputSols = parseDedupSolIds();
        const orderedSols = [
            ...inputSols.filter(sol => mapping[sol]),
            ...Object.keys(mapping).filter(sol => !inputSols.includes(sol))
        ];

        const seen = new Set();
        const allFiles = [];
        orderedSols.forEach(sol => {
            const files = mapping[sol] || [];
            files.forEach(f => {
                if (!seen.has(f.path)) {
                    seen.add(f.path);
                    allFiles.push({ path: f.path, name: f.name });
                }
            });
        });

        dedupState.solFiles = allFiles;
        renderDedupSolFileList();
        validateDedupInputs();

        const totalSols = Object.keys(mapping).length;
        const foundSols = Object.keys(mapping).filter(k => mapping[k].length > 0).length;
        showToast(`Found ${allFiles.length} file(s) for ${foundSols}/${totalSols} SOL(s)`, allFiles.length > 0 ? 'success' : 'warning');
    };

    // Callbacks from Python
    window._onDedupProgress = function (data) {
        const pct = data.totalPages > 0
            ? Math.round(((data.fileIdx * 100 + (data.pageNum / data.totalPages) * 100) / data.totalFiles))
            : 0;
        dedupDom.progressFill.style.width = pct + '%';
        dedupDom.progressText.textContent =
            'File ' + (data.fileIdx + 1) + '/' + data.totalFiles +
            ' — Page ' + data.pageNum + '/' + data.totalPages +
            ' — ' + (data.fileName || '');
    };

    window._onDedupDone = function (data) {
        dedupState.isRunning = false;
        dedupDom.btnRun.disabled = false;
        dedupDom.btnRun.textContent = '▶ Run Deduplicate';
        dedupDom.progress.classList.add('hidden');

        if (data.success && data.results) {
            let totalKept = 0, totalRemoved = 0, totalFiles = data.results.length;
            let strategies = new Set();
            data.results.forEach(r => {
                totalKept += r.kept_pages;
                totalRemoved += r.removed_pages;
                if (r.strategy) strategies.add(r.strategy);
            });
            const strategyText = strategies.size > 0 ? '<br>Strategy: ' + Array.from(strategies).join(', ') : '';

            // Build results grid
            let gridHtml = '<div style="margin-top:16px; margin-bottom:8px; font-size:0.9rem; font-weight:700;">Page Summary:</div>';
            gridHtml += '<table style="width:100%; border-collapse:collapse; font-size:0.85rem; border:1px solid var(--border-color);">';
            gridHtml += '<thead style="background:var(--bg-secondary);"><tr>';
            gridHtml += '<th style="text-align:left; padding:6px; border:1px solid var(--border-color);">SOL</th>';
            gridHtml += '<th style="text-align:left; padding:6px; border:1px solid var(--border-color);">File Name</th>';
            gridHtml += '<th style="text-align:right; padding:6px; border:1px solid var(--border-color);">Original Pages</th>';
            gridHtml += '<th style="text-align:right; padding:6px; border:1px solid var(--border-color);">After Deduplicate</th>';
            gridHtml += '</tr></thead><tbody>';

            data.results.forEach(r => {
                let sol = 'N/A';
                let fileName = r.input_path ? r.input_path.split(/[/\\]/).pop() : 'Unknown';
                if (dedupState.inputMode === 'sol' && dedupState.solMapping) {
                    for (const s of Object.keys(dedupState.solMapping)) {
                        if (dedupState.solMapping[s].find(f => f.path === r.input_path)) {
                            sol = s;
                            break;
                        }
                    }
                }
                gridHtml += '<tr>';
                gridHtml += `<td style="padding:4px 6px; border:1px solid var(--border-color);">${escHtml(sol)}</td>`;
                gridHtml += `<td style="padding:4px 6px; border:1px solid var(--border-color); word-break:break-all;">${escHtml(fileName)}</td>`;
                gridHtml += `<td style="padding:4px 6px; border:1px solid var(--border-color); text-align:right;">${r.total_pages}</td>`;
                gridHtml += `<td style="padding:4px 6px; border:1px solid var(--border-color); text-align:right;">${r.kept_pages}</td>`;
                gridHtml += '</tr>';
            });
            gridHtml += '</tbody></table>';

            // Build output files list
            let outputFilesHtml = '';
            if (data.combined_path) {
                const combinedName = data.combined_path.split(/[/\\]/).pop();
                outputFilesHtml += '<div style="margin-top:12px;font-size:0.85rem;font-weight:600;">Output File (Combined):</div>';
                outputFilesHtml += '<div style="border:1px solid var(--border-color);border-radius:4px;padding:4px;margin-top:4px;background:var(--bg-secondary);">';
                outputFilesHtml += '<div style="display:flex;justify-content:space-between;align-items:center;padding:4px;">' +
                    '<span style="font-size:0.85rem;font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:8px;" title="' + escHtml(data.combined_path) + '">📄 ' + escHtml(combinedName) + '</span>' +
                    '<button class="btn btn--secondary dedup-summary-open" data-path="' + escHtml(data.combined_path) + '" style="padding:2px 8px;font-size:0.8rem;">📂 Open</button>' +
                    '</div></div>';
            } else if (data.results.length > 0) {
                // sort results by file name
                const sortedResults = [...data.results].sort((a, b) => {
                    const nameA = a.output_path ? a.output_path.split(/[/\\]/).pop() : '';
                    const nameB = b.output_path ? b.output_path.split(/[/\\]/).pop() : '';
                    return nameA.localeCompare(nameB);
                });

                outputFilesHtml += '<div style="margin-top:12px;font-size:0.85rem;font-weight:600;">Output Files:</div>';
                outputFilesHtml += '<div style="max-height:150px;overflow-y:auto;border:1px solid var(--border-color);border-radius:4px;padding:4px;margin-top:4px;background:var(--bg-secondary);">';
                sortedResults.forEach(r => {
                    if (!r.output_path) return;
                    const fileName = r.output_path.split(/[/\\]/).pop();
                    outputFilesHtml += '<div style="display:flex;justify-content:space-between;align-items:center;padding:4px;border-bottom:1px solid var(--border-color);">' +
                        '<span style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;" title="' + escHtml(r.output_path) + '">📄 ' + escHtml(fileName) + '</span>' +
                        '<button class="btn btn--secondary dedup-summary-open" data-path="' + escHtml(r.output_path) + '" style="padding:2px 8px;font-size:0.75rem;">📂 Open</button>' +
                        '</div>';
                });
                outputFilesHtml += '</div>';
            }

            dedupDom.summary.classList.remove('hidden');
            dedupDom.summary.style.background = '';
            dedupDom.summary.style.borderColor = '';
            dedupDom.summary.style.color = '';
            dedupDom.summary.innerHTML =
                '✅ <strong>Deduplication Complete</strong><br>' +
                totalFiles + ' file(s) processed<br>' +
                totalKept + ' pages kept, ' + totalRemoved + ' pages removed' + strategyText + '<br>' +
                'Output: ' + escHtml(dedupState.outputFolder) +
                gridHtml +
                outputFilesHtml;

            dedupDom.summary.querySelectorAll('.dedup-summary-open').forEach(btn => {
                btn.addEventListener('click', () => {
                    window.pywebview.api.open_file(btn.dataset.path);
                });
            });
        } else {
            dedupDom.summary.classList.remove('hidden');
            dedupDom.summary.style.background = 'rgba(225,29,72,0.06)';
            dedupDom.summary.style.borderColor = 'rgba(225,29,72,0.2)';
            dedupDom.summary.style.color = 'var(--accent-danger)';
            dedupDom.summary.innerHTML = '❌ Error: ' + escHtml(data.error || 'Unknown error');
        }
        validateDedupInputs();
    };

    // ════════════════════════════════════════════════════════════
    // COMBINE MODULE
    // ════════════════════════════════════════════════════════════

    const combineState = {
        solIds: [],
        inputFolders: [],
        outputFolder: '',
        preview: {},    // { solId: [{ path, name, source }] }
        isRunning: false,
        hasPreview: false,
        exportMode: 'split',
    };

    const combineDom = {};

    function initCombine() {
        combineDom.btnBack = document.getElementById('btn-combine-back');
        combineDom.solTextarea = document.getElementById('combine-sol-textarea');
        combineDom.btnAddFolder = document.getElementById('btn-combine-add-folder');
        combineDom.folderList = document.getElementById('combine-folder-list');
        combineDom.outputFolder = document.getElementById('combine-output-folder');
        combineDom.btnOutputBrowse = document.getElementById('btn-combine-output-browse');
        combineDom.btnSearch = document.getElementById('btn-combine-search');
        combineDom.btnExport = document.getElementById('btn-combine-export');
        combineDom.progress = document.getElementById('combine-progress');
        combineDom.progressText = document.getElementById('combine-progress-text');
        combineDom.progressFill = document.getElementById('combine-progress-fill');
        combineDom.summary = document.getElementById('combine-summary');
        combineDom.previewBody = document.getElementById('combine-preview-body');
        combineDom.previewCount = document.getElementById('combine-preview-count');
        combineDom.exportOptions = document.querySelectorAll('input[name="combine-export-mode"]');
        combineDom.exportOptionsLabels = {
            split: document.getElementById('combine-opt-split'),
            merge: document.getElementById('combine-opt-merge')
        };

        // Export Options
        combineDom.exportOptions.forEach(opt => {
            opt.addEventListener('change', (e) => {
                if (e.target.checked) {
                    combineState.exportMode = e.target.value;
                    combineDom.exportOptionsLabels.split.classList.remove('selected');
                    combineDom.exportOptionsLabels.merge.classList.remove('selected');
                    combineDom.exportOptionsLabels[combineState.exportMode].classList.add('selected');
                }
            });
        });

        // Back
        combineDom.btnBack.addEventListener('click', () => window.location.reload());

        // SOL textarea change
        combineDom.solTextarea.addEventListener('input', validateCombineInputs);

        // Add folder
        combineDom.btnAddFolder.addEventListener('click', async () => {
            const folder = await window.pywebview.api.browse_folder_dialog();
            if (folder && !combineState.inputFolders.includes(folder)) {
                combineState.inputFolders.push(folder);
                renderCombineFolderList();
                validateCombineInputs();
            }
        });

        // Output folder
        combineDom.btnOutputBrowse.addEventListener('click', async () => {
            const folder = await window.pywebview.api.browse_folder_dialog();
            if (folder) {
                combineState.outputFolder = folder;
                combineDom.outputFolder.value = folder;
                validateCombineInputs();
            }
        });

        // Search
        combineDom.btnSearch.addEventListener('click', () => {
            if (combineState.isRunning) return;
            combineState.solIds = parseSolIds(combineDom.solTextarea.value);
            if (combineState.solIds.length === 0) return;

            combineDom.btnSearch.disabled = true;
            combineDom.btnSearch.textContent = '🔍 Searching…';
            combineState.hasPreview = false;
            combineDom.btnExport.disabled = true;

            window.pywebview.api.search_combine_files(
                JSON.stringify(combineState.solIds),
                JSON.stringify(combineState.inputFolders)
            );
        });

        // Export
        combineDom.btnExport.addEventListener('click', () => {
            if (combineState.isRunning || !combineState.hasPreview) return;
            combineState.isRunning = true;
            combineDom.btnExport.disabled = true;
            combineDom.btnExport.textContent = '📤 Exporting…';
            combineDom.progress.classList.remove('hidden');
            combineDom.summary.classList.add('hidden');

            window.pywebview.api.export_combine(
                JSON.stringify(combineState.solIds),
                JSON.stringify(combineState.preview),
                combineState.outputFolder,
                combineState.exportMode
            );
        });
    }

    function parseSolIds(text) {
        if (!text) return [];
        const set = new Set();
        text.split(/[\n,;\s]+/).forEach(s => {
            const t = s.trim();
            if (t) set.add(t);
        });
        return Array.from(set);
    }

    function renderCombineFolderList() {
        if (combineState.inputFolders.length === 0) {
            combineDom.folderList.innerHTML = '<div class="combine-folder-list__empty">No folders added.</div>';
            return;
        }
        combineDom.folderList.innerHTML = combineState.inputFolders.map((f, i) =>
            '<div class="combine-folder-item">' +
            '<span class="combine-folder-item__index">#' + (i + 1) + '</span>' +
            '<span class="combine-folder-item__path" title="' + escHtml(f) + '">' + escHtml(f) + '</span>' +
            '<button class="combine-folder-item__remove" data-idx="' + i + '" title="Remove">✕</button>' +
            '</div>'
        ).join('');

        combineDom.folderList.querySelectorAll('.combine-folder-item__remove').forEach(btn => {
            btn.addEventListener('click', () => {
                combineState.inputFolders.splice(parseInt(btn.dataset.idx), 1);
                renderCombineFolderList();
                validateCombineInputs();
            });
        });
    }

    function validateCombineInputs() {
        const solIds = parseSolIds(combineDom.solTextarea.value);
        const hasInput = solIds.length > 0 && combineState.inputFolders.length > 0;
        combineDom.btnSearch.disabled = !hasInput || combineState.isRunning;
        combineDom.btnSearch.textContent = '🔍 Search Files';
        combineDom.btnExport.disabled = !combineState.hasPreview || !combineState.outputFolder || combineState.isRunning;
    }

    // ── Combine Preview Rendering (with interactive editing) ──

    function renderCombinePreview() {
        // Lấy danh sách SOL ID theo thứ tự người dùng nhập vào để đảm bảo hiển thị đúng
        const solIds = combineState.solIds.length > 0
            ? combineState.solIds.filter(id => combineState.preview[id])
            : Object.keys(combineState.preview);

        if (solIds.length === 0) {
            combineDom.previewBody.innerHTML = '<div class="combine-preview-empty">No matching files found.</div>';
            combineDom.previewCount.textContent = '';
            return;
        }

        let totalFiles = 0;
        solIds.forEach(s => { totalFiles += combineState.preview[s].length; });
        combineDom.previewCount.textContent = solIds.length + ' SOL(s), ' + totalFiles + ' file(s)';

        let html = '';
        for (const solId of solIds) {
            const files = combineState.preview[solId];
            html += '<div class="sol-box" data-sol="' + escHtml(solId) + '">';
            html += '<div class="sol-box__header">';
            html += '<span>' + escHtml(solId) + '</span>';
            html += '<span class="sol-box__count">' + files.length + ' file(s)</span>';
            html += '</div>';
            html += '<div class="sol-box__files">';

            if (files.length === 0) {
                html += '<div class="sol-box__empty">No files matched</div>';
            } else {
                for (let i = 0; i < files.length; i++) {
                    const f = files[i];
                    html += '<div class="sol-box__file-row" draggable="true" data-sol="' + escHtml(solId) + '" data-idx="' + i + '">';
                    html += '<span class="sol-box__drag-handle">⠿</span>';
                    html += '<span class="sol-box__file-name" title="' + escHtml(f.path) + '">' + escHtml(f.name) + '</span>';
                    if (f.source === 'manual') {
                        html += '<span class="sol-box__badge--manual">manual</span>';
                    }
                    html += '<div style="display:flex;gap:4px;">';
                    html += '<button class="btn btn--secondary sol-box__open-btn" data-path="' + escHtml(f.path) + '" title="Open File" style="padding:2px 8px;font-size:0.75rem;">📂 Open</button>';
                    html += '<button class="sol-box__remove-btn" data-sol="' + escHtml(solId) + '" data-idx="' + i + '" title="Remove">✕</button>';
                    html += '</div>';
                    html += '</div>';
                }
            }

            html += '</div>'; // .sol-box__files
            html += '<button class="sol-box__add-btn" data-sol="' + escHtml(solId) + '">+ Add File</button>';
            html += '</div>'; // .sol-box
        }

        combineDom.previewBody.innerHTML = html;
        bindCombinePreviewEvents();
    }

    function bindCombinePreviewEvents() {
        // Open buttons
        combineDom.previewBody.querySelectorAll('.sol-box__open-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                window.pywebview.api.open_file(btn.dataset.path);
            });
        });

        // Remove buttons
        combineDom.previewBody.querySelectorAll('.sol-box__remove-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const solId = btn.dataset.sol;
                const idx = parseInt(btn.dataset.idx);
                combineState.preview[solId].splice(idx, 1);
                renderCombinePreview();
            });
        });

        // Add buttons
        combineDom.previewBody.querySelectorAll('.sol-box__add-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const solId = btn.dataset.sol;
                const paths = await window.pywebview.api.browse_files_dialog('PDF files (*.pdf)');
                if (paths && paths.length > 0) {
                    for (const p of paths) {
                        const parts = p.replace(/\\/g, '/').split('/');
                        if (!combineState.preview[solId].some(f => f.path === p)) {
                            combineState.preview[solId].push({
                                path: p,
                                name: parts[parts.length - 1],
                                source: 'manual',
                            });
                        }
                    }
                    renderCombinePreview();
                }
            });
        });

        // Drag & Drop reorder within each SOL box
        let dragSol = null;
        let dragIdx = null;

        combineDom.previewBody.querySelectorAll('.sol-box__file-row').forEach(row => {
            row.addEventListener('dragstart', e => {
                dragSol = row.dataset.sol;
                dragIdx = parseInt(row.dataset.idx);
                row.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });

            row.addEventListener('dragend', () => {
                row.classList.remove('dragging');
                // Remove all drop indicators
                combineDom.previewBody.querySelectorAll('.sol-box__drop-indicator').forEach(el => el.remove());
                dragSol = null;
                dragIdx = null;
            });

            row.addEventListener('dragover', e => {
                e.preventDefault();
                if (row.dataset.sol !== dragSol) return;
                e.dataTransfer.dropEffect = 'move';

                // Remove existing indicators in this box
                row.closest('.sol-box__files').querySelectorAll('.sol-box__drop-indicator').forEach(el => el.remove());

                // Insert indicator
                const rect = row.getBoundingClientRect();
                const midY = rect.top + rect.height / 2;
                const indicator = document.createElement('div');
                indicator.className = 'sol-box__drop-indicator';
                if (e.clientY < midY) {
                    row.parentNode.insertBefore(indicator, row);
                } else {
                    row.parentNode.insertBefore(indicator, row.nextSibling);
                }
            });

            row.addEventListener('drop', e => {
                e.preventDefault();
                if (row.dataset.sol !== dragSol) return;

                const dropIdx = parseInt(row.dataset.idx);
                if (dragIdx === dropIdx) return;

                const rect = row.getBoundingClientRect();
                const midY = rect.top + rect.height / 2;
                let targetIdx = e.clientY < midY ? dropIdx : dropIdx + 1;

                // Reorder array
                const arr = combineState.preview[dragSol];
                const [moved] = arr.splice(dragIdx, 1);
                if (targetIdx > dragIdx) targetIdx--;
                arr.splice(targetIdx, 0, moved);

                renderCombinePreview();
            });
        });
    }

    // Callbacks from Python
    window._onCombinePreviewReady = function (mapping) {
        // Dispatch based on active screen
        const dedupScreen = document.getElementById('deduplicate-screen');
        if (dedupScreen && dedupScreen.classList.contains('active')) {
            if (window._handleDedupSolSearchReady) {
                window._handleDedupSolSearchReady(mapping);
            }
            return;
        }

        // Default: Combine module handling
        combineDom.btnSearch.disabled = false;
        combineDom.btnSearch.textContent = '🔍 Search Files';

        if (mapping.error) {
            combineDom.previewBody.innerHTML = '<div class="combine-preview-empty">❌ Error: ' + escHtml(mapping.error) + '</div>';
            return;
        }

        combineState.preview = mapping;
        combineState.hasPreview = true;
        renderCombinePreview();
        validateCombineInputs();
    };

    window._onCombineProgress = function (data) {
        const pct = data.totalSols > 0 ? Math.round(((data.solIdx + 1) / data.totalSols) * 100) : 0;
        combineDom.progressFill.style.width = pct + '%';
        combineDom.progressText.textContent =
            'Exporting SOL ' + (data.solIdx + 1) + '/' + data.totalSols + ' — ' + (data.solId || '');
    };

    window._onCombineDone = function (data) {
        combineState.isRunning = false;
        combineDom.btnExport.disabled = false;
        combineDom.btnExport.textContent = '📤 Export';
        combineDom.progress.classList.add('hidden');

        if (data.success || (data.files && data.files.length > 0)) {
            combineDom.summary.classList.remove('hidden');
            combineDom.summary.style.background = '';
            combineDom.summary.style.borderColor = '';
            combineDom.summary.style.color = '';
            let msg = '✅ <strong>Combine Complete</strong><br>';
            if (data.fallback_used) {
                msg = '⚠️ <strong>Export failed, try to export to download folder. ✅ Combine Complete</strong><br>';
            }
            msg += data.files.length + ' file(s) exported<br>';
            if (data.errors && data.errors.length > 0) {
                msg += '⚠️ <strong>' + data.errors.length + ' warning(s):</strong><br>';
                msg += '<div style="margin-top:6px;padding:8px 10px;background:rgba(255,193,7,0.08);border-left:3px solid #f59e0b;border-radius:4px;font-size:0.82rem;line-height:1.7;">';
                data.errors.forEach(function (e) {
                    msg += '• <strong>' + escHtml(e.sol || 'Unknown') + '</strong>: ' + escHtml(e.error || 'Unknown error') + '<br>';
                });
                msg += '</div>';
            }
            if (data.fallback_used) {
                msg += 'Output: ' + escHtml(data.fallback_path) + '<br>';
            } else {
                msg += 'Output: ' + escHtml(combineState.outputFolder) + '<br>';
            }

            // Build output files list
            if (data.files && data.files.length > 0) {
                const sortedResults = [...data.files].sort((a, b) => {
                    const nameA = a.output_path ? a.output_path.split(/[/\\]/).pop() : '';
                    const nameB = b.output_path ? b.output_path.split(/[/\\]/).pop() : '';
                    return nameA.localeCompare(nameB);
                });

                msg += '<div style="margin-top:12px;font-size:0.85rem;font-weight:600;">Output Files:</div>';
                msg += '<div style="max-height:150px;overflow-y:auto;border:1px solid var(--border-color);border-radius:4px;padding:4px;margin-top:4px;background:var(--bg-secondary);">';
                sortedResults.forEach(r => {
                    if (!r.output_path) return;
                    const fileName = r.output_path.split(/[/\\]/).pop();
                    msg += '<div style="display:flex;justify-content:space-between;align-items:center;padding:4px;border-bottom:1px solid var(--border-color);">' +
                        '<span style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;" title="' + escHtml(r.output_path) + '">📄 ' + escHtml(fileName) + '</span>' +
                        '<button class="btn btn--secondary combine-summary-open" data-path="' + escHtml(r.output_path) + '" style="padding:2px 8px;font-size:0.75rem;">📂 Open</button>' +
                        '</div>';
                });
                msg += '</div>';
            }

            combineDom.summary.innerHTML = msg;

            combineDom.summary.querySelectorAll('.combine-summary-open').forEach(btn => {
                btn.addEventListener('click', () => {
                    window.pywebview.api.open_file(btn.dataset.path);
                });
            });
        } else {
            combineDom.summary.classList.remove('hidden');
            combineDom.summary.style.background = 'rgba(225,29,72,0.06)';
            combineDom.summary.style.borderColor = 'rgba(225,29,72,0.2)';
            combineDom.summary.style.color = 'var(--accent-danger)';
            combineDom.summary.innerHTML = '❌ Error: ' + escHtml(data.error || 'Unknown error');
        }
        validateCombineInputs();
    };

})();

