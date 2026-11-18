import { toggleBilingualMode } from './reader_bilingual.js';
import { setPdfZoomFitWidth } from './reader_pdf_view.js';
import { computeReadProgress } from './reader_progress.js';
import { renderReaderTocList } from './reader_toc.js';
import { toggleReaderTransDrawer } from './reader_translate.js';
import {
  CHROME_HIDE_SCROLL_PX, MOBILE_DEFAULT_LINE_HEIGHT, MOBILE_FONTS, MOBILE_LINE_HEIGHTS, MOBILE_SHEET_IDS,
  PDF_LONG_PRESS_TAP_GUARD_MS, appState, isMobileReader, pdfState, readerState,
} from './state.js';
import { isMostlyLatinText, keepScreenOn } from './utils.js';
import {
  applyReaderPreferences, closeMobileMenu, closeMobileSheets, effectiveReaderFont, setMobileChrome,
  setReaderFont, setReaderLineHeight,
} from './reader_prefs.js';

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
    renderReaderTocList(/** @type {HTMLInputElement} */ (document.getElementById('reader-toc-filter')).value);
    document.getElementById('reader-sidebar').classList.add('m-open');
  } else {
    if (name === 'progress') renderMobileProgressSheet();
    if (name === 'font') renderMobileFontSheet();
    document.getElementById(MOBILE_SHEET_IDS[name]).classList.add('open');
  }
  document.getElementById('m-sheet-backdrop').classList.add('open');
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

export function toggleMobileMenu(e) {
  e.stopPropagation();
  document.getElementById('m-menu').classList.toggle('hidden');
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
  /** @type {HTMLAnchorElement} */ (document.getElementById('m-download-link')).href = `/api/books/${readerState.bookId}/download`;
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
