import { MOBILE_QUERY, appState, pdfState, readerState, shelfFilters } from './state.js';
import { escapeHtml } from './utils.js';
import { aiRerankOn, loadStats, setAiRerank } from './api.js';
import {
  bindShelfSearchIme, clearShelfFilters, clearShelfGroup, clearShelfSearch, debounceShelfSearch,
  initMobileShelf, loadMoreShelfBooks, loadShelfBooks, resetShelfFilters, showReadingShelf, switchTab,
  toggleLangFilter, toggleShelfFilter, updateShelfSearchClear,
} from './shelf.js';
import {
  applyHomeBatch, clearHomeChecks, hideCommandSuggest, initHomeKeys, onCommandKey, renderCommandSuggest,
  runCommandSuggest, selectHomeRow, toggleHomeCheck, toggleHomeNav, toggleHomePageChecks, toggleHomeView,
} from './home.js';
import { initBackStack } from './back_stack.js';
import {
  askTonight, loadTonightView, moreTonight, runTonightPreset, tonightOpen, tonightSkip, tonightWant,
} from './tonight.js';
import { loadInterestView, saveInterestConfig, showExploreRatio } from './recommend.js';
import {
  loadConflicts, loadMismergedBooks, resolveConflict, rollbackRun, startOrganize,
  triggerBatchSplitMismerged, triggerSplitSingleBook,
} from './organize.js';
import {
  acceptProposedTitle, closeBookModal, openBookModal, saveBookMeta, toggleModalFavorite, toggleModalWant,
  triggerAiRefresh, triggerDeleteBook, triggerSplitFromModal,
} from './book_modal.js';
import { initPdfLongPress, openPdfPageTextSheet } from './reader_pdf.js';
import {
  clearTransHistory, copyDrawerCurrent, executeDrawerTranslate, getTransEngine, hideReaderTransPopover,
  initReaderSelectionHandler, onTransEngineChange, reselectHistoryItem, speakDrawerCurrent,
  speakEnglishText, toggleReaderTransDrawer,
} from './reader_translate.js';
import { closeOnlineReader, openOnlineReader } from './reader.js';
import {
  applyPdfSplitShare, copyPdfPageTrans, initPdfSplitDrag, savedPdfSplitShare, speakPdfPage,
  toggleBilingualMode, toggleTransOnly,
} from './reader_bilingual.js';
import {
  mobileOpenTransDrawer, mobileToggleBilingual, onMobileReaderScroll, onMobileReaderTap, openMobileSheet,
  previewMobileChapter, renderMobileFontSheet, renderMobileProgressSheet, toggleMobileMenu,
} from './reader_mobile.js';
import {
  adjustPdfZoom, alignAllPdfTransBlocks, scrollToPdfPage, setPdfZoomFitWidth,
} from './reader_pdf_view.js';
import {
  flushReaderProgress, migrateLocalReaderProgress, onReaderScrollProgress,
} from './reader_progress.js';
import {
  filterReaderToc, navigateReaderChapter, onReaderTocClick, scrollToReaderAnchor, stepReaderChapter,
} from './reader_toc.js';
import { cycleReadingStatus, toggleFavorite } from './shelf_actions.js';
import { closeShelfSheet, openShelfSheet, pickShelfSort } from './shelf_sheets.js';
import { exitAuthorFilter, showAuthorBooks } from './shelf_author.js';
import {
  adjustReaderFontSize, closeMobileMenu, closeMobileSheets, setMobileChrome, setReaderFont,
  setReaderLineHeight, setReaderTheme, toggleReaderSidebar, toggleReaderWidth,
} from './reader_prefs.js';

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
  escapeHtml, setAiRerank, toggleLangFilter, switchTab, loadStats,
  resetShelfFilters, debounceShelfSearch, toggleFavorite, cycleReadingStatus,
  toggleShelfFilter, clearShelfFilters, clearShelfGroup, updateShelfSearchClear, clearShelfSearch,
  selectHomeRow, toggleHomeCheck, toggleHomePageChecks, clearHomeChecks, applyHomeBatch, toggleHomeNav,
  toggleHomeView, onCommandKey, renderCommandSuggest, hideCommandSuggest, runCommandSuggest,
  openShelfSheet, closeShelfSheet, pickShelfSort, loadMoreShelfBooks,
  loadShelfBooks, showReadingShelf,
  loadTonightView, moreTonight, askTonight, runTonightPreset, tonightOpen, tonightSkip, tonightWant,
  loadInterestView, saveInterestConfig, showExploreRatio,
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
  toggleAppOfflineCopy, libraryAppSetOffline, showAuthorBooks, exitAuthorFilter,
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
bindShelfSearchIme();
initHomeKeys();
initMobileShelf();
loadStats();
// The phone opens on tonight; the shelf is one tab away
if (window.matchMedia(MOBILE_QUERY).matches) switchTab('tonight'); else loadShelfBooks(1);
initReaderSelectionHandler();
initBackStack();
