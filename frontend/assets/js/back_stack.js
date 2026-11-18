// Back gesture / browser back closes the topmost open layer instead of leaving the page.
// One history entry stands for "some layer is open"; it is added when the first layer opens,
// consumed by a back gesture, and re-added while more layers remain.
import { closeBookModal } from './book_modal.js';
import { closeOnlineReader, closeMobileSheets } from './reader.js';
import { closeShelfSheet } from './shelf.js';

const BACK_STATE_KEY = 'libraryLayerOpen';

// Topmost first; a layer counts as open while its element has the `open` class
const LAYERS = [
  { elementId: 'shelf-sheet', close: closeShelfSheet },
  { elementId: 'm-sheet-backdrop', close: closeMobileSheets },
  { elementId: 'immersive-reader-overlay', close: closeOnlineReader },
  { elementId: 'book-modal', close: closeBookModal },
];

let isIgnoringNextPop = false;

function hasBackEntry() {
  return Boolean(history.state && history.state[BACK_STATE_KEY]);
}

function topOpenLayer() {
  return LAYERS.find(layer => document.getElementById(layer.elementId)?.classList.contains('open'));
}

function syncBackEntry() {
  if (isIgnoringNextPop) return;
  const isLayerOpen = Boolean(topOpenLayer());
  if (isLayerOpen && !hasBackEntry()) {
    history.pushState({ [BACK_STATE_KEY]: true }, '');
  } else if (!isLayerOpen && hasBackEntry()) {
    // Closed by a button: drop the entry so the next back gesture is not a no-op
    isIgnoringNextPop = true;
    history.back();
  }
}

function onPopState() {
  if (isIgnoringNextPop) {
    isIgnoringNextPop = false;
  } else {
    topOpenLayer()?.close();
  }
  syncBackEntry();
}

export function initBackStack() {
  // Opens and closes happen in many places; watching the classes keeps them all in step
  const observer = new MutationObserver(syncBackEntry);
  LAYERS.forEach(layer => {
    const element = document.getElementById(layer.elementId);
    if (element) observer.observe(element, { attributes: true, attributeFilter: ['class'] });
  });
  window.addEventListener('popstate', onPopState);
  // The iPhone app's edge swipe; with nothing open there is nothing to go back to
  window.libraryAppBack = () => { if (hasBackEntry()) history.back(); };
  syncBackEntry();
}
