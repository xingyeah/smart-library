// The desktop home: filter column on the left, a compact list (or the cover grid) in the middle, the selected
// book's detail on the right. It talks to the shelf, detail and status code through their window globals, so it
// stays out of the shelf's import cycle (tests/test_frontend_structure.py).
import { MOBILE_QUERY, READING_STATUS_LABEL, SHELF_FILTER_GROUPS, appState, readerState, shelfFilters, shelfGroupValues } from './state.js';
import { escapeHtml, showMobileNotice, showUndoToast } from './utils.js';
import { icon } from './icons.js';
import { renderBookCard, splitBilingualTitle } from './shelf_render.js';

// The shelf, detail, reader and status functions, reached through the globals main.js sets up
const app = /** @type {any} */ (window);
const HOME_VIEW_KEY = 'lib_home_view';
const WIDE_HOME_QUERY = '(min-width: 1100px)';
const COMMAND_BOOK_MATCHES = 3;
const COMMAND_JUMP_MATCHES = 3;
// Holding j shows each row's detail only once the cursor rests, not one fetch per row passed
const DETAIL_FOLLOW_DELAY_MS = 150;
const MAX_BATCH_BOOKS = 200;
// Status and category are what the list is narrowed by most; the rest fold away until something is picked in them
const OPEN_NAV_GROUPS = ['status', 'category'];
const JUMP_TABS = [['interest', '推荐'], ['organize', '整理']];

const homeState = {
  items: /** @type {any[]} */ ([]),
  activeIndex: -1,
  checked: /** @type {Set<number>} */ (new Set()),
  view: readHomeView(),
  suggestions: /** @type {{label: string, hint: string, run: () => void}[]} */ ([]),
  suggestIndex: -1,
  detailTimer: /** @type {any} */ (null),
};

function readHomeView() {
  try { return localStorage.getItem(HOME_VIEW_KEY) === 'grid' ? 'grid' : 'list'; } catch (e) { return 'list'; }
}

const isDesktopHome = () => !window.matchMedia(MOBILE_QUERY).matches;
const isWideHome = () => window.matchMedia(WIDE_HOME_QUERY).matches;
const isHomeListView = () => isDesktopHome() && homeState.view === 'list';

/** Switches the desktop home between the compact list and the cover grid; the choice is remembered. */
export function toggleHomeView() {
  homeState.view = homeState.view === 'list' ? 'grid' : 'list';
  try { localStorage.setItem(HOME_VIEW_KEY, homeState.view); } catch (e) { console.warn('home view not saved', e); }
  app.loadShelfBooks(shelfFilters.page);
}

// ---------- the list ----------
/** Fills the shelf with one page of books: list rows on the desktop home when chosen, cards otherwise.
 * opts: { showRank?, isAiOrder?, prefixHtml?, emptyHtml? }. */
export function fillShelfGrid(items, opts = {}) {
  const grid = document.getElementById('shelf-grid');
  homeState.items = items;
  homeState.activeIndex = Math.min(homeState.activeIndex, items.length - 1);
  grid.className = isHomeListView() ? 'home-list' : 'book-grid';
  const viewBtn = document.getElementById('home-view-btn');
  if (viewBtn) viewBtn.textContent = homeState.view === 'list' ? '封面' : '列表';
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
      <label class="home-check-cell" title="勾选这一页的书"><input type="checkbox" id="home-check-page"
        ${isPageChecked ? 'checked' : ''} onchange="toggleHomePageChecks(this.checked)" />选本页</label>
      <span>书名</span><span>作者</span><span>分类</span><span>进度 / 年份</span><span></span>
    </div>`;
}

function homeRowHtml(b, index) {
  const { main, sub } = splitBilingualTitle(b.clean_title || b.title);
  const status = b.reading_status || 'unread';
  const isChecked = homeState.checked.has(b.id);
  const progress = b.last_opened_at && b.read_progress != null ? `已读 ${Math.round(b.read_progress * 100)}%` : '';
  const rating = b.composite_rating ? `<span class="home-row-rate">${icon('star')}${Number(b.composite_rating).toFixed(1)}</span>` : '';
  return `<div class="home-row${index === homeState.activeIndex ? ' active' : ''}${isChecked ? ' checked' : ''}"
      data-book-id="${b.id}" data-index="${index}" onclick="selectHomeRow(${index})" ondblclick="openOnlineReader(${b.id})">
      <span class="home-check-cell" onclick="event.stopPropagation()"><input type="checkbox" ${isChecked ? 'checked' : ''}
        aria-label="勾选" onchange="toggleHomeCheck(${b.id})" /></span>
      <span class="home-row-title"><span class="book-title" title="${escapeHtml(b.title)}">${escapeHtml(main)}</span>
        ${sub ? `<span class="home-row-sub">${escapeHtml(sub)}</span>` : ''}</span>
      <span class="home-row-author book-author">${escapeHtml(b.author)}</span>
      <span class="home-row-cat">${escapeHtml(b.category || '')}</span>
      <span class="home-row-year">${progress || escapeHtml(b.pub_year || '')}${rating}</span>
      <span class="card-actions" onclick="event.stopPropagation()">
        <button class="btn-icon card-fav ${b.is_favorite ? 'fav-active' : ''}" onclick="toggleFavorite(${b.id}, ${b.is_favorite ? 0 : 1})"
          title="${b.is_favorite ? '已喜欢' : '喜欢'}" aria-label="${b.is_favorite ? '已喜欢' : '喜欢'}">${icon('heart')}</button>
        <button class="btn-icon card-status${status === 'unread' ? '' : ' has-status'}" onclick="cycleReadingStatus(${b.id}, '${status}')"
          title="切换阅读状态">${READING_STATUS_LABEL[status] || '未读'}</button>
      </span>
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

/** The narrow window's filter column is a drawer; this opens or closes it. */
export function toggleHomeNav() {
  document.getElementById('home-nav').classList.toggle('open');
}

// ---------- the filter column ----------
function navItemHtml(group, option, picked) {
  const [val, label] = option;
  const count = (appState.libraryCounts[group.key] || {})[val];
  return `<button class="home-nav-item${picked.includes(val) ? ' active' : ''}" data-key="${group.key}" data-val="${escapeHtml(val)}"
      onclick="toggleShelfFilter(this.dataset.key, this.dataset.val)"><span>${escapeHtml(label)}</span>
      <span class="home-nav-count">${count != null ? Number(count).toLocaleString() : ''}</span></button>`;
}

function navGroupHtml(group) {
  const picked = shelfGroupValues(group.key);
  const items = `<button class="home-nav-item${picked.length ? '' : ' active'}" onclick="clearShelfGroup('${group.key}')"><span>全部</span></button>`
    + group.options.map(option => navItemHtml(group, option, picked)).join('');
  const isOpen = OPEN_NAV_GROUPS.includes(group.key) || picked.length > 0;
  return `<details class="home-nav-group" ${isOpen ? 'open' : ''}><summary>${escapeHtml(group.title)}</summary>${items}</details>`;
}

/** The left column: every filter group with its library-wide counts, the picked values marked. */
export function renderHomeNav() {
  const nav = document.getElementById('home-nav');
  if (!nav || !isDesktopHome()) return;
  nav.innerHTML = '<div class="home-nav-head"><span>筛选</span><button class="btn" onclick="toggleHomeNav()">完成</button></div>'
    + SHELF_FILTER_GROUPS.filter(g => g.options.length).map(navGroupHtml).join('');
}

// ---------- the command bar ----------
function jumpSuggestions(query) {
  const tabs = JUMP_TABS.filter(([, label]) => label.includes(query) || query.includes(label))
    .map(([tab, label]) => ({ label: `去「${label}」`, hint: '页面', run: () => app.switchTab(tab) }));
  const filters = SHELF_FILTER_GROUPS.flatMap(g => g.options
    .filter(([, label]) => label.includes(query) || query.includes(label))
    .map(([val, label]) => ({ label: `只看「${label}」`, hint: g.title, run: () => runFilterJump(g.key, val) })));
  return [...tabs, ...filters].slice(0, COMMAND_JUMP_MATCHES);
}

function runFilterJump(key, val) {
  /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input')).value = '';
  shelfFilters.q = '';
  if (!shelfGroupValues(key).includes(val)) app.toggleShelfFilter(key, val);
  else app.loadShelfBooks(1);
}

export function hideCommandSuggest() {
  homeState.suggestions = [];
  document.getElementById('cmd-suggest')?.classList.add('hidden');
}

/** Under the search box while it has focus: the first matching books and the pages or filters the words name.
 * Enter without picking one runs the search over the whole library by meaning. */
export function renderCommandSuggest() {
  const box = document.getElementById('cmd-suggest');
  const query = shelfFilters.q;
  if (!box || !isDesktopHome() || !query || document.activeElement?.id !== 'shelf-search-input') {
    hideCommandSuggest();
    return;
  }
  homeState.suggestions = [
    ...homeState.items.slice(0, COMMAND_BOOK_MATCHES).map(b => ({
      label: splitBilingualTitle(b.clean_title || b.title).main, hint: b.author || '', run: () => app.openBookModal(b.id),
    })),
    ...jumpSuggestions(query),
  ];
  homeState.suggestIndex = -1;
  box.innerHTML = homeState.suggestions.map((item, i) => `<button class="cmd-item" data-index="${i}"
      onmousedown="event.preventDefault(); runCommandSuggest(${i})"><span>${escapeHtml(item.label)}</span>
      <span class="cmd-hint">${escapeHtml(item.hint)}</span></button>`).join('')
    + '<div class="cmd-foot">↑↓ 选择 · 回车 按意思搜全库 · Esc 清空</div>';
  box.classList.remove('hidden');
}

export function runCommandSuggest(index) {
  const item = homeState.suggestions[index];
  hideCommandSuggest();
  if (item) item.run();
}

function moveCommandSuggest(delta) {
  // Positions run -1 (nothing picked, Enter searches) to count - 1, wrapping at both ends
  const slots = homeState.suggestions.length + 1;
  homeState.suggestIndex = (((homeState.suggestIndex + 1 + delta) % slots) + slots) % slots - 1;
  document.querySelectorAll('#cmd-suggest .cmd-item').forEach(el => {
    el.classList.toggle('active', Number(/** @type {HTMLElement} */ (el).dataset.index) === homeState.suggestIndex);
  });
}

/** Keys inside the search box; true when the key was handled here and the box's own Enter search must not run. */
export function onCommandKey(event) {
  if (!isDesktopHome() || event.isComposing) return false;
  if (event.key === 'Escape') {
    app.clearShelfSearch();
    hideCommandSuggest();
    /** @type {HTMLElement} */ (event.target).blur();
    return true;
  }
  if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && homeState.suggestions.length) {
    event.preventDefault();
    moveCommandSuggest(event.key === 'ArrowDown' ? 1 : -1);
    return true;
  }
  if (event.key === 'Enter' && homeState.suggestIndex >= 0) {
    runCommandSuggest(homeState.suggestIndex);
    return true;
  }
  // The search runs and the box lets go of the keys, so j/k walk the results at once
  if (event.key === 'Enter') /** @type {HTMLElement} */ (event.target).blur();
  return false;
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
  bar.classList.toggle('hidden', !count || !isDesktopHome());
  if (!count) return;
  const isTooMany = count > MAX_BATCH_BOOKS;
  bar.innerHTML = `<span class="home-batch-count">已选 ${count} 本${isTooMany ? `（一次最多 ${MAX_BATCH_BOOKS} 本）` : ''}</span>`
    + BATCH_ACTIONS.map(([label], i) => `<button class="btn" ${isTooMany ? 'disabled' : ''} onclick="applyHomeBatch(${i})">${label}</button>`).join('')
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

/** Sets one batch action's mark on every ticked book; the toast's 撤销 puts back each book's own marks. */
export async function applyHomeBatch(actionIndex) {
  const [label, marks] = BATCH_ACTIONS[actionIndex];
  const ids = Array.from(homeState.checked);
  const previous = await postBatchMarks(ids.map(id => ({ id, ...marks })));
  if (!previous) return;
  homeState.checked.clear();
  refreshAfterMarks();
  showUndoToast(`已改 ${ids.length} 本 · ${label}`, async () => {
    if (await postBatchMarks(previous)) refreshAfterMarks();
  });
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

export function initHomeKeys() {
  document.addEventListener('keydown', onHomeKey);
}
