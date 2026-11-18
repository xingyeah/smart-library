import { alignAllPdfTransBlocks } from './reader_pdf_view.js';
import { appState, isMobileReader, readerState } from './state.js';
import { toggleReaderTransDrawer } from './reader_translate.js';

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
  const lineHeightSelect = /** @type {HTMLSelectElement | null} */ (document.getElementById('reader-lineheight-select'));
  if (lineHeightSelect) lineHeightSelect.value = String(readerState.lineHeight || 0);

  document.querySelectorAll('.reader-theme-dot').forEach(dot => {
    dot.classList.toggle('active', dot.getAttribute('data-rtheme') === readerState.theme);
  });
  document.querySelectorAll('.m-swatch').forEach(sw => {
    sw.classList.toggle('active', sw.getAttribute('data-mtheme') === readerState.theme);
  });

  const fontSelect = /** @type {HTMLSelectElement | null} */ (document.getElementById('reader-font-select'));
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

export function setReaderLineHeight(value) {
  readerState.lineHeight = value;
  localStorage.setItem('lib_reader_lineheight', String(value));
  applyReaderPreferences();
  alignAllPdfTransBlocks();
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

export function setMobileChrome(visible) {
  document.getElementById('immersive-reader-overlay').classList.toggle('m-chrome', visible);
  appState.chromeScrollTop = document.getElementById('reader-main-scroll').scrollTop;
  if (!visible) {
    closeMobileSheets();
    closeMobileMenu();
  }
}

export function closeMobileSheets() {
  document.querySelectorAll('.m-sheet.open').forEach(sheet => sheet.classList.remove('open'));
  document.getElementById('reader-sidebar').classList.remove('m-open');
  const drawer = document.getElementById('reader-trans-drawer');
  if (!drawer.classList.contains('collapsed')) toggleReaderTransDrawer();
  document.getElementById('m-sheet-backdrop').classList.remove('open');
}

export function closeMobileMenu() {
  document.getElementById('m-menu').classList.add('hidden');
}
