import { appState, pdfState, readerState } from './state.js';

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
