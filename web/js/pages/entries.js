/**
 * 黑板知识条目 —— 列表 + 详情 + 乐观锁编辑 + 多条目合并
 * 对应后端 BlackboardCore：CRUD、版本控制、主题过滤、合并。
 */
import { api, ApiError } from '../api.js';
import { bindRealtime } from '../bus.js';
import {
  esc, enumBadge, chips, PRIORITY, fmtTime, fmtRelative, stringifyContent,
  openForm, confirmDialog, toast, toastOk, toastErr, skeletonRows, emptyState,
  renderStates, paginate, pagerHtml, setBusy, copyText,
} from '../ui.js';

const state = {
  items: [], agents: [], loading: true, error: null,
  q: '', topic: '', page: 1, size: 10, selected: new Set(),
};

let syncSummary = () => {};

const PRIORITY_OPTIONS = [
  { value: 'low', label: '低' },
  { value: 'normal', label: '普通' },
  { value: 'high', label: '高' },
  { value: 'critical', label: '紧急' },
];

function authorName(id) {
  const a = state.agents.find((x) => x.agent_id === id);
  return a ? a.name : (id ? String(id).slice(0, 8) : '系统');
}

function filtered() {
  const q = state.q.trim().toLowerCase();
  return state.items.filter((e) => {
    if (state.topic && e.topic !== state.topic) return false;
    if (!q) return true;
    return (e.topic || '').toLowerCase().includes(q)
      || String(stringifyContent(e.content)).toLowerCase().includes(q)
      || (e.tags || []).join(' ').toLowerCase().includes(q)
      || (e.entry_id || '').toLowerCase().includes(q);
  });
}

function preview(content) {
  const s = stringifyContent(content);
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > 64 ? `${flat.slice(0, 64)}…` : (flat || '—');
}

function rowsHtml(rows) {
  return rows.map((e) => `<tr>
    <td style="width:36px">
      <input type="checkbox" data-pick="${esc(e.entry_id)}"
        ${state.selected.has(e.entry_id) ? 'checked' : ''} aria-label="选择条目">
    </td>
    <td>
      <a href="#/entries/${encodeURIComponent(e.entry_id)}" class="font-medium">${esc(e.topic)}</a>
      <div class="table__id">${esc(e.entry_id)}</div>
    </td>
    <td class="mono faint" title="${esc(stringifyContent(e.content))}" style="max-width:280px">
      <span class="truncate" style="display:block;max-width:280px">${esc(preview(e.content))}</span>
    </td>
    <td>${esc(authorName(e.author_id))}</td>
    <td>${chips(e.tags)}</td>
    <td><span class="badge badge--neutral">v${e.version}</span></td>
    <td>${enumBadge(e.priority, PRIORITY)}</td>
    <td class="faint text-xs" title="${esc(fmtTime(e.updated_at))}">${esc(fmtRelative(e.updated_at))}</td>
    <td class="text-right">
      <div class="btn-group" style="justify-content:flex-end">
        <a class="btn btn--sm" href="#/entries/${encodeURIComponent(e.entry_id)}">详情</a>
        <button class="btn btn--sm" data-edit="${esc(e.entry_id)}">编辑</button>
        <button class="btn btn--sm btn--outline-danger" data-del="${esc(e.entry_id)}">删除</button>
      </div>
    </td>
  </tr>`).join('');
}

function tableShell() {
  return `<div class="table-wrap"><table class="table">
    <thead><tr>
      <th></th><th>主题</th><th>内容预览</th><th>作者</th><th>标签</th>
      <th>版本</th><th>优先级</th><th>更新时间</th><th></th>
    </tr></thead><tbody id="tbody"></tbody>
  </table></div><div id="pager"></div>`;
}

function paintList(mount) {
  const body = mount.querySelector('#tbody');
  const pager = mount.querySelector('#pager');
  if (!body || !pager) return;  // 页面已切换/卸载，放弃本次渲染
  const rows = filtered();
  const { rows: pageRows, page, pages, total } = paginate(rows, state.page, state.size);

  if (renderStates(body, {
    loading: state.loading,
    error: state.error,
    empty: rows.length === 0,
    skeleton: skeletonRows(5, 6),
    emptyHtml: emptyState({
      icon: '▤',
      title: state.q || state.topic ? '没有匹配的条目' : '黑板还是空的',
      desc: state.q || state.topic
        ? '试试清空搜索条件或切换主题。'
        : '知识条目是智能体共享信息的载体。发布后其他智能体可按主题订阅并实时收到更新。',
      actionHtml: state.q || state.topic
        ? '<button class="btn" data-act="reset">清空筛选</button>'
        : '<button class="btn btn--primary" data-act="create">+ 发布条目</button>',
    }),
  })) {
    pager.innerHTML = '';
    const resetBtn = body.querySelector('[data-act="reset"]');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.q = ''; state.topic = ''; state.page = 1;
      mount.querySelector('#q').value = '';
      mount.querySelector('#topic').value = '';
      paintList(mount);
    });
    const createBtn = body.querySelector('[data-act="create"]');
    if (createBtn) createBtn.addEventListener('click', () => openCreate(mount));
    syncSummary();
    return;
  }

  body.innerHTML = rowsHtml(pageRows);
  pager.innerHTML = pagerHtml({ page, pages, total, size: state.size });
  pager.querySelectorAll('[data-page]').forEach((b) => {
    b.addEventListener('click', () => {
      state.page = Number(b.getAttribute('data-page')) || 1;
      paintList(mount);
    });
  });

  body.querySelectorAll('[data-pick]').forEach((cb) => {
    cb.addEventListener('change', () => {
      const id = cb.getAttribute('data-pick');
      if (cb.checked) state.selected.add(id); else state.selected.delete(id);
      syncSummary();
    });
  });
  body.querySelectorAll('[data-edit]').forEach((b) => {
    b.addEventListener('click', () => {
      const id = b.getAttribute('data-edit');
      openEdit(mount, state.items.find((e) => e.entry_id === id));
    });
  });
  body.querySelectorAll('[data-del]').forEach((b) => {
    b.addEventListener('click', async () => {
      const id = b.getAttribute('data-del');
      const e = state.items.find((x) => x.entry_id === id);
      const ok = await confirmDialog({
        title: '删除知识条目',
        message: `确定删除「${e?.topic}」下的这条条目吗？仅条目的原作者可以删除。`,
        confirmText: '删除', danger: true,
      });
      if (!ok) return;
      try {
        await api.deleteEntry(id, e.author_id);
        toastOk('条目已删除');
        state.selected.delete(id);
        await load(mount);
      } catch (err) { toastErr(err); }
    });
  });

  syncSummary();
}

async function load(mount, quiet = false) {
  if (!quiet) { state.loading = true; paintList(mount); }
  try {
    const [entries, agents] = await Promise.all([api.listEntries(), api.listAgents()]);
    state.items = entries;
    state.agents = agents;
    state.error = null;
  } catch (e) {
    state.error = e;
  } finally {
    state.loading = false;
    paintList(mount);
    refreshTopicOptions(mount);
  }
}

function refreshTopicOptions(mount) {
  const sel = mount.querySelector('#topic');
  if (!sel) return;
  const topics = [...new Set(state.items.map((e) => e.topic))].sort();
  const cur = sel.value;
  sel.innerHTML = `<option value="">全部主题</option>` +
    topics.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
  sel.value = topics.includes(cur) ? cur : '';
  state.topic = sel.value;
}

function authorOptions() {
  if (!state.agents.length) {
    return [{ value: 'system', label: 'system（尚未注册智能体）' }];
  }
  return state.agents.map((a) => ({ value: a.agent_id, label: `${a.name}（${a.agent_id.slice(0, 8)}）` }));
}

function openCreate(mount) {
  openForm({
    title: '发布知识条目',
    desc: '内容支持任意 JSON，也可以直接写纯文本。',
    submitText: '发布',
    wide: true,
    fields: [
      { name: 'author_id', label: '作者', type: 'select', required: true, options: authorOptions() },
      { name: 'topic', label: '主题', required: true, placeholder: '例如：architecture / design / risk' },
      { name: 'content', label: '内容', type: 'json', required: true, rows: 7,
        value: { key: 'value' }, hint: 'JSON 或纯文本；解析失败时按纯文本保存' },
      { name: 'tags', label: '标签', type: 'tags', placeholder: '架构, 争议' },
      { name: 'priority', label: '优先级', type: 'select', value: 'normal', options: PRIORITY_OPTIONS },
      { name: 'confidence', label: '置信度', type: 'number', value: 1, min: 0, max: 1, step: 0.1,
        hint: '用于意见冲突的加权投票，0–1' },
    ],
    onSubmit: async (v) => {
      await api.createEntry(v.author_id, {
        topic: v.topic,
        content: v.content,
        tags: v.tags || [],
        priority: v.priority,
        confidence: v.confidence ?? 1,
      });
      toastOk(`已发布到主题「${v.topic}」`);
      await load(mount);
    },
  });
}

function openEdit(mount, entry) {
  if (!entry) return;
  openForm({
    title: '编辑知识条目',
    desc: `当前版本 v${entry.version}。更新时必须携带版本号，服务端用乐观锁校验：
      若期间已被他人修改，会返回 409 冲突。`,
    submitText: '提交更新',
    wide: true,
    fields: [
      { name: 'content', label: '内容', type: 'json', required: true, rows: 8, value: entry.content },
      { name: 'tags', label: '标签', type: 'tags', value: entry.tags || [] },
      { name: 'priority', label: '优先级', type: 'select', value: entry.priority, options: PRIORITY_OPTIONS },
      { name: 'confidence', label: '置信度', type: 'number', value: entry.confidence ?? 1, min: 0, max: 1, step: 0.1 },
    ],
    onSubmit: async (v) => {
      await api.updateEntry(entry.entry_id, entry.author_id, {
        content: v.content,
        version: entry.version,
        tags: v.tags || [],
        priority: v.priority,
        confidence: v.confidence ?? 1,
      });
      toastOk(`已更新到 v${entry.version + 1}`);
      await load(mount);
      // 详情也可能需要同步
      const dv = document.querySelector('#detailVersion');
      if (dv) dv.textContent = `v${entry.version + 1}`;
    },
  });
}

function openMerge(mount) {
  const ids = [...state.selected];
  openForm({
    title: `合并 ${ids.length} 条条目`,
    desc: '后端按来源集合做幂等：同一组来源重复合并会返回既有结果，不会产生脏数据。',
    submitText: '合并',
    fields: [
      { name: 'author_id', label: '执行合并的智能体', type: 'select', required: true, options: authorOptions() },
      { name: 'target_topic', label: '合并后的主题', required: true, placeholder: '例如：architecture-consensus' },
    ],
    onSubmit: async (v) => {
      const r = await api.mergeEntries(v.author_id, ids, v.target_topic);
      toastOk(`已合并为 1 条（目标主题 ${v.target_topic}）`, '合并完成');
      state.selected.clear();
      await load(mount);
      return r;
    },
  });
}

// ------------------------------------------------------------------ 详情页
async function renderDetail(mount, id) {
  mount.innerHTML = `<div class="empty"><span class="spinner" style="width:22px;height:22px"></span>
    <div class="empty__desc">加载条目详情…</div></div>`;

  let entry;
  try {
    entry = await api.getEntry(id);
  } catch (e) {
    mount.innerHTML = emptyState({
      icon: '!', title: '加载失败或条目不存在', desc: e.message,
      actionHtml: '<a class="btn" href="#/entries">返回列表</a>',
    });
    return;
  }

  mount.innerHTML = `
    <div class="page-head">
      <div style="min-width:0">
        <h1 class="page-title">${esc(entry.topic)}</h1>
        <p class="page-desc mono">${esc(entry.entry_id)}</p>
      </div>
      <div class="page-actions">
        <a class="btn" href="#/entries">← 返回</a>
        <button class="btn" id="dCopy">复制 ID</button>
        <button class="btn" id="dEdit">编辑</button>
        <button class="btn btn--outline-danger" id="dDel">删除</button>
      </div>
    </div>

    <div class="section" style="display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:var(--sp-4)" id="dGrid">
      <div class="card">
        <div class="card__head">
          <div><div class="card__title">条目内容</div>
            <div class="card__desc">版本 <span id="detailVersion">v${entry.version}</span> · 乐观锁保护</div></div>
          <span class="badge badge--neutral mono" id="detailVersionBadge">v${entry.version}</span>
        </div>
        <div class="card__body">
          <pre class="code" id="dContent">${esc(stringifyContent(entry.content))}</pre>
        </div>
      </div>

      <div class="card">
        <div class="card__head"><div class="card__title">元信息</div></div>
        <div class="card__body">
          <div class="kv">
            <div class="kv__k">作者</div><div class="kv__v">${esc(authorName(entry.author_id))}
              <div class="table__id">${esc(entry.author_id)}</div></div>
            <div class="kv__k">主题</div><div class="kv__v">${esc(entry.topic)}</div>
            <div class="kv__k">标签</div><div class="kv__v">${chips(entry.tags)}</div>
            <div class="kv__k">优先级</div><div class="kv__v">${enumBadge(entry.priority, PRIORITY)}</div>
            <div class="kv__k">置信度</div><div class="kv__v">${entry.confidence ?? 1}</div>
            <div class="kv__k">创建时间</div><div class="kv__v">${esc(fmtTime(entry.created_at))}</div>
            <div class="kv__k">更新时间</div><div class="kv__v">${esc(fmtTime(entry.updated_at))}
              <span class="faint">（${esc(fmtRelative(entry.updated_at))}）</span></div>
          </div>
        </div>
      </div>
    </div>`;

  const mq = window.matchMedia('(max-width: 980px)');
  const grid = mount.querySelector('#dGrid');
  const apply = () => { grid.style.gridTemplateColumns = mq.matches ? 'minmax(0,1fr)' : 'minmax(0,1.15fr) minmax(0,1fr)'; };
  apply();
  mq.addEventListener('change', apply);

  const reload = async () => { mount.innerHTML = ''; await renderDetail(mount, id); };

  mount.querySelector('#dCopy').addEventListener('click', () => copyText(entry.entry_id));
  mount.querySelector('#dEdit').addEventListener('click', () => openEdit(mount, entry));
  mount.querySelector('#dDel').addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: '删除知识条目', message: '删除后不可恢复，且仅原作者可删除。', confirmText: '删除', danger: true,
    });
    if (!ok) return;
    try { await api.deleteEntry(id, entry.author_id); toastOk('已删除'); location.hash = '#/entries'; }
    catch (err) {
      if (err instanceof ApiError && err.status === 403) toastErr(err, '无权限');
      else toastErr(err);
    }
  });

  return () => mq.removeEventListener('change', apply);
}

// ------------------------------------------------------------------ 页面入口
export default {
  title: '黑板条目',

  async render(mount, params) {
    if (params && params.id) return renderDetail(mount, params.id);

    mount.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">黑板条目</h1>
          <p class="page-desc">黑板上的共享知识。更新采用乐观锁：提交时带上当前版本号，
            若已被其他智能体改过会被拒绝（409），避免静默覆盖。</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="btnReload">刷新</button>
          <button class="btn" id="btnMerge" disabled>合并已选</button>
          <button class="btn btn--primary" id="btnCreate">+ 发布条目</button>
        </div>
      </div>

      <div class="toolbar">
        <input class="input" id="q" placeholder="搜索主题 / 内容 / 标签…" style="min-width:220px">
        <select class="select" id="topic"><option value="">全部主题</option></select>
        <div class="toolbar__spacer"></div>
        <span class="text-xs faint" id="summary"></span>
      </div>

      <div class="card card--flush">
        <div class="card__body" style="padding:0">${tableShell()}</div>
      </div>`;

    const q = mount.querySelector('#q');
    const topicSel = mount.querySelector('#topic');
    const mergeBtn = mount.querySelector('#btnMerge');

    syncSummary = () => {
      const el = mount.querySelector('#summary');
      if (el) el.textContent = `共 ${state.items.length} 条 · 筛选出 ${filtered().length} 条`;
      const n = state.selected.size;
      mergeBtn.disabled = n < 2;
      mergeBtn.textContent = n >= 2 ? `合并已选 (${n})` : '合并已选';
      mergeBtn.className = n >= 2 ? 'btn btn--primary' : 'btn';
    };

    q.addEventListener('input', () => { state.q = q.value; state.page = 1; paintList(mount); });
    topicSel.addEventListener('change', () => { state.topic = topicSel.value; state.page = 1; paintList(mount); });
    mount.querySelector('#btnCreate').addEventListener('click', () => openCreate(mount));
    mergeBtn.addEventListener('click', () => openMerge(mount));
    mount.querySelector('#btnReload').addEventListener('click', async (e) => {
      setBusy(e.currentTarget, true, '刷新中');
      await load(mount);
      setBusy(e.currentTarget, false, '刷新');
    });

    await load(mount);

    bindRealtime(
      ['entry.created', 'entry.updated', 'entry.deleted', 'entry.merged', 'conflict.detected'],
      () => load(mount, true),
      { debounce: 300 },
    );
  },
};
