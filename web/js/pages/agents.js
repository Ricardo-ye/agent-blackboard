/**
 * 智能体管理 —— 列表 + 详情
 * 对应后端 AgentRegistry：注册、心跳、状态流转、能力声明、负载计数。
 */
import { api } from '../api.js';
import { bindRealtime } from '../bus.js';
import {
  esc, enumBadge, chips, AGENT_STATUS, TASK_STATUS, PRIORITY,
  fmtTime, fmtRelative, initials, hashHue,
  openForm, confirmDialog, toast, toastOk, toastErr, skeletonRows, emptyState,
  renderStates, paginate, pagerHtml, setBusy,
} from '../ui.js';

const state = { items: [], loading: true, error: null, q: '', status: '', page: 1, size: 10 };

// 由列表页 render() 注入，用于在每次重绘后同步右上角摘要
let syncSummary = () => {};

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'online', label: '在线' },
  { value: 'busy', label: '忙碌' },
  { value: 'offline', label: '离线' },
  { value: 'error', label: '异常' },
];

function filtered() {
  const q = state.q.trim().toLowerCase();
  return state.items.filter((a) => {
    if (state.status && a.status !== state.status) return false;
    if (!q) return true;
    return (a.name || '').toLowerCase().includes(q)
      || (a.agent_id || '').toLowerCase().includes(q)
      || (a.capabilities || []).join(' ').toLowerCase().includes(q);
  });
}

function rowsHtml(rows) {
  return rows.map((a) => {
    const meta = AGENT_STATUS[a.status] || { label: a.status, tone: 'neutral' };
    const hue = hashHue(a.agent_id);
    return `<tr>
      <td>
        <div class="flex items-center gap-3">
          <div class="avatar" style="background:hsl(${hue} 62% 45%)">${esc(initials(a.name))}</div>
          <div style="min-width:0">
            <a href="#/agents/${encodeURIComponent(a.agent_id)}" class="font-medium">${esc(a.name || '(未命名)')}</a>
            <div class="table__id">${esc(a.agent_id)}</div>
          </div>
        </div>
      </td>
      <td>${chips(a.capabilities, 'brand')}</td>
      <td>${enumBadge(a.status, AGENT_STATUS)}</td>
      <td class="num">${a.current_task_count ?? 0}</td>
      <td class="faint text-xs" title="${esc(fmtTime(a.last_heartbeat))}">${esc(fmtRelative(a.last_heartbeat))}</td>
      <td class="text-right">
        <div class="btn-group" style="justify-content:flex-end">
          <button class="btn btn--sm" data-hb="${esc(a.agent_id)}" title="立即发送心跳">心跳</button>
          <button class="btn btn--sm" data-status="${esc(a.agent_id)}">改状态</button>
          <a class="btn btn--sm btn--subtle" href="#/agents/${encodeURIComponent(a.agent_id)}">详情</a>
          <button class="btn btn--sm btn--outline-danger" data-del="${esc(a.agent_id)}" data-name="${esc(a.name)}">注销</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function tableShell() {
  return `<div class="table-wrap"><table class="table">
    <thead><tr>
      <th>智能体</th><th>能力</th><th>状态</th><th class="num">负载</th><th>最近心跳</th><th></th>
    </tr></thead>
    <tbody id="tbody"></tbody>
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
    skeleton: skeletonRows(4, 4),
    emptyHtml: emptyState({
      icon: '◉',
      title: state.q || state.status ? '没有匹配的智能体' : '还没有注册任何智能体',
      desc: state.q || state.status
        ? '试试清空搜索条件或切换状态筛选。'
        : '智能体是协作的参与者。注册后即可发布知识、领取任务、参与冲突解决。',
      actionHtml: state.q || state.status
        ? '<button class="btn" data-act="reset">清空筛选</button>'
        : '<a class="btn btn--primary" href="#/agents" data-act="create">+ 注册智能体</a>',
    }),
  })) {
    pager.innerHTML = '';
    if (state.loading) return;
    const resetBtn = body.querySelector('[data-act="reset"]');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.q = ''; state.status = ''; state.page = 1;
      mount.querySelector('#q').value = '';
      mount.querySelector('#status').value = '';
      paintList(mount);
    });
    const createBtn = body.querySelector('[data-act="create"]');
    if (createBtn) createBtn.addEventListener('click', (e) => { e.preventDefault(); openCreate(mount); });
    syncSummary();
    return;
  }

  body.innerHTML = rowsHtml(pageRows);
  pager.innerHTML = pagerHtml({ page, pages, total, size: state.size });
  syncSummary();
  pager.querySelectorAll('[data-page]').forEach((b) => {
    b.addEventListener('click', () => {
      state.page = Number(b.getAttribute('data-page')) || 1;
      paintList(mount);
    });
  });

  body.querySelectorAll('[data-hb]').forEach((b) => b.addEventListener('click', async (e) => {
    setBusy(e.currentTarget, true, '…');
    try {
      await api.heartbeat(b.getAttribute('data-hb'));
      toastOk('心跳已更新');
      await load(mount);
    } catch (err) { toastErr(err); }
    finally { setBusy(e.currentTarget, false, '心跳'); }
  }));

  body.querySelectorAll('[data-status]').forEach((b) => b.addEventListener('click', (e) => {
    const id = e.currentTarget.getAttribute('data-status');
    const cur = state.items.find((a) => a.agent_id === id);
    openStatus(mount, id, cur);
  }));

  body.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async (e) => {
    const id = e.currentTarget.getAttribute('data-del');
    const name = e.currentTarget.getAttribute('data-name');
    const ok = await confirmDialog({
      title: '注销智能体',
      message: `确定注销「${name}」吗？注销后其已发布的知识条目仍保留在黑板上。`,
      confirmText: '注销',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteAgent(id);
      toastOk('智能体已注销');
      await load(mount);
    } catch (err) { toastErr(err); }
  }));
}

async function load(mount) {
  state.loading = true;
  paintList(mount);
  try {
    state.items = await api.listAgents();
    state.error = null;
  } catch (e) {
    state.error = e;
  } finally {
    state.loading = false;
    paintList(mount);
  }
}

function openCreate(mount) {
  openForm({
    title: '注册智能体',
    desc: '声明能力标签后，任务分配会按「能力匹配 + 负载均衡」挑选候选。',
    submitText: '注册',
    fields: [
      { name: 'name', label: '名称', required: true, placeholder: '例如：编码Agent' },
      { name: 'capabilities', label: '能力标签', type: 'tags', placeholder: 'python, code_gen, nlp', hint: '用逗号或空格分隔' },
      { name: 'endpoint', label: '回调地址', placeholder: 'https://example.com/hook（可留空）' },
    ],
    onSubmit: async (v) => {
      await api.createAgent({
        name: v.name,
        capabilities: v.capabilities || [],
        endpoint: v.endpoint || null,
      });
      toastOk(`已注册「${v.name}」`);
      await load(mount);
    },
  });
}

function openStatus(mount, id, cur) {
  openForm({
    title: '更新智能体状态',
    desc: `智能体 ${id.slice(0, 8)}`,
    submitText: '更新',
    fields: [{
      name: 'status', label: '状态', type: 'select', value: cur?.status || 'online',
      options: [
        { value: 'online', label: '在线' },
        { value: 'busy', label: '忙碌' },
        { value: 'offline', label: '离线' },
        { value: 'error', label: '异常' },
      ],
    }],
    onSubmit: async (v) => {
      await api.setAgentStatus(id, v.status);
      toastOk('状态已更新');
      await load(mount);
    },
  });
}

// ------------------------------------------------------------------ 详情页
async function renderDetail(mount, id) {
  mount.innerHTML = `<div class="empty"><span class="spinner" style="width:22px;height:22px"></span>
    <div class="empty__desc">加载智能体详情…</div></div>`;

  let agent;
  let tasks = { tasks: [], count: 0 };
  try {
    [agent, tasks] = await Promise.all([api.getAgent(id), api.agentTasks(id)]);
  } catch (e) {
    mount.innerHTML = emptyState({
      icon: '!', title: '加载失败或智能体不存在', desc: e.message,
      actionHtml: '<a class="btn" href="#/agents">返回列表</a>',
    });
    return;
  }

  const hue = hashHue(agent.agent_id);
  mount.innerHTML = `
    <div class="page-head">
      <div class="flex items-center gap-3">
        <div class="avatar avatar--lg" style="background:hsl(${hue} 62% 45%)">${esc(initials(agent.name))}</div>
        <div style="min-width:0">
          <h1 class="page-title">${esc(agent.name || '(未命名)')}</h1>
          <p class="page-desc mono">${esc(agent.agent_id)}</p>
        </div>
      </div>
      <div class="page-actions">
        <a class="btn" href="#/agents">← 返回</a>
        <button class="btn" id="dHb">发送心跳</button>
        <button class="btn" id="dStatus">改状态</button>
        <button class="btn btn--outline-danger" id="dDel">注销</button>
      </div>
    </div>

    <div class="section" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--sp-4)" id="dGrid">
      <div class="card">
        <div class="card__head"><div class="card__title">基本信息</div></div>
        <div class="card__body">
          <div class="kv">
            <div class="kv__k">状态</div><div class="kv__v">${enumBadge(agent.status, AGENT_STATUS)}</div>
            <div class="kv__k">能力</div><div class="kv__v">${chips(agent.capabilities, 'brand')}</div>
            <div class="kv__k">当前负载</div><div class="kv__v">${agent.current_task_count ?? 0} 个任务</div>
            <div class="kv__k">回调地址</div><div class="kv__v">${agent.endpoint ? esc(agent.endpoint) : '<span class="faint">未设置</span>'}</div>
            <div class="kv__k">注册时间</div><div class="kv__v">${esc(fmtTime(agent.registered_at))}</div>
            <div class="kv__k">最近心跳</div><div class="kv__v">${esc(fmtTime(agent.last_heartbeat))} <span class="faint">（${esc(fmtRelative(agent.last_heartbeat))}）</span></div>
            <div class="kv__k">元数据</div><div class="kv__v"><pre class="code">${esc(JSON.stringify(agent.metadata || {}, null, 2))}</pre></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card__head">
          <div><div class="card__title">已分配任务</div>
            <div class="card__desc">共 ${tasks.count} 个</div></div>
        </div>
        <div class="card__body" style="padding:0">
          ${tasks.tasks.length ? `<div class="table-wrap"><table class="table">
            <thead><tr><th>任务</th><th>状态</th><th>优先级</th></tr></thead>
            <tbody>${tasks.tasks.map((t) => `<tr>
              <td><a href="#/tasks/${encodeURIComponent(t.task_id)}">${esc(t.title)}</a>
                  <div class="table__id">${esc(t.task_id)}</div></td>
              <td>${enumBadge(t.status, TASK_STATUS)}</td>
              <td>${enumBadge(t.priority, PRIORITY)}</td>
            </tr>`).join('')}</tbody></table></div>`
            : emptyState({ icon: '◻', title: '暂无已分配任务', desc: '创建一个需要其能力的任务并自动分配即可。' })}
        </div>
      </div>
    </div>`;

  const mq = window.matchMedia('(max-width: 980px)');
  const grid = mount.querySelector('#dGrid');
  const apply = () => { grid.style.gridTemplateColumns = mq.matches ? 'minmax(0,1fr)' : 'minmax(0,1fr) minmax(0,1fr)'; };
  apply();
  mq.addEventListener('change', apply);

  // 就地重绘详情，不借助路由跳变
  const reload = async () => { mount.innerHTML = ''; await renderDetail(mount, id); };

  mount.querySelector('#dHb').addEventListener('click', async (e) => {
    setBusy(e.currentTarget, true, '…');
    try { await api.heartbeat(id); toastOk('心跳已更新'); await reload(); }
    catch (err) { toastErr(err); } finally { setBusy(e.currentTarget, false, '发送心跳'); }
  });

  mount.querySelector('#dStatus').addEventListener('click', () => openStatus(mount, id, agent));

  mount.querySelector('#dDel').addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: '注销智能体', message: `确定注销「${agent.name}」吗？`, confirmText: '注销', danger: true,
    });
    if (!ok) return;
    try { await api.deleteAgent(id); toastOk('已注销'); location.hash = '#/agents'; }
    catch (err) { toastErr(err); }
  });

  return () => mq.removeEventListener('change', apply);
}

// ------------------------------------------------------------------ 页面入口
export default {
  title: '智能体',

  async render(mount, params) {
    if (params && params.id) return renderDetail(mount, params.id);

    mount.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">智能体</h1>
          <p class="page-desc">参与协作的智能体注册表。能力标签决定任务分配时的匹配度，
            心跳超时（默认 60 秒）会自动标记为离线。</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="btnReload">刷新</button>
          <button class="btn btn--primary" id="btnCreate">+ 注册智能体</button>
        </div>
      </div>

      <div class="toolbar">
        <input class="input" id="q" placeholder="搜索名称 / ID / 能力…" style="min-width:220px">
        <select class="select" id="status">
          ${STATUS_OPTIONS.map((o) => `<option value="${o.value}">${o.label}</option>`).join('')}
        </select>
        <div class="toolbar__spacer"></div>
        <span class="text-xs faint" id="summary"></span>
      </div>

      <div class="card card--flush">
        <div class="card__body" style="padding:0">${tableShell()}</div>
      </div>`;

    const q = mount.querySelector('#q');
    const sel = mount.querySelector('#status');

    syncSummary = () => {
      const el = mount.querySelector('#summary');
      if (el) el.textContent = `共 ${state.items.length} 个智能体 · 筛选出 ${filtered().length} 个`;
    };

    q.addEventListener('input', () => { state.q = q.value; state.page = 1; paintList(mount); });
    sel.addEventListener('change', () => { state.status = sel.value; state.page = 1; paintList(mount); });
    mount.querySelector('#btnCreate').addEventListener('click', () => openCreate(mount));
    mount.querySelector('#btnReload').addEventListener('click', async (e) => {
      setBusy(e.currentTarget, true, '刷新中');
      await load(mount);
      setBusy(e.currentTarget, false, '刷新');
    });

    await load(mount);

    bindRealtime(
      ['agent.registered', 'agent.unregistered', 'agent.heartbeat',
       'agent.status_changed', 'agent.offline', 'task.assigned'],
      () => load(mount),
      { debounce: 300 },
    );
  },
};
