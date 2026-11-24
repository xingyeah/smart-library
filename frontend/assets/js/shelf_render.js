import { COVER_THUMB_WIDTH, READING_STATUS_LABEL, RATING_SOURCE_NAMES, shelfFilters } from './state.js';
import { escapeHtml, bookCoverImgHtml, timeAgo } from './utils.js';
import { icon } from './icons.js';

export function readProgressHtml(b, actionsHtml = '') {
  if (!b.last_opened_at) return '';
  const pct = b.read_progress != null ? Math.round(b.read_progress * 100) : null;
  const label = ((b.read_pos || {}).label || '').slice(0, 24);
  return `
    <div class="read-progress" style="margin:8px 0 4px;" onclick="event.stopPropagation()">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:12px; color:var(--text-muted);">
        <span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(label)}">${pct != null ? `已读 ${pct}%` : '已打开'} · ${timeAgo(b.last_opened_at)}</span>
        <button class="btn-icon" style="white-space:nowrap;" onclick="openOnlineReader(${b.id})">继续阅读</button>
        ${actionsHtml}
      </div>
      <div style="height:4px; border-radius:6px; background:var(--border); margin-top:5px; overflow:hidden;">
        <div style="height:100%; width:${pct || 0}%; background:var(--accent);"></div>
      </div>
    </div>`;
}

/** The detail page's author line: each named person links to all their books, placeholders stay plain text. */
export function authorLinksHtml(b) {
  const people = b.authors && b.authors.length ? b.authors : [{ name: b.author || '', linkable: false }];
  return people.filter(p => p.name).map(p => (p.linkable
    ? `<button type="button" class="author-link" data-author="${escapeHtml(p.name)}" title="查看这位作者的全部书"
        onclick="closeBookModal(); showAuthorBooks(this.dataset.author)">${escapeHtml(p.name)}</button>`
    : `<span>${escapeHtml(p.name)}</span>`)).join('、');
}

/** The author filter as a removable chip; '' when no author is picked. */
export function authorFilterChipHtml(className) {
  if (!shelfFilters.author) return '';
  return `<button class="${className}" onclick="exitAuthorFilter()" title="去掉作者筛选">作者：${escapeHtml(shelfFilters.author)}${icon('close')}</button>`;
}

export function getReaderButtonHtml(b) {
  if (!b.last_opened_at) return `<button class="btn btn-primary" onclick="openOnlineReader(${b.id})">在线阅读</button>`;
  const pct = b.read_progress != null ? ` ${Math.round(b.read_progress * 100)}%` : '';
  return `<button class="btn btn-primary" onclick="openOnlineReader(${b.id})">继续阅读${pct}</button>`;
}


// A search hit's reason is a long list; the card shows its gist and keeps the whole text as the tooltip
function searchReasonLine(b) {
  const concepts = (b.matched_concepts || []).slice(0, 2);
  const chapters = (b.matched_chapters || []).length;
  if (!concepts.length && !chapters) return b.recommend_reason;
  const parts = concepts.length ? [`命中：${concepts.join('、')}`] : [];
  if (chapters) parts.push(`相关章节 ${chapters} 处`);
  return parts.join(' · ');
}

// After AI reordering the standard match percentage no longer explains the order, so only the rank shows
function rankLabel(b, opts) {
  return b.match_score && !opts.isAiOrder ? `${b.rank} · 契合 ${b.match_score}%` : `${b.rank}`;
}

/** opts.showRank: show the rank line; opts.isAiOrder: the list is in AI order, so the rank stands alone. */
const LANGUAGE_BADGES = {
  'zh-CN': '<span class="tag-xs lang-cn" title="简体中文">简体</span>',
  'zh-TW': '<span class="tag-xs lang-tw" title="繁体中文">繁体</span>',
  'en': '<span class="tag-xs lang-en" title="英文原版">英文</span>',
};

/** Where the book came from and how good its record is: the classic score once it is high enough to mean something. */
export function sourceQualityText(b) {
  const score = b.classic_score >= 42 ? `经典度 ${Math.round(b.classic_score)}` : `质量 ${Math.round(b.quality_score || 0)}`;
  return `${b.source_dir || '本地书库'} · ${score}`;
}

/** Brings every card of one book on the page in line with a change made anywhere (card, detail view): the same
 * book can sit in several lists at once. state: { isFavorite?, readingStatus? }; a field left out is not touched. */
export function syncBookCards(bookId, state) {
  document.querySelectorAll(`.book-card[data-book-id="${Number(bookId)}"]`).forEach(card => {
    const fav = /** @type {HTMLElement | null} */ (card.querySelector('.card-fav'));
    if (fav && state.isFavorite !== undefined) {
      fav.classList.toggle('fav-active', state.isFavorite);
      fav.title = state.isFavorite ? '已喜欢' : '喜欢';
      fav.setAttribute('aria-label', fav.title);
      fav.setAttribute('onclick', `toggleFavorite(${bookId}, ${state.isFavorite ? 0 : 1}, this)`);
    }
    const statusBtn = card.querySelector('.card-status');
    if (statusBtn && state.readingStatus !== undefined) {
      statusBtn.textContent = READING_STATUS_LABEL[state.readingStatus] || '未读';
      statusBtn.classList.toggle('has-status', state.readingStatus !== 'unread');
      statusBtn.setAttribute('onclick', `cycleReadingStatus(${bookId}, '${state.readingStatus}', this)`);
    }
  });
}

const BILINGUAL_TITLE = /^(.*?)\s*[(（]([^()（）]+)[)）]\s*$/;
const CJK_CHAR = /[\u4e00-\u9fff]/;
const LATIN_LETTER = /[A-Za-z]/;

/** '价值评估 (Valuation …)' → the Chinese name and the English original; any other title stays whole in `main`. */
export function splitBilingualTitle(title) {
  const match = BILINGUAL_TITLE.exec(title || '');
  if (!match || !CJK_CHAR.test(match[1]) || CJK_CHAR.test(match[2]) || !LATIN_LETTER.test(match[2])) {
    return { main: title || '', sub: '' };
  }
  return { main: match[1], sub: match[2] };
}

export function renderBookCard(b, opts = {}) {
  const coverHtml = bookCoverImgHtml(b);
  const rankHtml = opts.showRank && b.rank
    ? `<div class="rank-medal" translate="no">${rankLabel(b, opts)}</div>`
    : '';
  const reasonHtml = b.recommend_reason
    ? `<div class="rec-reason-box" title="${escapeHtml(b.recommend_reason)}">${escapeHtml(searchReasonLine(b))}</div>`
    : '';
  const favClass = b.is_favorite ? 'fav-active' : '';
  const status = b.reading_status || 'unread';
  const langBadgeHtml = LANGUAGE_BADGES[b.language] || LANGUAGE_BADGES['zh-CN'];
  const { main: titleMain, sub: titleSub } = splitBilingualTitle(b.clean_title || b.title);

  const ratingDetail = (b.rating_sources || [])
    .map(r => `${RATING_SOURCE_NAMES[r.source] || r.source} ${Number(r.rating).toFixed(1)}（${r.votes} 人）`).join(' · ');
  const ratingBadgeHtml = b.composite_rating
    ? `<span class="tag-xs rating" style="background:rgba(202,138,4,0.1);color:#a16207;border:1px solid rgba(202,138,4,0.3);font-weight:600;" title="综合分 · ${escapeHtml(ratingDetail)}">${icon('star')}${Number(b.composite_rating).toFixed(1)}</span>`
    : '';

  // An opened book already has a progress row; the heart and status join it there instead of adding a row
  const actionsHtml = `<span class="card-actions" onclick="event.stopPropagation()">
        <button class="btn-icon card-fav ${favClass}" onclick="toggleFavorite(${b.id}, ${b.is_favorite ? 0 : 1}, this)" aria-label="${b.is_favorite ? '已喜欢' : '喜欢'}" title="${b.is_favorite ? '已喜欢' : '喜欢'}">${icon('heart')}</button>
        <button class="btn-icon card-status${status === 'unread' ? '' : ' has-status'}" onclick="cycleReadingStatus(${b.id}, '${status}', this)" title="切换阅读状态">${READING_STATUS_LABEL[status] || '未读'}</button>
      </span>`;

  return `
    <article class="book-card" data-book-id="${b.id}" onclick="openBookModal(${b.id})">
      <div class="book-card-top">
        <div class="book-cover">
          <span class="cover-fallback-cat">${escapeHtml(b.category || '藏书')}</span>
          <div class="cover-fallback-title">${escapeHtml(b.clean_title || b.title)}</div>
          <div class="cover-fallback-author">${escapeHtml(b.author)}</div>
          ${coverHtml}
        </div>
        <div class="book-info">
          ${rankHtml}
          <div class="book-title" title="${escapeHtml(b.title)}">
            ${escapeHtml(titleMain)}${b.volume_part ? ` <span style="font-size:12px;color:var(--text-muted);">(${escapeHtml(b.volume_part)})</span>` : ''}
          </div>
          ${titleSub ? `<div class="book-subtitle" title="${escapeHtml(titleSub)}">${escapeHtml(titleSub)}</div>` : ''}
          <div class="book-author">${escapeHtml(b.author)}${b.pub_year ? ` · ${escapeHtml(b.pub_year)}` : ''}</div>
          <div class="book-badges">
            ${langBadgeHtml}
            <span class="tag-xs cat">${escapeHtml(b.category)}</span>
            ${ratingBadgeHtml}
            ${b.offline_partial ? '<span class="tag-xs partial" title="在线读过的内容已缓存，未读部分离线不可用">部分缓存</span>' : ''}
          </div>
        </div>
      </div>
      ${reasonHtml}
      <div class="book-summary">${escapeHtml(b.summary || '暂无简介')}</div>
      ${b.last_opened_at ? readProgressHtml(b, actionsHtml) : `<div class="book-footer">${actionsHtml}</div>`}
    </article>
  `;
}

/** @returns {Array<number | { type: string, jumpTo: number }>} */
export function generatePaginationItems(current, total) {
  if (total <= 1) return [];
  if (total <= 10) {
    const items = [];
    for (let i = 1; i <= total; i++) items.push(i);
    return items;
  }

  let left = Math.max(2, current - 3);
  let right = Math.min(total - 1, current + 3);

  if (current <= 5) {
    left = 2;
    right = 8;
  } else if (current >= total - 4) {
    right = total - 1;
    left = total - 7;
  }

  /** @type {Array<number | { type: string, jumpTo: number }>} */
  const items = [1];
  if (left > 2) {
    items.push({ type: 'ellipsis', jumpTo: Math.max(1, current - 5) });
  }
  for (let p = left; p <= right; p++) {
    items.push(p);
  }
  if (right < total - 1) {
    items.push({ type: 'ellipsis', jumpTo: Math.min(total, current + 5) });
  }
  items.push(total);

  return items;
}

