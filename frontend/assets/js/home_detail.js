// The desktop home's right column: a short version of the book detail. The whole detail (reviews, contents,
// files, the edit form) opens as the usual dialog from 完整详情.
import { COVER_DETAIL_WIDTH } from './state.js';
import { escapeHtml } from './utils.js';
import { favoriteLabelHtml, wantLabelHtml } from './icons.js';
import { getReaderButtonHtml, splitBilingualTitle } from './shelf_render.js';

const SIMILAR_COVERS = 4;

/** The compact detail of book `b` (a /api/books/{id} answer); the similar books fill #detail-similar-slot later. */
export function homeDetailHtml(b) {
  const { main, sub } = splitBilingualTitle(b.clean_title || b.title);
  const cover = b.cover_path
    ? `<img class="home-detail-cover" src="${escapeHtml(b.cover_path)}?w=${COVER_DETAIL_WIDTH}" alt="" onerror="this.remove()" />`
    : '';
  return `<button class="btn home-detail-close" onclick="closeBookModal()" aria-label="关闭">×</button>${cover}
    <h3 class="home-detail-title" title="${escapeHtml(b.title)}">${escapeHtml(main)}</h3>
    ${sub ? `<div class="home-detail-sub">${escapeHtml(sub)}</div>` : ''}
    <div class="home-detail-by">${escapeHtml(b.author || '')}${b.category ? ` · ${escapeHtml(b.category)}` : ''}</div>
    <p class="home-detail-summary">${escapeHtml(b.summary || '暂无简介')}</p>
    <div class="home-detail-actions">
      ${getReaderButtonHtml(b)}
      <button id="modal-want-btn" class="btn${b.reading_status === 'want' ? ' fav-active' : ''}" onclick="toggleModalWant(${b.id})">${wantLabelHtml(b.reading_status === 'want')}</button>
      <button id="modal-fav-btn" class="btn${b.is_favorite ? ' fav-active' : ''}" onclick="toggleModalFavorite(${b.id})" aria-label="喜欢">${favoriteLabelHtml(b.is_favorite)}</button>
    </div>
    <button class="home-detail-more" onclick="openBookDialog(${b.id})">完整详情 ›</button>
    <div id="detail-similar-slot"></div>`;
}

/** The similar books as a row of covers; '' when there are none. */
export function homeSimilarHtml(items) {
  const covers = items.slice(0, SIMILAR_COVERS).map(sb => {
    const title = escapeHtml(sb.clean_title || sb.title);
    const face = sb.cover_path
      ? `<img src="${escapeHtml(sb.cover_path)}?w=${COVER_DETAIL_WIDTH}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'), { className: 'home-similar-text', textContent: this.parentElement.title }))" />`
      : `<span class="home-similar-text">${title}</span>`;
    return `<button class="home-similar" title="${title} · ${escapeHtml(sb.author || '')}" onclick="openBookModal(${sb.id})">${face}</button>`;
  }).join('');
  return covers ? `<div class="home-detail-label">相似的书</div><div class="home-similar-row">${covers}</div>` : '';
}
