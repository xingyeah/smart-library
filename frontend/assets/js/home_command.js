// The desktop command bar: the shelf search box in the top bar. A line like "把想读里讲决策的书找出来，设为在读"
// is read by fixed rules, no AI: status words narrow the list, "设为 X" offers to set X on what was found, and
// the rest is searched by meaning.
import { SHELF_FILTER_GROUPS, appState, shelfFilters, shelfGroupValues } from './state.js';
import { escapeHtml, showMobileNotice } from './utils.js';
import { splitBilingualTitle } from './shelf_render.js';
import { MAX_BATCH_BOOKS, applyMarksToBooks, focusFirstHomeRow, homeItems, isDesktopHome } from './home.js';
import { fetchSearchHistory, recordSearch } from './search_history.js';

const app = /** @type {any} */ (window);
const COMMAND_BOOK_MATCHES = 3;
const COMMAND_JUMP_MATCHES = 2;
const STATUS_WORDS = [['在读', 'reading'], ['想读', 'want'], ['已读', 'read'], ['喜欢', 'fav'], ['收藏', 'fav']];
const SET_STATUS_PATTERN = /(?:设为|设成|标为|标成|改为|改成|加入|放进)\s*[「“"]?(在读|想读|已读|未读|喜欢|收藏)[」”"]?/;
const SET_MARKS = {
  在读: { reading_status: 'reading' }, 想读: { reading_status: 'want' }, 已读: { reading_status: 'read' },
  未读: { reading_status: 'unread' }, 喜欢: { is_favorite: true }, 收藏: { is_favorite: true },
};
// Words that only shape the sentence; what is left after them is the topic
const FILLER_PATTERN = /把|帮我|给我|找出来|找一下|找找|里面|里的|里|这些|所有|全部|的书|[，,。.!！]/g;
const ORGANIZE_PATTERN = /整理|待确认|作者缺失|合并错/;
const JUMP_TABS = [['interest', '推荐'], ['organize', '整理']];

/** @typedef {{ query: string, filterStatus: string[], setWord: string, wantsOrganize: boolean }} ParsedCommand */

/** Splits a command line into the topic to search, the statuses to narrow by and a status to set.
 * @returns {ParsedCommand} */
export function parseCommand(raw) {
  let text = raw || '';
  const setMatch = SET_STATUS_PATTERN.exec(text);
  if (setMatch) text = text.replace(setMatch[0], ' ');
  const filterStatus = [...new Set(STATUS_WORDS.filter(([word]) => text.includes(word)).map(([, val]) => val))];
  STATUS_WORDS.forEach(([word]) => { text = text.split(word).join(' '); });
  const query = text.replace(FILLER_PATTERN, ' ').replace(/\s+/g, ' ').trim();
  return { query, filterStatus, setWord: setMatch ? setMatch[1] : '', wantsOrganize: ORGANIZE_PATTERN.test(raw || '') };
}

const commandState = {
  suggestions: /** @type {{kind: string, label: string, hint: string, isDisabled?: boolean, run: () => void}[]} */ ([]),
  index: -1,
  history: /** @type {string[]} */ ([]),
};

function commandInput() {
  return /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input'));
}

/** Only a line the reader settles on (Enter, or picking 搜索) goes into the history, not every keystroke. */
function rememberCommandText() {
  const text = commandInput().value.trim();
  if (text) recordSearch(text);
}

/** Loads the recent searches the empty command bar offers, then shows them if the bar is still empty and focused. */
export async function loadCommandHistory() {
  commandState.history = await fetchSearchHistory();
  renderCommandSuggest();
}

/** Puts a past line back in the bar and lists what it finds; a 设为 in it is only offered, never run. */
function runHistoryLine(text) {
  commandInput().value = text;
  app.updateShelfSearchClear();
  recordSearch(text);
  clearTimeout(appState.searchTimer);
  shelfFilters.q = text;
  app.loadShelfBooks(1, { ai: true });
}

function historySuggestions() {
  return commandState.history.map(text => ({ kind: '最近', label: text, hint: '', run: () => runHistoryLine(text) }));
}

function searchSuggestion(command) {
  const statusNames = STATUS_WORDS.filter(([, val]) => shelfGroupValues('status').includes(val)).map(([word]) => word);
  const scope = [...new Set(statusNames), command.query].filter(Boolean).join(' · ');
  const isSearching = Boolean(document.querySelector('.search-input-wrap.is-searching'));
  const found = isSearching ? '搜索中…' : `找到 ${appState.shelfLastTotal.toLocaleString()} 本`;
  return { kind: '搜索', label: `${scope || '全部'} · ${found}`, hint: '回车',
    run: () => { rememberCommandText(); /** @type {HTMLElement} */ (document.activeElement).blur(); focusFirstHomeRow(); } };
}

function actionSuggestion(command) {
  const total = appState.shelfLastTotal;
  if (total > MAX_BATCH_BOOKS) {
    return { kind: '操作', label: `找到 ${total.toLocaleString()} 本，超过 ${MAX_BATCH_BOOKS} 本，先缩小范围再设为「${command.setWord}」`,
      hint: '', isDisabled: true, run: () => {} };
  }
  return { kind: '操作', label: `把这 ${total} 本设为「${command.setWord}」`, hint: '可撤销', run: () => setStatusOnResults(command.setWord) };
}

async function setStatusOnResults(word) {
  try {
    const ids = await app.fetchShelfIds(MAX_BATCH_BOOKS);
    if (ids.length) await applyMarksToBooks(ids, SET_MARKS[word], word);
  } catch (e) {
    console.error('command batch failed', word, e);
    showMobileNotice('操作失败：连不上服务器');
  }
}

function jumpSuggestions(query) {
  if (!query) return [];
  const tabs = JUMP_TABS.filter(([, label]) => query.includes(label))
    .map(([tab, label]) => ({ kind: '跳转', label: `去「${label}」`, hint: '', run: () => app.switchTab(tab) }));
  const filters = SHELF_FILTER_GROUPS.flatMap(g => g.options
    .filter(([, label]) => label.includes(query) || query.includes(label))
    .map(([val, label]) => ({ kind: '筛选', label: `只看「${label}」`, hint: g.title, run: () => runFilterJump(g.key, val) })));
  return [...tabs, ...filters].slice(0, COMMAND_JUMP_MATCHES);
}

function runFilterJump(key, val) {
  /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input')).value = '';
  shelfFilters.q = '';
  if (!shelfGroupValues(key).includes(val)) app.toggleShelfFilter(key, val);
  else app.loadShelfBooks(1);
}

function buildSuggestions(raw) {
  const command = parseCommand(raw);
  const pending = (appState.libraryCounts.library || {}).pending;
  return [
    searchSuggestion(command),
    ...(command.setWord ? [actionSuggestion(command)] : []),
    ...(command.wantsOrganize ? [{ kind: '整理', label: `处理待确认 ${Number(pending || 0).toLocaleString()} 项`, hint: '',
      run: () => app.switchTab('organize') }] : []),
    ...homeItems().slice(0, COMMAND_BOOK_MATCHES).map(b => ({ kind: '书', label: splitBilingualTitle(b.clean_title || b.title).main,
      hint: b.author || '', run: () => app.openBookModal(b.id) })),
    ...jumpSuggestions(command.query),
  ];
}

export function hideCommandSuggest() {
  commandState.suggestions = [];
  document.getElementById('cmd-suggest')?.classList.add('hidden');
}

/** Under the command bar while it has focus: the recent searches when it is empty; with text, search, action,
 * organize, matching books and jumps. */
export function renderCommandSuggest() {
  const box = document.getElementById('cmd-suggest');
  const input = commandInput();
  const text = input.value.trim();
  const suggestions = text ? buildSuggestions(text) : historySuggestions();
  if (!box || !isDesktopHome() || !suggestions.length || document.activeElement !== input) {
    hideCommandSuggest();
    return;
  }
  commandState.suggestions = suggestions;
  commandState.index = -1;
  box.innerHTML = commandState.suggestions.map((item, i) => `<button class="cmd-item" data-index="${i}" ${item.isDisabled ? 'disabled' : ''}
      onmousedown="event.preventDefault(); runCommandSuggest(${i})"><b class="cmd-kind">${escapeHtml(item.kind)}</b>
      <span class="cmd-label">${escapeHtml(item.label)}</span><span class="cmd-hint">${escapeHtml(item.hint)}</span></button>`).join('')
    + '<div class="cmd-foot">↑↓ 选择 · 回车 执行 · Esc 清空</div>';
  box.classList.remove('hidden');
}

export function runCommandSuggest(index) {
  const item = commandState.suggestions[index];
  if (!item || item.isDisabled) return;
  hideCommandSuggest();
  item.run();
}

function moveCommandSuggest(delta) {
  // Positions run -1 (nothing picked, Enter searches) to count - 1, wrapping at both ends
  const slots = commandState.suggestions.length + 1;
  commandState.index = (((commandState.index + 1 + delta) % slots) + slots) % slots - 1;
  document.querySelectorAll('#cmd-suggest .cmd-item').forEach(el => {
    el.classList.toggle('active', Number(/** @type {HTMLElement} */ (el).dataset.index) === commandState.index);
  });
}

/** Keys inside the command bar; true when the key was handled here and the box's own Enter search must not run. */
export function onCommandKey(event) {
  if (!isDesktopHome() || event.isComposing) return false;
  if (event.key === 'Escape') {
    app.clearShelfSearch();
    hideCommandSuggest();
    /** @type {HTMLElement} */ (event.target).blur();
    return true;
  }
  if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && commandState.suggestions.length) {
    event.preventDefault();
    moveCommandSuggest(event.key === 'ArrowDown' ? 1 : -1);
    return true;
  }
  if (event.key === 'Enter' && commandState.index >= 0) {
    runCommandSuggest(commandState.index);
    return true;
  }
  // The search runs and the box lets go of the keys, so j/k walk the results at once
  if (event.key === 'Enter') {
    rememberCommandText();
    /** @type {HTMLElement} */ (event.target).blur();
  }
  return false;
}
