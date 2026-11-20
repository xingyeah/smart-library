// "All books by this author": a shelf filter entered from the book detail's author line.
// The back gesture leaves it like a layer and puts the shelf back the way it was before the click.
import { appState, langSelections, shelfFilters } from './state.js';
import { loadShelfBooks, switchTab, syncDesktopShelfChips } from './shelf.js';

/** @type {{ filters: object, languages: string[], query: string, sort: string, sortTouched: boolean } | null} */
let shelfBeforeAuthor = null;

const searchInput = () => /** @type {HTMLInputElement} */ (document.getElementById('shelf-search-input'));
const sortSelect = () => /** @type {HTMLSelectElement} */ (document.getElementById('shelf-sort'));

function showShelf() {
  syncDesktopShelfChips();
  if (appState.currentTab === 'shelf') loadShelfBooks(1, { scrollToTop: true });
  else switchTab('shelf');
}

/** Clears every other filter and the search box, then lists the books naming `name` as an author. */
export function showAuthorBooks(name) {
  if (!name) return;
  if (!shelfFilters.author) {
    shelfBeforeAuthor = {
      filters: { ...shelfFilters }, languages: Array.from(langSelections.shelf),
      query: searchInput().value, sort: sortSelect().value, sortTouched: appState.shelfSortTouched,
    };
  }
  Object.assign(shelfFilters, {
    q: '', category: '', language: '', format: '', source_dir: '', reading_status: '',
    pub_period: '', only_favorite: false, has_rating: false, author: name, page: 1,
  });
  langSelections.shelf.clear();
  searchInput().value = '';
  appState.shelfSortTouched = false;
  showShelf();
}

/** The chip's ✕: drops the author and keeps whatever else is picked now. */
export function exitAuthorFilter() {
  shelfFilters.author = '';
  shelfBeforeAuthor = null;
  showShelf();
}

/** Back gesture: the shelf as it was before the author was clicked. */
export function closeAuthorFilterLayer() {
  const before = shelfBeforeAuthor;
  shelfBeforeAuthor = null;
  if (!before) {
    exitAuthorFilter();
    return;
  }
  Object.assign(shelfFilters, before.filters, { author: '', page: 1 });
  langSelections.shelf.clear();
  before.languages.forEach(code => langSelections.shelf.add(code));
  searchInput().value = before.query;
  sortSelect().value = before.sort;
  appState.shelfSortTouched = before.sortTouched;
  showShelf();
}
