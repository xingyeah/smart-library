import {
  appState, shelfFilters, readerState, pdfState, isMobileReader,
  MOBILE_FONTS, MOBILE_LINE_HEIGHTS, MOBILE_DEFAULT_LINE_HEIGHT,
  CHROME_HIDE_SCROLL_PX, MOBILE_SHEET_IDS, PDF_LONG_PRESS_TAP_GUARD_MS,
} from './state.js';
import { escapeHtml, isMostlyLatinText, keepScreenOn, releaseScreenWakeLock, showMobileNotice } from './utils.js';
import { loadShelfBooks, switchTab } from './shelf.js';
import { closeBookModal } from './book_modal.js';
import {
  cleanupPdfReader, initPdfReaderEngine, initPdfImageReader, alignAllPdfTransBlocks,
  scrollToPdfPage, syncPdfActiveTocNode, adjustPdfZoom, setPdfZoomFitWidth,
} from './reader_pdf.js';
import {
  applyTransOnly, hideReaderSelectionBubble, hideReaderTransPopover, showReaderTransPopover,
  toggleReaderTransDrawer, toggleBilingualMode, toggleHtmlBilingualMode,
  togglePdfBilingualMode, getTransEngine,
} from './reader_translate.js';

export function setMobileChrome(visible) {
  document.getElementById('immersive-reader-overlay').classList.toggle('m-chrome', visible);
  appState.chromeScrollTop = document.getElementById('reader-main-scroll').scrollTop;
  if (!visible) {
    closeMobileSheets();
    closeMobileMenu();
  }
}

// A tap on the text shows or hides the bars; taps on links, buttons and selected text keep their own meaning
export function onMobileReaderTap(e) {
  if (!isMobileReader() || !readerState.isOpen) return;
  if (e.target.closest('a, button, input, select, textarea')) return;
  if (Date.now() - appState.pdfLongPressAt < PDF_LONG_PRESS_TAP_GUARD_MS) return;
  const sel = window.getSelection();
  if (sel && !sel.isCollapsed) return;
  setMobileChrome(!document.getElementById('immersive-reader-overlay').classList.contains('m-chrome'));
}

export function onMobileReaderScroll() {
  if (!isMobileReader() || !readerState.isOpen) return;
  const scrollEl = document.getElementById('reader-main-scroll');
  const chromeShown = document.getElementById('immersive-reader-overlay').classList.contains('m-chrome');
  if (chromeShown && Math.abs(scrollEl.scrollTop - appState.chromeScrollTop) > CHROME_HIDE_SCROLL_PX) setMobileChrome(false);
  updateMobileStatus();
}

// The faint chapter name and progress shown while the bars are hidden
export function updateMobileStatus() {
  const topEl = document.getElementById('m-status-top');
  const bottomEl = document.getElementById('m-status-bottom');
  if (!readerState.isOpen) return;
  let title = '';
  let progress = null;
  if (readerState.mode === 'pdf') {
    title = ((readerState.toc || [])[pdfState.currentTocIdx] || {}).title || `第 ${pdfState.currentPage} 页`;
    progress = computeReadProgress(0, 0, pdfState.currentPage);
  } else {
    const scrollEl = document.getElementById('reader-main-scroll');
    const span = scrollEl.scrollHeight - scrollEl.clientHeight;
    const offset = span > 0 ? Math.min(1, Math.max(0, scrollEl.scrollTop / span)) : 0;
    title = mobileChapterTitle(readerState.currentChapter);
    progress = computeReadProgress(readerState.currentChapter, offset, null);
  }
  topEl.textContent = title;
  bottomEl.textContent = progress == null ? '' : `${(progress * 100).toFixed(1)}%`;
}

export function mobileChapterTitle(idx) {
  return ((readerState.chapters || [])[idx] || {}).title || `第 ${Number(idx) + 1} 章`;
}

export function openMobileSheet(name) {
  closeMobileSheets();
  closeMobileMenu();
  if (name === 'toc') {
    renderReaderTocList(document.getElementById('reader-toc-filter').value);
    document.getElementById('reader-sidebar').classList.add('m-open');
  } else {
    if (name === 'progress') renderMobileProgressSheet();
    if (name === 'font') renderMobileFontSheet();
    document.getElementById(MOBILE_SHEET_IDS[name]).classList.add('open');
  }
  document.getElementById('m-sheet-backdrop').classList.add('open');
}

export function closeMobileSheets() {
  document.querySelectorAll('.m-sheet.open').forEach(sheet => sheet.classList.remove('open'));
  document.getElementById('reader-sidebar').classList.remove('m-open');
  const drawer = document.getElementById('reader-trans-drawer');
  if (!drawer.classList.contains('collapsed')) toggleReaderTransDrawer();
  document.getElementById('m-sheet-backdrop').classList.remove('open');
}

export function renderMobileProgressSheet() {
  const sheet = document.getElementById('m-sheet-progress');
  if (readerState.mode === 'pdf') {
    const pages = pdfState.pageCount || 1;
    sheet.innerHTML = `
      <div class="m-sheet-title" id="m-progress-label">第 ${pdfState.currentPage || 1} / ${pages} 页</div>
      <div class="m-row"><input class="m-range" type="range" min="1" max="${pages}" value="${pdfState.currentPage || 1}"
        oninput="document.getElementById('m-progress-label').textContent = '第 ' + this.value + ' / ${pages} 页'"
        onchange="scrollToPdfPage(Number(this.value), false)"></div>
      <div class="m-row">
        <button class="m-pill" onclick="stepReaderChapter(-1)">上一节</button><span class="m-grow"></span>
        <button class="m-pill" onclick="stepReaderChapter(1)">下一节</button>
      </div>
      <div class="m-row">
        <button class="m-pill" onclick="adjustPdfZoom(-0.15); renderMobileProgressSheet()">缩小</button>
        <span class="m-grow">${Math.round(pdfState.scale * 100)}%</span>
        <button class="m-pill" onclick="adjustPdfZoom(0.15); renderMobileProgressSheet()">放大</button>
        <button class="m-pill" onclick="setPdfZoomFitWidth(); setTimeout(renderMobileProgressSheet, 300)">适合屏宽</button>
      </div>`;
    return;
  }
  const total = readerState.totalChapters || 1;
  const cur = readerState.currentChapter || 0;
  sheet.innerHTML = `
    <div class="m-sheet-title" id="m-progress-label"></div>
    <div class="m-row"><input class="m-range" type="range" min="0" max="${total - 1}" value="${cur}"
      oninput="previewMobileChapter(this.value)"
      onchange="setMobileChrome(false); navigateReaderChapter(Number(this.value))"></div>
    <div class="m-row">
      <button class="m-pill" ${cur <= 0 ? 'disabled' : ''} onclick="setMobileChrome(false); stepReaderChapter(-1)">上一章</button>
      <span class="m-grow">${cur + 1} / ${total}</span>
      <button class="m-pill" ${cur >= total - 1 ? 'disabled' : ''} onclick="setMobileChrome(false); stepReaderChapter(1)">下一章</button>
    </div>`;
  previewMobileChapter(cur);
}

export function previewMobileChapter(idx) {
  document.getElementById('m-progress-label').textContent = mobileChapterTitle(idx);
}

export function renderMobileFontSheet() {
  const isPdf = readerState.mode === 'pdf';
  const font = effectiveReaderFont();
  const lineHeight = readerState.lineHeight || MOBILE_DEFAULT_LINE_HEIGHT;
  const size = isPdf ? readerState.transFontSize : readerState.fontSize;
  const chips = (items, isActive, onclick) => items.map(([value, label]) =>
    `<button class="m-pill ${isActive(value) ? 'active' : ''}" onclick="${onclick(value)}; renderMobileFontSheet()">${label}</button>`).join('');
  document.getElementById('m-sheet-font').innerHTML = `
    <div class="m-sheet-title">${isPdf ? '译文字号' : '字号'}</div>
    <div class="m-row">
      <button class="m-pill" onclick="adjustReaderFontSize(-1); renderMobileFontSheet()">A-</button>
      <span class="m-grow">${size}px</span>
      <button class="m-pill" onclick="adjustReaderFontSize(1); renderMobileFontSheet()">A+</button>
    </div>
    <div class="m-sheet-title">字体</div>
    <div class="m-row m-chips">${chips(MOBILE_FONTS, v => v === font, v => `setReaderFont('${v}')`)}</div>
    <div class="m-sheet-title">行距</div>
    <div class="m-row m-chips">${chips(MOBILE_LINE_HEIGHTS, v => Number(v) === lineHeight, v => `setReaderLineHeight(${v})`)}</div>`;
}

export function setReaderLineHeight(value) {
  readerState.lineHeight = value;
  localStorage.setItem('lib_reader_lineheight', String(value));
  applyReaderPreferences();
  alignAllPdfTransBlocks();
}

export function toggleMobileMenu(e) {
  e.stopPropagation();
  document.getElementById('m-menu').classList.toggle('hidden');
}

export function closeMobileMenu() {
  document.getElementById('m-menu').classList.add('hidden');
}

export function mobileOpenTransDrawer() {
  closeMobileMenu();
  closeMobileSheets();
  toggleReaderTransDrawer();
  document.getElementById('m-sheet-backdrop').classList.add('open');
}

// The 译 choice is remembered: the next chapter and the next book open with it as left
export async function mobileToggleBilingual() {
  const translating = toggleBilingualMode();  // flips isBilingual at once; translating a chapter takes a while
  try {
    localStorage.setItem('lib_reader_m_bilingual', readerState.isBilingual ? '1' : '0');
  } catch (e) {
    console.warn('Could not remember the bilingual setting:', e);
  }
  syncMobileButtons();
  await translating;
}

export function syncMobileButtons() {
  const on = !!readerState.isBilingual;
  const offerTranslation = readerState.isEnglish ? '' : 'none';  // nothing to translate in a Chinese book
  const topBtn = document.getElementById('m-bilingual-btn');
  const fab = document.getElementById('m-fab-trans');
  topBtn.style.display = offerTranslation;
  fab.style.display = offerTranslation;
  topBtn.classList.toggle('active', on);
  fab.classList.toggle('active', on);
  const transOnlyItem = document.getElementById('m-transonly-item');
  transOnlyItem.style.display = readerState.mode === 'pdf' && on ? '' : 'none';
  transOnlyItem.textContent = readerState.transOnly ? '显示原页' : '仅看译文';
  document.getElementById('m-pagetext-item').style.display = pdfState.imageMode ? '' : 'none';
}

export async function onMobileReaderLoaded(d) {
  document.getElementById('m-top-title').textContent = d.book_title || '';
  document.getElementById('m-download-link').href = `/api/books/${readerState.bookId}/download`;
  setMobileChrome(false);
  if (readerState.mode === 'pdf') setPdfZoomFitWidth();
  if (!readerState.isEnglish && readerState.mode !== 'pdf' && isMostlyLatinText()) {
    readerState.isEnglish = true;  // English text under a made-up Chinese title (batch-renamed books)
    applyReaderPreferences();
  }
  let keepBilingual = false;
  try {
    keepBilingual = localStorage.getItem('lib_reader_m_bilingual') === '1';
  } catch (e) {
    console.warn('Could not read the bilingual setting:', e);
  }
  if (keepBilingual && readerState.isEnglish && !readerState.isBilingual) {
    toggleBilingualMode().catch(e => console.warn('Bilingual view failed to load:', e));  // translates in the background
  }
  syncMobileButtons();
  updateMobileStatus();
  keepScreenOn();
}


export function computeReadProgress(chapterIdx, offset, pdfPage) {
  if (readerState.mode === 'pdf') return pdfPage && pdfState.pageCount ? Math.min(1, pdfPage / pdfState.pageCount) : null;
  const ch = readerState.chapters || [];
  if (!ch.length) return null;
  const total = ch.reduce((s, c) => s + (c.chars || 0), 0);
  if (!total) return Math.min(1, (chapterIdx + offset) / ch.length);
  const before = ch.slice(0, chapterIdx).reduce((s, c) => s + (c.chars || 0), 0);
  return Math.min(1, (before + offset * ((ch[chapterIdx] || {}).chars || 0)) / total);
}

export function saveReaderProgress(bookId, chapterIdx, label, pdfPage = null, offset = 0, immediate = false) {
  chapterIdx = parseInt(chapterIdx, 10) || 0;
  appState.pendingProgress = {
    bookId,
    body: { progress: computeReadProgress(chapterIdx, offset, pdfPage), chapter_idx: chapterIdx, offset, pdf_page: pdfPage || null, label: label || '' },
  };
  if (immediate) { flushReaderProgress(); return; }
  if (!appState.progressTimer) appState.progressTimer = setTimeout(flushReaderProgress, 5000);  // at most one save per 5 s while reading
}

export function flushReaderProgress() {
  clearTimeout(appState.progressTimer);
  appState.progressTimer = null;
  const p = appState.pendingProgress;
  appState.pendingProgress = null;
  if (!p) return Promise.resolve();
  return fetch(`/api/books/${p.bookId}/progress`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p.body),
    keepalive: true,  // still sent when the tab closes
  }).catch(() => {});
}

export function onReaderScrollProgress() {
  if (!readerState.isOpen || readerState.mode === 'pdf' || !readerState.bookId) return;
  const sc = document.getElementById('reader-main-scroll');
  const span = sc.scrollHeight - sc.clientHeight;
  const offset = span > 0 ? Math.min(1, Math.max(0, sc.scrollTop / span)) : 0;
  const ch = (readerState.chapters || [])[readerState.currentChapter] || {};
  saveReaderProgress(readerState.bookId, readerState.currentChapter, ch.title || '', null, Math.round(offset * 1000) / 1000);
}

// One-time upload of progress the browser kept locally before it moved to the server
export async function migrateLocalReaderProgress() {
  let keys = [];
  try { keys = Object.keys(localStorage).filter(k => k.startsWith('lib_reader_pos_')); } catch (e) { return; }
  for (const k of keys) {
    try {
      const old = JSON.parse(localStorage.getItem(k));
      const r = await fetch(`/api/books/${k.slice('lib_reader_pos_'.length)}/progress`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chapter_idx: old.chapterIdx || 0, pdf_page: old.pdfPage || null, label: old.chapterTitle || '', opened_at: old.ts || null }),
      });
      if (r.ok) localStorage.removeItem(k);
    } catch (e) {}
  }
}


export function applyReaderPreferences() {
  const overlay = document.getElementById('immersive-reader-overlay');
  const paper = document.getElementById('reader-paper');
  const bodyEl = document.getElementById('reader-article-body');
  overlay.setAttribute('data-theme', readerState.theme);
  overlay.setAttribute('data-font', effectiveReaderFont());
  overlay.setAttribute('data-lang', readerState.isEnglish ? 'en' : 'zh');
  bodyEl.lang = readerState.isEnglish ? 'en' : 'zh-CN';  // hyphenation follows the text's language
  if (readerState.lineHeight) overlay.style.setProperty('--m-line-height', String(readerState.lineHeight));
  else overlay.style.removeProperty('--m-line-height');
  const lineHeightSelect = document.getElementById('reader-lineheight-select');
  if (lineHeightSelect) lineHeightSelect.value = String(readerState.lineHeight || 0);

  document.querySelectorAll('.reader-theme-dot').forEach(dot => {
    dot.classList.toggle('active', dot.getAttribute('data-rtheme') === readerState.theme);
  });
  document.querySelectorAll('.m-swatch').forEach(sw => {
    sw.classList.toggle('active', sw.getAttribute('data-mtheme') === readerState.theme);
  });

  const fontSelect = document.getElementById('reader-font-select');
  if (fontSelect && fontSelect.value !== effectiveReaderFont()) {
    fontSelect.value = effectiveReaderFont();
  }

  paper.classList.toggle('wide', readerState.wide);
  const wideBtn = document.getElementById('reader-width-btn');
  if (wideBtn) wideBtn.textContent = readerState.wide ? '↔ 标准版心' : '↔ 宽屏';

  const isPdf = readerState.mode === 'pdf';
  const destFs = readerState.transFontSize || 15;
  const origFs = Math.max(11, Math.round(destFs * 0.82));
  overlay.style.setProperty('--trans-dest-fs', `${destFs}px`);
  overlay.style.setProperty('--trans-orig-fs', `${origFs}px`);

  bodyEl.style.fontSize = `${readerState.fontSize}px`;

  const sizeLabel = document.getElementById('reader-fontsize-label');
  if (sizeLabel) {
    sizeLabel.textContent = isPdf ? `${destFs}px` : `${readerState.fontSize}px`;
    sizeLabel.title = isPdf ? '双语译文字号 (点击A-/A+调节)' : '正文字号 (点击A-/A+调节)';
  }

  document.querySelectorAll('.reader-trans-fs-badge').forEach(badge => {
    badge.textContent = `${destFs}px`;
  });
}

export function setReaderTheme(theme) {
  readerState.theme = theme;
  localStorage.setItem('lib_reader_theme', theme);
  applyReaderPreferences();
}

// No saved choice: English books read in a serif on phones, everything else in the sans default
export function effectiveReaderFont() {
  if (readerState.font) return readerState.font;
  return isMobileReader() && readerState.isEnglish ? 'serif' : 'sans';
}

export function setReaderFont(fontId) {
  readerState.font = fontId || 'sans';
  localStorage.setItem('lib_reader_font', readerState.font);
  applyReaderPreferences();
}

export function adjustReaderFontSize(delta) {
  if (readerState.mode === 'pdf') {
    readerState.transFontSize = Math.max(11, Math.min(26, (readerState.transFontSize || 15) + delta));
    localStorage.setItem('lib_reader_trans_fontsize', String(readerState.transFontSize));
    alignAllPdfTransBlocks();
  } else {
    readerState.fontSize = Math.max(13, Math.min(30, readerState.fontSize + delta));
    readerState.transFontSize = Math.max(11, Math.min(26, Math.round(readerState.fontSize * 0.85)));
    localStorage.setItem('lib_reader_fontsize', String(readerState.fontSize));
    localStorage.setItem('lib_reader_trans_fontsize', String(readerState.transFontSize));
  }
  applyReaderPreferences();
}

export function toggleReaderWidth() {
  readerState.wide = !readerState.wide;
  localStorage.setItem('lib_reader_wide', readerState.wide ? '1' : '0');
  applyReaderPreferences();
}

export function toggleReaderSidebar() {
  document.getElementById('reader-sidebar').classList.toggle('collapsed');
}

export function closeOnlineReader() {
  // Refresh the shelf after the last position is saved so its card shows the new progress
  flushReaderProgress().then(() => { if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page); });
  readerState.isOpen = false;
  cleanupPdfReader();
  cleanupHtmlScrollSpy();
  hideReaderSelectionBubble();
  hideReaderTransPopover();
  setMobileChrome(false);
  releaseScreenWakeLock();
  document.getElementById('immersive-reader-overlay').classList.remove('open');
}

export function syncActiveTocNode(activeIdx) {
  if (activeIdx < 0) return;
  document.querySelectorAll('.reader-toc-item').forEach((el, idx) => {
    const isAct = idx === activeIdx;
    el.classList.toggle('active', isAct);
    if (isAct) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  });
  const toc = readerState.toc || [];
  if (toc[activeIdx] && toc[activeIdx].title) {
    const topInfo = document.getElementById('reader-top-chapter-info');
    if (topInfo && readerState.mode !== 'pdf') {
      topInfo.textContent = toc[activeIdx].title;
    }
  }
}

export function findTargetElement(anchorId, title = '') {
  if (!anchorId && !title) return null;
  const bodyEl = document.getElementById('reader-article-body');
  if (!bodyEl) return null;

  // 1. Exact or decoded ID
  if (anchorId) {
    const cleanA = anchorId.replace(/^#/, '');
    let el = document.getElementById(cleanA);
    if (el && bodyEl.contains(el)) return el;

    try {
      const dec = decodeURIComponent(cleanA);
      el = document.getElementById(dec);
      if (el && bodyEl.contains(el)) return el;
    } catch (e) {}

    // 2. Query name attribute
    try {
      el = bodyEl.querySelector(`[name="${CSS.escape(cleanA)}"]`);
      if (el) return el;
    } catch (e) {}

    // 3. Match id case-insensitively or suffix match
    const lowerA = cleanA.toLowerCase();
    const allWithId = bodyEl.querySelectorAll('[id]');
    for (let i = 0; i < allWithId.length; i++) {
      const idVal = allWithId[i].id.toLowerCase();
      if (idVal === lowerA || idVal.endsWith('_' + lowerA) || idVal.includes(lowerA)) {
        return allWithId[i];
      }
    }
  }

  // 4. Match heading text if title provided
  if (title) {
    const cleanTitle = title.trim().toLowerCase();
    const headings = bodyEl.querySelectorAll('h1, h2, h3, h4, h5, h6, .chapter-title, .section-title, b, strong');
    for (let i = 0; i < headings.length; i++) {
      const ht = headings[i].textContent.trim().toLowerCase();
      if (ht === cleanTitle || ht.includes(cleanTitle) || cleanTitle.includes(ht)) {
        return headings[i];
      }
    }
  }

  return null;
}

export function scrollToReaderAnchor(anchorId, title = '') {
  const scrollEl = document.getElementById('reader-main-scroll');
  if (!scrollEl) return;
  if (!anchorId && !title) {
    scrollEl.scrollTo({ top: 0, behavior: 'smooth' });
    return;
  }
  const target = findTargetElement(anchorId, title);
  if (target) {
    const cRect = scrollEl.getBoundingClientRect();
    const tRect = target.getBoundingClientRect();
    const targetScrollTop = tRect.top - cRect.top + scrollEl.scrollTop - 24;
    scrollEl.scrollTo({ top: Math.max(0, targetScrollTop), behavior: 'smooth' });

    target.classList.add('reader-anchor-flash');
    setTimeout(() => target.classList.remove('reader-anchor-flash'), 1600);
  } else {
    scrollEl.scrollTo({ top: 0, behavior: 'smooth' });
  }
}


export function cleanupHtmlScrollSpy() {
  const scrollEl = document.getElementById('reader-main-scroll');
  if (scrollEl && appState.htmlScrollHandler) {
    scrollEl.removeEventListener('scroll', appState.htmlScrollHandler);
    appState.htmlScrollHandler = null;
  }
}

export function initHtmlScrollSpy() {
  cleanupHtmlScrollSpy();
  const scrollEl = document.getElementById('reader-main-scroll');
  const bodyEl = document.getElementById('reader-article-body');
  if (!scrollEl || !bodyEl || readerState.mode === 'pdf') return;

  const toc = readerState.toc || [];
  const curChapter = readerState.currentChapter;
  const chapterTocs = [];
  for (let i = 0; i < toc.length; i++) {
    if (toc[i].chapter_idx === curChapter) {
      chapterTocs.push({ ...toc[i], tocIdx: i });
    }
  }
  if (!chapterTocs.length) return;

  // Pre-resolve DOM element for each TOC item
  const resolved = [];
  chapterTocs.forEach(item => {
    const el = findTargetElement(item.anchor, item.title);
    if (el) resolved.push({ item, el });
  });

  appState.htmlScrollHandler = () => {
    if (appState.htmlScrollPending) return;
    appState.htmlScrollPending = true;
    requestAnimationFrame(() => {
      appState.htmlScrollPending = false;
      if (readerState.mode === 'pdf' || !readerState.isOpen) return;

      const cTop = scrollEl.getBoundingClientRect().top;
      let bestItem = chapterTocs[0];

      for (let i = 0; i < resolved.length; i++) {
        const r = resolved[i];
        const top = r.el.getBoundingClientRect().top;
        if (top <= cTop + 140) {
          bestItem = r.item;
        } else {
          break;
        }
      }

      if (bestItem) {
        syncActiveTocNode(bestItem.tocIdx);
        readerState.activeAnchor = bestItem.anchor || '';
      }
    });
  };

  scrollEl.addEventListener('scroll', appState.htmlScrollHandler, { passive: true });
}

export function renderReaderTocList(filterKw = '') {
  const listEl = document.getElementById('reader-toc-list');
  const kw = (filterKw || '').trim().toLowerCase();
  const toc = readerState.toc || [];

  if (!toc.length) {
    listEl.innerHTML = `<div style="padding:16px; color:var(--r-muted); font-size:12.5px; text-align:center;">暂无章节目录</div>`;
    return;
  }

  let activeTocId = -1;
  if (readerState.mode === 'pdf') {
    activeTocId = pdfState.currentTocIdx;
  } else {
    for (let i = 0; i < toc.length; i++) {
      if (toc[i].chapter_idx === readerState.currentChapter) {
        if (!readerState.activeAnchor || toc[i].anchor === readerState.activeAnchor) {
          activeTocId = i;
          break;
        }
        if (activeTocId === -1) activeTocId = i;
      }
    }
  }

  const itemsHtml = toc.map((t, idx) => {
    if (kw && !(t.title || '').toLowerCase().includes(kw)) return '';
    const lvl = Math.min(3, Math.max(0, parseInt(t.level || 0, 10)));
    const isActive = idx === activeTocId;
    const badge = readerState.mode === 'pdf' && t.page ? `P.${t.page}` : `${(t.chapter_idx || 0) + 1}`;
    return `
      <div class="reader-toc-item lvl-${lvl} ${isActive ? 'active' : ''}"
           id="reader-toc-node-${idx}"
           onclick="onReaderTocClick(${Number(t.chapter_idx) || 0}, ${escapeHtml(JSON.stringify(String(t.anchor || '')))}, ${Number(t.page) || 1}, ${idx})">
        <span style="overflow:hidden; text-overflow:ellipsis;">${escapeHtml(t.title || `第 ${idx + 1} 节`)}</span>
        <span style="font-size:11px; color:var(--r-muted); flex-shrink:0;">${badge}</span>
      </div>
    `;
  }).join('');

  listEl.innerHTML = itemsHtml || `<div style="padding:16px; color:var(--r-muted); font-size:12.5px; text-align:center;">无匹配章节</div>`;

  if (activeTocId >= 0 && !kw) {
    const activeEl = document.getElementById(`reader-toc-node-${activeTocId}`);
    if (activeEl) {
      activeEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}

export function filterReaderToc(val) {
  renderReaderTocList(val);
}

/* ==================== PDF.JS HIGH-FIDELITY READER ENGINE ==================== */

export async function onReaderTocClick(chapterIdx, anchor, pdfPage, tocNodeIdx) {
  readerState.activeAnchor = anchor || '';
  if (isMobileReader()) setMobileChrome(false);
  if (readerState.mode === 'pdf') {
    scrollToPdfPage(pdfPage || 1, true);
    syncActiveTocNode(tocNodeIdx);
    return;
  }

  const toc = readerState.toc || [];
  const t = toc[tocNodeIdx];
  const targetTitle = t ? t.title : '';

  if (chapterIdx === readerState.currentChapter) {
    syncActiveTocNode(tocNodeIdx);
    scrollToReaderAnchor(anchor, targetTitle);
    return;
  }
  await navigateReaderChapter(chapterIdx, anchor, targetTitle, tocNodeIdx);
}

export async function stepReaderChapter(delta) {
  if (readerState.mode === 'pdf') {
    const toc = readerState.toc || [];
    if (!toc.length) {
      scrollToPdfPage(pdfState.currentPage + delta * 5, true);
      return;
    }
    const nextIdx = Math.max(0, Math.min(toc.length - 1, (pdfState.currentTocIdx || 0) + delta));
    if (toc[nextIdx] && toc[nextIdx].page) {
      scrollToPdfPage(toc[nextIdx].page, true);
    }
    return;
  }
  const nextIdx = readerState.currentChapter + delta;
  if (nextIdx < 0 || nextIdx >= readerState.totalChapters) return;
  await navigateReaderChapter(nextIdx, '');
}

export async function navigateReaderChapter(chapterIdx, anchor = '', title = '', targetTocIdx = -1) {
  if (!readerState.bookId) return;
  await openOnlineReader(readerState.bookId, chapterIdx, anchor, title, targetTocIdx);
}

export async function openOnlineReader(bookId, chapterIdx = null, anchor = '', targetTitle = '', targetTocIdx = -1) {
  closeBookModal();
  const overlay = document.getElementById('immersive-reader-overlay');
  const bodyEl = document.getElementById('reader-article-body');
  const scrollEl = document.getElementById('reader-main-scroll');
  const warnEl = document.getElementById('reader-warning-banner');
  const footerEl = document.getElementById('reader-nav-footer');

  // No chapter given: the server resumes the saved chapter and returns saved_pos for the exact spot
  const resume = chapterIdx === null || chapterIdx === undefined;
  chapterIdx = resume ? null : (parseInt(chapterIdx, 10) || 0);
  if (readerState.bookId !== bookId) flushReaderProgress();

  readerState.isOpen = true;
  readerState.bookId = bookId;
  readerState.activeAnchor = anchor || '';
  cleanupHtmlScrollSpy();
  applyReaderPreferences();
  overlay.classList.add('open');

  bodyEl.innerHTML = `<div style="padding:80px 20px; text-align:center; color:var(--r-muted); font-size:15px;">📖 正在解析电子书章节与排版结构...</div>`;
  warnEl.classList.add('hidden');

  try {
    const r = await fetch(`/api/books/${bookId}/read${resume ? '' : `?chapter=${chapterIdx}`}`);
    const d = await r.json();
    readerState.savedPos = resume ? d.saved_pos : null;
    if (!d.ok) {
      bodyEl.innerHTML = `<div style="color:var(--rose); padding:30px; text-align:center;">${escapeHtml(d.error || '无法加载书籍内容')}</div>`;
      return;
    }

    readerState.mode = d.mode || 'html';
    readerState.toc = d.toc || [];
    readerState.chapters = d.chapters || [];
    readerState.currentChapter = d.current_chapter || 0;
    readerState.totalChapters = d.total_chapters || Math.max(1, readerState.chapters.length);
    readerState.pdfUrl = d.pdf_url || '';
    readerState.language = (d.language || '').toLowerCase();

    const isEn = readerState.language.startsWith('en') || (!(/[\u4e00-\u9fa5]/.test(d.book_title || '')) && !(/[\u4e00-\u9fa5]/.test(d.author || '')));
    readerState.isEnglish = isEn;

    const transToggleBtn = document.getElementById('reader-trans-toggle-btn');
    if (transToggleBtn) {
      if (isEn) {
        transToggleBtn.style.color = '#38bdf8';
        transToggleBtn.style.borderColor = 'rgba(56, 189, 248, 0.45)';
        transToggleBtn.innerHTML = '🌐 英文助手 <span style="font-size:10px;background:#0284c7;color:#fff;padding:1px 4px;border-radius:3px;vertical-align:middle;">EN</span>';
      } else {
        transToggleBtn.style.color = '';
        transToggleBtn.style.borderColor = '';
        transToggleBtn.textContent = '🌐 翻译助手';
      }
    }
    const bilingualBtn = document.getElementById('reader-bilingual-btn');
    if (bilingualBtn) {
      bilingualBtn.textContent = '📖 双语对照';
      bilingualBtn.classList.remove('active');
      readerState.isBilingual = false;
    }
    applyTransOnly();

    document.getElementById('reader-top-title').textContent = d.book_title || '在线阅读';
    document.getElementById('reader-top-sub').textContent = `${d.author || '未知作者'} · ${(d.format || 'EBOOK').toUpperCase()}`;
    document.getElementById('reader-download-link').href = `/api/books/${bookId}/download`;

    if (d.warning) {
      warnEl.textContent = `⚠️ ${d.warning}`;
      warnEl.classList.remove('hidden');
    } else {
      warnEl.classList.add('hidden');
    }

    if (d.mode === 'pdf') {
      // Switch UI to PDF mode
      document.querySelectorAll('.reader-html-ctrl').forEach(el => el.classList.add('hidden'));
      document.getElementById('reader-pdf-controls').classList.remove('hidden');
      document.getElementById('reader-paper').classList.add('mode-pdf');
      footerEl.classList.add('hidden');
      document.getElementById('reader-top-prev').disabled = false;
      document.getElementById('reader-top-next').disabled = false;

      cleanupPdfReader();
      applyReaderPreferences();
      if (!(isMobileReader() && await initPdfImageReader(chapterIdx))) {
        if (isMobileReader()) showMobileNotice('此书使用兼容模式');
        await initPdfReaderEngine(d, chapterIdx);
      }
    } else {
      // Switch UI to HTML mode
      document.querySelectorAll('.reader-html-ctrl').forEach(el => el.classList.remove('hidden'));
      document.getElementById('reader-pdf-controls').classList.add('hidden');
      document.getElementById('reader-paper').classList.remove('mode-pdf');
      footerEl.classList.remove('hidden');
      cleanupPdfReader();
      applyReaderPreferences();

      const curIdx = readerState.currentChapter;
      const tot = readerState.totalChapters;
      const pct = tot > 1 ? Math.round(((curIdx + 1) / tot) * 100) : 100;
      const chapTitle = d.chapter_title || `第 ${curIdx + 1} 节`;

      document.getElementById('reader-top-chapter-info').textContent = `${chapTitle}  (${curIdx + 1} / ${tot} · ${pct}%)`;
      document.getElementById('reader-bot-progress').textContent = `当前章节：${curIdx + 1} / ${tot} (${pct}%)`;

      const disablePrev = curIdx <= 0;
      const disableNext = curIdx >= tot - 1;
      document.getElementById('reader-top-prev').disabled = disablePrev;
      document.getElementById('reader-bot-prev').disabled = disablePrev;
      document.getElementById('reader-top-next').disabled = disableNext;
      document.getElementById('reader-bot-next').disabled = disableNext;

      const prevChap = curIdx > 0 && readerState.chapters[curIdx - 1] ? readerState.chapters[curIdx - 1].title : '';
      const nextChap = curIdx < tot - 1 && readerState.chapters[curIdx + 1] ? readerState.chapters[curIdx + 1].title : '';
      document.getElementById('reader-bot-prev').textContent = prevChap ? `← 上一章：${prevChap.slice(0, 18)}` : '← 上一章';
      document.getElementById('reader-bot-next').textContent = nextChap ? `下一章：${nextChap.slice(0, 18)} →` : '下一章 →';

      // Render formatted HTML or fallback text
      if (d.html) {
        bodyEl.innerHTML = d.html;
      } else {
        bodyEl.innerHTML = `<div style="white-space:pre-wrap;">${escapeHtml(d.content || '')}</div>`;
      }

      const sp = readerState.savedPos;
      const startOffset = sp && sp.chapter_idx === curIdx ? (sp.offset || 0) : 0;
      saveReaderProgress(bookId, curIdx, chapTitle, null, startOffset, true);

      if (anchor || targetTitle) {
        setTimeout(() => scrollToReaderAnchor(anchor, targetTitle), 40);
        setTimeout(() => scrollToReaderAnchor(anchor, targetTitle), 220);
      } else if (startOffset > 0) {
        // Images load after the text and change the height; set the spot again once they settle
        const toOffset = () => { scrollEl.scrollTop = startOffset * (scrollEl.scrollHeight - scrollEl.clientHeight); };
        setTimeout(toOffset, 40);
        setTimeout(toOffset, 400);
      } else {
        scrollEl.scrollTop = 0;
      }

      if (targetTocIdx >= 0) {
        syncActiveTocNode(targetTocIdx);
      }
      initHtmlScrollSpy();
    }

    renderReaderTocList(document.getElementById('reader-toc-filter').value);
    if (isMobileReader()) await onMobileReaderLoaded(d);
  } catch (err) {
    bodyEl.innerHTML = `<div style="color:var(--rose); padding:30px; text-align:center;">读取异常：${escapeHtml(String(err))}</div>`;
  }
}

// Global keyboard shortcuts: Cmd+K search, Escape close, ArrowLeft/ArrowRight chapter turn
window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (readerState.isOpen) closeOnlineReader();
    switchTab('shelf');
    document.getElementById('shelf-search-input').focus();
  } else if (e.key === 'Escape') {
    if (appState.readerTransPopover && appState.readerTransPopover.style.display !== 'none') {
      hideReaderTransPopover();
    } else if (readerState.isOpen) {
      closeOnlineReader();
    } else {
      closeBookModal();
    }
  } else if (readerState.isOpen && !['INPUT', 'TEXTAREA'].includes((e.target && e.target.tagName) || '')) {
    if ((e.key === 't' || e.key === 'T') && !e.metaKey && !e.ctrlKey) {
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && sel.toString().trim()) {
        const range = sel.getRangeAt(0);
        showReaderTransPopover(range.getBoundingClientRect(), sel.toString().trim());
      }
    } else if (e.key === 'ArrowLeft') {
      stepReaderChapter(-1);
    } else if (e.key === 'ArrowRight') {
      stepReaderChapter(1);
    }
  }
});

// Reader selection bubble logic (works for both PDF text layer and EPUB/HTML)
