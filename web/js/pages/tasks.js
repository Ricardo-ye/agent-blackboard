/**
 * 任务协调 —— 列表 + 详情 + 依赖管理 + 自动/手动分配 + 状态流转
 * 对应后端 TaskCoordinator：能力匹配、负载均衡、依赖拓扑、循环依赖检测。
 */
import { api, ApiError } from '../api.js';
import { bindRealtime } from '../bus.js';
import {
  esc, enumBadge, chips, TASK_STATUS, PRIORITY, fmtTime, fmtRelative, stringifyContent,
  openForm, confirmDialog, toast, toastOk, toastErr, skeletonRows, emptyState,
  renderStates, paginate, pagerHtml, setBusy,
} from '../ui.js';

const state = {
  items: [], agents: [], loading: true, error: null,
  q: '', status: '', page: 1, size: 10,
};

let syncSummary = () => {};

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待分配' },
  { value: 'assigned', label: '已分配' },
  { value: 'in_progress', label: '进行中' },
  { value: 'review', label: '待评审' },
  { value: 'done', label: '已完成' },
  { value: 'blocked', label: '已阻塞' },
  { value: 'failed', label: '失败' },
];

const PRIORITY_OPTIONS = PRIORITY_OPTIONS_BUILD();

function PRIORITY_OPTIONS_BUILD() {
  return [
    { value: 'low', label: '低' },
    { value: 'normal', label: '普通' },
    { value: 'high', label: '高' },
    { value: 'critical', label: '紧急' },
  ];
}

function agentName(id) {
  if (!id) return '—';
  const a = state.agents.find((x) => x.agent_id === id);
  return a ? a.name : String(id).slice(0, 8);
}

function filtered() {
  const q = state.q.trim().toLowerCase();
  return state.items.filter((t) => {
    if (state.status && t.status !== state.status) return false;
    if (!q) return true;
    return (t.title || '').toLowerCase().includes(q)
      || (t.description || '').toLowerCase().includes(q)
      || (t.required_capabilities || []).join(' ').toLowerCase().includes(q)
      || (t.task_id || '').toLowerCase().includes(q);
  });
}

function rowsHtml(rows) {
  return rows.map((t) => `<tr>
    <td>
      <a href="#/tasks/${encodeURIComponent(t.task_id)}" class="font-medium">${esc(t.title)}</a>
      <div class="table__id">${esc(t.task_id)}</div>
    </td>
    <td>${enumBadge(t.status, TASK_STATUS)}</td>
    <td>${enumBadge(t.priority, PRIORITY)}</td>
    <td>${chips(t.required_capabilities, 'brand')}</td>
    <td>${t.assignee_id ? esc(agentName(t.assignee_id)) : '<span class="faint">未分配</span>'}</td>
    <td class="num">${(t.dependencies || []).length ? `<span class="badge badge--neutral">${t.dependencies.length} 项</span>` : '<span class="faint">—</span>'}</td>
    <td class="faint text-xs" title="${esc(fmtTime(t.updated_at))}">${esc(fmtRelative(t.updated_at))}</td>
    <td class="text-right">
      <div class="btn-group" style="justify-content:flex-end">
        <button class="btn btn--sm" data-auto="${esc(t.task_id)}" title="按能力+负载挑选最佳智能体">自动分配</button>
        <button class="btn btn--sm" data-assign="${esc(t.task_id)}">手动分配</button>
        <a class="btn btn--sm btn--subtle" href="#/tasks/${encodeURIComponent(t.task_id)}">详情</a>
      </div>
    </td>
  </tr>`).join('');
}

function tableShell() {
  return `<div class="table-wrap"><table class="table">
    <thead><tr>
      <th>任务</th><th>状态</th><th>优先级</th><th>所需能力</th><th>负责人</th>
      <th class="num">依赖</th><th>更新时间</th><th></th>
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
    skeleton: skeletonRows(5, 5),
    emptyHtml: emptyState({
      icon: '✓',
      title: state.q || state.status ? '没有匹配的任务' : '还没有创建任务',
      desc: state.q || state.status
        ? '试试清空搜索或切换状态筛选。'
        : '任务声明所需能力后，可由规则自动分配，也可手动指派给某个智能体。',
      actionHtml: state.q || state.status
        ? '<button class="btn" data-act="reset">清空筛选</button>'
        : '<button class="btn btn--primary" data-act="create">+ 创建任务</button>',
    }),
  })) {
    pager.innerHTML = '';
    const resetBtn = body.querySelector('[data-act="reset"]');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.q = ''; state.status = ''; state.page = 1;
      mount.querySelector('#q').value = '';
      mount.querySelector('#status').value = '';
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

  body.querySelectorAll('[data-auto]').forEach((b) => {
    b.addEventListener('click', async (e) => {
      const id = b.getAttribute('data-auto');
      setBusy(e.currentTarget, true, '分配中');
      try {
        const t = await api.autoAssignTask(id);
        toastOk(`已分配给 ${agentName(t.assignee_id) || t.assignee_id}`, '自动分配成功');
        await load(mount);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          toastErr(new Error('依赖未就绪，或没有具备所需能力的空闲智能体'), '无法分配');
        } else toastErr(err);
      } finally { setBusy(e.currentTarget, false, '自动分配'); }
    });
  });

  body.querySelectorAll('[data-assign]').forEach((b) => {
    b.addEventListener('click', () => {
      const id = b.getAttribute('data-assign');
      openAssign(mount, state.items.find((t) => t.task_id === id));
    });
  });

  syncSummary();
}

async function load(mount, quiet = false) {
  if (!quiet) { state.loading = true; paintList(mount); }
  try {
    const [tasks, agents] = await Promise.all([api.listTasks(), api.listAgents()]);
    state.items = tasks;
    state.agents = agents;
    state.error = null;
  } catch (e) {
    state.error = e;
  } finally {
    state.loading = false;
    paintList(mount);
  }
}

function depOptions(excludeId) {
  return state.items
    .filter((t) => t.task_id !== excludeId)
    .map((t) => ({ value: t.task_id, label: `${t.title}（${t.task_id.slice(0, 8)}）` }));
}

function openCreate(mount) {
  openForm({
    title: '创建任务',
    desc: '声明所需能力后，自动分配会按「能力匹配度 + 当前负载」挑选智能体；'
        + '优先级为 high/critical 的任务会在创建时命中默认规则自动分配。',
    submitText: '创建',
    wide: true,
    fields: [
      { name: 'title', label: '标题', required: true, placeholder: '例如：实现黑板核心读写' },
      { name: 'description', label: '描述', type: 'textarea', rows: 3 },
      { name: 'required_capabilities', label: '所需能力', type: 'tags', placeholder: 'python, code_gen', hint: '逗号分隔；填不存在的能力可用于演示「无可用智能体」' },
      { name: 'priority', label: '优先级', type: 'select', value: 'normal', options: PRIORITY_OPTIONS },
      { name: 'dependencies', label: '前置依赖', type: 'multi', options: depOptions(null),
        emptyHint: '还没有其它任务可作为前置依赖', hint: '点击选择；依赖未完成前任务无法被分配' },
    ],
    onSubmit: async (v) => {
      await api.createTask({
        title: v.title,
        description: v.description || '',
        required_capabilities: v.required_capabilities || [],
        priority: v.priority,
        dependencies: v.dependencies || [],
      });
      toastOk(`任务「${v.title}」已创建`);
      await load(mount);
    },
  });
}

function openAssign(mount, task) {
  if (!task) return;
  if (!state.agents.length) {
    toastErr(new Error('还没有注册任何智能体，请先到「智能体」页面注册'), '无法分配');
    return;
  }
  openForm({
    title: '手动分配任务',
    desc: `任务：${task.title}`,
    submitText: '分配',
    fields: [{
      name: 'assignee_id', label: '负责人', type: 'select', required: true,
      value: task.assignee_id || '',
      options: state.agents.map((a) => ({
        value: a.agent_id,
        label: `${a.name}（负载 ${a.current_task_count} · ${a.capabilities.join('/') || '无能力'}）`,
      })),
    }],
    onSubmit: async (v) => {
      await api.assignTask(task.task_id, v.assignee_id);
      toastOk(`已指派给 ${agentName(v.assignee_id)}`);
      await load(mount);
    },
  });
}

// ------------------------------------------------------------------ 详情页
async function renderDetail(mount, id) {
  mount.innerHTML = `<div class="empty"><span class="spinner" style="width:22px;height:22px"></span>
    <div class="empty__desc">加载任务详情…</div></div>`;

  let task;
  try {
    task = await api.getTask(id);
  } catch (e) {
    mount.innerHTML = emptyState({
      icon: '!', title: '加载失败或任务不存在', desc: e.message,
      actionHtml: '<a class="btn" href="#/tasks">返回列表</a>',
    });
    return;
  }

  // 依赖详情（含完成状态），用于直观展示「依赖是否就绪」
  const deps = [];
  for (const d of task.dependencies || []) {
    try { deps.push(await api.getTask(d)); } catch { deps.push({ task_id: d, title: '(已删除)', status: '?' }); }
  }

  const updater = task.assignee_id || 'console-ui';

  mount.innerHTML = `
    <div class="page-head">
      <div style="min-width:0">
        <h1 class="page-title">${esc(task.title)}</h1>
        <p class="page-desc">${esc(task.description || '（无描述）')}</p>
      </div>
      <div class="page-actions">
        <a class="btn" href="#/tasks">← 返回</a>
        <button class="btn" id="dAuto">自动分配</button>
        <button class="btn" id="dAssign">手动分配</button>
      </div>
    </div>

    <div class="section" style="display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:var(--sp-4)" id="dGrid">
      <div class="stack">
        <div class="card">
          <div class="card__head"><div class="card__title">状态流转</div>
            <div class="card__desc">当前 ${TASK_STATUS[task.status]?.label || task.status}</div></div>
          <div class="card__body">
            <div class="flex gap-2 flex-wrap">
              <button class="btn" data-st="in_progress">开始处理</button>
              <button class="btn" data-st="review">提交评审</button>
              <button class="btn btn--primary" data-st="done">标记完成</button>
              <button class="btn" data-st="blocked">阻塞</button>
              <button class="btn btn--outline-danger" data-st="failed">标记失败</button>
              <button class="btn btn--subtle" data-st="pending">重置为待分配</button>
            </div>
            <div class="field__hint mt-3">完成或失败会释放负责人负载，并检查下游依赖是否可以解锁。</div>
          </div>
        </div>

        <div class="card">
          <div class="card__head">
            <div><div class="card__title">任务结果</div>
              <div class="card__desc">任意 JSON</div></div>
            <button class="btn btn--sm" id="dResult">更新结果</button>
          </div>
          <div class="card__body">
            <pre class="code">${esc(stringifyContent(task.result) || '（尚未提交结果）')}</pre>
          </div>
        </div>

        <div class="card">
          <div class="card__head"><div class="card__title">前置依赖</div>
            <div class="card__desc">共 ${deps.length} 项</div></div>
          <div class="card__body" style="padding:0">
            ${deps.length ? `<div class="table-wrap"><table class="table">
              <thead><tr><th>任务</th><th>状态</th><th>是否就绪</th></tr></thead>
              <tbody>${deps.map((d) => `<tr>
                <td><a href="#/tasks/${encodeURIComponent(d.task_id)}">${esc(d.title)}</a>
                    <div class="table__id">${esc(d.task_id)}</div></td>
                <td>${TASK_STATUS[d.status] ? enumBadge(d.status, TASK_STATUS) : esc(d.status)}</td>
                <td>${d.status === 'done'
                  ? '<span class="badge badge--success">已就绪</span>'
                  : '<span class="badge badge--warn">未完成</span>'}</td>
              </tr>`).join('')}</tbody></table></div>`
              : emptyState({ icon: '—', title: '无前置依赖', desc: '该任务可以立即被分配。' })}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card__head"><div class="card__title">任务信息</div></div>
        <div class="card__body">
          <div class="kv">
            <div class="kv__k">任务 ID</div><div class="kv__v mono">${esc(task.task_id)}</div>
            <div class="kv__k">状态</div><div class="kv__v">${enumBadge(task.status, TASK_STATUS)}</div>
            <div class="kv__k">优先级</div><div class="kv__v">${enumBadge(task.priority, PRIORITY)}</div>
            <div class="kv__k">所需能力</div><div class="kv__v">${chips(task.required_capabilities, 'brand')}</div>
            <div class="kv__k">负责人</div><div class="kv__v">${task.assignee_id ? esc(agentName(task.assignee_id)) : '<span class="faint">未分配</span>'}</div>
            <div class="kv__k">创建者</div><div class="kv__v">${task.creator_id ? esc(agentName(task.creator_id)) : '<span class="faint">未指定</span>'}</div>
            <div class="kv__k">截止时间</div><div class="kv__v">${task.deadline ? esc(fmtTime(task.deadline)) : '<span class="faint">无</span>'}</div>
            <div class="kv__k">创建时间</div><div class="kv__v">${esc(fmtTime(task.created_at))}</div>
            <div class="kv__k">更新时间</div><div class="kv__v">${esc(fmtTime(task.updated_at))} <span class="faint">（${esc(fmtRelative(task.updated_at))}）</span></div>
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

  // 状态流转
  mount.querySelectorAll('[data-st]').forEach((b) => {
    b.addEventListener('click', async (e) => {
      const st = b.getAttribute('data-st');
      setBusy(e.currentTarget, true, '…');
      try {
        await api.updateTask(id, updater, { status: st });
        toastOk(`状态已更新为「${TASK_STATUS[st].label}」`);
        await reload();
      } catch (err) { toastErr(err); }
      finally { setBusy(e.currentTarget, false, b.textContent); }
    });
  });

  mount.querySelector('#dResult').addEventListener('click', () => {
    openForm({
      title: '更新任务结果',
      submitText: '保存',
      wide: true,
      fields: [{ name: 'result', label: '结果', type: 'json', rows: 6, value: task.result ?? { ok: true } }],
      onSubmit: async (v) => {
        await api.updateTask(id, updater, { result: v.result });
        toastOk('结果已保存');
        await reload();
      },
    });
  });

  mount.querySelector('#dAuto').addEventListener('click', async (e) => {
    setBusy(e.currentTarget, true, '分配中');
    try {
      const t = await api.autoAssignTask(id);
      toastOk(`已分配给 ${agentName(t.assignee_id) || t.assignee_id}`, '自动分配成功');
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toastErr(new Error('依赖未就绪，或没有具备所需能力的空闲智能体'), '无法分配');
      } else toastErr(err);
    } finally { setBusy(e.currentTarget, false, '自动分配'); }
  });

  mount.querySelector('#dAssign').addEventListener('click', () => {
    openForm({
      title: '手动分配任务',
      submitText: '分配',
      fields: [{
        name: 'assignee_id', label: '负责人', type: 'select', required: true, value: task.assignee_id || '',
        options: state.agents.length
          ? state.agents.map((a) => ({ value: a.agent_id, label: `${a.name}（负载 ${a.current_task_count}）` }))
          : [{ value: '', label: '请先注册智能体' }],
      }],
      onSubmit: async (v) => {
        await api.assignTask(id, v.assignee_id);
        toastOk('已指派');
        await reload();
      },
    });
  });

  return () => mq.removeEventListener('change', apply);
}

// ------------------------------------------------------------------ 页面入口
export default {
  title: '任务',

  async render(mount, params) {
    if (params && params.id) {
      await load(mount, true);   // 详情需要智能体列表来显示负责人名称
      return renderDetail(mount, params.id);
    }

    mount.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">任务</h1>
          <p class="page-desc">任务声明所需能力，由协调器按「能力匹配 + 负载均衡」分配；
            支持前置依赖，依赖全部完成后才可分配，并自动解锁下游任务。</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="btnReload">刷新</button>
          <button class="btn btn--primary" id="btnCreate">+ 创建任务</button>
        </div>
      </div>

      <div class="toolbar">
        <input class="input" id="q" placeholder="搜索标题 / 描述 / 能力…" style="min-width:220px">
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
      if (el) el.textContent = `共 ${state.items.length} 个任务 · 筛选出 ${filtered().length} 个`;
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
      ['task.created', 'task.assigned', 'task.status_changed', 'task.unblocked'],
      () => load(mount, true),
      { debounce: 300 },
    );
  },
};
