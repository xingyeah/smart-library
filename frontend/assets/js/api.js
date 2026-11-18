import { appState, shelfFilters, AI_FALLBACK_TEXT } from './state.js';
import { renderBookCard, generatePaginationItems } from './shelf_render.js';
import { updateOrganizeBanner, renderRunsTable } from './organize.js';

export function aiRerankOn() {
  try { return localStorage.getItem('aiRerank') !== '0'; } catch (e) { return true; }
}

export function setAiRerank(on) {
  try { localStorage.setItem('aiRerank', on ? '1' : '0'); } catch (e) {}
  ['shelf-ai-toggle', 'describe-ai-toggle', 'interest-ai-toggle', 'm-ai-toggle'].forEach(id => {
    const el = /** @type {HTMLInputElement | null} */ (document.getElementById(id));
    if (el) el.checked = on;
  });
}


export async function aiRerankItems(query, items) {
  const head = items.slice(0, 20);
  try {
    const r = await fetch('/api/search/ai_rerank', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, ids: head.map(b => b.id) }),
    });
    const d = await r.json();
    if (!d.ok) return { items, status: AI_FALLBACK_TEXT[d.reason] || AI_FALLBACK_TEXT.error, ok: false };
    const byId = new Map(head.map(b => [b.id, b]));
    const ranked = d.ids.map(id => byId.get(id)).filter(Boolean);
    ranked.forEach((b, i) => {
      b.rank = i + 1;
      const sc = d.scores[String(b.id)];
      if (sc != null) b.ai_score = sc;
    });
    return { items: ranked.concat(items.slice(20)), status: 'AI 精排完成', ok: true };
  } catch (e) {
    return { items, status: AI_FALLBACK_TEXT.error, ok: false };
  }
}

export async function applyShelfAiRerank(d, bannerHtml, reqId) {
  const info = document.getElementById('shelf-result-info');
  const base = info.innerText;
  info.innerText = `${base} · AI 精排中…`;
  const res = await aiRerankItems(shelfFilters.q, d.items);
  if (reqId !== appState.shelfReqSeq) return;  // a newer search replaced this one
  info.innerText = `${base} · ${res.status}`;
  if (res.ok) {
    document.getElementById('shelf-grid').innerHTML =
      bannerHtml + res.items.map(b => renderBookCard(b, { showRank: true, isAiOrder: true })).join('');
  }
}

export async function loadAiUsage() {
  const el = document.getElementById('ai-usage-info');
  if (!el) return;
  try {
    const u = await (await fetch('/api/ai/usage')).json();
    if (!u.configured) { el.textContent = 'AI：未配置 DeepSeek'; return; }
    const t = u.today || {};
    el.textContent = `今日 AI 用量 ¥${u.today_capped.toFixed(2)} / ¥${u.cap}`
      + `（搜索 ¥${(t.search || 0).toFixed(2)} · 推荐 ¥${(t.recommend || 0).toFixed(2)} · 翻译 ¥${(t.translate || 0).toFixed(2)}）`
      + ` · 离线整理累计 ¥${((u.totals || {}).enrich || 0).toFixed(2)}`;
  } catch (e) {}
}

export async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    document.getElementById('st-total-books').innerText = d.total_books.toLocaleString();
    document.getElementById('st-total-files').innerText = `物理文件 ${d.total_files.toLocaleString()} 个`;
    document.getElementById('st-merged').innerText = d.duplicates_merged.toLocaleString();
    document.getElementById('st-size-gb').innerText = `${d.total_size_gb} GB`;
    document.getElementById('st-fav-count').innerText = d.favorite_count.toLocaleString();
    const st = d.reading_statuses || {};
    document.getElementById('st-read-breakdown').innerText =
      `在读 ${st.reading || 0} · 已读 ${st.read || 0} · 想读 ${st.want || 0}`;
    document.getElementById('st-conflicts').innerText = d.pending_conflicts.toLocaleString();
    document.getElementById('nav-book-count').innerText = d.total_books.toLocaleString();

    const langs = d.languages || {};
    const btnCn = document.getElementById('lang-btn-shelf-cn');
    const btnTw = document.getElementById('lang-btn-shelf-tw');
    const btnEn = document.getElementById('lang-btn-shelf-en');
    if (btnCn && langs['zh-CN']) btnCn.innerText = `简体中文 (${langs['zh-CN']})`;
    if (btnTw && langs['zh-TW']) btnTw.innerText = `繁体中文 (${langs['zh-TW']})`;
    if (btnEn && langs['en']) btnEn.innerText = `英文 (${langs['en']})`;

    const confBadge = document.getElementById('nav-conflict-count');
    if (d.pending_conflicts > 0) {
      confBadge.innerText = d.pending_conflicts;
      confBadge.classList.remove('hidden');
    } else {
      confBadge.classList.add('hidden');
    }

    document.getElementById('top-subtitle').innerText = `${d.total_books.toLocaleString()} 本 · ${Math.round(d.total_size_gb)} GB`
      + ` · 在读 ${(st.reading || 0).toLocaleString()} · 待审 ${d.pending_conflicts.toLocaleString()}`;

    renderRunsTable(d.recent_runs || []);
    updateOrganizeBanner(d.organize_state);
  } catch (e) {
    console.error(e);
  }
}

