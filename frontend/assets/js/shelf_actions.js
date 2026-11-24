import { loadStats } from './api.js';
import { loadShelfBooks } from './shelf.js';
import { syncBookCards } from './shelf_render.js';
import { appState, READING_STATUS_LABEL, shelfFilters } from './state.js';
import { showUndoToast } from './utils.js';

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

async function saveReadingStatus(bookId, status) {
  const r = await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reading_status: status }),
  });
  if (!r.ok) return false;
  // Update the button itself: interest and describe views are not reloaded (that would drop the card and re-run AI)
  syncBookCards(bookId, { readingStatus: status });
  loadStats();
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
  return true;
}

export async function cycleReadingStatus(bookId, currentSt) {
  const order = ['unread', 'want', 'reading', 'read'];
  const nextSt = order[(order.indexOf(currentSt) + 1) % order.length];
  if (!(await saveReadingStatus(bookId, nextSt))) return;
  // One tap writes the library and the button shows the state, not the action: a slip must be cheap to take back
  showUndoToast(`已设为「${READING_STATUS_LABEL[nextSt]}」`, () => saveReadingStatus(bookId, currentSt));
}
