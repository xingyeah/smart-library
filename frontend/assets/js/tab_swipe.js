// Phone: a sideways swipe on the page moves between 今晚 and 书架, like tapping the tabs. App 设置 opens a native
// screen, so a swipe never goes there.
import { MOBILE_QUERY, appState } from './state.js';
import { switchTab } from './shelf.js';

const SWIPE_TABS = ['tonight', 'shelf'];
const MIN_SWIPE_PX = 70;
const MAX_SWIPE_MS = 700;
// Mostly sideways: a slanted vertical scroll must not flip the page
const SIDEWAYS_RATIO = 2;
// Places with a sideways gesture of their own: the tonight cards, text fields, and rows that scroll sideways
const OWN_GESTURE = '.tonight-card, input, textarea, select';

function scrollsSideways(element, view) {
  for (let node = element; node && node !== view; node = node.parentElement) {
    if (node.scrollWidth > node.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(node).overflowX)) return true;
  }
  return false;
}

function swipeView(target) {
  const view = target instanceof Element ? target.closest('#view-tonight, #view-shelf') : null;
  if (!view || target.closest(OWN_GESTURE) || scrollsSideways(target, view)) return null;
  return view;
}

/** Listens for sideways swipes on the phone. */
export function initTabSwipe() {
  /** @type {{ x: number, y: number, at: number } | null} */
  let start = null;
  document.addEventListener('touchstart', event => {
    const isPhone = window.matchMedia(MOBILE_QUERY).matches;
    start = isPhone && event.touches.length === 1 && swipeView(event.target)
      ? { x: event.touches[0].clientX, y: event.touches[0].clientY, at: Date.now() } : null;
  }, { passive: true });
  document.addEventListener('touchend', event => {
    if (!start) return;
    const dx = event.changedTouches[0].clientX - start.x;
    const dy = event.changedTouches[0].clientY - start.y;
    const isSwipe = Math.abs(dx) >= MIN_SWIPE_PX && Math.abs(dx) >= SIDEWAYS_RATIO * Math.abs(dy)
      && Date.now() - start.at <= MAX_SWIPE_MS;
    start = null;
    if (!isSwipe) return;
    const next = SWIPE_TABS[SWIPE_TABS.indexOf(appState.currentTab) + (dx < 0 ? 1 : -1)];
    if (next) switchTab(next);
  }, { passive: true });
}
