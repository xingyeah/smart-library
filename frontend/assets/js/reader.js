import { appState, isMobileReader, readerState, shelfFilters } from './state.js';
import { escapeHtml, releaseScreenWakeLock, showMobileNotice } from './utils.js';
import { loadShelfBooks, switchTab } from './shelf.js';
import { closeBookModal } from './book_modal.js';
import { cleanupPdfReader, initPdfImageReader, initPdfReaderEngine } from './reader_pdf.js';
import {
  hideReaderSelectionBubble, hideReaderTransPopover, showReaderTransPopover,
} from './reader_translate.js';
import { applyTransOnly, extractPdfPageParagraphs, toggleHtmlBilingualMode, translatePdfPage } from './reader_bilingual.js';
import { onMobileReaderLoaded, openMobileSheet } from './reader_mobile.js';
import { readerHooks } from './reader_hooks.js';
import { flushReaderProgress, saveReaderProgress } from './reader_progress.js';
import {
  cleanupHtmlScrollSpy, initHtmlScrollSpy, renderReaderTocList, scrollToReaderAnchor, stepReaderChapter,
  syncActiveTocNode,
} from './reader_toc.js';
import { applyReaderPreferences, setMobileChrome } from './reader_prefs.js';

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

/* ==================== PDF.JS HIGH-FIDELITY READER ENGINE ==================== */

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
    /** @type {HTMLAnchorElement} */ (document.getElementById('reader-download-link')).href = `/api/books/${bookId}/download`;

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
      /** @type {HTMLButtonElement} */ (document.getElementById('reader-top-prev')).disabled = false;
      /** @type {HTMLButtonElement} */ (document.getElementById('reader-top-next')).disabled = false;

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
      /** @type {HTMLButtonElement} */ (document.getElementById('reader-top-prev')).disabled = disablePrev;
      /** @type {HTMLButtonElement} */ (document.getElementById('reader-bot-prev')).disabled = disablePrev;
      /** @type {HTMLButtonElement} */ (document.getElementById('reader-top-next')).disabled = disableNext;
      /** @type {HTMLButtonElement} */ (document.getElementById('reader-bot-next')).disabled = disableNext;

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

    renderReaderTocList(/** @type {HTMLInputElement} */ (document.getElementById('reader-toc-filter')).value);
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
  } else if (readerState.isOpen && !['INPUT', 'TEXTAREA'].includes(e.target instanceof Element ? e.target.tagName : '')) {
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

Object.assign(readerHooks, {
  openOnlineReader, closeOnlineReader, openMobileSheet,
  toggleHtmlBilingualMode, translatePdfPage, extractPdfPageParagraphs,
});
