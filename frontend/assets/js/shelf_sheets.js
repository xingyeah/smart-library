import { aiRerankOn } from './api.js';
import { loadShelfBooks, shelfChipHtml, shelfGroupValues } from './shelf.js';
import { SHEET_CLOSE_DRAG_PX, SHELF_FILTER_GROUPS, SORT_SHORT_LABELS, appState } from './state.js';

// ---------- bottom sheets ----------
export function openShelfSheet(kind, section) {
  appState.shelfSheetKind = kind;
  renderShelfSheet();
  document.getElementById('shelf-sheet').classList.add('open');
  document.getElementById('shelf-sheet-backdrop').classList.add('open');
  document.documentElement.classList.add('shelf-sheet-lock');
  const target = section && document.getElementById(`shelf-group-${section}`);
  const body = document.getElementById('shelf-sheet-body');
  body.scrollTop = target ? target.offsetTop - body.offsetTop : 0;
}

export function closeShelfSheet() {
  appState.shelfSheetKind = '';
  document.getElementById('shelf-sheet').classList.remove('open');
  document.getElementById('shelf-sheet-backdrop').classList.remove('open');
  document.documentElement.classList.remove('shelf-sheet-lock');
}

export function renderShelfSheet() {
  const body = document.getElementById('shelf-sheet-body');
  const keepScroll = body.scrollTop;
  if (appState.shelfSheetKind === 'sort') renderSortSheet(); else renderFilterSheet();
  body.scrollTop = keepScroll;
}

export function renderFilterSheet() {
  document.getElementById('shelf-sheet-head').innerHTML =
    '<span>筛选</span><button onclick="resetShelfFilters()">重置</button>';
  document.getElementById('shelf-sheet-body').innerHTML = SHELF_FILTER_GROUPS.map(g => {
    const picked = shelfGroupValues(g.key);
    const chips = g.options.map(([v, label]) => shelfChipHtml(g.key, v, label, picked.includes(v))).join('');
    return `<div class="shelf-sheet-group" id="shelf-group-${g.key}"><div class="shelf-sheet-group-title">${g.title}</div>`
      + `<div class="shelf-sheet-chips">${chips}</div></div>`;
  }).join('');
  document.getElementById('shelf-sheet-foot').innerHTML = `
    <label><input type="checkbox" id="m-ai-toggle" ${aiRerankOn() ? 'checked' : ''} onchange="setAiRerank(this.checked)" /> AI 精排</label>
    <button class="btn btn-primary" onclick="closeShelfSheet()">完成 · ${appState.shelfLastTotal.toLocaleString()} 本</button>`;
}

export function renderSortSheet() {
  const current = /** @type {HTMLSelectElement} */ (document.getElementById('shelf-sort')).value;
  document.getElementById('shelf-sheet-head').innerHTML = '<span>排序</span>';
  document.getElementById('shelf-sheet-body').innerHTML = Object.entries(SORT_SHORT_LABELS).map(([v, label]) =>
    `<button class="shelf-sort-option ${v === current ? 'active' : ''}" onclick="pickShelfSort('${v}')">`
    + `<span>${label}</span><span>${v === current ? '✓' : ''}</span></button>`).join('');
  document.getElementById('shelf-sheet-foot').innerHTML = '';
}

export function pickShelfSort(value) {
  /** @type {HTMLSelectElement} */ (document.getElementById('shelf-sort')).value = value;
  appState.shelfSortTouched = true;
  closeShelfSheet();
  loadShelfBooks(1, { scrollToTop: true });
}

// Dragging the handle, title or footer down closes the sheet; the list in the middle scrolls instead
export function initShelfSheetDrag() {
  const sheet = document.getElementById('shelf-sheet');
  let startY = null;
  sheet.addEventListener('touchstart', e => {
    if (e.target instanceof Element && e.target.closest('.shelf-sheet-body')) return;
    startY = e.touches[0].clientY;
    sheet.style.transition = 'none';
  }, { passive: true });
  sheet.addEventListener('touchmove', e => {
    if (startY === null) return;
    sheet.style.transform = `translateY(${Math.max(0, e.touches[0].clientY - startY)}px)`;
  }, { passive: true });
  sheet.addEventListener('touchend', e => {
    if (startY === null) return;
    const dragged = e.changedTouches[0].clientY - startY;
    startY = null;
    sheet.style.transition = '';
    sheet.style.transform = '';
    if (dragged > SHEET_CLOSE_DRAG_PX) closeShelfSheet();
  });
}
