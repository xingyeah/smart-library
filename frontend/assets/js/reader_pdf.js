import {
  appState, readerState, pdfState, isMobileReader,
  PDF_MAX_PIXEL_RATIO, PDF_KEPT_PAGE_RADIUS, PDF_RANGE_CHUNK_BYTES,
  PDF_IMAGE_SETTLE_MS, PDF_IMAGE_ZOOM_MIN, PDF_IMAGE_ZOOM_MAX,
  PDF_LONG_PRESS_MS, PDF_LONG_PRESS_MOVE_PX, PDF_LONG_PRESS_TAP_GUARD_MS,
} from './state.js';
import { escapeHtml, showMobileNotice } from './utils.js';
import { adjustReaderFontSize, openMobileSheet, saveReaderProgress } from './reader.js';
import {
  applyPdfSplitShare, savedPdfSplitShare, ensurePdfTransPanels,
  extractPdfPageParagraphs, hideReaderSelectionBubble, hideReaderTransPopover,
  translatePdfPage, speakPdfPage, copyPdfPageTrans,
} from './reader_translate.js';

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
  if (readerState.isBilingual) translatePdfPage(pageNo);
}

/** Blocks of a page ({text, role, top}) from the server's layout analysis; pdf.js paragraphs (no role, no
 * position) only when the server cannot read this PDF. */
export async function getPdfPageBlocks(pageNo) {
  const resp = await fetch(`/api/books/${readerState.bookId}/pdf_text/${pageNo}`);
  const result = await resp.json().catch(() => ({ ok: false }));
  if (result.ok) return result.blocks;
  if (pdfState.imageMode) throw new Error(result.error || `第 ${pageNo} 页文字读取失败`);
  const paragraphs = await extractPdfPageParagraphs(await pdfState.doc.getPage(pageNo));
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
  const blocks = panel.querySelectorAll('.pdf-trans-para[data-top]');
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

export async function openPdfPageTextSheet(pageNo) {
  openMobileSheet('pagetext');
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
    const slot = pdfState.imageMode && e.touches.length === 1 && e.target.closest('.pdf-page-slot');
    if (!slot) return;
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
    const canvas = document.getElementById(`pdf-canvas-${pageNo}`);
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
      translatePdfPage(pageNo);
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
  document.querySelectorAll('#pdf-stream-wrapper canvas').forEach(canvas => {
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
  const pInput = document.getElementById('reader-pdf-page-input');
  if (pInput && document.activeElement !== pInput) {
    pInput.value = visiblePage;
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
  const pInput = document.getElementById('reader-pdf-page-input');
  if (pInput) pInput.value = pageNo;

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
    document.querySelectorAll('.pdf-page-slot').forEach(slot => {
      slot.style.width = `${w}px`;
      slot.style.minHeight = `${h}px`;
      slot.style.height = `${h}px`;
    });
    document.querySelectorAll('.pdf-trans-panel').forEach(panel => {
      panel.style.minHeight = `${h}px`;
    });
    document.querySelectorAll('.textLayer').forEach(tl => {
      tl.innerHTML = '';
      tl.style.width = `${w}px`;
      tl.style.height = `${h}px`;
      tl.style.setProperty('--scale-factor', scale);
    });
    const cur = pdfState.currentPage || 1;
    renderPdfPage(cur);
    if (cur + 1 <= pdfState.pageCount) renderPdfPage(cur + 1);
    if (cur - 1 >= 1) renderPdfPage(cur - 1);
    alignAllPdfTransBlocks();
  });
}

