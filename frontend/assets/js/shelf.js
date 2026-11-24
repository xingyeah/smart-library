import {
  CATEGORIES, MOBILE_QUERY, QUICK_STATUS_KEYS, SHELF_FILTER_GROUPS, SHELF_MAX_PAGE_SIZE,
  SHELF_STATUS_OPTIONS, appState, langSelections, shelfFilters,
} from './state.js';
import { escapeHtml } from './utils.js';
import { icon } from './icons.js';
import { authorFilterChipHtml, generatePaginationItems, renderBookCard } from './shelf_render.js';
import { aiRerankOn, applyShelfAiRerank, loadAiUsage, loadStats } from './api.js';
import { loadInterestView, runPresetDescribe, triggerDescribeRecommend } from './recommend.js';
import { loadConflicts, loadMismergedBooks } from './organize.js';
import { initShelfSheetDrag, renderShelfSheet } from './shelf_sheets.js';
import { loadTonightView } from './tonight.js';

const isMobileViewport = () => window.matchMedia(MOBILE_QUERY).matches;

export function initCategoryChips() {
  const row = document.getElementById('cat-filter-row');
  let html = '<span class="filter-label">分类:</span>';
  CATEGORIES.forEach((cat, idx) => {
    const val = cat === '全部' ? '' : cat;
    html += `<button class="chip ${idx === 0 ? 'active' : ''}" data-cat="${val}" onclick="setFilter('category', '${val}', this)">${cat}</button>`;
  });
  const isOpen = document.getElementById('shelf-more-filters').classList.contains('open');
  html += `<button class="more-filters-toggle" id="shelf-more-filters-btn" aria-expanded="${isOpen}"
    aria-controls="shelf-more-filters" onclick="toggleShelfMoreFilters()" title="更多筛选" aria-label="更多筛选">${icon('chevron')}</button>`;
  row.innerHTML = html;
}

export function toggleLangFilter(scope, langCode, btn) {
  const sel = langSelections[scope];
  if (!langCode) {
    sel.clear();
  } else {
    if (sel.has(langCode)) {
      sel.delete(langCode);
    } else {
      sel.add(langCode);
    }
    // If all 3 languages are selected, reset to '全部'
    if (sel.size >= 3) {
      sel.clear();
    }
  }
  document.querySelectorAll(`[data-lang-scope="${scope}"]`).forEach(el => {
    const code = el.getAttribute('data-lang') || '';
    if (!code) {
      el.classList.toggle('active', sel.size === 0);
    } else {
      el.classList.toggle('active', sel.has(code));
    }
  });

  const joined = Array.from(sel).join(',');
  if (scope === 'shelf') {
    shelfFilters.language = joined;
    loadShelfBooks(1);
  } else if (scope === 'describe') {
    triggerDescribeRecommend();
  } else if (scope === 'interest') {
    loadInterestView();
  }
}

export function switchTab(tab) {
  appState.currentTab = tab;
  ['tonight', 'shelf', 'describe', 'interest', 'organize'].forEach(t => {
    document.getElementById(`view-${t}`).classList.toggle('hidden', t !== tab);
    document.getElementById(`tab-btn-${t}`).classList.toggle('active', t === tab);
  });
  if (tab === 'tonight') loadTonightView();
  if (tab === 'shelf') loadShelfBooks(shelfFilters.page);
  if (tab === 'describe' && !document.getElementById('describe-grid').innerHTML.trim()) {
    runPresetDescribe('剖析人类非理性决策、认知偏差、思考快与慢或潜意识行为心理学的经典著作');
  }
  if (tab === 'interest') loadInterestView();
  if (tab === 'organize') {
    loadStats();
    loadAiUsage();
    loadConflicts('pending');
    loadMismergedBooks();
  }
}

/** The shelf narrowed to the books marked as being read (the tonight page's 'other books being read'). */
export function showReadingShelf() {
  applyShelfStatus('reading');
  shelfFilters.page = 1;
  switchTab('shelf');
}

export function setFilter(key, val, btn) {
  const parent = btn.parentElement;
  if (key === 'category') {
    parent.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    shelfFilters.category = val;
  } else if (key === 'format') {
    parent.querySelectorAll('[data-fmt]').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    shelfFilters.format = val;
  } else if (key === 'source_dir') {
    parent.querySelectorAll('[data-src]').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    shelfFilters.source_dir = val;
  } else if (key === 'pub_period') {
    parent.querySelectorAll('[data-period]').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    shelfFilters.pub_period = val;
  } else if (key === 'reading_status') {
    parent.querySelectorAll('[data-st]').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    if (val === 'fav') {
      shelfFilters.only_favorite = true;
      shelfFilters.reading_status = '';
      shelfFilters.has_rating = false;
    } else if (val === 'has_rating') {
      shelfFilters.has_rating = true;
      shelfFilters.only_favorite = false;
      shelfFilters.reading_status = '';
    } else {
      shelfFilters.only_favorite = false;
      shelfFilters.reading_status = val;
      shelfFilters.has_rating = false;
    }
  }
  loadShelfBooks(1);
}

// Until the user picks a sort: newest imports when browsing, relevance once there is a query

export function resetShelfFilters() {
  appState.shelfSortTouched = false;
  /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input')).value = '';
  Object.assign(shelfFilters, {
    q: '', category: '', language: '', format: '', source_dir: '',
    reading_status: '', pub_period: '', only_favorite: false, has_rating: false, author: '', page: 1, page_size: 24
  });
  langSelections.shelf.clear();
  initCategoryChips();
  document.querySelectorAll('[data-fmt],[data-src],[data-period],[data-st],[data-lang-scope="shelf"]').forEach(el => {
    const isAll = !el.getAttribute('data-fmt') && !el.getAttribute('data-src') && !el.getAttribute('data-period') && !el.getAttribute('data-st') && !el.getAttribute('data-lang');
    el.classList.toggle('active', isAll);
  });
  loadShelfBooks(1);
}

export function debounceShelfSearch() {
  if (appState.isImeComposing) return;
  clearTimeout(appState.searchTimer);
  appState.searchTimer = setTimeout(() => {
    if (appState.isImeComposing) return;
    shelfFilters.q = /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input')).value.trim();
    loadShelfBooks(1);
  }, 260);
}

// Composition events have no on* handler attributes, so inline oncompositionstart never runs
export function bindShelfSearchIme() {
  const input = document.getElementById('shelf-search-input');
  input.addEventListener('compositionstart', () => { appState.isImeComposing = true; });
  input.addEventListener('compositionend', () => { appState.isImeComposing = false; debounceShelfSearch(); });
}

export function shelfStatusKey() {
  if (shelfFilters.only_favorite) return 'fav';
  if (shelfFilters.has_rating) return 'has_rating';
  return shelfFilters.reading_status;
}

export function shelfGroupValues(key) {
  if (key === 'status') return [shelfStatusKey()].filter(Boolean);
  if (key === 'language') return Array.from(langSelections.shelf);
  return [shelfFilters[key]].filter(Boolean);
}

export function applyShelfStatus(val) {
  shelfFilters.only_favorite = val === 'fav';
  shelfFilters.has_rating = val === 'has_rating';
  shelfFilters.reading_status = (val === 'fav' || val === 'has_rating') ? '' : val;
}

/** Phone filter tap: picks the value, or clears it when already picked (languages toggle within a set). */
export function toggleShelfFilter(key, val) {
  const isPicked = shelfGroupValues(key).includes(val);
  if (key === 'language') {
    if (isPicked) langSelections.shelf.delete(val); else langSelections.shelf.add(val);
    if (langSelections.shelf.size >= 3) langSelections.shelf.clear();
    shelfFilters.language = Array.from(langSelections.shelf).join(',');
  } else if (key === 'status') {
    applyShelfStatus(isPicked ? '' : val);
  } else {
    shelfFilters[key] = isPicked ? '' : val;
  }
  syncDesktopShelfChips();
  loadShelfBooks(1, { scrollToTop: true });
}

/** Clears the filters but keeps the search text and sort. */
export function clearShelfFilters() {
  applyShelfStatus('');
  ['category', 'format', 'source_dir', 'pub_period', 'language', 'author'].forEach(k => { shelfFilters[k] = ''; });
  langSelections.shelf.clear();
  syncDesktopShelfChips();
  loadShelfBooks(1, { scrollToTop: true });
}

// The desktop chips stay in step so a phone rotated past 768px shows the same filters
export function syncDesktopShelfChips() {
  const picked = { cat: shelfFilters.category, fmt: shelfFilters.format, src: shelfFilters.source_dir,
                   period: shelfFilters.pub_period, st: shelfStatusKey() };
  Object.entries(picked).forEach(([attr, val]) => {
    document.querySelectorAll(`#view-shelf [data-${attr}]`).forEach(el => {
      const h = /** @type {HTMLElement} */ (el);
      h.classList.toggle('active', h.dataset[attr] === (val || ''));
    });
  });
  document.querySelectorAll('[data-lang-scope="shelf"]').forEach(el => {
    const h = /** @type {HTMLElement} */ (el);
    const code = h.dataset.lang || '';
    h.classList.toggle('active', code ? langSelections.shelf.has(code) : langSelections.shelf.size === 0);
  });
}

export function shelfOptionLabel(group, val) {
  const hit = group.options.find(([v]) => v === val);
  return hit ? hit[1] : val;
}

export function shelfPickedFilters() {
  return SHELF_FILTER_GROUPS.flatMap(g => shelfGroupValues(g.key).map(v => ({ group: g, val: v })));
}

export function shelfChipHtml(key, val, label, isActive) {
  return `<button class="chip ${isActive ? 'active' : ''}" onclick="toggleShelfFilter('${key}', '${escapeHtml(val)}')">${escapeHtml(label)}</button>`;
}

export function renderShelfChipRow() {
  const row = document.getElementById('m-chip-row');
  const picked = shelfPickedFilters();
  const authorChip = authorFilterChipHtml('chip active');
  if (!picked.length && !authorChip) {
    row.innerHTML = SHELF_STATUS_OPTIONS.filter(([k]) => QUICK_STATUS_KEYS.includes(k))
      .map(([k, label]) => shelfChipHtml('status', k, label, false)).join('')
      + `<button class="chip" onclick="openShelfSheet('filter', 'category')">分类 ▾</button>`;
    return;
  }
  row.innerHTML = authorChip + picked.map(p => shelfChipHtml(p.group.key, p.val, `${shelfOptionLabel(p.group, p.val)} ✕`, true)).join('')
    + `<button class="chip" onclick="clearShelfFilters()">清除</button>`;
}

/** The filters in force, after the result count: each one removes itself on click, 清空 removes them all. */
export function renderShelfActiveFilters() {
  const picked = shelfPickedFilters();
  const authorChip = authorFilterChipHtml('active-filter');
  document.getElementById('shelf-active-filters').innerHTML = picked.length || authorChip
    ? authorChip + picked.map(p => `<button class="active-filter" onclick="toggleShelfFilter('${p.group.key}', '${escapeHtml(p.val)}')"
        title="去掉这个筛选">${escapeHtml(shelfOptionLabel(p.group, p.val))}${icon('close')}</button>`).join('')
      + '<button class="active-filter-clear" onclick="clearShelfFilters()">清空</button>'
    : '';
}

export function renderMobileShelfUi() {
  if (!document.getElementById('m-chip-row')) return;
  const picks = shelfPickedFilters().length;
  const btn = document.getElementById('m-filter-btn');
  // The count only helps once the list is narrowed; the whole library size is on the stats line
  const isNarrowed = picks > 0 || Boolean(shelfFilters.q || shelfFilters.author);
  btn.textContent = isNarrowed ? `筛选 · ${appState.shelfLastTotal.toLocaleString()}` : '筛选';
  btn.classList.toggle('has-picks', picks > 0);
  renderShelfChipRow();
  updateShelfSearchClear();
  if (appState.shelfSheetKind) renderShelfSheet();
}

export function updateShelfSearchClear() {
  const input = /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input'));
  document.getElementById('m-search-clear').classList.toggle('hidden', !input.value);
}

export function clearShelfSearch() {
  /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input')).value = '';
  shelfFilters.q = '';
  loadShelfBooks(1, { scrollToTop: true });
}

// ---------- endless list ----------
export function shelfHasMore() {
  return shelfFilters.page * shelfFilters.page_size < appState.shelfLastTotal;
}

export async function loadMoreShelfBooks() {
  if (appState.shelfIsAppending || !shelfHasMore() || appState.currentTab !== 'shelf' || !isMobileViewport()) return;
  appState.shelfIsAppending = true;
  appState.shelfLoadFailed = false;
  renderShelfLoadMore();
  try {
    await loadShelfBooks(shelfFilters.page + 1, { append: true });
  } finally {
    appState.shelfIsAppending = false;
    renderShelfLoadMore();
  }
}

export function renderShelfLoadMore() {
  const el = document.getElementById('m-load-more');
  if (appState.shelfLoadFailed) {
    el.innerHTML = '<button class="btn" onclick="loadMoreShelfBooks()">加载失败，点此重试</button>';
  } else if (appState.shelfIsAppending) {
    el.textContent = '加载中…';
  } else {
    el.textContent = shelfHasMore() || !appState.shelfLastTotal ? '' : `已全部显示 · ${appState.shelfLastTotal.toLocaleString()} 本`;
  }
  // Observing again fires at once if the marker is still near the screen, so short pages keep filling
  if (appState.shelfLoadMoreObserver && !appState.shelfIsAppending) {
    appState.shelfLoadMoreObserver.unobserve(el);
    appState.shelfLoadMoreObserver.observe(el);
  }
}

export function initShelfLoadMore() {
  appState.shelfLoadMoreObserver = new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting) && !appState.shelfLoadFailed) loadMoreShelfBooks();
  }, { rootMargin: '0px 0px 100% 0px' });
  appState.shelfLoadMoreObserver.observe(document.getElementById('m-load-more'));
}

export function initMobileShelf() {
  if (isMobileViewport()) /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input')).placeholder = '搜索书名、作者、拼音';
  initShelfSheetDrag();
  initShelfLoadMore();
}

/** Page and page size to request. On a phone the list is endless, so reloading page N fetches pages 1..N in
 * one request and the cards already scrolled past stay. */
export function shelfRequestWindow(page, opts) {
  if (!isMobileViewport() || opts.append || page <= 1) return { page, size: shelfFilters.page_size, loadedPages: page };
  const pages = Math.min(page, Math.floor(SHELF_MAX_PAGE_SIZE / shelfFilters.page_size));
  return { page: 1, size: pages * shelfFilters.page_size, loadedPages: pages };
}

// An author's books lead with their best known; plain browsing shows the newest imports
function defaultBrowseSort() {
  if (shelfFilters.author) return 'classic';
  return shelfFilters.reading_status === 'reading' ? 'opened' : 'imported';
}

export async function loadShelfBooks(page = 1, opts = {}) {
  shelfFilters.page = page;
  if (opts.scrollToTop) {
    const topEl = document.getElementById('view-shelf');
    if (topEl) {
      window.scrollTo({ top: Math.max(0, topEl.offsetTop - 60), behavior: 'smooth' });
    }
  }
  const searchInput = /** @type {HTMLInputElement | null} */ (document.getElementById('shelf-search-input'));
  const currentInputVal = searchInput
    ? searchInput.value.trim()
    : shelfFilters.q;
  shelfFilters.q = currentInputVal;
  const sortEl = /** @type {HTMLSelectElement} */ (document.getElementById('shelf-sort'));
  // A picked sort holds for the search it was picked on; a new search ranks by relevance again
  if (currentInputVal !== appState.shelfLastQuery) appState.shelfSortTouched = false;
  appState.shelfLastQuery = currentInputVal;
  if (!appState.shelfSortTouched) sortEl.value = currentInputVal ? 'relevance' : defaultBrowseSort();
  // The back gesture closes the author filter like a layer (back_stack.js), so its marker follows the state
  document.getElementById('author-filter-layer').classList.toggle('open', Boolean(shelfFilters.author));
  const sort_by = sortEl.value;
  const reqWindow = shelfRequestWindow(page, opts);
  if (!opts.append) shelfFilters.page = reqWindow.loadedPages;
  if (!opts.append) appState.shelfLoadFailed = false;
  renderMobileShelfUi();

  if (appState.shelfAbortController) {
    appState.shelfAbortController.abort();
  }
  appState.shelfAbortController = new AbortController();
  const reqId = ++appState.shelfReqSeq;

  const params = new URLSearchParams({
    q: shelfFilters.q,
    category: shelfFilters.category,
    language: shelfFilters.language,
    format: shelfFilters.format,
    source_dir: shelfFilters.source_dir,
    reading_status: shelfFilters.reading_status,
    only_favorite: shelfFilters.only_favorite ? '1' : '0',
    has_rating: shelfFilters.has_rating ? '1' : '0',
    pub_period: shelfFilters.pub_period,
    author: shelfFilters.author,
    sort_by,
    page: String(reqWindow.page),
    page_size: String(reqWindow.size),
  });
  let d;
  try {
    const r = await fetch(`/api/books?${params.toString()}`, {
      signal: appState.shelfAbortController.signal,
    });
    if (reqId !== appState.shelfReqSeq) return;
    d = await r.json();
    if (reqId !== appState.shelfReqSeq) return;
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    console.error('loadShelfBooks error:', err);
    if (opts.append) {
      shelfFilters.page = page - 1;
      appState.shelfLoadFailed = true;
    }
    return;
  }
  appState.shelfLastTotal = d.total;
  renderMobileShelfUi();
  if (!opts.append) renderShelfLoadMore();
  document.getElementById('shelf-result-info').innerText =
    `共检索到 ${d.total.toLocaleString()} 本藏书（第 ${d.page} 页）`;
  renderShelfActiveFilters();

  const grid = document.getElementById('shelf-grid');
  if (opts.append) {
    grid.insertAdjacentHTML('beforeend', (d.items || []).map(b => renderBookCard(b, { showRank: Boolean(d.is_semantic_intent) })).join(''));
    return;
  }
  let bannerHtml = '';
  if (d.category_relaxed) {
    bannerHtml = `
      <div style="grid-column:1/-1; background:var(--ground); border:1px solid var(--border); border-radius:10px; padding:12px 16px; margin-bottom:4px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; font-size:14px; color:var(--text);">
        <div>
          <b>智能意图纠偏：</b>检测到您的找书意图为「<b>${escapeHtml(d.inferred_category)}</b>」，与之前选中的「<b>${escapeHtml(d.requested_category)}</b>」分类冲突，系统已自动放宽为您在全库呈现精准匹配图书。
        </div>
        <button class="btn" style="padding:4px 12px; font-size:12px;" onclick="resetShelfFilters()">清空所有筛选</button>
      </div>
    `;
  }

  if (!d.items || d.items.length === 0) {
    grid.innerHTML = bannerHtml + `<div class="section-card" style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:30px;">未找到匹配书目。可尝试点击上方「重置筛选」或更换关键词。</div>`;
  } else {
    grid.innerHTML = bannerHtml + d.items.map(b => renderBookCard(b, { showRank: Boolean(d.is_semantic_intent) })).join('');
    if (opts.ai && aiRerankOn() && shelfFilters.q && page === 1 && sort_by === 'relevance' && d.items.length > 1) {
      applyShelfAiRerank(d, bannerHtml, reqId);
    }
  }

  const totalPages = Math.max(1, Math.ceil(d.total / d.page_size));
  const topPagHtml = `
    <button class="btn" ${page <= 1 ? 'disabled' : ''} onclick="loadShelfBooks(${page - 1})">上一页</button>
    <span style="align-self:center;font-size:14px;color:var(--text-muted);">${page} / ${totalPages}</span>
    <button class="btn" ${page >= totalPages ? 'disabled' : ''} onclick="loadShelfBooks(${page + 1})">下一页</button>
  `;
  document.getElementById('shelf-pagination-top').innerHTML = topPagHtml;

  let bottomPagHtml = '';
  if (totalPages > 1) {
    const pageItems = generatePaginationItems(page, totalPages);
    const itemsHtml = pageItems.map(item => {
      if (typeof item === 'number') {
        const isActive = item === page;
        return `<button class="page-btn ${isActive ? 'active' : ''}" onclick="loadShelfBooks(${item}, {scrollToTop:true})">${item}</button>`;
      } else if (item && item.type === 'ellipsis') {
        const title = item.jumpTo > page ? `向后跳转至第 ${item.jumpTo} 页` : `向前跳转至第 ${item.jumpTo} 页`;
        return `<span class="page-ellipsis" title="${title}" onclick="loadShelfBooks(${item.jumpTo}, {scrollToTop:true})">…</span>`;
      }
      return '';
    }).join('');

    bottomPagHtml = `
      <div class="pagination-wrap">
        <button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="loadShelfBooks(${page - 1}, {scrollToTop:true})">← 上一页</button>
        ${itemsHtml}
        <button class="page-btn" ${page >= totalPages ? 'disabled' : ''} onclick="loadShelfBooks(${page + 1}, {scrollToTop:true})">下一页 →</button>
      </div>
    `;
  }
  document.getElementById('shelf-pagination-bottom').innerHTML = bottomPagHtml;
}

// Language, format, source, period and status fold away; categories stay in view as the filter used most
export function toggleShelfMoreFilters() {
  const isOpen = document.getElementById('shelf-more-filters').classList.toggle('open');
  document.getElementById('shelf-more-filters-btn').setAttribute('aria-expanded', String(isOpen));
}
