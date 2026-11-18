import {
  PDF_LONG_PRESS_MOVE_PX, PDF_LONG_PRESS_MS, PDF_RANGE_CHUNK_BYTES, appState, pdfState, readerState,
} from './state.js';
import { escapeHtml } from './utils.js';
import { hideReaderSelectionBubble, hideReaderTransPopover } from './reader_translate.js';
import { getPdfPageBlocks, onPdfViewScroll, renderPdfPage, scrollToPdfPage } from './reader_pdf_view.js';
import { readerHooks } from './reader_hooks.js';

export function cleanupPdfReader() {
  if (pdfState.observer) {
    pdfState.observer.disconnect();
    pdfState.observer = null;
  }
  if (pdfState.textLayerTasks) {
    pdfState.textLayerTasks.forEach(task => {
      try { task.cancel(); } catch (e) {}
    });
    pdfState.textLayerTasks.clear();
  }
  const scrollEl = document.getElementById('reader-main-scroll');
  if (scrollEl && pdfState.scrollHandler) {
    scrollEl.removeEventListener('scroll', pdfState.scrollHandler);
    pdfState.scrollHandler = null;
  }
  pdfState.doc = null;
  pdfState.imageMode = false;
  pdfState.pageCount = 0;
  pdfState.renderedPages.clear();
  pdfState.renderingPages.clear();
  pdfState.bilingualPages.clear();
  pdfState.pageTranslations.clear();
  pdfState.isJumping = false;
  document.getElementById('immersive-reader-overlay')?.classList.remove('pdf-bilingual-active');
  hideReaderSelectionBubble();
  hideReaderTransPopover();
}

// ==================== READING PROGRESS (stored on the server, shared by all devices) ====================

export async function initPdfReaderEngine(d, initialChapterIdx) {
  const bodyEl = document.getElementById('reader-article-body');
  const scrollEl = document.getElementById('reader-main-scroll');

  if (!window.pdfjsLib) {
    bodyEl.innerHTML = `<div style="color:var(--rose); padding:50px; text-align:center;">
      <p style="font-size:16px; margin-bottom:12px;">⚠️ PDF 引擎组件正在加载中，请稍候重试...</p>
      <a class="btn btn-primary" href="${escapeHtml(d.pdf_url)}" target="_blank">📥 在新标签页打开原生 PDF</a>
    </div>`;
    return;
  }

  pdfjsLib.GlobalWorkerOptions.workerSrc = '/vendor/pdf.worker.min.js';
  bodyEl.innerHTML = `<div style="padding:60px 20px; text-align:center; color:var(--r-muted); font-size:15px;">📄 正在建立 PDF 高保真流式画布...</div>`;

  try {
    const loadingTask = pdfjsLib.getDocument({
      url: d.pdf_url,
      enableXfa: false,
      // Fetch only the byte ranges the visible pages need: a phone must not download a 50 MB book to show page 1
      disableAutoFetch: true,
      disableStream: true,
      rangeChunkSize: PDF_RANGE_CHUNK_BYTES
    });
    const doc = await loadingTask.promise;
    pdfState.doc = doc;
    pdfState.pageCount = doc.numPages;

    document.getElementById('reader-pdf-total-badge').textContent = `/ ${doc.numPages} 页`;
    document.getElementById('reader-top-chapter-info').textContent = `共 ${doc.numPages} 页 · 目录联动阅读`;

    // Calculate auto-scale to comfortably fill reader width
    const scrollWidth = scrollEl.clientWidth - 48;
    const firstPage = await doc.getPage(1);
    const unscaledVp = firstPage.getViewport({ scale: 1.0 });
    const autoScale = Math.min(1.8, Math.max(0.65, (scrollWidth - 36) / unscaledVp.width));
    pdfState.scale = autoScale;
    document.getElementById('reader-pdf-zoom-label').textContent = `${Math.round(autoScale * 100)}%`;

    const slotW = Math.round(unscaledVp.width * autoScale);
    const slotH = Math.round(unscaledVp.height * autoScale);

    let slotsHtml = `<div id="pdf-stream-wrapper" style="display:flex; flex-direction:column; align-items:center; width:100%;">`;
    for (let p = 1; p <= doc.numPages; p++) {
      slotsHtml += `
        <div class="pdf-page-container" id="pdf-page-container-${p}" data-page="${p}">
          <div class="pdf-page-slot" id="pdf-slot-${p}" data-page="${p}" style="width:${slotW}px; min-height:${slotH}px;">
            <div id="pdf-spin-${p}" style="position:absolute; top:45%; left:50%; transform:translate(-50%, -50%); color:var(--text-dim); font-size:12px; pointer-events:none;">第 ${p} 页加载中...</div>
            <canvas id="pdf-canvas-${p}" width="0" height="0" style="display:block; width:100%; height:auto;"></canvas>
            <div class="textLayer" id="pdf-text-${p}"></div>
          </div>
          <div class="pdf-trans-panel ${readerState.isBilingual ? '' : 'hidden'}" id="pdf-trans-${p}" style="min-height:${slotH}px;">
            <div class="pdf-trans-header">
              <div style="display:flex;align-items:center;gap:6px;">
                <span style="font-weight:700;font-size:13px;">📄 第 ${p} 页译文对照</span>
                <span class="tag-xs" style="background:#0284c7;color:#fff;">双语</span>
              </div>
              <div style="display:flex;align-items:center;gap:4px;">
                <button class="trans-action-icon-btn" onclick="adjustReaderFontSize(-1)" title="减小译文字号 (A-)">A-</button>
                <span class="reader-trans-fs-badge" style="font-size:11px;color:var(--r-muted);min-width:28px;text-align:center;">${readerState.transFontSize || 15}px</span>
                <button class="trans-action-icon-btn" onclick="adjustReaderFontSize(1)" title="增大译文字号 (A+)">A+</button>
                <span style="width:1px;height:12px;background:var(--r-border);margin:0 2px;"></span>
                <button class="trans-action-icon-btn" onclick="speakPdfPage(${p})" title="朗读本页英文">🔊</button>
                <button class="trans-action-icon-btn" onclick="copyPdfPageTrans(${p}, this)" title="复制本页译文">📋</button>
              </div>
            </div>
            <div class="pdf-trans-body" id="pdf-trans-body-${p}">
              <div style="color:var(--r-muted);font-style:italic;font-size:13px;padding:24px 0;text-align:center;">⏳ 正在翻译解析本页内容...</div>
            </div>
          </div>
        </div>
      `;
    }
    slotsHtml += `</div>`;
    bodyEl.innerHTML = slotsHtml;

    watchPdfPages(scrollEl);
    const initialPage = pdfInitialPage(initialChapterIdx);
    setTimeout(() => {
      scrollToPdfPage(initialPage, false);
      renderPdfPage(initialPage);
      if (initialPage + 1 <= doc.numPages) renderPdfPage(initialPage + 1);
    }, 60);

  } catch (err) {
    bodyEl.innerHTML = `<div style="color:var(--rose); padding:40px; text-align:center;">PDF 解析失败：${escapeHtml(String(err))}</div>`;
  }
}

export function watchPdfPages(scrollEl) {
  pdfState.observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) renderPdfPage(parseInt(entry.target.getAttribute('data-page'), 10));
    });
  }, { root: scrollEl, rootMargin: '600px 0px 600px 0px' });
  document.querySelectorAll('.pdf-page-slot').forEach(el => pdfState.observer.observe(el));

  let scrollPending = false;
  pdfState.scrollHandler = () => {
    if (scrollPending) return;
    scrollPending = true;
    requestAnimationFrame(() => {
      scrollPending = false;
      onPdfViewScroll();
    });
  };
  scrollEl.addEventListener('scroll', pdfState.scrollHandler, { passive: true });
}

export function pdfInitialPage(chapterIdx) {
  if (readerState.savedPos && readerState.savedPos.pdf_page) return readerState.savedPos.pdf_page;
  const tocEntry = (readerState.toc || [])[chapterIdx];
  return (tocEntry && tocEntry.page) || 1;
}

/** Phone PDF reader on server-rendered page images. False when the server cannot render this book. */
export async function initPdfImageReader(initialChapterIdx) {
  let layout = null;
  try {
    const resp = await fetch(`/api/books/${readerState.bookId}/pdf_layout`);
    layout = await resp.json();
  } catch (e) {
    console.warn(`PDF page layout of book ${readerState.bookId} failed:`, e);
    return false;
  }
  if (!layout || !layout.ok) return false;
  pdfState.imageMode = true;
  pdfState.imageVersion = layout.version;
  pdfState.pageCount = layout.page_count;
  pdfState.scale = 1;
  document.getElementById('reader-pdf-total-badge').textContent = `/ ${layout.page_count} 页`;
  const slots = layout.sizes.map(([w, h], i) => `
    <div class="pdf-page-container" id="pdf-page-container-${i + 1}" data-page="${i + 1}">
      <div class="pdf-page-slot" id="pdf-slot-${i + 1}" data-page="${i + 1}" style="aspect-ratio:${w} / ${h};"><span class="pdf-img-num">${i + 1}</span></div>
    </div>`).join('');
  document.getElementById('reader-article-body').innerHTML =
    `<div id="pdf-stream-wrapper" class="pdf-image-mode" style="width:100%;">${slots}</div>`;
  const scrollEl = document.getElementById('reader-main-scroll');
  watchPdfPages(scrollEl);
  const initialPage = Math.min(pdfInitialPage(initialChapterIdx), layout.page_count);
  setTimeout(() => scrollToPdfPage(initialPage, false), 60);
  return true;
}

export async function openPdfPageTextSheet(pageNo) {
  readerHooks.openMobileSheet('pagetext');
  document.getElementById('m-pagetext-title').textContent = `第 ${pageNo} 页文字 · 长按可选择`;
  const body = document.getElementById('m-pagetext-body');
  body.innerHTML = '<p style="color:var(--r-muted);">正在读取…</p>';
  try {
    const blocks = await getPdfPageBlocks(pageNo);
    body.innerHTML = blocks.length
      ? blocks.map(block => `<p>${escapeHtml(block.text)}</p>`).join('')
      : '<p style="color:var(--r-muted);">本页没有可提取的文字（可能是图片或扫描页）。</p>';
  } catch (e) {
    body.innerHTML = `<p style="color:var(--rose);">${escapeHtml(String(e.message || e))}</p>`;
  }
}

// A long press on a page image opens its text; the tap that ends the press must not toggle the bars
export function initPdfLongPress() {
  const scrollEl = document.getElementById('reader-main-scroll');
  let pressTimer = null;
  let startY = 0;
  const cancel = () => { clearTimeout(pressTimer); pressTimer = null; };
  scrollEl.addEventListener('touchstart', (e) => {
    const slot = pdfState.imageMode && e.touches.length === 1 && e.target instanceof Element
      ? e.target.closest('.pdf-page-slot')
      : null;
    if (!(slot instanceof HTMLElement)) return;
    startY = e.touches[0].clientY;
    pressTimer = setTimeout(() => {
      appState.pdfLongPressAt = Date.now();
      openPdfPageTextSheet(Number(slot.dataset.page));
    }, PDF_LONG_PRESS_MS);
  }, { passive: true });
  scrollEl.addEventListener('touchmove', (e) => {
    if (pressTimer && Math.abs(e.touches[0].clientY - startY) > PDF_LONG_PRESS_MOVE_PX) cancel();
  }, { passive: true });
  scrollEl.addEventListener('touchend', cancel);
  scrollEl.addEventListener('touchcancel', cancel);
}
