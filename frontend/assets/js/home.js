// The desktop home: library and filter column on the left, a compact list (or the cover grid) in the middle, the
// selected book's detail on the right. It talks to the shelf, detail and status code through their window
// globals, so it stays out of the shelf's import cycle (tests/test_frontend_structure.py).
import {
  COVER_THUMB_WIDTH, MOBILE_QUERY, READING_STATUS_LABEL, SHELF_FILTER_GROUPS, appState, readerState, shelfFilters,
  shelfGroupValues,
} from './state.js';
import { escapeHtml, showMobileNotice, showUndoToast } from './utils.js';
import { icon } from './icons.js';
import { renderBookCard, splitBilingualTitle } from './shelf_render.js';

// The shelf, detail, reader and status functions, reached through the globals main.js sets up
const app = /** @type {any} */ (window);
const HOME_VIEW_KEY = 'lib_home_view';
// One button steps through these, in order; the label names the view showing now
const HOME_VIEWS = ['small', 'big', 'grid'];
const HOME_VIEW_LABELS = { small: '列表 · 小图', big: '列表 · 大图', grid: '封面' };
const WIDE_HOME_QUERY = '(min-width: 1100px)';
// Holding j shows each row's detail only once the cursor rests, not one fetch per row passed
const DETAIL_FOLLOW_DELAY_MS = 150;
export const MAX_BATCH_BOOKS = 200;
const LIBRARY_ITEMS = [['reading', '在读'], ['want', '想读'], ['read', '已读'], ['fav', '喜欢']];
// The library section covers the main statuses; the rarer filter groups fold under 更多筛选
const MORE_FILTER_KEYS = ['language', 'format', 'pub_period', 'source_dir'];

const homeState = {
  items: /** @type {any[]} */ ([]),
  activeIndex: -1,
  checked: /** @type {Set<number>} */ (new Set()),
  view: readHomeView(),
  detailTimer: /** @type {any} */ (null),
};

function readHomeView() {
  let saved = '';
  try { saved = localStorage.getItem(HOME_VIEW_KEY) || ''; } catch (e) { console.warn('home view not read', e); }
  return HOME_VIEWS.includes(saved) ? saved : 'small';
}

export const isDesktopHome = () => !window.matchMedia(MOBILE_QUERY).matches;
const isWideHome = () => window.matchMedia(WIDE_HOME_QUERY).matches;
const isHomeListView = () => isDesktopHome() && homeState.view !== 'grid';

/** The books on the page now, in list order. */
export function homeItems() {
  return homeState.items;
}

/** Steps the desktop home to its next view (list with small covers, list with big covers, cover grid); the choice
 * is remembered. */
export function cycleHomeView() {
  homeState.view = HOME_VIEWS[(HOME_VIEWS.indexOf(homeState.view) + 1) % HOME_VIEWS.length];
  try { localStorage.setItem(HOME_VIEW_KEY, homeState.view); } catch (e) { console.warn('home view not saved', e); }
  app.loadShelfBooks(shelfFilters.page);
}

// ---------- the list ----------
/** Fills the shelf with one page of books: list rows on the desktop home when chosen, cards otherwise.
 * opts: { showRank?, isAiOrder?, prefixHtml?, emptyHtml? }. */
export function fillShelfGrid(items, opts = {}) {
  const grid = document.getElementById('shelf-grid');
  // The cursor stays on its book when the list reloads (a status change); a different list starts without one
  const activeId = homeState.items[homeState.activeIndex]?.id;
  homeState.items = items;
  homeState.activeIndex = items.findIndex(b => b.id === activeId);
  grid.className = isHomeListView() ? 'home-list' : 'book-grid';
  document.body.classList.toggle('home-big-covers', homeState.view === 'big');
  const viewBtn = document.getElementById('home-view-btn');
  if (viewBtn) viewBtn.textContent = HOME_VIEW_LABELS[homeState.view];
  if (!items.length) {
    grid.innerHTML = (opts.prefixHtml || '') + (opts.emptyHtml || '');
  } else if (isHomeListView()) {
    grid.innerHTML = (opts.prefixHtml || '') + homeListHeadHtml() + items.map(homeRowHtml).join('');
  } else {
    grid.innerHTML = (opts.prefixHtml || '') + items.map(b => renderBookCard(b, opts)).join('');
  }
  renderHomeBatchBar();
}

function homeListHeadHtml() {
  const isPageChecked = homeState.items.length > 0 && homeState.items.every(b => homeState.checked.has(b.id));
  return `<div class="home-list-head">
      <span class="home-check-cell"><input type="checkbox" id="home-check-page" title="勾选这一页" aria-label="勾选这一页"
        ${isPageChecked ? 'checked' : ''} onchange="toggleHomePageChecks(this.checked)" /></span>
      <span></span><span>书名</span><span>进度</span><span>状态</span><span>评分</span>
    </div>`;
}

function rowCoverHtml(b) {
  if (!b.cover_path) return '<span class="home-row-cover is-empty"></span>';
  return `<img class="home-row-cover" src="${escapeHtml(b.cover_path)}?w=${COVER_THUMB_WIDTH}" alt="" loading="lazy"
    decoding="async" onerror="this.outerHTML='<span class=&quot;home-row-cover is-empty&quot;></span>'" />`;
}

function rowProgressHtml(b) {
  if (!b.last_opened_at) return '<span class="home-row-progress is-empty">—</span>';
  const pct = b.read_progress != null ? Math.round(b.read_progress * 100) : 0;
  return `<span class="home-row-progress" title="已读 ${pct}%"><span class="home-bar"><i style="width:${pct}%"></i></span>${pct}%</span>`;
}

function homeRowHtml(b, index) {
  const { main: titleMain, sub: titleSub } = splitBilingualTitle(b.clean_title || b.title);
  const status = b.reading_status || 'unread';
  const isChecked = homeState.checked.has(b.id);
  const rating = b.composite_rating ? Number(b.composite_rating).toFixed(1) : '—';
  return `<div class="home-row${index === homeState.activeIndex ? ' active' : ''}${isChecked ? ' checked' : ''}"
      data-book-id="${b.id}" data-index="${index}" onclick="selectHomeRow(${index})" ondblclick="openOnlineReader(${b.id})">
      <span class="home-check-cell" onclick="event.stopPropagation()"><input type="checkbox" ${isChecked ? 'checked' : ''}
        aria-label="勾选" onchange="toggleHomeCheck(${b.id})" /></span>
      ${rowCoverHtml(b)}
      <span class="home-row-title"><span class="book-title" title="${escapeHtml(b.title)}">${escapeHtml(titleMain)}${b.is_favorite ? `<span class="home-row-fav" title="已喜欢">${icon('heart')}</span>` : ''}</span>
        ${titleSub ? `<span class="home-row-sub" title="${escapeHtml(titleSub)}">${escapeHtml(titleSub)}</span>` : ''}
        <span class="home-row-author book-author">${escapeHtml(b.author)}</span></span>
      ${rowProgressHtml(b)}
      <span onclick="event.stopPropagation()"><button class="home-pill card-status${status === 'unread' ? '' : ' has-status'}${status === 'reading' ? ' is-reading' : ''}"
        onclick="cycleReadingStatus(${b.id}, '${status}')" title="切换阅读状态（s）">${READING_STATUS_LABEL[status] || '未读'}</button></span>
      <span class="home-row-rate">${rating}</span>
    </div>`;
}

function markActiveRow() {
  document.querySelectorAll('#shelf-grid .home-row').forEach(row => {
    row.classList.toggle('active', Number(/** @type {HTMLElement} */ (row).dataset.index) === homeState.activeIndex);
  });
}

/** A click on a row: it becomes the current row and its detail shows on the right. */
export function selectHomeRow(index) {
  homeState.activeIndex = index;
  markActiveRow();
  app.openBookModal(homeState.items[index].id);
}

/** Puts the cursor on the first row, so j/k carry on from there after a search. */
export function focusFirstHomeRow() {
  if (!homeState.items.length) return;
  homeState.activeIndex = -1;
  moveHomeRow(1);
}

function moveHomeRow(delta) {
  if (!homeState.items.length) return;
  homeState.activeIndex = Math.max(0, Math.min(homeState.items.length - 1, homeState.activeIndex + delta));
  markActiveRow();
  document.querySelector('#shelf-grid .home-row.active')?.scrollIntoView({ block: 'nearest' });
  // In a narrow window the detail covers the list, so it opens on a click only
  if (!isWideHome()) return;
  clearTimeout(homeState.detailTimer);
  const bookId = homeState.items[homeState.activeIndex].id;
  homeState.detailTimer = setTimeout(() => app.openBookModal(bookId), DETAIL_FOLLOW_DELAY_MS);
}

// ---------- the left column ----------
/** The narrow window's left column is a drawer; this opens or closes it. */
export function toggleHomeNav() {
  document.getElementById('home-nav').classList.toggle('open');
}

const isRecentSort = () => appState.shelfSortTouched
  && /** @type {HTMLSelectElement} */ (document.getElementById('shelf-sort')).value === 'opened';

/** 最近打开: every book, the last opened first. */
export function showRecentlyOpened() {
  /** @type {HTMLSelectElement} */ (document.getElementById('shelf-sort')).value = 'opened';
  appState.shelfSortTouched = true;
  app.clearShelfGroup('status');
}

function navCountHtml(count) {
  return `<span class="home-nav-count">${count != null ? Number(count).toLocaleString() : ''}</span>`;
}

function navButtonHtml(item) {
  return `<button class="home-nav-item${item.isActive ? ' active' : ''}" ${item.attrs}><span>${escapeHtml(item.label)}</span>${navCountHtml(item.count)}</button>`;
}

function filterItemAttrs(key, val) {
  return `data-key="${key}" data-val="${escapeHtml(val)}" onclick="toggleShelfFilter(this.dataset.key, this.dataset.val)"`;
}

function librarySectionHtml() {
  const picked = shelfGroupValues('status');
  const counts = appState.libraryCounts;
  const items = [
    { label: '全部', count: (counts.library || {}).total, isActive: !picked.length && !isRecentSort(), attrs: 'onclick="clearShelfGroup(\'status\')"' },
    ...LIBRARY_ITEMS.map(([val, label]) => ({ label, count: (counts.status || {})[val], isActive: picked.includes(val), attrs: filterItemAttrs('status', val) })),
    { label: '最近打开', count: null, isActive: isRecentSort(), attrs: 'onclick="showRecentlyOpened()"' },
  ];
  return `<h6 class="home-nav-title">书库</h6>${items.map(navButtonHtml).join('')}`;
}

function pendingSectionHtml() {
  const items = [
    { label: '待确认', count: (appState.libraryCounts.library || {}).pending, isActive: false, attrs: 'onclick="switchTab(\'organize\')"' },
    { label: '合并错了', count: null, isActive: false, attrs: 'onclick="switchTab(\'organize\')"' },
  ];
  return `<h6 class="home-nav-title">待处理</h6>${items.map(navButtonHtml).join('')}`;
}

function filterGroupItemsHtml(group) {
  const picked = shelfGroupValues(group.key);
  const counts = appState.libraryCounts[group.key] || {};
  return [{ label: '全部', count: null, isActive: !picked.length, attrs: `onclick="clearShelfGroup('${group.key}')"` },
    ...group.options.map(([val, label]) => ({ label, count: counts[val], isActive: picked.includes(val), attrs: filterItemAttrs(group.key, val) })),
  ].map(navButtonHtml).join('');
}

function moreFiltersHtml() {
  const groups = SHELF_FILTER_GROUPS.filter(g => MORE_FILTER_KEYS.includes(g.key) && g.options.length);
  const ratingPicked = shelfGroupValues('status').includes('has_rating');
  const isOpen = ratingPicked || groups.some(g => shelfGroupValues(g.key).length);
  const rating = navButtonHtml({ label: '有评分 / 书评', count: null, isActive: ratingPicked, attrs: filterItemAttrs('status', 'has_rating') });
  return `<details class="home-nav-more" ${isOpen ? 'open' : ''}><summary>更多筛选</summary>${rating}`
    + groups.map(g => `<div class="home-nav-sub">${escapeHtml(g.title)}</div>${filterGroupItemsHtml(g)}`).join('') + '</details>';
}

/** The left column: library statuses, what waits to be sorted out, categories, and the rarer filters folded. */
export function renderHomeNav() {
  const nav = document.getElementById('home-nav');
  if (!nav || !isDesktopHome()) return;
  const category = SHELF_FILTER_GROUPS.find(g => g.key === 'category');
  nav.innerHTML = '<div class="home-nav-head"><span>筛选</span><button class="btn" onclick="toggleHomeNav()">完成</button></div>'
    + librarySectionHtml() + pendingSectionHtml()
    + `<h6 class="home-nav-title">分类</h6>${filterGroupItemsHtml(category)}` + moreFiltersHtml();
}

// ---------- ticks and the batch bar ----------
export function toggleHomeCheck(bookId) {
  if (homeState.checked.has(bookId)) homeState.checked.delete(bookId); else homeState.checked.add(bookId);
  document.querySelector(`#shelf-grid .home-row[data-book-id="${bookId}"]`)?.classList.toggle('checked', homeState.checked.has(bookId));
  renderHomeBatchBar();
}

export function toggleHomePageChecks(isOn) {
  homeState.items.forEach(b => { if (isOn) homeState.checked.add(b.id); else homeState.checked.delete(b.id); });
  fillShelfGrid(homeState.items);
}

export function clearHomeChecks() {
  homeState.checked.clear();
  fillShelfGrid(homeState.items);
}

/** @type {[string, Object<string, any>][]} */
const BATCH_ACTIONS = [
  ['想读', { reading_status: 'want' }], ['在读', { reading_status: 'reading' }], ['已读', { reading_status: 'read' }],
  ['未读', { reading_status: 'unread' }], ['喜欢', { is_favorite: true }], ['取消喜欢', { is_favorite: false }],
];

function renderHomeBatchBar() {
  const bar = document.getElementById('home-batch-bar');
  if (!bar) return;
  const count = homeState.checked.size;
  document.body.classList.toggle('home-has-checks', count > 0);
  bar.classList.toggle('hidden', !count || !isDesktopHome());
  if (!count) return;
  const isTooMany = count > MAX_BATCH_BOOKS;
  bar.innerHTML = `<span class="home-batch-count">已选 ${count} 本${isTooMany ? `（一次最多 ${MAX_BATCH_BOOKS} 本）` : ''}</span>`
    + BATCH_ACTIONS.map(([label], i) => `<button class="btn" ${isTooMany ? 'disabled' : ''} onclick="applyHomeBatch(${i})">${label}</button>`).join('')
    + `<button class="btn home-batch-delete" ${isTooMany ? 'disabled' : ''} onclick="deleteHomeChecked()">删除</button>`
    + '<button class="btn home-batch-clear" onclick="clearHomeChecks()">取消选择</button>';
}

async function postBatchMarks(items) {
  try {
    const r = await fetch('/api/books/batch_meta', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }),
    });
    const d = await r.json();
    if (r.ok) return d.previous;
    showMobileNotice(`批量修改失败：${d.error || r.status}`);
  } catch (e) {
    console.error('batch marks failed', items.length, e);
    showMobileNotice('批量修改失败：连不上服务器');
  }
  return null;
}

function refreshAfterMarks() {
  app.loadShelfBooks(shelfFilters.page);
  app.loadStats();
}

/** Sets `marks` on every book of `ids` in one call; the toast's 撤销 puts back each book's own marks.
 * Resolves true when the server took them. */
export async function applyMarksToBooks(ids, marks, label) {
  const previous = await postBatchMarks(ids.map(id => ({ id, ...marks })));
  if (!previous) return false;
  refreshAfterMarks();
  showUndoToast(`已改 ${ids.length} 本 · ${label}`, async () => {
    if (await postBatchMarks(previous)) refreshAfterMarks();
  });
  return true;
}

/** The batch bar's 删除: the ticked books go after one confirmation (multi-file ones are asked one by one). */
export async function deleteHomeChecked() {
  const byId = new Map(homeItems().map(b => [Number(b.id), b]));
  const books = Array.from(homeState.checked).map(id => ({ id, title: (byId.get(Number(id)) || {}).title || '' }));
  const deletedIds = await app.deleteBooks(books);
  deletedIds.forEach(id => homeState.checked.delete(id));
  renderHomeBatchBar();
}

/** One batch bar button: its mark on every ticked book. */
export async function applyHomeBatch(actionIndex) {
  const [label, marks] = BATCH_ACTIONS[actionIndex];
  if (await applyMarksToBooks(Array.from(homeState.checked), marks, label)) homeState.checked.clear();
}

// ---------- keys ----------
function activeBook() {
  return homeState.items[homeState.activeIndex];
}

const HOME_KEY_ACTIONS = {
  j: () => moveHomeRow(1),
  k: () => moveHomeRow(-1),
  Enter: () => { if (activeBook()) app.openOnlineReader(activeBook().id); },
  s: () => { if (activeBook()) app.cycleReadingStatus(activeBook().id, activeBook().reading_status || 'unread'); },
  f: () => { if (activeBook()) app.toggleFavorite(activeBook().id, activeBook().is_favorite ? 0 : 1); },
  x: () => { if (activeBook()) toggleHomeCheck(activeBook().id); },
  '/': () => document.getElementById('shelf-search-input').focus(),
  Escape: () => { if (homeState.checked.size) clearHomeChecks(); },
};

function isHomeKeyTarget(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (target && (target.closest('input, textarea, select, [contenteditable="true"]'))) return false;
  // Enter on a focused button or link is that control's own click
  if (event.key === 'Enter' && target && target.closest('button, a, summary')) return false;
  return appState.currentTab === 'shelf' && isDesktopHome() && !readerState.isOpen
    && !document.getElementById('book-modal').classList.contains('open');
}

function onHomeKey(event) {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const action = HOME_KEY_ACTIONS[event.key];
  if (!action || !isHomeKeyTarget(event)) return;
  // The row keys need rows; the cover grid keeps only / to reach the search box
  if (!isHomeListView() && event.key !== '/') return;
  event.preventDefault();
  action();
}

// ---------- column widths ----------
const PANE_WIDTH_KEY = 'lib_home_pane_widths';
const PANE_LIMITS = { nav: { min: 160, max: 380, cssVar: '--home-nav-w' }, detail: { min: 260, max: 620, cssVar: '--home-detail-w' } };
// The list in the middle never gets narrower than this, whatever the two sides ask for
const MIN_LIST_WIDTH_PX = 420;

function readPaneWidths() {
  try { return JSON.parse(localStorage.getItem(PANE_WIDTH_KEY) || '{}'); } catch (e) { return {}; }
}

function setPaneWidth(layout, pane, width) {
  const limits = PANE_LIMITS[pane];
  const other = PANE_LIMITS[pane === 'nav' ? 'detail' : 'nav'];
  const otherWidth = parseFloat(getComputedStyle(layout).getPropertyValue(other.cssVar)) || 0;
  const room = layout.clientWidth - otherWidth - MIN_LIST_WIDTH_PX;
  const clamped = Math.round(Math.max(limits.min, Math.min(limits.max, room, width)));
  layout.style.setProperty(limits.cssVar, `${clamped}px`);
  return clamped;
}

function savePaneWidth(pane, width) {
  try {
    localStorage.setItem(PANE_WIDTH_KEY, JSON.stringify({ ...readPaneWidths(), [pane]: width }));
  } catch (e) {
    console.warn('pane width not saved', pane, e);
  }
}

function dragPane(handle, layout, downEvent) {
  const pane = handle.dataset.pane;
  const box = layout.getBoundingClientRect();
  let width = 0;
  const onMove = (event) => {
    width = setPaneWidth(layout, pane, pane === 'nav' ? event.clientX - box.left : box.right - event.clientX);
  };
  const onUp = () => {
    handle.classList.remove('dragging');
    document.body.classList.remove('home-resizing');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    if (width) savePaneWidth(pane, width);
  };
  downEvent.preventDefault();
  handle.classList.add('dragging');
  document.body.classList.add('home-resizing');
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

/** The column borders are handles: drag to set the left column's or the detail's width, double-click to reset. */
function initPaneResizers() {
  const layout = /** @type {HTMLElement} */ (document.querySelector('.home-layout'));
  Object.entries(readPaneWidths()).forEach(([pane, width]) => {
    if (PANE_LIMITS[pane]) layout.style.setProperty(PANE_LIMITS[pane].cssVar, `${Number(width)}px`);
  });
  layout.querySelectorAll('.home-resizer').forEach(el => {
    const handle = /** @type {HTMLElement} */ (el);
    handle.addEventListener('pointerdown', event => dragPane(handle, layout, event));
    handle.addEventListener('dblclick', () => {
      layout.style.removeProperty(PANE_LIMITS[handle.dataset.pane].cssVar);
      savePaneWidth(handle.dataset.pane, undefined);
    });
  });
}

/** Desktop only: the search box moves into the top bar, where it serves as the command bar on every page, and the
 * home's column borders become drag handles. */
export function initDesktopHome() {
  document.addEventListener('keydown', onHomeKey);
  if (!isDesktopHome()) return;
  document.getElementById('top-cmd-slot').appendChild(document.querySelector('#view-shelf .search-input-wrap'));
  initPaneResizers();
}
