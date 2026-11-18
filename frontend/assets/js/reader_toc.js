import { scrollToPdfPage } from './reader_pdf_view.js';
import { appState, isMobileReader, pdfState, readerState } from './state.js';
import { escapeHtml } from './utils.js';
import { setMobileChrome } from './reader_prefs.js';
import { readerHooks } from './reader_hooks.js';

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
  await readerHooks.openOnlineReader(readerState.bookId, chapterIdx, anchor, title, targetTocIdx);
}
