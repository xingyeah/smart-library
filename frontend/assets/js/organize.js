import { appState, shelfFilters, CONFLICT_LABELS, RUN_TYPE_LABELS, RUN_STATUS_LABELS } from './state.js';
import { escapeHtml } from './utils.js';
import { loadStats } from './api.js';
import { loadShelfBooks } from './shelf.js';

export async function startOrganize(limitPerDir) {
  const enableOcr = document.getElementById('org-ocr-toggle').checked;
  await fetch('/api/organize/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit_per_dir: limitPerDir, enable_ocr: enableOcr }),
  });
  pollOrganizeStatus();
}

export function updateOrganizeBanner(orgState) {
  if (!orgState) return;
  const stEl = document.getElementById('org-status-text');
  if (orgState.running) {
    const p = orgState.progress || {};
    stEl.innerHTML = `<span style="color:var(--amber);">正在后台整理归档中... 已扫描 ${p.scanned_files || 0} 个文件，新增入库 ${p.indexed_books || 0} 本，合并重复 ${p.duplicates_merged || 0} 个</span>`;
    if (!appState.orgPollTimer) {
      appState.orgPollTimer = setInterval(pollOrganizeStatus, 1500);
    }
  } else {
    if (appState.orgPollTimer) {
      clearInterval(appState.orgPollTimer);
      appState.orgPollTimer = null;
    }
    const last = orgState.last_result;
    if (last) {
      stEl.innerHTML = `<span style="color:var(--emerald);">最近一次整理已完成：扫描 ${last.scanned_files || 0} 个新文件，规范入库 ${last.indexed_books || 0} 本，合并重复 ${last.duplicates_merged || 0} 个</span>`;
    } else {
      stEl.innerText = '空闲 · 已入库的书都建好了全文和向量索引';
    }
  }
}

export async function pollOrganizeStatus() {
  const r = await fetch('/api/organize/status');
  const d = await r.json();
  updateOrganizeBanner(d);
  if (!d.running) {
    loadStats();
    loadConflicts('pending');
    if (appState.currentTab === 'shelf') loadShelfBooks(shelfFilters.page);
  }
}

export async function loadConflicts(status = 'pending', btn = null) {
  if (btn) {
    btn.parentElement.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
  }
  const r = await fetch(`/api/conflicts?status=${status}`);
  const d = await r.json();
  const tbody = document.getElementById('conflict-tbody');
  if (!d.items || d.items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">当前没有${status === 'pending' ? '待处理' : '已解决'}的冲突项</td></tr>`;
    return;
  }
  tbody.innerHTML = d.items.map(c => `
    <tr>
      <td data-label="来源">
        <span class="tag-xs cat">${escapeHtml(c.source_name)}</span>
        <span class="tag-xs fmt">${escapeHtml(c.format)}</span>
      </td>
      <td data-label="书名">
        <input type="text" id="conf-title-${c.id}" class="search-input" style="padding:5px 8px;font-size:12px;" value="${escapeHtml(c.detected_title)}" />
      </td>
      <td data-label="作者">
        <input type="text" id="conf-author-${c.id}" class="search-input" style="padding:5px 8px;font-size:12px;" value="${escapeHtml(c.detected_author)}" />
      </td>
      <td data-label="原因" style="font-size:12px; color:var(--text-muted);">
        <div><b>${escapeHtml(CONFLICT_LABELS[c.conflict_type] || c.conflict_type)}</b>： ${escapeHtml(c.conflict_detail)}</div>
        <div style="color:var(--text-dim); word-break:break-all;">${escapeHtml(c.file_path)}</div>
      </td>
      <td>
        <div style="display:flex; gap:6px;">
          <button class="btn" style="padding:4px 10px;font-size:12px;" onclick="resolveConflict(${c.id}, 'update_book')">确认</button>
          <button class="btn" style="padding:4px 8px;font-size:12px;" onclick="resolveConflict(${c.id}, 'ignore')">忽略</button>
        </div>
      </td>
    </tr>
  `).join('');
}

export async function resolveConflict(cid, action) {
  const title = document.getElementById(`conf-title-${cid}`)?.value || '';
  const author = document.getElementById(`conf-author-${cid}`)?.value || '';
  await fetch(`/api/conflicts/${cid}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, title, author }),
  });
  loadConflicts('pending');
  loadStats();
}

// A run that scanned, added, merged and archived nothing left nothing behind to roll back
function hasRunChanges(run) {
  return [run.scanned_files, run.indexed_books, run.duplicates_merged, run.archived_links, run.conflicts_found]
    .some(count => count > 0);
}

export function renderRunsTable(runs) {
  const tbody = document.getElementById('runs-tbody');
  if (!runs || runs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);">暂无整理记录</td></tr>`;
    return;
  }
  tbody.innerHTML = runs.map(r => `
    <tr>
      <td data-label="批次">#${r.id}</td>
      <td data-label="类型"><span><span class="tag-xs cat">${escapeHtml(RUN_TYPE_LABELS[r.run_type] || r.run_type)}</span> <span class="tag-xs fmt">${escapeHtml(RUN_STATUS_LABELS[r.status] || r.status)}</span></span></td>
      <td data-label="扫描">${(r.scanned_files || 0).toLocaleString()}</td>
      <td data-label="新增">${(r.indexed_books || 0).toLocaleString()}</td>
      <td data-label="合并">${(r.duplicates_merged || 0).toLocaleString()}</td>
      <td data-label="归档">${(r.archived_links || 0).toLocaleString()}</td>
      <td data-label="待确认">${(r.conflicts_found || 0).toLocaleString()}</td>
      <td data-label="时间" style="font-size:12px;color:var(--text-muted);">${escapeHtml(r.started_at)}</td>
      <td>${hasRunChanges(r)
        ? `<button class="btn" style="padding:3px 8px;font-size:12px;" onclick="rollbackRun(${r.id})">回滚</button>` : ''}</td>
    </tr>
  `).join('');
}

export async function rollbackRun(runId) {
  const r = await fetch('/api/organize/rollback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  });
  const d = await r.json();
  alert(`已安全移除批次 #${runId} 创建的 ${d.unlinked_files} 个归档硬链接（源目录原始文件完好无损）。`
    + (d.kept_last_copies ? `\n另有 ${d.kept_last_copies} 本的源文件已被删除，归档里是唯一副本，已保留未删。` : ''));
  loadStats();
}


export async function loadMismergedBooks() {
  const container = document.getElementById('mismerged-list-container');
  if (!container) return;
  container.innerHTML = `<div style="text-align:center; color:var(--text-muted); padding:20px;">正在扫描合并错了的书...</div>`;
  try {
    const resp = await fetch('/api/organize/mismerged?limit=100');
    const data = await resp.json();
    const items = data.items || [];
    if (items.length === 0) {
      container.innerHTML = `<div style="text-align:center; color:var(--emerald); padding:24px; font-weight:600;">全库未检测到误合并的图书。</div>`;
      return;
    }

    container.innerHTML = `
      <div style="margin-bottom:12px; font-size:14px; color:var(--text-muted);">
        共检测到 <b style="color:var(--rose);">${items.length}</b> 本图书存在异构多文件误合并，点击右侧按钮可单个或批量一键自动拆分立户：
      </div>
      <div style="display:flex; flex-direction:column; gap:12px; max-height:480px; overflow-y:auto; padding-right:6px;">
        ${items.map(b => `
          <div style="background:var(--bg); border:1px solid var(--border); border-radius:10px; padding:12px; display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
            <div style="flex:1;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-weight:700; font-size:14px; color:var(--text);">#${b.book_id} 《${escapeHtml(b.title)}》</span>
                <span class="tag-xs cat">${escapeHtml(b.category || '未分类')}</span>
                <span style="font-size:12px; color:var(--text-muted);">作者：${escapeHtml(b.author || '未知')}</span>
              </div>
              <div style="margin-top:8px; font-size:12px; color:var(--text-muted);">
                关联 <b>${b.file_count}</b> 个文件，检测出 <b>${b.distinct_groups_count}</b> 个互不相干的书籍分组：
              </div>
              <div style="margin-top:6px; display:flex; flex-direction:column; gap:4px;">
                ${(b.groups || []).map(g => `
                  <div style="background:var(--ground); border-left:3px solid ${g.is_retained_in_book ? 'var(--emerald)' : 'var(--amber)'}; padding:4px 8px; border-radius:6px; font-size:12px;">
                    <span style="font-weight:600; color:${g.is_retained_in_book ? 'var(--emerald)' : 'var(--amber)'};">${g.is_retained_in_book ? '[保留原书主版本]' : '[将拆分立户为新书]'}:</span>
                    ${g.files.map(f => escapeHtml(f.filename)).join(' | ')}
                  </div>
                `).join('')}
              </div>
            </div>
            <div style="flex-shrink:0;">
              <button class="btn btn-secondary" style="font-size:12px; padding:6px 12px;" onclick="triggerSplitSingleBook(${b.book_id})">一键拆分</button>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div style="text-align:center; color:var(--rose); padding:20px;">扫描失败：${escapeHtml(err.message || '网络或接口错误')}</div>`;
  }
}

export async function triggerSplitSingleBook(bookId) {
  try {
    const resp = await fetch('/api/organize/split_mismerged', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ book_id: bookId }),
    });
    const d = await resp.json();
    if (!resp.ok || !d.ok) {
      throw new Error(d.error || '拆分失败');
    }
    alert(`拆分成功！已为图书 #${bookId} 创建 ${d.created_books.length} 本新独立图书。`);
    loadMismergedBooks();
    loadStats();
  } catch (err) {
    alert('拆分失败：' + (err.message || '请检查系统状态'));
  }
}

export async function triggerBatchSplitMismerged() {
  if (!confirm('确定要对全库扫描出的异常合并图书进行批量一键拆分立户吗？\n\n系统将自动剥离所有不兼容文件并为每本书建立独立图书记录与索引。')) return;
  const container = document.getElementById('mismerged-list-container');
  if (container) {
    container.innerHTML = `<div style="text-align:center; color:var(--text-muted); padding:24px;">正在批量拆分，请稍候...</div>`;
  }
  try {
    const resp = await fetch('/api/organize/split_mismerged', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch: true, limit: 50 }),
    });
    const d = await resp.json();
    if (!resp.ok || !d.ok) {
      throw new Error(d.error || '批量拆分失败');
    }
    alert(`批量拆分完成！\n已处理 ${d.split_success_count} 本异常图书，共成功独立立户 ${d.total_new_books_created} 本新书，并已启动后台向量增量索引。`);
    loadMismergedBooks();
    loadStats();
  } catch (err) {
    alert('批量拆分失败：' + (err.message || '请检查系统状态'));
    loadMismergedBooks();
  }
}

/* ==================== IMMERSIVE ONLINE READER ENGINE ==================== */
/* ==================== MOBILE READER CHROME (<= 768px) ==================== */
