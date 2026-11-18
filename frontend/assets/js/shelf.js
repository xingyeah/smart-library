import {
  appState, shelfFilters, langSelections, CATEGORIES, MOBILE_QUERY,
  SHELF_MAX_PAGE_SIZE, SHEET_CLOSE_DRAG_PX,
  SHELF_STATUS_OPTIONS, QUICK_STATUS_KEYS, SHELF_FILTER_GROUPS,
  SORT_SHORT_LABELS,
} from './state.js';
import { escapeHtml } from './utils.js';
import { icon } from './icons.js';
import { renderBookCard, generatePaginationItems, syncBookCards } from './shelf_render.js';
import { aiRerankOn, setAiRerank, applyShelfAiRerank, loadStats, loadAiUsage } from './api.js';
import { runPresetDescribe, triggerDescribeRecommend, loadInterestView } from './recommend.js';
import { loadConflicts, loadMismergedBooks } from './organize.js';

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
  ['shelf', 'describe', 'interest', 'organize'].forEach(t => {
    document.getElementById(`view-${t}`).classList.toggle('hidden', t !== tab);
    document.getElementById(`tab-btn-${t}`).classList.toggle('active', t === tab);
  });
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
  document.getElementById('shelf-search-input').value = '';
  Object.assign(shelfFilters, {
    q: '', category: '', language: '', format: '', source_dir: '',
    reading_status: '', pub_period: '', only_favorite: false, has_rating: false, page: 1, page_size: 24
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
    shelfFilters.q = document.getElementById('shelf-search-input').value.trim();
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
  ['category', 'format', 'source_dir', 'pub_period', 'language'].forEach(k => { shelfFilters[k] = ''; });
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
      el.classList.toggle('active', el.dataset[attr] === (val || ''));
    });
  });
  document.querySelectorAll('[data-lang-scope="shelf"]').forEach(el => {
    const code = el.dataset.lang || '';
    el.classList.toggle('active', code ? langSelections.shelf.has(code) : langSelections.shelf.size === 0);
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
  if (!picked.length) {
    row.innerHTML = SHELF_STATUS_OPTIONS.filter(([k]) => QUICK_STATUS_KEYS.includes(k))
      .map(([k, label]) => shelfChipHtml('status', k, label, false)).join('')
      + `<button class="chip" onclick="openShelfSheet('filter', 'category')">分类 ▾</button>`;
    return;
  }
  row.innerHTML = picked.map(p => shelfChipHtml(p.group.key, p.val, `${shelfOptionLabel(p.group, p.val)} ✕`, true)).join('')
    + `<button class="chip" onclick="clearShelfFilters()">清除</button>`;
}

/** The filters in force, after the result count: each one removes itself on click, 清空 removes them all. */
export function renderShelfActiveFilters() {
  const picked = shelfPickedFilters();
  document.getElementById('shelf-active-filters').innerHTML = picked.length
    ? picked.map(p => `<button class="active-filter" onclick="toggleShelfFilter('${p.group.key}', '${escapeHtml(p.val)}')"
        title="去掉这个筛选">${escapeHtml(shelfOptionLabel(p.group, p.val))}${icon('close')}</button>`).join('')
      + '<button class="active-filter-clear" onclick="clearShelfFilters()">清空</button>'
    : '';
}

export function renderMobileShelfUi() {
  if (!document.getElementById('m-chip-row')) return;
  const picks = shelfPickedFilters().length;
  const btn = document.getElementById('m-filter-btn');
  // The count only helps once the list is narrowed; the whole library size is on the stats line
  const isNarrowed = picks > 0 || Boolean(shelfFilters.q);
  btn.textContent = isNarrowed ? `筛选 · ${appState.shelfLastTotal.toLocaleString()}` : '筛选';
  btn.classList.toggle('has-picks', picks > 0);
  renderShelfChipRow();
  updateShelfSearchClear();
  if (appState.shelfSheetKind) renderShelfSheet();
}

export function updateShelfSearchClear() {
  const input = document.getElementById('shelf-search-input');
  document.getElementById('m-search-clear').classList.toggle('hidden', !input.value);
}

export function clearShelfSearch() {
  document.getElementById('shelf-search-input').value = '';
  shelfFilters.q = '';
  loadShelfBooks(1, { scrollToTop: true });
}

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
  const current = document.getElementById('shelf-sort').value;
  document.getElementById('shelf-sheet-head').innerHTML = '<span>排序</span>';
  document.getElementById('shelf-sheet-body').innerHTML = Object.entries(SORT_SHORT_LABELS).map(([v, label]) =>
    `<button class="shelf-sort-option ${v === current ? 'active' : ''}" onclick="pickShelfSort('${v}')">`
    + `<span>${label}</span><span>${v === current ? '✓' : ''}</span></button>`).join('');
  document.getElementById('shelf-sheet-foot').innerHTML = '';
}

export function pickShelfSort(value) {
  document.getElementById('shelf-sort').value = value;
  appState.shelfSortTouched = true;
  closeShelfSheet();
  loadShelfBooks(1, { scrollToTop: true });
}

// Dragging the handle, title or footer down closes the sheet; the list in the middle scrolls instead
export function initShelfSheetDrag() {
  const sheet = document.getElementById('shelf-sheet');
  let startY = null;
  sheet.addEventListener('touchstart', e => {
    if (e.target.closest('.shelf-sheet-body')) return;
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

// ---------- endless list ----------
export function shelfHasMore() {
  return shelfFilters.page * shelfFilters.page_size < appState.shelfLastTotal;
}

export async function loadMoreShelfBooks() {
  if (appState.shelfIsAppending || !shelfHasMore() || appState.currentTab !== 'shelf' || !MOBILE_QUERY.matches) return;
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
  if (MOBILE_QUERY.matches) document.getElementById('shelf-search-input').placeholder = '搜索书名、作者、拼音';
  initShelfSheetDrag();
  initShelfLoadMore();
}

/** Page and page size to request. On a phone the list is endless, so reloading page N fetches pages 1..N in
 * one request and the cards already scrolled past stay. */
export function shelfRequestWindow(page, opts) {
  if (!MOBILE_QUERY.matches || opts.append || page <= 1) return { page, size: shelfFilters.page_size, loadedPages: page };
  const pages = Math.min(page, Math.floor(SHELF_MAX_PAGE_SIZE / shelfFilters.page_size));
  return { page: 1, size: pages * shelfFilters.page_size, loadedPages: pages };
}

export async function loadShelfBooks(page = 1, opts = {}) {
  shelfFilters.page = page;
  if (opts.scrollToTop) {
    const topEl = document.getElementById('view-shelf');
    if (topEl) {
      window.scrollTo({ top: Math.max(0, topEl.offsetTop - 60), behavior: 'smooth' });
    }
  }
  const currentInputVal = document.getElementById('shelf-search-input')
    ? document.getElementById('shelf-search-input').value.trim()
    : shelfFilters.q;
  shelfFilters.q = currentInputVal;
  const sortEl = document.getElementById('shelf-sort');
  // A picked sort holds for the search it was picked on; a new search ranks by relevance again
  if (currentInputVal !== appState.shelfLastQuery) appState.shelfSortTouched = false;
  appState.shelfLastQuery = currentInputVal;
  if (!appState.shelfSortTouched) sortEl.value = currentInputVal ? 'relevance' : (shelfFilters.reading_status === 'reading' ? 'opened' : 'imported');
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
    `共检索到 ${d.total.toLocaleString()} 本藏书（耗时 ${d.elapsed_ms} ms，第 ${d.page} 页）`;
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

export async function toggleFavorite(bookId, newVal) {
  const r = await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_favorite: Boolean(newVal) }),
  });
  if (!r.ok) return;
  // Like the status button: no reload, so the interest view keeps its cards and does not re-run AI
  syncBookCards(bookId, { isFavorite: Boolean(newVal) });
  loadStats();
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
}


export async function cycleReadingStatus(bookId, currentSt) {
  const order = ['unread', 'want', 'reading', 'read'];
  const nextSt = order[(order.indexOf(currentSt) + 1) % order.length];
  const r = await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reading_status: nextSt }),
  });
  if (!r.ok) return;
  // Update the button itself: interest and describe views are not reloaded (that would drop the card and re-run AI)
  syncBookCards(bookId, { readingStatus: nextSt });
  loadStats();
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
}

