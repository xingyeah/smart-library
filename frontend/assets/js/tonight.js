// The phone home page: the book to carry on with, one swipeable pick at a time, and a line to say what to read.
// Right = want to read, left = skip (kept as a record of what was turned down), up or tap = details.
import { READING_STATUS_LABEL } from './state.js';
import { bookCoverImgHtml, escapeHtml, showMobileNotice, showUndoToast } from './utils.js';
import { splitBilingualTitle } from './shelf_render.js';
import { icon } from './icons.js';
import { deleteSearch, fetchSearchHistory, recordSearch } from './search_history.js';

const SWIPE_COMMIT_PX = 90;
const TAP_SLOP_PX = 8;
const STAMP_RANGE_PX = 60;
const LEAVE_MS = 200;
const LEAVE_DISTANCE_PX = 420;
const POSITION_LABEL_CHARS = 20;
const SEARCH_SUMMARY_CHARS = 70;
const CONTEXT_TONIGHT = 'tonight';
const CONTEXT_SEARCH = 'search';
const TONIGHT_PRESETS = [
  ['认知偏差与行为决策心理学', '剖析人类非理性决策、认知偏差、思考快与慢或潜意识行为心理学的经典著作'],
  ['计算机底层与分布式架构', '讲解计算机操作系统底层原理、分布式系统架构或算法设计的硬核技术书'],
  ['王朝政治博弈与历史兴衰', '关于中国历代王朝兴衰、明清政治制度博弈、资治通鉴或人物传记的深度历史书'],
  ['价值投资与经济金融周期', '讲解价值投资、金融周期、资本市场规律、巴菲特芒格智慧或宏观经济的书'],
  ['宏大硬科幻与星际文明', '格局宏大的硬科幻小说，涉及星际文明、宇宙社会学、时间或人工智能哲学思考'],
];

/** @type {{ cards: any[], index: number, seen: number[], mode: string, query: string, requestSeq: number, history: string[], isExhausted: boolean, continueId: number }} */
const tonight = {
  cards: [], index: 0, seen: [], mode: CONTEXT_TONIGHT, query: '', requestSeq: 0, history: [], isExhausted: false, continueId: 0,
};

const byId = id => document.getElementById(id);

async function postJson(url, body) {
  try {
    const response = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    return response.ok;
  } catch (error) {
    console.error('tonight request failed', url, error);
    return false;
  }
}

function friendlyReason(text) {
  const shortTitles = (text || '').replace(/《([^》]+)》/g, (_whole, title) => `《${splitBilingualTitle(title).main}》`);
  return shortTitles.replace(/^与你标记的(.+)相近$/, '因为你标记过$1，这本和它相近');
}

function continueHtml(book, others) {
  if (!book) return '';
  const percent = book.read_progress != null ? Math.round(book.read_progress * 100) : 0;
  const label = ((book.read_pos || {}).label || '').slice(0, POSITION_LABEL_CHARS);
  const where = `已读 ${percent}%${label ? ` · 读到 ${escapeHtml(label)}` : ''}`;
  const othersHtml = others > 0
    ? `<div class="tonight-others"><button class="tonight-link" onclick="showReadingShelf()">其他在读 ${others} 本 ›</button></div>` : '';
  return `<div class="tonight-now">
      <div class="tonight-cover tonight-cover-now" onclick="openBookModal(${book.id})">${bookCoverImgHtml(book)}</div>
      <div class="tonight-now-info">
        <span class="tonight-now-label">接着读</span>
        <div class="tonight-now-title" onclick="openBookModal(${book.id})">${escapeHtml(splitBilingualTitle(book.clean_title || book.title).main)}</div>
        <div class="tonight-now-pos">${where}</div>
        <div class="tonight-bar"><i style="width:${percent}%"></i></div>
        <button class="btn btn-primary tonight-now-go" onclick="openOnlineReader(${book.id})">继续阅读</button>
      </div>
    </div>${othersHtml}`;
}

function cardHtml(book, isTop) {
  const { main, sub } = splitBilingualTitle(book.clean_title || book.title);
  const reason = tonight.mode === CONTEXT_TONIGHT
    ? friendlyReason(book.tonight_reason) : (book.summary || '').slice(0, SEARCH_SUMMARY_CHARS);
  const stamps = isTop ? '<span class="tonight-stamp want">想读</span><span class="tonight-stamp skip">跳过</span>' : '';
  return `<div class="tonight-card${isTop ? '' : ' behind'}" data-book-id="${book.id}">
      ${stamps}
      <div class="tonight-cover">${bookCoverImgHtml(book)}</div>
      <div class="tonight-card-info">
        <div class="tonight-card-title">${escapeHtml(main)}</div>
        ${sub ? `<div class="tonight-card-sub">${escapeHtml(sub)}</div>` : ''}
        <div class="tonight-card-author">${escapeHtml(book.author || '')}</div>
        <div class="tonight-card-reason">${escapeHtml(reason)}</div>
      </div>
    </div>`;
}

function searchEndHtml() {
  const back = '<button class="btn" onclick="loadTonightView()">返回今晚</button>';
  if (tonight.isExhausted) {
    return `<div class="tonight-end"><div>这句搜的书都看过了</div><div class="tonight-end-actions">
      <button class="btn btn-primary" onclick="restartTonightSearch()">从头再看</button>${back}</div></div>`;
  }
  if (!tonight.cards.length) return `<div class="tonight-end"><div>暂时没有合适的书</div>${back}</div>`;
  return `<div class="tonight-end"><div>这一批看完了</div><div class="tonight-end-actions">
      <button class="btn btn-primary" onclick="nextTonightSearch()">换一批</button>${back}</div></div>`;
}

function endHtml() {
  if (tonight.mode === CONTEXT_SEARCH) return searchEndHtml();
  const text = tonight.cards.length ? '这一批看完了' : '暂时没有合适的书';
  return `<div class="tonight-end"><div>${text}</div><button class="btn btn-primary" onclick="moreTonight()">换一批</button></div>`;
}

function renderStack() {
  const stack = byId('tonight-stack');
  const current = tonight.cards[tonight.index];
  if (!current) {
    stack.innerHTML = endHtml();
    return;
  }
  const next = tonight.cards[tonight.index + 1];
  stack.innerHTML = (next ? cardHtml(next, false) : '') + cardHtml(current, true);
  bindSwipe(stack.querySelector('.tonight-card:not(.behind)'));
}

function showMessage(text, retryCall) {
  byId('tonight-stack').innerHTML = `<div class="tonight-end"><div>${escapeHtml(text)}</div>${
    retryCall ? `<button class="btn" onclick="${retryCall}">重试</button>` : ''}</div>`;
}

function setHeader(title, isSearch) {
  byId('tonight-stack-title').textContent = title;
  byId('tonight-back').classList.toggle('hidden', !isSearch);
}

function historyChipHtml(query, index) {
  return `<span class="chip tonight-history-chip">
      <button class="tonight-history-run" onclick="runTonightHistory(${index})">${icon('history')}<span>${escapeHtml(query)}</span></button>
      <button class="tonight-history-del" onclick="deleteTonightHistory(${index})" aria-label="删除这条搜索记录">×</button>
    </span>`;
}

function renderPresets() {
  const history = tonight.history.map(historyChipHtml).join('');
  byId('tonight-presets').innerHTML = history + TONIGHT_PRESETS.map(([label], index) =>
    `<button class="chip" onclick="runTonightPreset(${index})">${escapeHtml(label)}</button>`).join('');
}

async function refreshHistory() {
  tonight.history = await fetchSearchHistory();
  renderPresets();
}

async function rememberSearch(text) {
  await recordSearch(text);
  await refreshHistory();
}

/** Opens the page: the book to carry on with and a first batch of picks. */
export async function loadTonightView() {
  const requestSeq = ++tonight.requestSeq;
  Object.assign(tonight, { mode: CONTEXT_TONIGHT, query: '', seen: [], index: 0, cards: [] });
  byId('tonight-date').textContent = new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });
  setHeader('今晚推荐', false);
  renderPresets();
  refreshHistory();
  showMessage('正在挑书…', '');
  try {
    const data = await (await fetch('/api/tonight')).json();
    if (requestSeq !== tonight.requestSeq) return;
    byId('tonight-continue').innerHTML = continueHtml(data.continue, data.other_reading);
    tonight.continueId = data.continue ? Number(data.continue.id) : 0;
    tonight.cards = data.picks || [];
    tonight.seen = tonight.cards.map(book => Number(book.id));
    renderStack();
  } catch (error) {
    console.error('tonight failed to load', error);
    if (requestSeq === tonight.requestSeq) showMessage('没能连上书库', 'loadTonightView()');
  }
}

/** Another batch of picks, leaving out the ones already shown. */
export async function moreTonight() {
  const requestSeq = ++tonight.requestSeq;
  showMessage('正在挑书…', '');
  try {
    const data = await (await fetch(`/api/tonight?seen=${tonight.seen.join(',')}`)).json();
    if (requestSeq !== tonight.requestSeq) return;
    tonight.cards = data.picks || [];
    tonight.seen.push(...tonight.cards.map(book => Number(book.id)));
    tonight.index = 0;
    renderStack();
  } catch (error) {
    console.error('tonight failed to load more', error);
    if (requestSeq === tonight.requestSeq) showMessage('没能连上书库', 'moreTonight()');
  }
}

/** The line at the bottom: the same swipeable cards, now the best matches for what was said. */
export function askTonight(event) {
  event.preventDefault();
  const text = /** @type {HTMLInputElement} */ (byId('tonight-ask-input')).value.trim();
  if (!text) return;
  rememberSearch(text);
  searchTonight(text);
}

async function searchTonight(text) {
  const input = /** @type {HTMLInputElement} */ (byId('tonight-ask-input'));
  input.value = text;
  input.blur();
  Object.assign(tonight, { mode: CONTEXT_SEARCH, query: text });
  setHeader(`「${text}」的结果`, true);
  await loadSearchBatch();
}

/** The next batch for the current sentence; the server leaves out what was already answered under it. */
async function loadSearchBatch() {
  const requestSeq = ++tonight.requestSeq;
  Object.assign(tonight, { index: 0, cards: [], isExhausted: false });
  showMessage('正在找…', '');
  try {
    const response = await fetch('/api/tonight/search', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: tonight.query }),
    });
    if (!response.ok) throw new Error(`tonight search: server answered ${response.status}`);
    const data = await response.json();
    if (requestSeq !== tonight.requestSeq) return;
    Object.assign(tonight, { cards: data.items || [], isExhausted: Boolean(data.exhausted) });
    renderStack();
  } catch (error) {
    console.error('tonight search failed', tonight.query, error);
    if (requestSeq === tonight.requestSeq) showMessage('没能连上书库', 'nextTonightSearch()');
  }
}

export function nextTonightSearch() {
  loadSearchBatch();
}

/** Skipped books of this sentence come back; wanted, reading and read ones stay out. */
export async function restartTonightSearch() {
  if (!(await postJson('/api/tonight/search/reset', { query: tonight.query }))) {
    showMobileNotice('没成功，请再试一次');
    return;
  }
  await loadSearchBatch();
}

/** A book deleted from its detail leaves the page: its card goes, and a deleted carry-on book brings the next one. */
export function dropTonightBook(bookId) {
  if (Number(bookId) === tonight.continueId) {
    loadTonightView();
    return;
  }
  const position = tonight.cards.findIndex((book, index) => index >= tonight.index && Number(book.id) === Number(bookId));
  if (position < 0) return;
  tonight.cards.splice(position, 1);
  renderStack();
}

/** A preset is always on show, so searching it leaves the history alone. */
export function runTonightPreset(index) {
  searchTonight(TONIGHT_PRESETS[index][1]);
}

export function runTonightHistory(index) {
  const query = tonight.history[index];
  if (!query) return;
  rememberSearch(query);
  searchTonight(query);
}

export async function deleteTonightHistory(index) {
  const query = tonight.history[index];
  if (!query) return;
  if (!(await deleteSearch(query))) {
    showMobileNotice('没删掉，请再试一次');
    return;
  }
  await refreshHistory();
}

function advanceFrom(cardIndex) {
  tonight.index = cardIndex + 1;
  renderStack();
}

function returnTo(cardIndex) {
  tonight.index = cardIndex;
  renderStack();
}

async function wantCurrent() {
  const cardIndex = tonight.index;
  const book = tonight.cards[cardIndex];
  const previous = book.reading_status || 'unread';
  if (!(await postJson(`/api/books/${book.id}/meta`, { reading_status: 'want' }))) return false;
  // Under a search the want also steers that sentence's next batch toward look-alikes
  const isFromSearch = tonight.mode === CONTEXT_SEARCH;
  if (isFromSearch) postJson('/api/tonight/search/want', { book_id: Number(book.id), query: tonight.query });
  advanceFrom(cardIndex);
  showUndoToast(`已设为「${READING_STATUS_LABEL.want}」`, async () => {
    if (!(await postJson(`/api/books/${book.id}/meta`, { reading_status: previous }))) return;
    if (isFromSearch) await postJson('/api/tonight/search/want/undo', { book_id: Number(book.id) });
    returnTo(cardIndex);
  });
  return true;
}

async function skipCurrent() {
  const cardIndex = tonight.index;
  const book = tonight.cards[cardIndex];
  const context = tonight.mode === CONTEXT_TONIGHT ? CONTEXT_TONIGHT : CONTEXT_SEARCH;
  if (!(await postJson('/api/skips', { book_id: Number(book.id), context, query: tonight.query }))) return false;
  advanceFrom(cardIndex);
  showUndoToast('已跳过', async () => {
    if (await postJson('/api/skips/undo', { book_id: Number(book.id) })) returnTo(cardIndex);
  });
  return true;
}

function openCurrent() {
  const book = tonight.cards[tonight.index];
  if (book) /** @type {any} */ (window).openBookModal(book.id);
}

function flyOut(card, direction) {
  card.classList.add('leaving');
  card.style.transform = `translate(${direction * LEAVE_DISTANCE_PX}px, 0) rotate(${direction * 12}deg)`;
  return new Promise(resolve => setTimeout(resolve, LEAVE_MS));
}

function springBack(card) {
  card.classList.remove('leaving');
  card.classList.add('returning');
  card.style.transform = '';
  card.querySelectorAll('.tonight-stamp').forEach(stamp => { /** @type {HTMLElement} */ (stamp).style.opacity = '0'; });
}

async function decide(card, action) {
  if (action === 'open') {
    springBack(card);
    openCurrent();
    return;
  }
  const isWant = action === 'want';
  const isDone = await (isWant ? wantCurrent() : skipCurrent());
  if (isDone) return;
  showMobileNotice('没成功，请再试一次');
  springBack(card);
}

async function throwCard(card, action) {
  await flyOut(card, action === 'want' ? 1 : -1);
  await decide(card, action);
}

function actionFor(dx, dy) {
  if (Math.abs(dx) < TAP_SLOP_PX && Math.abs(dy) < TAP_SLOP_PX) return 'open';
  if (dx >= SWIPE_COMMIT_PX) return 'want';
  if (dx <= -SWIPE_COMMIT_PX) return 'skip';
  if (dy <= -SWIPE_COMMIT_PX && Math.abs(dx) < SWIPE_COMMIT_PX) return 'open';
  return 'none';
}

function showStamps(card, dx) {
  const opacity = value => String(Math.max(0, Math.min(1, value / STAMP_RANGE_PX)));
  /** @type {HTMLElement} */ (card.querySelector('.tonight-stamp.want')).style.opacity = opacity(dx);
  /** @type {HTMLElement} */ (card.querySelector('.tonight-stamp.skip')).style.opacity = opacity(-dx);
}

function bindSwipe(card) {
  let start = null;
  let dx = 0;
  let dy = 0;
  card.addEventListener('pointerdown', event => {
    start = { x: event.clientX, y: event.clientY };
    card.classList.remove('returning');
    card.setPointerCapture(event.pointerId);
  });
  card.addEventListener('pointermove', event => {
    if (!start) return;
    dx = event.clientX - start.x;
    dy = event.clientY - start.y;
    card.style.transform = `translate(${dx}px, ${Math.min(0, dy) * 0.4}px) rotate(${dx / 25}deg)`;
    showStamps(card, dx);
  });
  card.addEventListener('pointerup', () => {
    if (!start) return;
    start = null;
    const action = actionFor(dx, dy);
    dx = 0;
    dy = 0;
    if (action === 'want' || action === 'skip') throwCard(card, action);
    else if (action === 'open') decide(card, 'open');
    else springBack(card);
  });
  card.addEventListener('pointercancel', () => {
    start = null;
    springBack(card);
  });
}
