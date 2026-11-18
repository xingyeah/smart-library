import {
  alignAllPdfTransBlocks, alignPdfTransBlocks, applyPdfScale, getPdfPageBlocks, scrollToPdfPage,
} from './reader_pdf_view.js';
import {
  PDF_SPLIT_CHROME_PX, PDF_SPLIT_KEY, PDF_SPLIT_MAX, PDF_SPLIT_MIN, getTransEngine, noteTransEngine,
  speakEnglishText,
} from './reader_translate.js';
import { pdfState, readerState } from './state.js';
import { escapeHtml } from './utils.js';

export async function toggleBilingualMode() {
  if (readerState.mode === 'pdf') {
    await togglePdfBilingualMode();
  } else {
    await toggleHtmlBilingualMode();
  }
}

export function savedPdfSplitShare() {
  try {
    const share = Number(localStorage.getItem(PDF_SPLIT_KEY));
    return share >= PDF_SPLIT_MIN && share <= PDF_SPLIT_MAX ? share : null;
  } catch (e) {
    console.warn('Could not read the PDF split setting:', e);
    return null;
  }
}

export function setPdfTransWidth(px) {
  const overlay = document.getElementById('immersive-reader-overlay');
  overlay.style.setProperty('--pdf-trans-w', `${px}px`);
  overlay.style.setProperty('--pdf-trans-max', 'none');
}

// The translation takes `share` of the width; the page is zoomed to fill the rest
export async function applyPdfSplitShare(share) {
  const wrapper = document.getElementById('pdf-stream-wrapper');
  if (!wrapper || !pdfState.doc) return;
  const width = wrapper.clientWidth;
  const transWidth = Math.round(width * share);
  setPdfTransWidth(transWidth);
  const page = await pdfState.doc.getPage(1);
  const pageWidth = page.getViewport({ scale: 1.0 }).width;
  applyPdfScale(Math.max(0.3, (width - transWidth - PDF_SPLIT_CHROME_PX) / pageWidth));
}

export function resetPdfSplit() {
  try { localStorage.removeItem(PDF_SPLIT_KEY); } catch (e) { console.warn('Could not reset the PDF split:', e); }
  const overlay = document.getElementById('immersive-reader-overlay');
  overlay.style.removeProperty('--pdf-trans-w');
  overlay.style.removeProperty('--pdf-trans-max');
  if (readerState.isBilingual) {
    togglePdfBilingualMode().then(togglePdfBilingualMode);  // off and on again: the default automatic fit
  }
}

// While dragging only the panel width changes; the page is redrawn once, on release
export function initPdfSplitDrag() {
  const scrollEl = document.getElementById('reader-main-scroll');
  scrollEl.addEventListener('dblclick', (e) => {
    if (e.target instanceof Element && e.target.classList.contains('pdf-split-handle')) resetPdfSplit();
  });
  scrollEl.addEventListener('mousedown', (e) => {
    if (!(e.target instanceof Element) || !e.target.classList.contains('pdf-split-handle')) return;
    e.preventDefault();
    const wrapper = document.getElementById('pdf-stream-wrapper');
    const box = wrapper.getBoundingClientRect();
    let share = savedPdfSplitShare() || 0.4;
    const onMove = (ev) => {
      share = Math.min(PDF_SPLIT_MAX, Math.max(PDF_SPLIT_MIN, (box.right - ev.clientX) / box.width));
      setPdfTransWidth(Math.round(box.width * share));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('pdf-splitting');
      try { localStorage.setItem(PDF_SPLIT_KEY, share.toFixed(3)); } catch (err) { console.warn('Could not save the PDF split:', err); }
      applyPdfSplitShare(share);
    };
    document.body.classList.add('pdf-splitting');
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

export function ensurePdfTransPanels() {
  const slots = document.querySelectorAll('.pdf-page-slot');
  slots.forEach(slot => {
    const p = parseInt(slot.getAttribute('data-page'), 10);
    if (!p) return;
    let container = slot.parentElement;
    if (!container || !container.classList.contains('pdf-page-container')) {
      container = document.createElement('div');
      container.className = 'pdf-page-container';
      container.id = `pdf-page-container-${p}`;
      container.setAttribute('data-page', String(p));
      slot.parentNode.insertBefore(container, slot);
      container.appendChild(slot);
    }
    if (!document.getElementById(`pdf-trans-${p}`)) {
      const panel = document.createElement('div');
      panel.className = `pdf-trans-panel ${readerState.isBilingual ? '' : 'hidden'}`;
      panel.id = `pdf-trans-${p}`;
      panel.style.minHeight = pdfState.imageMode ? '' : (/** @type {HTMLElement} */ (slot).style.minHeight || '600px');
      panel.innerHTML = `
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
      `;
      container.appendChild(panel);
    }
    if (!container.querySelector('.pdf-split-handle')) {
      const handle = document.createElement('div');
      handle.className = 'pdf-split-handle';
      handle.title = '拖动调整原文与译文的宽度，双击恢复默认';
      container.insertBefore(handle, document.getElementById(`pdf-trans-${p}`));
    }
  });
}

// PDF only: hide the original page and keep the translation panel, remembered across books
export function applyTransOnly() {
  const bilingualPdf = readerState.mode === 'pdf' && readerState.isBilingual;
  document.getElementById('immersive-reader-overlay')?.classList.toggle('pdf-trans-only', bilingualPdf && readerState.transOnly);
  const btn = document.getElementById('reader-transonly-btn');
  if (!btn) return;
  btn.style.display = bilingualPdf ? '' : 'none';
  btn.classList.toggle('active', readerState.transOnly);
  btn.textContent = readerState.transOnly ? '📄 显示原页' : '📃 仅译文';
}

export function toggleTransOnly() {
  readerState.transOnly = !readerState.transOnly;
  try { localStorage.setItem('lib_reader_trans_only', readerState.transOnly ? '1' : '0'); } catch (e) {}
  const page = pdfState.currentPage || 1;
  applyTransOnly();
  alignAllPdfTransBlocks();
  scrollToPdfPage(page, false);  // layout height changes; stay on the same page
}

export async function togglePdfBilingualMode() {
  const overlay = document.getElementById('immersive-reader-overlay');
  const btn = document.getElementById('reader-bilingual-btn');
  const drawerBtn = document.getElementById('drawer-bilingual-btn');
  const scrollEl = document.getElementById('reader-main-scroll');

  if (readerState.isBilingual) {
    // Turn OFF PDF bilingual mode
    readerState.isBilingual = false;
    overlay.classList.remove('pdf-bilingual-active');
    document.querySelectorAll('.pdf-trans-panel').forEach(p => p.classList.add('hidden'));
    if (btn) { btn.textContent = '📖 双语对照'; btn.classList.remove('active'); }
    if (drawerBtn) { drawerBtn.textContent = '开启对照'; drawerBtn.className = 'btn btn-emerald'; }

    applyTransOnly();
    // Restore scale if we saved it
    if (pdfState.preBilingualScale) {
      applyPdfScale(pdfState.preBilingualScale);
      delete pdfState.preBilingualScale;
    }
    return;
  }

  // Ensure all trans panels exist in DOM
  ensurePdfTransPanels();

  // Turn ON PDF bilingual mode
  readerState.isBilingual = true;
  pdfState.preBilingualScale = pdfState.scale;
  overlay.classList.add('pdf-bilingual-active');

  // Smart auto-scale: adjust zoom so PDF canvas and translation panel fit comfortably side-by-side
  if (pdfState.doc && savedPdfSplitShare()) {
    applyPdfSplitShare(savedPdfSplitShare());
  } else if (pdfState.doc) {
    try {
      const firstPage = await pdfState.doc.getPage(1);
      const unscaledVp = firstPage.getViewport({ scale: 1.0 });
      const availW = scrollEl ? (scrollEl.clientWidth - 48) : window.innerWidth;
      if (availW >= 880) {
        const maxSlotW = Math.max(340, availW - 542);  // panel 480 + gaps + the split handle
        const fitScale = Math.min(1.35, Math.max(0.45, maxSlotW / unscaledVp.width));
        applyPdfScale(fitScale);
      }
    } catch (e) {}
  }

  document.querySelectorAll('.pdf-trans-panel').forEach(p => p.classList.remove('hidden'));
  if (btn) { btn.textContent = '📖 关闭对照'; btn.classList.add('active'); }
  if (drawerBtn) { drawerBtn.textContent = '关闭对照'; drawerBtn.className = 'btn'; }
  applyTransOnly();

  alignAllPdfTransBlocks();  // pages translated before the panels were hidden
  // Immediately translate currently visible page and adjacent pages
  const cur = pdfState.currentPage || 1;
  translatePdfPage(cur);
  if (cur + 1 <= pdfState.pageCount) translatePdfPage(cur + 1);
}

export async function extractPdfPageParagraphs(page) {
  const tc = await page.getTextContent();
  if (!tc || !tc.items || !tc.items.length) return [];

  // Group items by vertical line position (Y coord)
  const linesMap = new Map();
  for (const it of tc.items) {
    if (!it.str) continue;
    const y = Math.round(it.transform[5] / 3) * 3;
    if (!linesMap.has(y)) linesMap.set(y, []);
    linesMap.get(y).push({ x: it.transform[4], str: it.str });
  }

  // Sort lines top to bottom
  const sortedYs = Array.from(linesMap.keys()).sort((a, b) => b - a);
  const rawLines = [];
  for (const y of sortedYs) {
    const items = linesMap.get(y).sort((a, b) => a.x - b.x);
    let lineStr = '';
    for (const it of items) {
      if (lineStr && !lineStr.endsWith(' ') && !it.str.startsWith(' ')) lineStr += ' ';
      lineStr += it.str;
    }
    lineStr = lineStr.trim();
    if (lineStr) rawLines.push(lineStr);
  }

  // Reconstruct paragraphs and handle line wrap & hyphenation
  const paragraphs = [];
  let curPara = '';
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i];
    if (curPara.endsWith('-') && /^[a-zA-Z]/.test(line)) {
      curPara = curPara.slice(0, -1) + line;
    } else if (curPara) {
      if ((curPara.endsWith('.') || curPara.endsWith('!') || curPara.endsWith('?')) && /^[A-Z0-9\u4e00-\u9fa5]/.test(line)) {
        paragraphs.push(curPara.trim());
        curPara = line;
      } else {
        curPara += ' ' + line;
      }
    } else {
      curPara = line;
    }
  }
  if (curPara.trim()) paragraphs.push(curPara.trim());

  return paragraphs.filter(p => p.length >= 6 && /[a-zA-Z\u4e00-\u9fa5]/.test(p));
}

export async function translatePdfPage(pageNo) {
  if (!pdfState.pageCount || pageNo < 1 || pageNo > pdfState.pageCount) return;
  if (pdfState.bilingualPages.has(pageNo)) return;
  pdfState.bilingualPages.add(pageNo);

  let panelBody = document.getElementById(`pdf-trans-body-${pageNo}`);
  if (!panelBody) {
    ensurePdfTransPanels();
    panelBody = document.getElementById(`pdf-trans-body-${pageNo}`);
  }
  if (!panelBody) return;

  panelBody.innerHTML = `<div style="color:var(--r-muted); font-style:italic; font-size:13px; padding:24px 0; text-align:center;">⏳ 正在提取解析第 ${pageNo} 页文本并请求翻译...</div>`;

  try {
    const blocks = await getPdfPageBlocks(pageNo);
    const paras = blocks.map(block => block.text);

    if (!paras.length) {
      panelBody.innerHTML = `<div style="color:var(--r-muted); font-size:12.5px; padding:24px 0; text-align:center;">（本页未提取到有效文本，可能是插图或扫描件）</div>`;
      return;
    }

    const resp = await fetch('/api/translate/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paragraphs: paras, book_id: readerState.bookId, engine: getTransEngine() }),
    });
    const data = await resp.json();
    if (!data.ok || !data.results) throw new Error(data.error || '翻译服务响应异常');

    pdfState.pageTranslations.set(pageNo, data.results);
    noteTransEngine((data.results.find(r => r.engine) || {}).engine);

    panelBody.innerHTML = data.results.map((item, idx) => {
      const block = blocks[idx] || {};
      const top = typeof block.top === 'number' ? ` data-top="${block.top}"` : '';
      return `
      <div class="pdf-trans-para pdf-role-${block.role || 'para'}" id="pdf-trans-p-${pageNo}-${idx}"${top}>
        <div class="pdf-trans-orig">${escapeHtml(item.original)}</div>
        <div class="pdf-trans-dest">${escapeHtml(item.translation || '（暂无译文）')}</div>
      </div>`;
    }).join('');
    alignPdfTransBlocks(pageNo);

  } catch (err) {
    pdfState.bilingualPages.delete(pageNo); // Allow retry
    panelBody.innerHTML = `<div style="color:var(--rose); font-size:12.5px; padding:16px 0;">⚠️ 翻译第 ${pageNo} 页失败：${escapeHtml(String(err.message || err))}</div>`;
  }
}

export function speakPdfPage(pageNo) {
  const transList = pdfState.pageTranslations.get(pageNo);
  if (!transList || !transList.length) return;
  const fullEn = transList.map(t => t.original).join(' ');
  speakEnglishText(fullEn);
}

export async function copyPdfPageTrans(pageNo, btn = null) {
  const transList = pdfState.pageTranslations.get(pageNo);
  if (!transList || !transList.length) return;
  const fullZh = transList.map(t => t.translation).filter(Boolean).join('\n\n');
  try {
    await navigator.clipboard.writeText(fullZh);
    if (btn) {
      btn.textContent = '✅';
      setTimeout(() => { btn.textContent = '📋'; }, 800);
    }
  } catch (e) {}
}

export async function toggleHtmlBilingualMode() {
  const bodyEl = document.getElementById('reader-article-body');
  const btn = document.getElementById('reader-bilingual-btn');
  const drawerBtn = document.getElementById('drawer-bilingual-btn');

  if (readerState.isBilingual) {
    // Turn OFF: remove all translation blocks
    bodyEl.querySelectorAll('.bilingual-trans-block').forEach(el => el.remove());
    readerState.isBilingual = false;
    if (btn) {
      btn.textContent = '📖 双语对照';
      btn.classList.remove('active');
    }
    if (drawerBtn) {
      drawerBtn.textContent = '开启对照';
      drawerBtn.className = 'btn btn-emerald';
    }
    return;
  }

  // Turn ON
  readerState.isBilingual = true;
  if (btn) {
    btn.textContent = '📖 关闭对照';
    btn.classList.add('active');
  }
  if (drawerBtn) {
    drawerBtn.textContent = '关闭对照';
    drawerBtn.className = 'btn';
  }

  // Collect paragraphs
  const pEls = Array.from(bodyEl.querySelectorAll('p, div, li, blockquote')).filter(el => {
    const t = el.textContent.trim();
    return t.length >= 8 && /[a-zA-Z]{3,}/.test(t) && !el.querySelector('p, div');
  });

  if (!pEls.length) return;

  const batches = [];
  let curBatch = [];
  for (const p of pEls) {
    if (p.nextElementSibling && p.nextElementSibling.classList.contains('bilingual-trans-block')) continue;
    const block = document.createElement('div');
    block.className = 'bilingual-trans-block loading';
    block.textContent = '⏳ 正在翻译段落...';
    p.after(block);
    curBatch.push({ el: block, text: p.textContent.trim() });
    if (curBatch.length >= 8) {
      batches.push(curBatch);
      curBatch = [];
    }
  }
  if (curBatch.length) batches.push(curBatch);

  for (const batch of batches) {
    if (!readerState.isBilingual) break;
    try {
      const resp = await fetch('/api/translate/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paragraphs: batch.map(b => b.text), book_id: readerState.bookId, engine: getTransEngine() }),
      });
      const d = await resp.json();
      if (d.ok && d.results) {
        noteTransEngine((d.results.find(r => r.engine) || {}).engine);
        d.results.forEach((resItem, idx) => {
          const target = batch[idx];
          if (target && target.el) {
            target.el.classList.remove('loading');
            target.el.textContent = resItem.translation || '（未能获取译文）';
          }
        });
      }
    } catch (err) {
      batch.forEach(b => {
        if (b.el) {
          b.el.classList.remove('loading');
          b.el.textContent = '⚠️ 翻译超时，请稍候重试';
        }
      });
    }
  }
}
