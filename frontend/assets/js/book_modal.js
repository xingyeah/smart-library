import { appState, shelfFilters, COVER_DETAIL_WIDTH, RATING_SOURCE_NAMES } from './state.js';
import { escapeHtml, bookCoverImgHtml } from './utils.js';
import { icon, favoriteLabelHtml, wantLabelHtml } from './icons.js';
import { authorLinksHtml, getReaderButtonHtml, sourceQualityText, syncBookCards } from './shelf_render.js';
import { loadStats } from './api.js';
import { loadShelfBooks } from './shelf.js';
import { loadMismergedBooks } from './organize.js';

// The phone shows the modal full screen, so the loading and error states need their own way back
function bookModalStatusHtml(message) {
  return `<div style="padding:40px 20px;text-align:center;color:var(--text-muted);">
    <div>${escapeHtml(message)}</div>
    <button class="btn" style="margin-top:18px;" onclick="closeBookModal()">‹ 返回</button>
  </div>`;
}

async function fetchBookDetail(bookId) {
  try {
    const r = await fetch(`/api/books/${bookId}`);
    const b = await r.json();
    return r.ok ? { book: b } : { error: b.error || `服务器返回 ${r.status}` };
  } catch (e) {
    console.error('book detail failed', bookId, e);
    return { error: '连不上服务器' };
  }
}

export async function openBookModal(bookId) {
  const modal = document.getElementById('book-modal');
  const box = document.getElementById('book-modal-content');
  modal.classList.add('open');
  box.innerHTML = bookModalStatusHtml('正在加载书籍详情...');

  const { book: b, error } = await fetchBookDetail(bookId);
  if (error) {
    box.innerHTML = bookModalStatusHtml(`详情加载失败：${error}`);
    return;
  }
  appState.currentActiveBookDetail = b;

  box.innerHTML = `
    ${detailCornerHtml(b)}
    ${detailHeadHtml(b)}
    ${detailSummaryHtml(b)}
    ${detailReviewsHtml(b)}
    <div id="detail-similar-slot"></div>
    ${detailFoldsHtml(b)}
  `;
  loadSimilarBooks(b.id);
}

// The vector search and its rows are most of the detail's cost, so they arrive after the detail shows
async function loadSimilarBooks(bookId) {
  let items = [];
  try {
    const r = await fetch(`/api/books/${bookId}/similar`);
    if (r.ok) items = (await r.json()).items || [];
  } catch (e) {
    console.error('similar books failed', bookId, e);
  }
  const slot = document.getElementById('detail-similar-slot');
  // Another book may have been opened while this was loading
  if (!slot || !appState.currentActiveBookDetail || appState.currentActiveBookDetail.id !== bookId) return;
  slot.outerHTML = detailSimilarHtml(items);
}

function detailCornerHtml(b) {
  const hideMenu = "document.getElementById('detail-more-menu').classList.add('hidden')";
  return `
    <div class="detail-corner">
      <button class="btn" onclick="event.stopPropagation(); document.getElementById('detail-more-menu').classList.toggle('hidden')" aria-label="更多">${icon('more')}</button>
      <button class="btn" onclick="closeBookModal()" aria-label="关闭">${icon('close')}</button>
      <div class="detail-more-menu hidden" id="detail-more-menu">
        <button id="modal-more-want-btn" onclick="toggleModalWant(${b.id}); ${hideMenu}">${b.reading_status === 'want' ? '取消想读' : '想读'}</button>
        <button id="modal-more-fav-btn" onclick="toggleModalFavorite(${b.id}); ${hideMenu}">${b.is_favorite ? '取消喜欢' : '喜欢'}</button>
        ${window.libraryAppOffline ? `<button onclick="toggleAppOfflineCopy(${b.id})">${window.libraryAppOffline.has(b.id) ? '删除离线副本' : '下载离线'}</button>` : ''}
        <button class="danger" onclick="triggerDeleteBook(${b.id})" title="文件移到 data/removed_books/，从书库移除">删除本书</button>
      </div>
    </div>`;
}

// External ratings, falling back to the douban columns of the book itself for books enriched before external_meta
function externalRatings(b) {
  const ratings = ((b.external_meta && b.external_meta.ratings) || [])
    .filter(r => Number(r.rating) > 0 || Number(r.raw_rating) > 0);
  if (ratings.length || !(Number(b.douban_rating) > 0)) return ratings;
  return [{ source: 'douban', raw_rating: Number(b.douban_rating), votes: Number(b.douban_votes || 0) }];
}

// One quiet line under the author: the composite score stands out, the rest reads as plain facts
function detailMetaHtml(b) {
  const composite = b.external_meta && b.external_meta.composite;
  const parts = externalRatings(b).map(r => {
    const votes = r.votes ? `（${Number(r.votes).toLocaleString()} 人）` : '';
    return `${escapeHtml(RATING_SOURCE_NAMES[r.source] || r.source)} ${escapeHtml(r.raw_rating || r.rating)}${votes}`;
  });
  parts.push(escapeHtml(b.category), escapeHtml(String(b.primary_format || '').toUpperCase()), escapeHtml(sourceQualityText(b)));
  const compositeHtml = composite
    ? `<span class="detail-score" title="综合分：按评价人数加权，人少时向 7.5 收敛">${icon('star')}${Number(composite).toFixed(1)}</span>`
    : '';
  return `<div class="detail-meta">${compositeHtml}<span>${parts.filter(Boolean).join(' · ')}</span></div>`;
}

function detailHeadHtml(b) {
  const others = [b.translator ? `${b.translator} 译` : '', b.publisher, b.pub_year].filter(Boolean).map(escapeHtml);
  const people = [authorLinksHtml(b), ...others].filter(Boolean);
  return `
    <div class="detail-head">
      ${b.cover_path ? `<img class="detail-cover" src="${escapeHtml(b.cover_path)}?w=${COVER_DETAIL_WIDTH}" alt="" onerror="this.remove()" />` : ''}
      <div class="detail-main">
        <h2 class="detail-title" onclick="this.classList.toggle('expanded')" title="${escapeHtml(b.title)}">${escapeHtml(b.clean_title || b.title)}</h2>
        ${b.subtitle ? `<div class="detail-subtitle">${escapeHtml(b.subtitle)}</div>` : ''}
        <div class="detail-people">${people.join(' · ')}</div>
        ${detailMetaHtml(b)}
        <div class="detail-actions">
          ${getReaderButtonHtml(b)}
          <button id="modal-want-btn" class="btn${b.reading_status === 'want' ? ' fav-active' : ''}" onclick="toggleModalWant(${b.id})">${wantLabelHtml(b.reading_status === 'want')}</button>
          <button id="modal-fav-btn" class="btn${b.is_favorite ? ' fav-active' : ''}" onclick="toggleModalFavorite(${b.id})">${favoriteLabelHtml(b.is_favorite)}</button>
        </div>
      </div>
    </div>`;
}

function detailSummaryHtml(b) {
  const points = (b.core_points || []).map(point => `<li>${escapeHtml(point)}</li>`).join('');
  return `
    <section class="detail-section">
      <div class="detail-section-head">
        <h3>简介</h3>
        <button id="btn-ai-refresh" class="detail-link" onclick="triggerAiRefresh(${b.id})">AI 重新提炼</button>
      </div>
      <p class="detail-summary${b.summary ? '' : ' is-empty'}">${escapeHtml(b.summary || '暂无简介。点右上方“AI 重新提炼”可从正文生成。')}</p>
      ${b.target_audience ? `<p class="detail-note"><b>适合读者</b>　${escapeHtml(b.target_audience)}</p>` : ''}
      ${points ? `<div class="detail-note"><b>看点</b><ul>${points}</ul></div>` : ''}
    </section>`;
}

function detailReviewsHtml(b) {
  const reviews = ((b.external_meta && b.external_meta.reviews) || []).slice(0, 6);
  if (!reviews.length) return '';
  return `
    <section class="detail-section">
      <div class="detail-section-head"><h3>读者短评</h3></div>
      ${reviews.map(rv => `
        <div class="detail-review">
          <p>${escapeHtml(rv.content)}</p>
          <div class="detail-review-by">${escapeHtml(rv.author || '读者')} · ${escapeHtml(RATING_SOURCE_NAMES[rv.source] || rv.source || '外部')}</div>
        </div>`).join('')}
    </section>`;
}

function detailSimilarHtml(similarBooks) {
  const items = similarBooks.map(sb => `
    <div class="detail-similar" onclick="openBookModal(${sb.id})">
      <div class="book-cover sim-cover">
        <div class="cover-fallback-title">${escapeHtml(sb.clean_title || sb.title)}</div>
        ${bookCoverImgHtml(sb)}
      </div>
      <div style="min-width:0;">
        <div class="detail-similar-title">${escapeHtml(sb.clean_title || sb.title)}</div>
        <div class="detail-similar-by">${escapeHtml(sb.author)} · <span style="color:var(--accent-text);">相似度 ${sb.similarity}%</span></div>
      </div>
    </div>`).join('');
  if (!items) return '';
  return `
    <section class="detail-section">
      <div class="detail-section-head"><h3>相似的书</h3></div>
      <div class="detail-similar-grid">${items}</div>
    </section>`;
}

// Things looked up now and then: the contents, the files on disk and the edit form fold away at the bottom
function detailFoldsHtml(b) {
  const chapters = b.toc_text ? b.toc_text.split(' | ') : [];
  return `
    <section class="detail-section detail-folds">
      ${chapters.length ? `
      <details>
        <summary>目录 · ${chapters.length} 节</summary>
        <div class="detail-toc">${chapters.map(ch => `<div>${escapeHtml(ch)}</div>`).join('')}</div>
      </details>` : ''}
      <details>
        <summary>文件 · ${(b.files || []).length} 个版本</summary>
        ${detailFilesHtml(b)}
        <button class="btn" style="margin-top:8px; font-size:12px; padding:3px 10px;" onclick="triggerSplitFromModal(${b.id})" title="检查这些文件是不是混进了别的书，是就拆开">重新整理拆分</button>
      </details>
      <details>
        <summary>修订书目信息</summary>
        ${detailEditFormHtml(b)}
      </details>
    </section>`;
}

function detailFilesHtml(b) {
  return (b.files || []).map(f => `
    <div class="detail-file">
      <div style="min-width:0; word-break:break-all;">
        <span class="tag-xs fmt">${escapeHtml(f.format)}</span>
        <span class="tag-xs cat">${escapeHtml(f.source_name)}</span>
        <div class="detail-file-path">${escapeHtml(f.original_path)}</div>
        ${f.archived_path ? `<div class="detail-file-path">书库：${escapeHtml(f.archived_path)}</div>` : ''}
      </div>
      <a class="btn" style="padding:4px 10px; font-size:12px; text-decoration:none; flex-shrink:0;" href="/api/books/${b.id}/download?file_id=${f.id}">${icon('download')}下载</a>
    </div>`).join('');
}

function detailEditFormHtml(b) {
  const fields = [['edit-title', '书名', b.title, ''], ['edit-author', '作者', b.author, ''],
    ['edit-category', '分类', b.category, ''], ['edit-pub-year', '出版年份', b.pub_year, '如 2024'],
    ['edit-publisher', '出版社', b.publisher, '出版社名称']];
  return `
    <div class="detail-edit-grid">
      ${fields.map(([id, label, value, hint]) => `
        <label>${label}<input id="${id}" class="search-input" placeholder="${hint}" value="${escapeHtml(value || '')}" /></label>`).join('')}
    </div>
    <label class="detail-edit-summary">简介<textarea id="edit-summary" class="textarea-lg" rows="3">${escapeHtml(b.summary)}</textarea></label>
    <button class="btn btn-primary" style="margin-top:10px;" onclick="saveBookMeta(${b.id})">保存并重算向量</button>`;
}

export function closeBookModal() {
  document.getElementById('book-modal').classList.remove('open');
}

function _syncModalStatusButtons(book) {
  const wantBtn = document.getElementById('modal-want-btn');
  const moreWantBtn = document.getElementById('modal-more-want-btn');
  const isWant = book.reading_status === 'want';
  if (wantBtn) {
    wantBtn.classList.toggle('fav-active', isWant);
    wantBtn.innerHTML = wantLabelHtml(isWant);
  }
  if (moreWantBtn) moreWantBtn.textContent = isWant ? '取消想读' : '想读';

  const favBtn = document.getElementById('modal-fav-btn');
  const moreFavBtn = document.getElementById('modal-more-fav-btn');
  const isFav = Boolean(book.is_favorite);
  if (favBtn) {
    favBtn.classList.toggle('fav-active', isFav);
    favBtn.innerHTML = favoriteLabelHtml(isFav);
  }
  if (moreFavBtn) moreFavBtn.textContent = isFav ? '取消喜欢' : '喜欢';
}

export async function toggleModalWant(bookId) {
  const curr = appState.currentActiveBookDetail;
  const nextSt = (curr && curr.id === bookId && curr.reading_status === 'want') ? 'unread' : 'want';
  const r = await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reading_status: nextSt }),
  });
  if (!r.ok) return;
  const updated = await r.json();
  if (appState.currentActiveBookDetail && appState.currentActiveBookDetail.id === bookId) {
    appState.currentActiveBookDetail.reading_status = updated.reading_status || nextSt;
    _syncModalStatusButtons(appState.currentActiveBookDetail);
  }
  syncBookCards(bookId, { readingStatus: updated.reading_status || nextSt });
  loadStats();
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
}

export async function toggleModalFavorite(bookId) {
  const curr = appState.currentActiveBookDetail;
  const nextVal = !(curr && curr.id === bookId && curr.is_favorite);
  const r = await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_favorite: nextVal }),
  });
  if (!r.ok) return;
  const updated = await r.json();
  if (appState.currentActiveBookDetail && appState.currentActiveBookDetail.id === bookId) {
    appState.currentActiveBookDetail.is_favorite = updated.is_favorite ? 1 : 0;
    _syncModalStatusButtons(appState.currentActiveBookDetail);
  }
  syncBookCards(bookId, { isFavorite: Boolean(updated.is_favorite ?? nextVal) });
  loadStats();
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
}

export async function saveBookMeta(bookId) {
  const title = /** @type {HTMLInputElement} */ (document.getElementById('edit-title')).value.trim();
  const author = /** @type {HTMLInputElement} */ (document.getElementById('edit-author')).value.trim();
  const category = /** @type {HTMLInputElement} */ (document.getElementById('edit-category')).value.trim();
  const pub_year = (/** @type {HTMLInputElement | null} */ (document.getElementById('edit-pub-year'))?.value || '').trim();
  const publisher = (/** @type {HTMLInputElement | null} */ (document.getElementById('edit-publisher'))?.value || '').trim();
  const summary = /** @type {HTMLTextAreaElement} */ (document.getElementById('edit-summary')).value.trim();
  await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, author, category, pub_year, publisher, summary }),
  });
  openBookModal(bookId);
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
}

// AI never renames a book on its own: the user edits or rejects its suggestion here
function showTitleProposal(bookId, currentTitle, proposedTitle) {
  const head = document.querySelector('#book-modal-content .detail-head');
  if (!head) return;
  head.insertAdjacentHTML('afterend', `
    <div class="detail-title-proposal" id="detail-title-proposal">
      <label for="proposed-title-input">AI 建议的书名（可修改）</label>
      <input id="proposed-title-input" type="text" value="${escapeHtml(proposedTitle)}" />
      <div class="detail-title-proposal-current">当前：${escapeHtml(currentTitle)}</div>
      <div class="detail-title-proposal-actions">
        <button class="btn btn-primary" onclick="acceptProposedTitle(${bookId})">使用此书名</button>
        <button class="btn" onclick="document.getElementById('detail-title-proposal').remove()">保留原名</button>
      </div>
    </div>`);
}

export async function acceptProposedTitle(bookId) {
  const title = /** @type {HTMLInputElement} */ (document.getElementById('proposed-title-input')).value.trim();
  if (!title) return;
  const resp = await fetch(`/api/books/${bookId}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!resp.ok) { alert('书名保存失败'); return; }
  openBookModal(bookId);
  if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
}

export async function triggerAiRefresh(bookId) {
  const btn = /** @type {HTMLButtonElement | null} */ (document.getElementById('btn-ai-refresh'));
  if (!btn || btn.disabled) return;
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = '正在分析正文并重构...';

  try {
    const resp = await fetch(`/api/books/${bookId}/enrich_refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || 'AI 优化请求失败');
    }
    await openBookModal(bookId);
    if (data.enriched && data.enriched.proposed_title) showTitleProposal(bookId, data.book.title, data.enriched.proposed_title);
    if (typeof appState.currentTab !== 'undefined' && appState.currentTab === 'shelf') {
      loadShelfBooks(shelfFilters.page);
    }
  } catch (err) {
    alert('AI 纠错优化失败：' + (err.message || '请检查网络或 API Key'));
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

// The title is read here, not passed through onclick: a book title with a backslash could break out of the attribute
export async function postBookDelete(bookId, confirmedFileIds) {
  const resp = await fetch(`/api/books/${bookId}/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(confirmedFileIds ? { confirmed_file_ids: confirmedFileIds } : {}),
  });
  return { status: resp.status, data: await resp.json() };
}

export function multiFileDeleteMessage(displayTitle, files) {
  const others = files.filter(f => f.looks_like_other_book);
  const lines = files.map((f, i) => `  ${i + 1}. ${f.name}${f.looks_like_other_book ? '  ← 疑似别的书' : ''}`).join('\n');
  const warning = others.length
    ? `其中 ${others.length} 个（标「疑似别的书」）看起来是别的书，可能是误合并。建议点【取消】，先用详情里的【重新整理拆分】拆出来再删除。\n\n`
    : '';
  return `《${displayTitle}》关联了 ${files.length} 个文件，删除会把它们全部移到 data/removed_books/：\n\n${lines}\n\n${warning}确定全部删除吗？`;
}

export async function triggerDeleteBook(bookId) {
  const book = (appState.currentActiveBookDetail && appState.currentActiveBookDetail.id === bookId) ? appState.currentActiveBookDetail : {};
  const displayTitle = book.title || '本书';
  // A multi-file book is confirmed against the server's own file list below; a single file is confirmed here
  if ((book.files || []).length <= 1
      && !confirm(`确定要删除《${displayTitle}》吗？\n\n文件会移到 data/removed_books/，可以找回；书架和搜索里不再出现。`)) return;
  try {
    let res = await postBookDelete(bookId, null);
    if (res.status === 409 && res.data.needs_confirmation) {
      if (!confirm(multiFileDeleteMessage(displayTitle, res.data.files))) return;
      res = await postBookDelete(bookId, res.data.files.map(f => f.id));
    }
    if (res.status !== 200 || !res.data.ok) throw new Error(res.data.error || '删除操作未成功');
    closeBookModal();
    alert(`已删除《${displayTitle}》，文件已移至 data/removed_books/`);
    if (typeof appState.currentTab !== 'undefined' && appState.currentTab === 'shelf' && typeof loadShelfBooks === 'function') {
      loadShelfBooks(shelfFilters.page);
    }
    if (typeof loadStats === 'function') loadStats();
  } catch (err) {
    alert('删除图书失败：' + (err.message || '请检查系统状态'));
  }
}

export async function triggerSplitFromModal(bookId) {
  if (!confirm('确定要对本书关联的文件进行拓扑重新整理与拆分吗？\n\n系统将自动把互不兼容的其它异构书籍独立拆出立户，原书保留其主版本。')) return;
  try {
    const resp = await fetch('/api/organize/split_mismerged', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ book_id: bookId }),
    });
    const d = await resp.json();
    if (!resp.ok || !d.ok) {
      throw new Error(d.error || '拆分处理失败');
    }
    const createdCount = (d.created_books || []).length;
    if (createdCount === 0) {
      alert('经算法检测，本书关联文件完全同属一书，无需拆分。');
    } else {
      alert(`已成功拆分出 ${createdCount} 本独立图书！\n` + d.created_books.map(b => `• 《${b.title}》 (${b.file_count} 个文件)`).join('\n'));
      await openBookModal(bookId);
      if (typeof appState.currentTab !== 'undefined' && appState.currentTab === 'shelf' && typeof loadShelfBooks === 'function') {
        loadShelfBooks(shelfFilters.page);
      }
      if (typeof loadStats === 'function') {
        loadStats();
      }
    }
  } catch (err) {
    alert('拆分异常合并失败：' + (err.message || '请检查系统状态'));
  }
}

