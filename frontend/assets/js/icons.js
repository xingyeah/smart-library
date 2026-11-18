// The few places a shape reads faster than a word. One line style, coloured by the text around it;
// a filled look (a liked heart) comes from CSS on the button, not from a second icon.
const ICON_PATHS = {
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>',
  star: '<polygon points="12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 8.9 8.3 12 2"/>',
  close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  chevron: '<polyline points="6 9 12 15 18 9"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
};

/** Inline SVG markup for one of ICON_PATHS; throws on an unknown name so a typo shows at once. */
export function icon(name) {
  const paths = ICON_PATHS[name];
  if (!paths) throw new Error(`unknown icon: ${name}`);
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}

export function favoriteLabelHtml(isFavorite) {
  return `${icon('heart')}${isFavorite ? '已喜欢' : '喜欢'}`;
}

export function wantLabelHtml(isWant) {
  return `${icon('star')}${isWant ? '已想读' : '想读'}`;
}
