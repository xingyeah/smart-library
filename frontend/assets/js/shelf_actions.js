import { loadStats } from './api.js';
import { loadShelfBooks } from './shelf.js';
import { syncBookCards } from './shelf_render.js';
import { appState, shelfFilters } from './state.js';

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
