import { appState, shelfFilters, readerState, pdfState } from './state.js';
import { escapeHtml } from './utils.js';
import { aiRerankOn, setAiRerank, loadAiUsage, loadStats } from './api.js';
import {
  initCategoryChips, bindShelfSearchIme, toggleLangFilter, switchTab, setFilter, resetShelfFilters,
  debounceShelfSearch, toggleFavorite, cycleReadingStatus, toggleShelfMoreFilters,
  toggleShelfFilter, clearShelfFilters, updateShelfSearchClear, clearShelfSearch,
  openShelfSheet, closeShelfSheet, pickShelfSort, loadMoreShelfBooks,
  initMobileShelf, loadShelfBooks,
} from './shelf.js';
import { initBackStack } from './back_stack.js';
import { runPresetDescribe, triggerDescribeRecommend, loadInterestView, saveInterestConfig, showExploreRatio } from './recommend.js';
import {
  startOrganize, loadConflicts, resolveConflict, rollbackRun,
  loadMismergedBooks, triggerSplitSingleBook, triggerBatchSplitMismerged,
} from './organize.js';
import {
  openBookModal, closeBookModal, saveBookMeta, triggerAiRefresh, acceptProposedTitle,
  triggerDeleteBook, triggerSplitFromModal, toggleModalWant, toggleModalFavorite,
} from './book_modal.js';
import {
  initPdfLongPress, alignAllPdfTransBlocks, openPdfPageTextSheet,
  scrollToPdfPage, adjustPdfZoom, setPdfZoomFitWidth,
} from './reader_pdf.js';
import {
  initReaderSelectionHandler, speakEnglishText, hideReaderTransPopover,
  toggleReaderTransDrawer, executeDrawerTranslate, speakDrawerCurrent,
  copyDrawerCurrent, reselectHistoryItem, clearTransHistory,
  getTransEngine, onTransEngineChange, toggleBilingualMode,
  initPdfSplitDrag, applyPdfSplitShare, savedPdfSplitShare,
  toggleTransOnly, speakPdfPage, copyPdfPageTrans,
} from './reader_translate.js';
import {
  setMobileChrome, onMobileReaderTap, onMobileReaderScroll,
  openMobileSheet, closeMobileSheets, renderMobileProgressSheet,
  previewMobileChapter, renderMobileFontSheet, setReaderLineHeight,
  toggleMobileMenu, closeMobileMenu, mobileOpenTransDrawer, mobileToggleBilingual,
  onReaderScrollProgress, flushReaderProgress, migrateLocalReaderProgress,
  setReaderTheme, setReaderFont, adjustReaderFontSize, toggleReaderWidth,
  toggleReaderSidebar, closeOnlineReader, filterReaderToc,
  onReaderTocClick, stepReaderChapter, navigateReaderChapter, openOnlineReader, scrollToReaderAnchor,
} from './reader.js';

export function postToLibraryApp(message) {
  window.webkit.messageHandlers.library.postMessage(message);
}

export function toggleAppOfflineCopy(bookId) {
  document.getElementById('detail-more-menu').classList.add('hidden');
  postToLibraryApp({ action: window.libraryAppOffline.has(bookId) ? 'remove' : 'download', bookId });
}

export function initLibraryAppHooks() {
  if (!window.libraryAppOffline) return;
  const btn = document.createElement('button');
  btn.className = 'nav-tab';
  btn.textContent = 'App 设置';
  btn.onclick = () => postToLibraryApp({ action: 'settings' });
  document.querySelector('.nav-tabs').appendChild(btn);
  if (window.libraryAppIsOffline) libraryAppSetOffline(true);
  postToLibraryApp({ action: 'ready' });
}

// Called by the iPhone app whenever the server becomes unreachable or reachable again. Offline only the shelf
// works (answered from the phone's cache), so the shelf is shown and refreshed; back online it reloads.
export function libraryAppSetOffline(isOffline) {
  if (document.body.classList.contains('app-offline') === isOffline) return;
  document.body.classList.toggle('app-offline', isOffline);
  if (!isOffline) {
    loadStats();
    loadShelfBooks(1);
    return;
  }
  const note = '离线 · 只显示本机缓存的书';
  document.getElementById('top-subtitle').innerText = note;
  if (appState.currentTab === 'shelf') loadShelfBooks(1);
  else switchTab('shelf');
}


// Expose inline HTML event handlers and shared state objects to global scope
Object.assign(window, {
  appState, shelfFilters, readerState, pdfState,
  escapeHtml, setAiRerank, toggleLangFilter, switchTab, setFilter,
  resetShelfFilters, debounceShelfSearch, toggleFavorite, cycleReadingStatus, toggleShelfMoreFilters,
  toggleShelfFilter, clearShelfFilters, updateShelfSearchClear, clearShelfSearch,
  openShelfSheet, closeShelfSheet, pickShelfSort, loadMoreShelfBooks,
  loadShelfBooks,
  runPresetDescribe, triggerDescribeRecommend, loadInterestView, saveInterestConfig, showExploreRatio,
  startOrganize, loadConflicts, resolveConflict, rollbackRun,
  loadMismergedBooks, triggerSplitSingleBook, triggerBatchSplitMismerged,
  openBookModal, closeBookModal, saveBookMeta, triggerAiRefresh, acceptProposedTitle,
  triggerDeleteBook, triggerSplitFromModal, toggleModalWant, toggleModalFavorite,
  openPdfPageTextSheet, scrollToPdfPage, adjustPdfZoom, setPdfZoomFitWidth,
  speakEnglishText, hideReaderTransPopover, toggleReaderTransDrawer,
  executeDrawerTranslate, speakDrawerCurrent, copyDrawerCurrent,
  reselectHistoryItem, clearTransHistory, onTransEngineChange,
  toggleBilingualMode, toggleTransOnly, speakPdfPage, copyPdfPageTrans,
  setMobileChrome, openMobileSheet, closeMobileSheets, renderMobileProgressSheet,
  previewMobileChapter, renderMobileFontSheet, setReaderLineHeight,
  toggleMobileMenu, closeMobileMenu, mobileOpenTransDrawer, mobileToggleBilingual,
  setReaderTheme, setReaderFont, adjustReaderFontSize, toggleReaderWidth,
  toggleReaderSidebar, closeOnlineReader, filterReaderToc,
  onReaderTocClick, stepReaderChapter, navigateReaderChapter, openOnlineReader, scrollToReaderAnchor,
  toggleAppOfflineCopy, libraryAppSetOffline,
});

// Initial boot
initLibraryAppHooks();
/** @type {HTMLSelectElement} */ (document.getElementById('reader-trans-engine')).value = getTransEngine();
setAiRerank(aiRerankOn());
document.getElementById('reader-main-scroll').addEventListener('scroll', onReaderScrollProgress, { passive: true });
document.getElementById('reader-main-scroll').addEventListener('scroll', onMobileReaderScroll, { passive: true });
document.getElementById('reader-main-scroll').addEventListener('click', onMobileReaderTap);
initPdfLongPress();
initPdfSplitDrag();
window.addEventListener('resize', alignAllPdfTransBlocks);
window.addEventListener('resize', () => {
  clearTimeout(appState.splitResizeTimer);
  appState.splitResizeTimer = setTimeout(() => {
    if (readerState.mode === 'pdf' && readerState.isBilingual && savedPdfSplitShare()) applyPdfSplitShare(savedPdfSplitShare());
  }, 250);
});
document.addEventListener('visibilitychange', () => { if (document.hidden) flushReaderProgress(); });
migrateLocalReaderProgress();
initCategoryChips();
bindShelfSearchIme();
initMobileShelf();
loadStats();
loadShelfBooks(1);
initReaderSelectionHandler();
initBackStack();
