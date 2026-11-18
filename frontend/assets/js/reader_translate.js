import {
  appState, shelfFilters, readerState, pdfState, isMobileReader, IS_TOUCH_SCREEN,
} from './state.js';
import { escapeHtml } from './utils.js';
import { loadShelfBooks, switchTab } from './shelf.js';
import { adjustReaderFontSize, closeOnlineReader } from './reader.js';
import {
  alignAllPdfTransBlocks, alignPdfTransBlocks, applyPdfScale,
  getPdfPageBlocks, scrollToPdfPage,
} from './reader_pdf.js';

export function isReaderSelectableNode(node) {
  return document.getElementById('reader-main-scroll').contains(node)
    || document.getElementById('m-pagetext-body').contains(node);
}

export function initReaderSelectionHandler() {
  const scrollEl = document.getElementById('reader-main-scroll');
  if (!scrollEl) return;

  document.addEventListener('mouseup', (e) => {
    if (!readerState.isOpen) {
      hideReaderSelectionBubble();
      return;
    }

    setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.toString().trim()) {
        hideReaderSelectionBubble();
        return;
      }

      const anchorNode = sel.anchorNode;
      if (!anchorNode || !isReaderSelectableNode(anchorNode)) {
        hideReaderSelectionBubble();
        return;
      }

      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      if (!rect || (rect.width === 0 && rect.height === 0)) {
        hideReaderSelectionBubble();
        return;
      }

      const txt = sel.toString().trim();
      showReaderSelectionBubble(rect, txt);

      // If translation drawer is currently open, automatically update translation in drawer
      const drawer = document.getElementById('reader-trans-drawer');
      if (drawer && !drawer.classList.contains('collapsed') && /[a-zA-Z]/.test(txt)) {
        executeDrawerTranslate(txt);
      }
    }, 10);
  });

  // A long-press selection on a phone fires no mouseup; show the bubble once the selection settles
  let selectionTimer = null;
  if (IS_TOUCH_SCREEN) {
    document.addEventListener('selectionchange', () => {
      clearTimeout(selectionTimer);
      selectionTimer = setTimeout(() => {
        const sel = window.getSelection();
        const txt = sel && !sel.isCollapsed ? sel.toString().trim() : '';
        if (!readerState.isOpen || !txt || !isReaderSelectableNode(sel.anchorNode)) return;
        showReaderSelectionBubble(sel.getRangeAt(0).getBoundingClientRect(), txt);
      }, 450);
    });
  }

  document.addEventListener('mousedown', (e) => {
    if (appState.readerSelectionBubble && !appState.readerSelectionBubble.contains(e.target)) {
      hideReaderSelectionBubble();
    }
    if (appState.readerTransPopover && !appState.readerTransPopover.contains(e.target)) {
      hideReaderTransPopover();
    }
  });
}

// Web Speech API for native English pronunciation
export function speakEnglishText(text) {
  if (!text || !('speechSynthesis' in window)) return;
  try {
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'en-US';
    utter.rate = 0.92;
    window.speechSynthesis.speak(utter);
  } catch (e) {
    console.warn('Speech synthesis error:', e);
  }
}

export function showReaderSelectionBubble(rect, selectedText) {
  if (!appState.readerSelectionBubble) {
    appState.readerSelectionBubble = document.createElement('div');
    appState.readerSelectionBubble.className = 'reader-selection-bubble';
    document.body.appendChild(appState.readerSelectionBubble);
  }

  appState.readerSelectionBubble.innerHTML = `
    <button class="reader-selection-btn" id="bubble-copy-btn">📋 复制</button>
    <button class="reader-selection-btn accent-trans" id="bubble-trans-btn">🌐 翻译</button>
    <button class="reader-selection-btn" id="bubble-search-btn">🔍 检索全书</button>
  `;

  const x = Math.max(70, Math.min(window.innerWidth - 70, rect.left + rect.width / 2));
  // Touch screens put their own copy menu above a selection, so ours goes below it
  const below = IS_TOUCH_SCREEN;
  const y = below ? Math.min(window.innerHeight - 70, rect.bottom) : Math.max(50, rect.top);
  appState.readerSelectionBubble.classList.toggle('below', below);

  appState.readerSelectionBubble.style.left = `${x}px`;
  appState.readerSelectionBubble.style.top = `${y}px`;
  appState.readerSelectionBubble.style.display = 'flex';

  document.getElementById('bubble-copy-btn').onclick = async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(selectedText);
    } catch (err) {
      const ta = document.createElement('textarea');
      ta.value = selectedText;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    const btn = document.getElementById('bubble-copy-btn');
    if (btn) btn.innerHTML = '✅ 已复制';
    setTimeout(() => hideReaderSelectionBubble(), 600);
  };

  document.getElementById('bubble-trans-btn').onclick = (e) => {
    e.stopPropagation();
    hideReaderSelectionBubble();
    showReaderTransPopover(rect, selectedText);
  };

  document.getElementById('bubble-search-btn').onclick = (e) => {
    e.stopPropagation();
    hideReaderSelectionBubble();
    hideReaderTransPopover();
    closeOnlineReader();
    switchTab('shelf');
    const qInput = /** @type {HTMLInputElement | null} */ (document.getElementById('shelf-search-input'));
    if (qInput) {
      qInput.value = selectedText.slice(0, 40);
      shelfFilters.q = qInput.value;
      loadShelfBooks(1);
    }
  };
}

export function hideReaderSelectionBubble() {
  if (appState.readerSelectionBubble) {
    appState.readerSelectionBubble.style.display = 'none';
  }
}

/* ==================== TRANSLATION POPOVER & DRAWER ENGINE ==================== */

export async function showReaderTransPopover(rect, text) {
  if (!appState.readerTransPopover) {
    appState.readerTransPopover = document.createElement('div');
    appState.readerTransPopover.className = 'reader-trans-popover';
    document.body.appendChild(appState.readerTransPopover);
  }

  let popX = rect.left + rect.width / 2;
  popX = Math.max(175, Math.min(window.innerWidth - 175, popX));
  let popY = rect.bottom + 8;
  if (popY + 230 > window.innerHeight && rect.top > 220) {
    popY = rect.top - 8;
    appState.readerTransPopover.style.transform = 'translate(-50%, -100%)';
  } else {
    appState.readerTransPopover.style.transform = 'translate(-50%, 0)';
  }
  appState.readerTransPopover.style.left = `${popX}px`;
  appState.readerTransPopover.style.top = `${popY}px`;
  appState.readerTransPopover.style.display = 'block';

  appState.readerTransPopover.innerHTML = `
    <div class="trans-popover-header">
      <div style="display:flex;align-items:center;gap:6px;">
        <span style="font-size:12.5px;font-weight:700;">🌐 释义与翻译</span>
        <span class="tag-xs">AI翻译</span>
      </div>
      <button class="trans-action-icon-btn" onclick="hideReaderTransPopover()">✕</button>
    </div>
    <div class="trans-popover-body">
      <div class="trans-orig-text">${escapeHtml(text)}</div>
      <div class="trans-dest-text" style="color:var(--r-muted);font-style:italic;">⏳ 正在翻译解析...</div>
    </div>
  `;

  try {
    const resp = await fetch('/api/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, book_id: readerState.bookId, engine: getTransEngine() }),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || '翻译失败');

    noteTransEngine(data.engine, data.cached);
    renderTransPopoverContent(text, data);
    updateDrawerCurrentCard(text, data);
    addTransToHistory(text, data);
  } catch (err) {
    appState.readerTransPopover.innerHTML = `
      <div class="trans-popover-header">
        <span style="font-size:12.5px;font-weight:700;color:var(--rose);">⚠️ 翻译异常</span>
        <button class="trans-action-icon-btn" onclick="hideReaderTransPopover()">✕</button>
      </div>
      <div style="padding:10px 0; font-size:12.5px; color:var(--rose);">${escapeHtml(String(err.message || err))}</div>
    `;
  }
}

export function renderTransPopoverContent(text, data) {
  if (!appState.readerTransPopover) return;
  const phoneticHtml = data.phonetic
    ? `<span style="font-family:monospace;font-size:12px;color:#93c5fd;margin-left:4px;">[${escapeHtml(data.phonetic)}]</span>`
    : '';

  let dictHtml = '';
  if (data.dict && data.dict.length > 0) {
    dictHtml = `<div class="trans-dict-box">` + data.dict.map(item => `
      <div><b style="color:#60a5fa;">${escapeHtml(item.pos)}:</b> ${escapeHtml((item.meanings || []).join('；'))}</div>
    `).join('') + `</div>`;
  }

  appState.readerTransPopover.innerHTML = `
    <div class="trans-popover-header">
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:12.5px;font-weight:700;">🌐 释义与翻译</span>
        <span class="tag-xs" style="background:#0284c7;color:#fff;">${escapeHtml((data.source_lang || 'en').toUpperCase())} ➔ 中文</span>
        ${phoneticHtml}
      </div>
      <div style="display:flex;gap:4px;align-items:center;">
        <button class="trans-action-icon-btn" id="pop-speak-btn" title="朗读原生发音">🔊</button>
        <button class="trans-action-icon-btn" id="pop-copy-btn" title="复制译文">📋</button>
        <button class="trans-action-icon-btn" onclick="hideReaderTransPopover()" title="关闭">✕</button>
      </div>
    </div>
    <div class="trans-popover-body">
      <div class="trans-orig-text">${escapeHtml(text)}</div>
      <div class="trans-dest-text">${escapeHtml(data.translation)}</div>
      ${dictHtml}
    </div>
  `;

  document.getElementById('pop-speak-btn').onclick = () => speakEnglishText(text);
  document.getElementById('pop-copy-btn').onclick = async () => {
    try {
      await navigator.clipboard.writeText(data.translation);
      const btn = document.getElementById('pop-copy-btn');
      if (btn) btn.textContent = '✅';
      setTimeout(() => {
        const b = document.getElementById('pop-copy-btn');
        if (b) b.textContent = '📋';
      }, 800);
    } catch (e) {}
  };
}

export function hideReaderTransPopover() {
  if (appState.readerTransPopover) {
    appState.readerTransPopover.style.display = 'none';
  }
}

export function toggleReaderTransDrawer() {
  const drawer = document.getElementById('reader-trans-drawer');
  if (!drawer) return;
  const isCollapsed = drawer.classList.toggle('collapsed');
  const toggleBtn = document.getElementById('reader-trans-toggle-btn');
  if (toggleBtn) {
    toggleBtn.classList.toggle('active', !isCollapsed);
  }
  if (!isCollapsed) {
    const sel = window.getSelection();
    const curText = sel && !sel.isCollapsed ? sel.toString().trim() : '';
    if (curText) {
      executeDrawerTranslate(curText);
    }
  }
}

export async function executeDrawerTranslate(text) {
  text = (text || '').trim();
  if (!text) return;
  const drawer = document.getElementById('reader-trans-drawer');
  if (drawer && drawer.classList.contains('collapsed')) {
    drawer.classList.remove('collapsed');
    document.getElementById('reader-trans-toggle-btn')?.classList.add('active');
  }

  document.getElementById('drawer-cur-orig').textContent = text;
  document.getElementById('drawer-cur-phonetic').textContent = '';
  document.getElementById('drawer-cur-trans').textContent = '⏳ 正在翻译中...';
  document.getElementById('drawer-cur-dict').innerHTML = '';

  try {
    const resp = await fetch('/api/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, book_id: readerState.bookId, engine: getTransEngine() }),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || '翻译失败');

    updateDrawerCurrentCard(text, data);
    addTransToHistory(text, data);
  } catch (err) {
    document.getElementById('drawer-cur-trans').textContent = `⚠️ ${err.message || err}`;
  }
}

export function updateDrawerCurrentCard(text, data) {
  appState.currentDrawerTrans = { text, data };
  const origEl = document.getElementById('drawer-cur-orig');
  const phEl = document.getElementById('drawer-cur-phonetic');
  const transEl = document.getElementById('drawer-cur-trans');
  const dictEl = document.getElementById('drawer-cur-dict');

  if (origEl) origEl.textContent = text;
  if (phEl) phEl.textContent = data.phonetic ? `[${data.phonetic}]` : '';
  if (transEl) transEl.textContent = data.translation;

  if (dictEl) {
    if (data.dict && data.dict.length > 0) {
      dictEl.innerHTML = data.dict.map(item => `
        <div style="margin-top:3px;"><b style="color:#60a5fa;">${escapeHtml(item.pos)}:</b> ${escapeHtml((item.meanings || []).join('；'))}</div>
      `).join('');
    } else {
      dictEl.innerHTML = '';
    }
  }
}

export function speakDrawerCurrent() {
  if (appState.currentDrawerTrans && appState.currentDrawerTrans.text) {
    speakEnglishText(appState.currentDrawerTrans.text);
  }
}

export async function copyDrawerCurrent() {
  if (appState.currentDrawerTrans && appState.currentDrawerTrans.data && appState.currentDrawerTrans.data.translation) {
    try {
      await navigator.clipboard.writeText(appState.currentDrawerTrans.data.translation);
      const btn = document.getElementById('drawer-copy-btn');
      if (btn) btn.textContent = '✅';
      setTimeout(() => {
        const b = document.getElementById('drawer-copy-btn');
        if (b) b.textContent = '📋';
      }, 800);
    } catch (e) {}
  }
}

export function addTransToHistory(text, data) {
  appState.readerTransHistory = appState.readerTransHistory.filter(h => h.text !== text);
  appState.readerTransHistory.unshift({ text, data, time: new Date() });
  if (appState.readerTransHistory.length > 50) appState.readerTransHistory.pop();
  renderTransHistoryList();
}

export function renderTransHistoryList() {
  const listEl = document.getElementById('drawer-trans-history-list');
  const countEl = document.getElementById('drawer-history-count');
  if (countEl) countEl.textContent = String(appState.readerTransHistory.length);
  if (!listEl) return;

  if (!appState.readerTransHistory.length) {
    listEl.innerHTML = `<div style="padding:16px; color:var(--r-muted); font-size:12px; text-align:center;">暂无查词记录</div>`;
    return;
  }

  listEl.innerHTML = appState.readerTransHistory.map((item, idx) => `
    <div style="background:var(--r-paper); border:1px solid var(--r-border); border-radius:7px; padding:8px 10px; margin-bottom:8px; cursor:pointer;" onclick="reselectHistoryItem(${idx})">
      <div style="display:flex; justify-content:space-between; align-items:baseline;">
        <div style="font-size:13px; font-weight:700; color:var(--r-text);">${escapeHtml(item.text.slice(0, 36))}</div>
        <button class="trans-action-icon-btn" onclick="event.stopPropagation(); speakEnglishText('${escapeHtml(item.text).replace(/'/g, "\\'")}')" title="发音">🔊</button>
      </div>
      ${item.data.phonetic ? `<div style="font-family:monospace; font-size:11.5px; color:#93c5fd;">[${escapeHtml(item.data.phonetic)}]</div>` : ''}
      <div style="font-size:12.5px; color:#38bdf8; margin-top:2px;">${escapeHtml(item.data.translation)}</div>
    </div>
  `).join('');
}

export function reselectHistoryItem(idx) {
  const item = appState.readerTransHistory[idx];
  if (item) {
    updateDrawerCurrentCard(item.text, item.data);
  }
}

export function clearTransHistory() {
  appState.readerTransHistory = [];
  renderTransHistoryList();
}

/* ==================== BILINGUAL TRANSLATION (EPUB & PDF) ==================== */
export const TRANS_ENGINE_LABELS = { claude: 'Claude', deepseek: 'DeepSeek', apple: 'Apple 本机', google: 'Google', mymemory: 'MyMemory' };

export function getTransEngine() {
  try { return localStorage.getItem('readerTransEngine') || 'auto'; } catch (e) { return 'auto'; }
}

// Shows which engine actually answered: a chosen engine falls back to the next one when it is unavailable
export function noteTransEngine(engine, cached) {
  const el = document.getElementById('reader-trans-engine-status');
  if (!el || !engine) return;
  const chosen = getTransEngine();
  const fellBack = chosen !== 'auto' && engine !== chosen;
  el.textContent = `上次：${TRANS_ENGINE_LABELS[engine] || engine}${cached ? '（缓存）' : ''}${fellBack ? '（所选不可用，已回退）' : ''}`;
  el.style.color = fellBack ? 'var(--amber)' : 'var(--r-muted)';
}

export async function onTransEngineChange(engine) {
  try { localStorage.setItem('readerTransEngine', engine); } catch (e) {}
  document.getElementById('reader-trans-engine-status').textContent = '';
  pdfState.pageTranslations.clear();
  pdfState.bilingualPages.clear();
  if (!readerState.isBilingual) return;
  // Re-translate what is on screen with the new engine
  if (readerState.mode === 'pdf') {
    const cur = pdfState.currentPage || 1;
    translatePdfPage(cur);
    if (cur + 1 <= pdfState.pageCount) translatePdfPage(cur + 1);
  } else {
    await toggleHtmlBilingualMode();  // off: removes translation blocks
    await toggleHtmlBilingualMode();  // on: translates again
  }
}

export async function toggleBilingualMode() {
  if (readerState.mode === 'pdf') {
    await togglePdfBilingualMode();
  } else {
    await toggleHtmlBilingualMode();
  }
}

/* ==================== PDF SIDE-BY-SIDE SPLIT: drag the line between page and translation ==================== */
export const PDF_SPLIT_KEY = 'lib_reader_pdf_split';
export const PDF_SPLIT_MIN = 0.25;  // share of the width the translation may take
export const PDF_SPLIT_MAX = 0.65;
export const PDF_SPLIT_CHROME_PX = 64;  // two flex gaps around the 8px handle, plus slack

export function savedPdfSplitShare() {
  try {
    const share = Number(localStorage.getItem(PDF_SPLIT_KEY));
    return share >= PDF_SPLIT_MIN && share <= PDF_SPLIT_MAX ? share : null;
  } catch (e) {
    console.warn('Could not read the PDF split setting:', e);
    return null;
  }
}

export function setPdfTransWidth(px) {
  const overlay = document.getElementById('immersive-reader-overlay');
  overlay.style.setProperty('--pdf-trans-w', `${px}px`);
  overlay.style.setProperty('--pdf-trans-max', 'none');
}

// The translation takes `share` of the width; the page is zoomed to fill the rest
export async function applyPdfSplitShare(share) {
  const wrapper = document.getElementById('pdf-stream-wrapper');
  if (!wrapper || !pdfState.doc) return;
  const width = wrapper.clientWidth;
  const transWidth = Math.round(width * share);
  setPdfTransWidth(transWidth);
  const page = await pdfState.doc.getPage(1);
  const pageWidth = page.getViewport({ scale: 1.0 }).width;
  applyPdfScale(Math.max(0.3, (width - transWidth - PDF_SPLIT_CHROME_PX) / pageWidth));
}

export function resetPdfSplit() {
  try { localStorage.removeItem(PDF_SPLIT_KEY); } catch (e) { console.warn('Could not reset the PDF split:', e); }
  const overlay = document.getElementById('immersive-reader-overlay');
  overlay.style.removeProperty('--pdf-trans-w');
  overlay.style.removeProperty('--pdf-trans-max');
  if (readerState.isBilingual) {
    togglePdfBilingualMode().then(togglePdfBilingualMode);  // off and on again: the default automatic fit
  }
}

// While dragging only the panel width changes; the page is redrawn once, on release
export function initPdfSplitDrag() {
  const scrollEl = document.getElementById('reader-main-scroll');
  scrollEl.addEventListener('dblclick', (e) => {
    if (e.target instanceof Element && e.target.classList.contains('pdf-split-handle')) resetPdfSplit();
  });
  scrollEl.addEventListener('mousedown', (e) => {
    if (!(e.target instanceof Element) || !e.target.classList.contains('pdf-split-handle')) return;
    e.preventDefault();
    const wrapper = document.getElementById('pdf-stream-wrapper');
    const box = wrapper.getBoundingClientRect();
    let share = savedPdfSplitShare() || 0.4;
    const onMove = (ev) => {
      share = Math.min(PDF_SPLIT_MAX, Math.max(PDF_SPLIT_MIN, (box.right - ev.clientX) / box.width));
      setPdfTransWidth(Math.round(box.width * share));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('pdf-splitting');
      try { localStorage.setItem(PDF_SPLIT_KEY, share.toFixed(3)); } catch (err) { console.warn('Could not save the PDF split:', err); }
      applyPdfSplitShare(share);
    };
    document.body.classList.add('pdf-splitting');
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

export function ensurePdfTransPanels() {
  const slots = document.querySelectorAll('.pdf-page-slot');
  slots.forEach(slot => {
    const p = parseInt(slot.getAttribute('data-page'), 10);
    if (!p) return;
    let container = slot.parentElement;
    if (!container || !container.classList.contains('pdf-page-container')) {
      container = document.createElement('div');
      container.className = 'pdf-page-container';
      container.id = `pdf-page-container-${p}`;
      container.setAttribute('data-page', String(p));
      slot.parentNode.insertBefore(container, slot);
      container.appendChild(slot);
    }
    if (!document.getElementById(`pdf-trans-${p}`)) {
      const panel = document.createElement('div');
      panel.className = `pdf-trans-panel ${readerState.isBilingual ? '' : 'hidden'}`;
      panel.id = `pdf-trans-${p}`;
      panel.style.minHeight = pdfState.imageMode ? '' : (/** @type {HTMLElement} */ (slot).style.minHeight || '600px');
      panel.innerHTML = `
        <div class="pdf-trans-header">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-weight:700;font-size:13px;">📄 第 ${p} 页译文对照</span>
            <span class="tag-xs" style="background:#0284c7;color:#fff;">双语</span>
          </div>
          <div style="display:flex;align-items:center;gap:4px;">
            <button class="trans-action-icon-btn" onclick="adjustReaderFontSize(-1)" title="减小译文字号 (A-)">A-</button>
            <span class="reader-trans-fs-badge" style="font-size:11px;color:var(--r-muted);min-width:28px;text-align:center;">${readerState.transFontSize || 15}px</span>
            <button class="trans-action-icon-btn" onclick="adjustReaderFontSize(1)" title="增大译文字号 (A+)">A+</button>
            <span style="width:1px;height:12px;background:var(--r-border);margin:0 2px;"></span>
            <button class="trans-action-icon-btn" onclick="speakPdfPage(${p})" title="朗读本页英文">🔊</button>
            <button class="trans-action-icon-btn" onclick="copyPdfPageTrans(${p}, this)" title="复制本页译文">📋</button>
          </div>
        </div>
        <div class="pdf-trans-body" id="pdf-trans-body-${p}">
          <div style="color:var(--r-muted);font-style:italic;font-size:13px;padding:24px 0;text-align:center;">⏳ 正在翻译解析本页内容...</div>
        </div>
      `;
      container.appendChild(panel);
    }
    if (!container.querySelector('.pdf-split-handle')) {
      const handle = document.createElement('div');
      handle.className = 'pdf-split-handle';
      handle.title = '拖动调整原文与译文的宽度，双击恢复默认';
      container.insertBefore(handle, document.getElementById(`pdf-trans-${p}`));
    }
  });
}

// PDF only: hide the original page and keep the translation panel, remembered across books
export function applyTransOnly() {
  const bilingualPdf = readerState.mode === 'pdf' && readerState.isBilingual;
  document.getElementById('immersive-reader-overlay')?.classList.toggle('pdf-trans-only', bilingualPdf && readerState.transOnly);
  const btn = document.getElementById('reader-transonly-btn');
  if (!btn) return;
  btn.style.display = bilingualPdf ? '' : 'none';
  btn.classList.toggle('active', readerState.transOnly);
  btn.textContent = readerState.transOnly ? '📄 显示原页' : '📃 仅译文';
}

export function toggleTransOnly() {
  readerState.transOnly = !readerState.transOnly;
  try { localStorage.setItem('lib_reader_trans_only', readerState.transOnly ? '1' : '0'); } catch (e) {}
  const page = pdfState.currentPage || 1;
  applyTransOnly();
  alignAllPdfTransBlocks();
  scrollToPdfPage(page, false);  // layout height changes; stay on the same page
}

export async function togglePdfBilingualMode() {
  const overlay = document.getElementById('immersive-reader-overlay');
  const btn = document.getElementById('reader-bilingual-btn');
  const drawerBtn = document.getElementById('drawer-bilingual-btn');
  const scrollEl = document.getElementById('reader-main-scroll');

  if (readerState.isBilingual) {
    // Turn OFF PDF bilingual mode
    readerState.isBilingual = false;
    overlay.classList.remove('pdf-bilingual-active');
    document.querySelectorAll('.pdf-trans-panel').forEach(p => p.classList.add('hidden'));
    if (btn) { btn.textContent = '📖 双语对照'; btn.classList.remove('active'); }
    if (drawerBtn) { drawerBtn.textContent = '开启对照'; drawerBtn.className = 'btn btn-emerald'; }

    applyTransOnly();
    // Restore scale if we saved it
    if (pdfState.preBilingualScale) {
      applyPdfScale(pdfState.preBilingualScale);
      delete pdfState.preBilingualScale;
    }
    return;
  }

  // Ensure all trans panels exist in DOM
  ensurePdfTransPanels();

  // Turn ON PDF bilingual mode
  readerState.isBilingual = true;
  pdfState.preBilingualScale = pdfState.scale;
  overlay.classList.add('pdf-bilingual-active');

  // Smart auto-scale: adjust zoom so PDF canvas and translation panel fit comfortably side-by-side
  if (pdfState.doc && savedPdfSplitShare()) {
    applyPdfSplitShare(savedPdfSplitShare());
  } else if (pdfState.doc) {
    try {
      const firstPage = await pdfState.doc.getPage(1);
      const unscaledVp = firstPage.getViewport({ scale: 1.0 });
      const availW = scrollEl ? (scrollEl.clientWidth - 48) : window.innerWidth;
      if (availW >= 880) {
        const maxSlotW = Math.max(340, availW - 542);  // panel 480 + gaps + the split handle
        const fitScale = Math.min(1.35, Math.max(0.45, maxSlotW / unscaledVp.width));
        applyPdfScale(fitScale);
      }
    } catch (e) {}
  }

  document.querySelectorAll('.pdf-trans-panel').forEach(p => p.classList.remove('hidden'));
  if (btn) { btn.textContent = '📖 关闭对照'; btn.classList.add('active'); }
  if (drawerBtn) { drawerBtn.textContent = '关闭对照'; drawerBtn.className = 'btn'; }
  applyTransOnly();

  alignAllPdfTransBlocks();  // pages translated before the panels were hidden
  // Immediately translate currently visible page and adjacent pages
  const cur = pdfState.currentPage || 1;
  translatePdfPage(cur);
  if (cur + 1 <= pdfState.pageCount) translatePdfPage(cur + 1);
}

export async function extractPdfPageParagraphs(page) {
  const tc = await page.getTextContent();
  if (!tc || !tc.items || !tc.items.length) return [];

  // Group items by vertical line position (Y coord)
  const linesMap = new Map();
  for (const it of tc.items) {
    if (!it.str) continue;
    const y = Math.round(it.transform[5] / 3) * 3;
    if (!linesMap.has(y)) linesMap.set(y, []);
    linesMap.get(y).push({ x: it.transform[4], str: it.str });
  }

  // Sort lines top to bottom
  const sortedYs = Array.from(linesMap.keys()).sort((a, b) => b - a);
  const rawLines = [];
  for (const y of sortedYs) {
    const items = linesMap.get(y).sort((a, b) => a.x - b.x);
    let lineStr = '';
    for (const it of items) {
      if (lineStr && !lineStr.endsWith(' ') && !it.str.startsWith(' ')) lineStr += ' ';
      lineStr += it.str;
    }
    lineStr = lineStr.trim();
    if (lineStr) rawLines.push(lineStr);
  }

  // Reconstruct paragraphs and handle line wrap & hyphenation
  const paragraphs = [];
  let curPara = '';
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i];
    if (curPara.endsWith('-') && /^[a-zA-Z]/.test(line)) {
      curPara = curPara.slice(0, -1) + line;
    } else if (curPara) {
      if ((curPara.endsWith('.') || curPara.endsWith('!') || curPara.endsWith('?')) && /^[A-Z0-9\u4e00-\u9fa5]/.test(line)) {
        paragraphs.push(curPara.trim());
        curPara = line;
      } else {
        curPara += ' ' + line;
      }
    } else {
      curPara = line;
    }
  }
  if (curPara.trim()) paragraphs.push(curPara.trim());

  return paragraphs.filter(p => p.length >= 6 && /[a-zA-Z\u4e00-\u9fa5]/.test(p));
}

export async function translatePdfPage(pageNo) {
  if (!pdfState.pageCount || pageNo < 1 || pageNo > pdfState.pageCount) return;
  if (pdfState.bilingualPages.has(pageNo)) return;
  pdfState.bilingualPages.add(pageNo);

  let panelBody = document.getElementById(`pdf-trans-body-${pageNo}`);
  if (!panelBody) {
    ensurePdfTransPanels();
    panelBody = document.getElementById(`pdf-trans-body-${pageNo}`);
  }
  if (!panelBody) return;

  panelBody.innerHTML = `<div style="color:var(--r-muted); font-style:italic; font-size:13px; padding:24px 0; text-align:center;">⏳ 正在提取解析第 ${pageNo} 页文本并请求翻译...</div>`;

  try {
    const blocks = await getPdfPageBlocks(pageNo);
    const paras = blocks.map(block => block.text);

    if (!paras.length) {
      panelBody.innerHTML = `<div style="color:var(--r-muted); font-size:12.5px; padding:24px 0; text-align:center;">（本页未提取到有效文本，可能是插图或扫描件）</div>`;
      return;
    }

    const resp = await fetch('/api/translate/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paragraphs: paras, book_id: readerState.bookId, engine: getTransEngine() }),
    });
    const data = await resp.json();
    if (!data.ok || !data.results) throw new Error(data.error || '翻译服务响应异常');

    pdfState.pageTranslations.set(pageNo, data.results);
    noteTransEngine((data.results.find(r => r.engine) || {}).engine);

    panelBody.innerHTML = data.results.map((item, idx) => {
      const block = blocks[idx] || {};
      const top = typeof block.top === 'number' ? ` data-top="${block.top}"` : '';
      return `
      <div class="pdf-trans-para pdf-role-${block.role || 'para'}" id="pdf-trans-p-${pageNo}-${idx}"${top}>
        <div class="pdf-trans-orig">${escapeHtml(item.original)}</div>
        <div class="pdf-trans-dest">${escapeHtml(item.translation || '（暂无译文）')}</div>
      </div>`;
    }).join('');
    alignPdfTransBlocks(pageNo);

  } catch (err) {
    pdfState.bilingualPages.delete(pageNo); // Allow retry
    panelBody.innerHTML = `<div style="color:var(--rose); font-size:12.5px; padding:16px 0;">⚠️ 翻译第 ${pageNo} 页失败：${escapeHtml(String(err.message || err))}</div>`;
  }
}

export function speakPdfPage(pageNo) {
  const transList = pdfState.pageTranslations.get(pageNo);
  if (!transList || !transList.length) return;
  const fullEn = transList.map(t => t.original).join(' ');
  speakEnglishText(fullEn);
}

export async function copyPdfPageTrans(pageNo, btn = null) {
  const transList = pdfState.pageTranslations.get(pageNo);
  if (!transList || !transList.length) return;
  const fullZh = transList.map(t => t.translation).filter(Boolean).join('\n\n');
  try {
    await navigator.clipboard.writeText(fullZh);
    if (btn) {
      btn.textContent = '✅';
      setTimeout(() => { btn.textContent = '📋'; }, 800);
    }
  } catch (e) {}
}

export async function toggleHtmlBilingualMode() {
  const bodyEl = document.getElementById('reader-article-body');
  const btn = document.getElementById('reader-bilingual-btn');
  const drawerBtn = document.getElementById('drawer-bilingual-btn');

  if (readerState.isBilingual) {
    // Turn OFF: remove all translation blocks
    bodyEl.querySelectorAll('.bilingual-trans-block').forEach(el => el.remove());
    readerState.isBilingual = false;
    if (btn) {
      btn.textContent = '📖 双语对照';
      btn.classList.remove('active');
    }
    if (drawerBtn) {
      drawerBtn.textContent = '开启对照';
      drawerBtn.className = 'btn btn-emerald';
    }
    return;
  }

  // Turn ON
  readerState.isBilingual = true;
  if (btn) {
    btn.textContent = '📖 关闭对照';
    btn.classList.add('active');
  }
  if (drawerBtn) {
    drawerBtn.textContent = '关闭对照';
    drawerBtn.className = 'btn';
  }

  // Collect paragraphs
  const pEls = Array.from(bodyEl.querySelectorAll('p, div, li, blockquote')).filter(el => {
    const t = el.textContent.trim();
    return t.length >= 8 && /[a-zA-Z]{3,}/.test(t) && !el.querySelector('p, div');
  });

  if (!pEls.length) return;

  const batches = [];
  let curBatch = [];
  for (const p of pEls) {
    if (p.nextElementSibling && p.nextElementSibling.classList.contains('bilingual-trans-block')) continue;
    const block = document.createElement('div');
    block.className = 'bilingual-trans-block loading';
    block.textContent = '⏳ 正在翻译段落...';
    p.after(block);
    curBatch.push({ el: block, text: p.textContent.trim() });
    if (curBatch.length >= 8) {
      batches.push(curBatch);
      curBatch = [];
    }
  }
  if (curBatch.length) batches.push(curBatch);

  for (const batch of batches) {
    if (!readerState.isBilingual) break;
    try {
      const resp = await fetch('/api/translate/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paragraphs: batch.map(b => b.text), book_id: readerState.bookId, engine: getTransEngine() }),
      });
      const d = await resp.json();
      if (d.ok && d.results) {
        noteTransEngine((d.results.find(r => r.engine) || {}).engine);
        d.results.forEach((resItem, idx) => {
          const target = batch[idx];
          if (target && target.el) {
            target.el.classList.remove('loading');
            target.el.textContent = resItem.translation || '（未能获取译文）';
          }
        });
      }
    } catch (err) {
      batch.forEach(b => {
        if (b.el) {
          b.el.classList.remove('loading');
          b.el.textContent = '⚠️ 翻译超时，请稍候重试';
        }
      });
    }
  }
}

