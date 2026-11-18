import { appState, langSelections, AI_FALLBACK_TEXT } from './state.js';
import { escapeHtml } from './utils.js';
import { renderBookCard } from './shelf_render.js';
import { aiRerankOn, aiRerankItems, loadAiUsage } from './api.js';

export function runPresetDescribe(text) {
  document.getElementById('describe-input').value = text;
  triggerDescribeRecommend();
}

export async function triggerDescribeRecommend() {
  const desc = document.getElementById('describe-input').value.trim();
  if (!desc) return;
  const langParam = Array.from(langSelections.describe).join(',');
  const aiOn = aiRerankOn();
  const reqId = ++appState.describeReqSeq;
  const telemetry = document.getElementById('describe-telemetry');
  telemetry.innerText = '正在检索…';
  const r = await fetch('/api/recommend/describe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // AI on: fetch 20 for DeepSeek to rerank, and skip the local cross-encoder
    body: JSON.stringify({ description: desc, top_k: aiOn ? 20 : 10, language: langParam, ai: aiOn }),
  });
  const d = await r.json();
  if (reqId !== appState.describeReqSeq) return;
  const shown = (d.items || []).slice(0, 10);
  telemetry.innerText = `已找到 ${shown.length} 本 · 用时 ${(d.elapsed_ms / 1000).toFixed(1)} 秒`;

  const expBar = document.getElementById('describe-expansion-bar');
  const tokens = (d.expanded_tokens || []).map(t => `<span class="tag-xs cat">${escapeHtml(t)}</span>`).join(' ');
  const cats = (d.inferred_categories || []).map(c => `<span class="tag-xs fmt">${escapeHtml(c)}</span>`).join(' ');

  expBar.classList.remove('hidden');
  expBar.innerHTML = `
    <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
      <b>关键词：</b> ${tokens || '无'}
      <span style="margin-left:10px;"><b>预测学科：</b> ${cats || '综合'}</span>
    </div>
  `;

  const grid = document.getElementById('describe-grid');
  grid.innerHTML = shown.map(b => renderBookCard(b, { showRank: true })).join('');
  if (aiOn && (d.items || []).length > 1) {
    const base = telemetry.innerText;
    telemetry.innerText = `${base} · AI 精排中…`;
    const res = await aiRerankItems(desc, d.items);
    if (reqId !== appState.describeReqSeq) return;
    telemetry.innerText = `${base} · ${res.status}`;
    if (res.ok) grid.innerHTML = res.items.slice(0, 10).map(b => renderBookCard(b, { showRank: true, isAiOrder: true })).join('');
  }
}

// ==================== TAB 3: INTEREST PROFILE ====================

export async function applyInterestAi(langParam, reqId) {
  const st = document.getElementById('interest-ai-status');
  const fallback = reason => (AI_FALLBACK_TEXT[reason] || AI_FALLBACK_TEXT.error).replace('标准检索', '标准推荐');
  st.textContent = 'AI 挑选中…';
  try {
    const r = await fetch('/api/recommend/interests/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language: langParam }),
    });
    const a = await r.json();
    if (reqId !== appState.interestReqSeq) return;  // a newer load replaced this one
    if (!a.ok) { st.textContent = fallback(a.reason); return; }
    document.getElementById('interest-grid').innerHTML =
      a.items.map(b => renderBookCard(b, { showRank: true, isAiOrder: true })).join('');
    st.textContent = 'AI 推荐完成';
  } catch (e) {
    if (reqId === appState.interestReqSeq) st.textContent = fallback('error');
  }
}

/** Shows the exploration share (0 to 1) on the slider label and in the description above it. */
export function showExploreRatio(ratio) {
  const explorePct = Math.round(Number(ratio) * 100);
  document.getElementById('explore-ratio-val').innerText = `${explorePct}%`;
  document.getElementById('explore-pct').innerText = `${explorePct}%`;
  document.getElementById('explore-core-pct').innerText = `${100 - explorePct}%`;
}

export async function loadInterestView() {
  const reqId = ++appState.interestReqSeq;
  document.getElementById('interest-ai-status').textContent = '';
  const langParam = Array.from(langSelections.interest).join(',');
  const clEl = document.getElementById('active-clusters-list');
  let d;
  try {
    const r = await fetch(`/api/recommend/interests?top_k=10&language=${encodeURIComponent(langParam)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    d = await r.json();
  } catch (err) {
    console.error('loadInterestView error:', err);
    clEl.innerHTML = `<div style="color:var(--rose);">兴趣推荐加载失败（${escapeHtml(err.message)}）。<button class="btn" style="margin-left:8px;" onclick="loadInterestView()">重试</button></div>`;
    document.getElementById('interest-grid').innerHTML = '';
    return;
  }
  document.getElementById('interest-prompt-input').value = d.interest_prompt || '';
  document.getElementById('explore-ratio-slider').value = d.exploration_ratio ?? 0.2;
  showExploreRatio(d.exploration_ratio ?? 0.2);

  // A direction from marked books reads as its category and book count; one from the prompt as the phrase itself
  clEl.innerHTML = (d.active_clusters || []).map(c => (c.book_count
    ? `<span class="cluster-tag">${escapeHtml(c.category)} <span class="cluster-count">${c.book_count}</span></span>`
    : `<span class="cluster-tag" title="${escapeHtml(c.category)}">${escapeHtml(c.cluster_name)}</span>`)).join('')
    || '<span style="color:var(--text-muted);">还没有兴趣方向。收藏几本书，或在上方写下关注点后保存。</span>';

  if (reqId !== appState.interestReqSeq) return;
  document.getElementById('interest-grid').innerHTML =
    (d.items || []).map(b => renderBookCard(b, { showRank: true })).join('');
  if (aiRerankOn() && (d.items || []).length) applyInterestAi(langParam, reqId);
}

export async function saveInterestConfig() {
  const prompt = document.getElementById('interest-prompt-input').value.trim();
  const ratio = parseFloat(document.getElementById('explore-ratio-slider').value || '0.2');
  const r = await fetch('/api/interests/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interest_prompt: prompt, exploration_ratio: ratio }),
  });
  const d = await r.json();
  loadInterestView();
}

