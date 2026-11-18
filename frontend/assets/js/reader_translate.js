import { IS_TOUCH_SCREEN, appState, pdfState, readerState, shelfFilters } from './state.js';
import { escapeHtml } from './utils.js';
import { loadShelfBooks, switchTab } from './shelf.js';
import { readerHooks } from './reader_hooks.js';

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
    readerHooks.closeOnlineReader();
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
    readerHooks.translatePdfPage(cur);
    if (cur + 1 <= pdfState.pageCount) readerHooks.translatePdfPage(cur + 1);
  } else {
    await readerHooks.toggleHtmlBilingualMode();  // off: removes translation blocks
    await readerHooks.toggleHtmlBilingualMode();  // on: translates again
  }
}

/* ==================== PDF SIDE-BY-SIDE SPLIT: drag the line between page and translation ==================== */
export const PDF_SPLIT_KEY = 'lib_reader_pdf_split';
export const PDF_SPLIT_MIN = 0.25;  // share of the width the translation may take
export const PDF_SPLIT_MAX = 0.65;
export const PDF_SPLIT_CHROME_PX = 64;  // two flex gaps around the 8px handle, plus slack
