import { saveReaderProgress } from './reader_progress.js';
import {
  PDF_IMAGE_SETTLE_MS, PDF_IMAGE_ZOOM_MAX, PDF_IMAGE_ZOOM_MIN, PDF_KEPT_PAGE_RADIUS, PDF_MAX_PIXEL_RATIO,
  isMobileReader, pdfState, readerState,
} from './state.js';
import { readerHooks } from './reader_hooks.js';

export async function renderPdfPage(pageNo) {
  if (pdfState.imageMode) {
    if (!pdfState.renderedPages.has(pageNo)) schedulePdfPageImage(pageNo);
    return;
  }
  if (!pdfState.doc || pageNo < 1 || pageNo > pdfState.pageCount) return;
  if (pdfState.renderedPages.has(pageNo) || pdfState.renderingPages.has(pageNo)) return;
  pdfState.renderingPages.add(pageNo);

  try {
    const page = await pdfState.doc.getPage(pageNo);
    const canvas = /** @type {HTMLCanvasElement | null} */ (document.getElementById(`pdf-canvas-${pageNo}`));
    const slot = document.getElementById(`pdf-slot-${pageNo}`);
    const spinner = document.getElementById(`pdf-spin-${pageNo}`);
    if (!canvas || !slot || isPdfPageDistant(pageNo)) return;  // scrolled past while queued

    const dpr = Math.min(window.devicePixelRatio || 1, PDF_MAX_PIXEL_RATIO);
    const vp = page.getViewport({ scale: pdfState.scale * dpr });
    const cssW = Math.round(vp.width / dpr);
    const cssH = Math.round(vp.height / dpr);

    canvas.width = vp.width;
    canvas.height = vp.height;
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    slot.style.width = `${cssW}px`;
    slot.style.minHeight = `${cssH}px`;
    slot.style.height = 'auto';

    const ctx = canvas.getContext('2d');
    await page.render({
      canvasContext: ctx,
      viewport: vp
    }).promise;
    if (canvas.width === 0) return;  // released while drawing: render again when it comes back into view

    if (spinner) spinner.style.display = 'none';

    // Render Text Layer for mouse selection & copying
    const textLayerDiv = document.getElementById(`pdf-text-${pageNo}`);
    if (textLayerDiv && typeof pdfjsLib.renderTextLayer === 'function') {
      if (pdfState.textLayerTasks && pdfState.textLayerTasks.has(pageNo)) {
        try { pdfState.textLayerTasks.get(pageNo).cancel(); } catch (e) {}
        pdfState.textLayerTasks.delete(pageNo);
      }
      textLayerDiv.innerHTML = '';
      textLayerDiv.style.width = `${cssW}px`;
      textLayerDiv.style.height = `${cssH}px`;

      const textVp = page.getViewport({ scale: pdfState.scale });
      textLayerDiv.style.setProperty('--scale-factor', textVp.scale);

      try {
        const textContent = await page.getTextContent();
        const textLayerTask = pdfjsLib.renderTextLayer({
          textContentSource: textContent,
          container: textLayerDiv,
          viewport: textVp
        });
        if (pdfState.textLayerTasks) {
          pdfState.textLayerTasks.set(pageNo, textLayerTask);
        }
        await textLayerTask.promise;
      } catch (textErr) {
        if (textErr && textErr.name !== 'AbortException') {
          console.warn(`TextLayer page ${pageNo}:`, textErr);
        }
      }
    }

    pdfState.renderedPages.add(pageNo);
    if (readerState.isBilingual) {
      readerHooks.translatePdfPage(pageNo);
    }
  } catch (e) {
    console.warn(`Render page ${pageNo} warning:`, e);
  } finally {
    pdfState.renderingPages.delete(pageNo);
  }
}

export function isPdfPageDistant(pageNo) {
  return Math.abs(pageNo - pdfState.currentPage) > PDF_KEPT_PAGE_RADIUS;
}

/** Free the pixels of pages far from the reader: iOS Safari stalls, then reloads the tab, past ~384 MB of canvas. */
export function releaseDistantPdfPages() {
  document.querySelectorAll('#pdf-stream-wrapper img.pdf-page-img').forEach(img => {
    const pageNo = Number(img.parentElement.dataset.page);
    if (!isPdfPageDistant(pageNo)) return;
    img.remove();
    pdfState.renderedPages.delete(pageNo);
  });
  /** @type {NodeListOf<HTMLCanvasElement>} */ (document.querySelectorAll('#pdf-stream-wrapper canvas')).forEach(canvas => {
    const pageNo = parseInt(canvas.id.slice('pdf-canvas-'.length), 10);
    if (canvas.width === 0 || !isPdfPageDistant(pageNo)) return;
    canvas.width = 0;
    canvas.height = 0;
    pdfState.renderedPages.delete(pageNo);
    const textLayer = document.getElementById(`pdf-text-${pageNo}`);
    if (textLayer) textLayer.innerHTML = '';
    const spinner = document.getElementById(`pdf-spin-${pageNo}`);
    if (spinner) spinner.style.display = '';
  });
}

export function onPdfViewScroll() {
  if (readerState.mode !== 'pdf' || !pdfState.pageCount || pdfState.isJumping) return;
  const scrollEl = document.getElementById('reader-main-scroll');
  if (!scrollEl) return;
  const containerTop = scrollEl.getBoundingClientRect().top;
  const slots = document.querySelectorAll('.pdf-page-slot');
  if (!slots.length) return;

  let visiblePage = 1;
  for (let i = 0; i < slots.length; i++) {
    const rect = slots[i].getBoundingClientRect();
    if (rect.bottom >= containerTop + 80) {
      visiblePage = parseInt(slots[i].getAttribute('data-page'), 10) || 1;
      break;
    }
  }

  if (visiblePage === pdfState.currentPage) return;
  pdfState.currentPage = visiblePage;
  releaseDistantPdfPages();

  // Update page input
  const pInput = /** @type {HTMLInputElement | null} */ (document.getElementById('reader-pdf-page-input'));
  if (pInput && document.activeElement !== pInput) {
    pInput.value = String(visiblePage);
  }

  // Match hierarchical TOC item by page range
  const toc = readerState.toc || [];
  let activeIdx = 0;
  for (let i = 0; i < toc.length; i++) {
    if ((toc[i].page || 1) <= visiblePage) {
      activeIdx = i;
    } else {
      break;
    }
  }

  pdfState.currentTocIdx = activeIdx;
  readerState.currentChapter = toc[activeIdx] ? (toc[activeIdx].chapter_idx || 0) : 0;

  // Update top info
  const activeTitle = toc[activeIdx] ? toc[activeIdx].title : '';
  document.getElementById('reader-top-chapter-info').textContent =
    `P.${visiblePage} / ${pdfState.pageCount}${activeTitle ? ' · ' + activeTitle : ''}`;

  // Highlight in TOC sidebar
  syncPdfActiveTocNode(activeIdx);

  // Save progress with exact page
  saveReaderProgress(readerState.bookId, activeIdx, `P.${visiblePage} ${activeTitle}`, visiblePage);
}

export function syncPdfActiveTocNode(activeIdx) {
  document.querySelectorAll('.reader-toc-item').forEach((el, idx) => {
    const isAct = idx === activeIdx;
    el.classList.toggle('active', isAct);
    if (isAct) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  });
}

export function scrollToPdfPage(pageNo, smooth = true) {
  pageNo = Math.max(1, Math.min(pdfState.pageCount, parseInt(pageNo, 10) || 1));
  pdfState.currentPage = pageNo;
  releaseDistantPdfPages();
  const slot = document.getElementById(`pdf-slot-${pageNo}`);
  if (slot) {
    pdfState.isJumping = true;
    slot.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    setTimeout(() => { pdfState.isJumping = false; }, smooth ? 500 : 50);

    renderPdfPage(pageNo);
    if (pageNo + 1 <= pdfState.pageCount) renderPdfPage(pageNo + 1);
    if (pageNo - 1 >= 1) renderPdfPage(pageNo - 1);
  }
  const pInput = /** @type {HTMLInputElement | null} */ (document.getElementById('reader-pdf-page-input'));
  if (pInput) pInput.value = String(pageNo);

  // Match TOC item
  const toc = readerState.toc || [];
  let activeIdx = 0;
  for (let i = 0; i < toc.length; i++) {
    if ((toc[i].page || 1) <= pageNo) {
      activeIdx = i;
    } else {
      break;
    }
  }
  pdfState.currentTocIdx = activeIdx;
  readerState.currentChapter = toc[activeIdx] ? (toc[activeIdx].chapter_idx || 0) : 0;
  const activeTitle = toc[activeIdx] ? toc[activeIdx].title : '';
  document.getElementById('reader-top-chapter-info').textContent =
    `P.${pageNo} / ${pdfState.pageCount}${activeTitle ? ' · ' + activeTitle : ''}`;
  syncPdfActiveTocNode(activeIdx);
  saveReaderProgress(readerState.bookId, activeIdx, `P.${pageNo} ${activeTitle}`, pageNo);
}

export function adjustPdfZoom(delta) {
  if (pdfState.imageMode) {
    applyPdfScale(Math.min(PDF_IMAGE_ZOOM_MAX, Math.max(PDF_IMAGE_ZOOM_MIN, pdfState.scale + delta)));
    return;
  }
  if (!pdfState.doc) return;
  const newScale = Math.min(2.5, Math.max(0.5, pdfState.scale + delta));
  applyPdfScale(newScale);
}

export function setPdfZoomFitWidth() {
  if (pdfState.imageMode) {
    applyPdfScale(1);  // image mode's 100% is the screen width
    return;
  }
  if (!pdfState.doc) return;
  const scrollEl = document.getElementById('reader-main-scroll');
  const availW = scrollEl.clientWidth - (isMobileReader() ? 8 : 84);  // phones use the whole width
  pdfState.doc.getPage(1).then(p => {
    const vp = p.getViewport({ scale: 1.0 });
    const fitScale = Math.min(2.2, Math.max(0.3, availW / vp.width));
    applyPdfScale(fitScale);
  });
}

export function applyPdfScale(scale) {
  pdfState.scale = scale;
  document.getElementById('reader-pdf-zoom-label').textContent = `${Math.round(scale * 100)}%`;
  if (pdfState.imageMode) {
    document.getElementById('pdf-stream-wrapper').style.width = `${scale * 100}%`;
    return;
  }
  pdfState.renderedPages.clear();

  if (pdfState.textLayerTasks) {
    pdfState.textLayerTasks.forEach(task => {
      try { task.cancel(); } catch (e) {}
    });
    pdfState.textLayerTasks.clear();
  }

  pdfState.doc.getPage(1).then(p => {
    const unscaled = p.getViewport({ scale: 1.0 });
    const w = Math.round(unscaled.width * scale);
    const h = Math.round(unscaled.height * scale);
    /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('.pdf-page-slot')).forEach(slot => {
      slot.style.width = `${w}px`;
      slot.style.minHeight = `${h}px`;
      slot.style.height = `${h}px`;
    });
    /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('.pdf-trans-panel')).forEach(panel => {
      panel.style.minHeight = `${h}px`;
    });
    /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('.textLayer')).forEach(tl => {
      tl.innerHTML = '';
      tl.style.width = `${w}px`;
      tl.style.height = `${h}px`;
      tl.style.setProperty('--scale-factor', String(scale));
    });
    const cur = pdfState.currentPage || 1;
    renderPdfPage(cur);
    if (cur + 1 <= pdfState.pageCount) renderPdfPage(cur + 1);
    if (cur - 1 >= 1) renderPdfPage(cur - 1);
    alignAllPdfTransBlocks();
  });
}

// Requested only once scrolling settles near the page, so a fling does not fetch every page it passes
export function schedulePdfPageImage(pageNo) {
  setTimeout(() => loadPdfPageImage(pageNo), PDF_IMAGE_SETTLE_MS);
}

export function loadPdfPageImage(pageNo) {
  const slot = document.getElementById(`pdf-slot-${pageNo}`);
  if (!pdfState.imageMode || !slot || slot.querySelector('img') || isPdfPageDistant(pageNo)) return;
  const img = new Image();
  img.className = 'pdf-page-img';
  img.alt = '';
  img.decoding = 'async';
  img.onerror = () => {
    console.warn(`PDF page ${pageNo} image failed to load`);
    img.remove();
    pdfState.renderedPages.delete(pageNo);
  };
  img.src = `/api/books/${readerState.bookId}/pdf_page/${pageNo}?v=${pdfState.imageVersion}`;
  slot.appendChild(img);
  pdfState.renderedPages.add(pageNo);
  if (readerState.isBilingual) readerHooks.translatePdfPage(pageNo);
}

/** Blocks of a page ({text, role, top}) from the server's layout analysis; pdf.js paragraphs (no role, no
 * position) only when the server cannot read this PDF. */
export async function getPdfPageBlocks(pageNo) {
  const resp = await fetch(`/api/books/${readerState.bookId}/pdf_text/${pageNo}`);
  const result = await resp.json().catch(() => ({ ok: false }));
  if (result.ok) return result.blocks;
  if (pdfState.imageMode) throw new Error(result.error || `第 ${pageNo} 页文字读取失败`);
  const paragraphs = await readerHooks.extractPdfPageParagraphs(await pdfState.doc.getPage(pageNo));
  return paragraphs.map(text => ({ text, role: 'para', top: null }));
}

export function isPdfSideBySide(panel) {
  const overlay = document.getElementById('immersive-reader-overlay');
  return getComputedStyle(panel.parentElement).flexDirection === 'row' && !overlay.classList.contains('pdf-trans-only');
}

// Side by side, each translated block starts level with its source on the page, or right after the previous one
export function alignPdfTransBlocks(pageNo) {
  const panel = document.getElementById(`pdf-trans-${pageNo}`);
  const slot = document.getElementById(`pdf-slot-${pageNo}`);
  if (!panel || !slot) return;
  const blocks = /** @type {NodeListOf<HTMLElement>} */ (panel.querySelectorAll('.pdf-trans-para[data-top]'));
  blocks.forEach(el => { el.style.marginTop = ''; });
  if (!isPdfSideBySide(panel)) return;
  const pageHeight = slot.getBoundingClientRect().height;
  const panelTop = panel.getBoundingClientRect().top;
  blocks.forEach(el => {
    const shift = Number(el.dataset.top) * pageHeight - (el.getBoundingClientRect().top - panelTop);
    if (shift > 0) el.style.marginTop = `${shift}px`;
  });
}

export function alignAllPdfTransBlocks() {
  if (readerState.mode !== 'pdf') return;
  requestAnimationFrame(() => pdfState.pageTranslations.forEach((_, pageNo) => alignPdfTransBlocks(pageNo)));
}
