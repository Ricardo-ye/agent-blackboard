/**
 * 冲突治理 —— 列表 + 详情 + 意见冲突检测 + 策略化解决
 * 对应后端 ConflictResolver：检测幂等、策略链、终态保护。
 */
import { api, ApiError } from '../api.js';
import { bindRealtime } from '../bus.js';
import {
  esc, enumBadge, chips, CONFLICT_STATUS, CONFLICT_TYPE, fmtTime, fmtRelative,
  openForm, toast, toastOk, toastErr, skeletonRows, emptyState,
  renderStates, paginate, pagerHtml, setBusy,
} from '../ui.js';

const state = { items: [], agents: [], topics: [], loading: true, error: null, status: '', page: 1, size: 10 };

let syncSummary = () => {};

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'detected', label: '已发现' },
  { value: 'resolving', label: '解决中' },
  { value: 'resolved', label: '已解决' },
  { value: 'escalated', label: '已升级' },
  { value: 'dismissed', label: '已忽略' },
];

const STRATEGY_OPTIONS = [
  { value: '', label: '自动选择（按冲突类型推荐）' },
  { value: 'merge', label: 'merge · 版本合并（写冲突）' },
  { value: 'priority', label: 'priority · 优先级仲裁（任务/资源）' },
  { value: 'vote', label: 'vote · 置信度加权投票（意见冲突）' },
  { value: 'escalate', label: 'escalate · 升级人工处理' },
];

const STRATEGY_HINT = {
  '': '意见冲突→vote，写冲突→merge，任务/资源冲突→priority',
  merge: '重新拉取最新版本，让冲突方基于新版本重试',
  priority: '按优先级排队，等待下一个可用智能体',
  vote: '按置信度加权投票，胜出条目会被合并为新条目',
  escalate: '标记为待人工处理（进入活跃态，仍可继续解决）',
};

function filtered() {
  return state.items.filter((c) => !state.status || c.status === state.status);
}

function agentLabel(id) {
  const a = state.agents.find((x) => x.agent_id === id);
  return a ? a.name : String(id).slice(0, 8);
}

function rowsHtml(rows) {
  return rows.map((c) => {
    const terminal = c.status === 'resolved' || c.status === 'dismissed';
    return `<tr>
      <td>${esc(CONFLICT_TYPE[c.conflict_type] || c.conflict_type)}</td>
      <td>${enumBadge(c.status, CONFLICT_STATUS)}</td>
      <td style="max-width:320px">
        <div class="truncate" style="max-width:320px" title="${esc(c.description)}">${esc(c.description)}</div>
      </td>
      <td>${chips((c.involved_agents || []).map(agentLabel))}</td>
      <td class="faint text-xs" title="${esc(fmtTime(c.created_at))}">${esc(fmtRelative(c.created_at))}</td>
      <td class="text-right">
        <div class="btn-group" style="justify-content:flex-end">
          <button class="btn btn--sm" data-resolve="${esc(c.conflict_id)}" ${terminal ? 'disabled title="已处于终态"' : ''}>解决</button>
          <a class="btn btn--sm btn--subtle" href="#/conflicts/${encodeURIComponent(c.conflict_id)}">详情</a>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function tableShell() {
  return `<div class="table-wrap"><table class="table">
    <thead><tr>
      <th>类型</th><th>状态</th><th>描述</th><th>涉及智能体</th><th>发现时间</th><th></th>
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
    skeleton: skeletonRows(4, 4),
    emptyHtml: emptyState({
      icon: '⚠',
      title: state.status ? '没有该状态的冲突' : '目前没有冲突',
      desc: state.status
        ? '切换筛选条件看看其它状态。'
        : '冲突会在多智能体对同一主题产生不同观点时自动发现，也可手动对某个主题触发检测。',
      actionHtml: state.status
        ? '<button class="btn" data-act="reset">清空筛选</button>'
        : '<button class="btn btn--primary" data-act="detect">检测意见冲突</button>',
    }),
  })) {
    pager.innerHTML = '';
    const resetBtn = body.querySelector('[data-act="reset"]');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.status = ''; state.page = 1;
      mount.querySelector('#status').value = '';
      paintList(mount);
    });
    const detectBtn = body.querySelector('[data-act="detect"]');
    if (detectBtn) detectBtn.addEventListener('click', () => openDetect(mount));
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

  body.querySelectorAll('[data-resolve]').forEach((b) => {
    b.addEventListener('click', () => {
      const id = b.getAttribute('data-resolve');
      const c = state.items.find((x) => x.conflict_id === id);
      openResolve(mount, c, true);
    });
  });

  syncSummary();
}

async function load(mount, quiet = false) {
  if (!quiet) { state.loading = true; paintList(mount); }
  try {
    const [list, agents, entries] = await Promise.all([
      api.listConflicts(), api.listAgents(), api.listEntries(),
    ]);
    state.items = list;
    state.agents = agents;
    state.topics = [...new Set(entries.map((e) => e.topic))].sort();
    state.error = null;
  } catch (e) {
    state.error = e;
  } finally {
    state.loading = false;
    paintList(mount);
  }
}

function openDetect(mount) {
  openForm({
    title: '检测意见冲突',
    desc: '同一主题下存在 ≥2 个智能体、且内容互不相同的条目时，判定为意见冲突。'
        + '检测结果幂等：已存在活跃冲突时复用，不会重复创建。',
    submitText: '检测',
    fields: [{
      name: 'topic', label: '主题', required: true,
      placeholder: state.topics[0] || '例如：architecture',
      hint: state.topics.length ? `当前已有主题：${state.topics.join('、')}` : '尚无任何主题，请先发布条目',
    }],
    onSubmit: async (v) => {
      const r = await api.detectOpinion(v.topic);
      if (r.conflict) {
        toastOk(`已发现冲突（${CONFLICT_TYPE[r.conflict.conflict_type] || r.conflict.conflict_type}）`, '检测完成');
      } else {
        toast(`主题「${v.topic}」未发现意见冲突`, 'info', '检测完成');
      }
      await load(mount);
    },
  });
}

function openResolve(mount, conflict, refreshAfter = false) {
  if (!conflict) return;
  openForm({
    title: '解决冲突',
    desc: `${CONFLICT_TYPE[conflict.conflict_type] || conflict.conflict_type} · ${conflict.description}`,
    submitText: '执行解决',
    fields: [
      {
        name: 'strategy', label: '解决策略', type: 'select', value: '',
        options: STRATEGY_OPTIONS,
        hint: STRATEGY_HINT[''],
      },
      { name: 'resolved_by', label: '解决者', value: 'console-ui', placeholder: '记录是谁解决的' },
    ],
    onSubmit: async (v) => {
      const r = await api.resolveConflict(conflict.conflict_id, v.strategy || null, v.resolved_by || 'console-ui');
      toastOk(r.resolution || '已解决', `状态：${CONFLICT_STATUS[r.status]?.label || r.status}`);
      if (refreshAfter) await load(mount);
    },
  });
}

// ------------------------------------------------------------------ 详情页
async function renderDetail(mount, id) {
  mount.innerHTML = `<div class="empty"><span class="spinner" style="width:22px;height:22px"></span>
    <div class="empty__desc">加载冲突详情…</div></div>`;

  let conflict;
  try {
    [conflict] = await Promise.all([api.getConflict(id), load(mount, true)]);
  } catch (e) {
    mount.innerHTML = emptyState({
      icon: '!', title: '加载失败或冲突不存在', desc: e.message,
      actionHtml: '<a class="btn" href="#/conflicts">返回列表</a>',
    });
    return;
  }

  const terminal = conflict.status === 'resolved' || conflict.status === 'dismissed';

  mount.innerHTML = `
    <div class="page-head">
      <div style="min-width:0">
        <h1 class="page-title">${esc(CONFLICT_TYPE[conflict.conflict_type] || conflict.conflict_type)}</h1>
        <p class="page-desc">${esc(conflict.description)}</p>
      </div>
      <div class="page-actions">
        <a class="btn" href="#/conflicts">← 返回</a>
        <button class="btn btn--primary" id="dResolve" ${terminal ? 'disabled' : ''}>
          ${terminal ? '已处于终态' : '解决冲突'}</button>
      </div>
    </div>

    ${terminal ? `<div class="alert alert--info mb-4">
      <span class="alert__icon">i</span>
      <div class="alert__body"><div class="alert__title">该冲突已终结</div>
        <div class="alert__desc">已解决 / 已忽略的冲突不允许重复解决，避免投票策略反复生成合并条目。</div></div>
    </div>` : ''}

    <div class="section" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--sp-4)" id="dGrid">
      <div class="card">
        <div class="card__head"><div class="card__title">冲突上下文</div></div>
        <div class="card__body"><pre class="code">${esc(JSON.stringify(conflict.context || {}, null, 2))}</pre></div>
      </div>
      <div class="card">
        <div class="card__head"><div class="card__title">解决记录</div></div>
        <div class="card__body">
          <div class="kv">
            <div class="kv__k">状态</div><div class="kv__v">${enumBadge(conflict.status, CONFLICT_STATUS)}</div>
            <div class="kv__k">涉及智能体</div><div class="kv__v">${chips((conflict.involved_agents || []).map(agentLabel))}</div>
            <div class="kv__k">解决者</div><div class="kv__v">${conflict.resolved_by ? esc(conflict.resolved_by) : '<span class="faint">尚未解决</span>'}</div>
            <div class="kv__k">解决时间</div><div class="kv__v">${conflict.resolved_at ? esc(fmtTime(conflict.resolved_at)) : '—'}</div>
            <div class="kv__k">发现时间</div><div class="kv__v">${esc(fmtTime(conflict.created_at))}</div>
          </div>
          ${conflict.resolution ? `<div class="mt-4">
            <div class="field__label">处理结论</div>
            <pre class="code mt-2">${esc(conflict.resolution)}</pre></div>` : ''}
        </div>
      </div>
    </div>`;

  const mq = window.matchMedia('(max-width: 980px)');
  const grid = mount.querySelector('#dGrid');
  const apply = () => { grid.style.gridTemplateColumns = mq.matches ? 'minmax(0,1fr)' : 'minmax(0,1fr) minmax(0,1fr)'; };
  apply();
  mq.addEventListener('change', apply);

  const reload = async () => { mount.innerHTML = ''; await renderDetail(mount, id); };

  mount.querySelector('#dResolve').addEventListener('click', async () => {
    openForm({
      title: '解决冲突',
      desc: `${CONFLICT_TYPE[conflict.conflict_type] || conflict.conflict_type} · ${conflict.description}`,
      submitText: '执行解决',
      fields: [
        { name: 'strategy', label: '解决策略', type: 'select', value: '', options: STRATEGY_OPTIONS },
        { name: 'resolved_by', label: '解决者', value: 'console-ui' },
      ],
      onSubmit: async (v) => {
        try {
          const r = await api.resolveConflict(id, v.strategy || null, v.resolved_by || 'console-ui');
          toastOk(r.resolution || '已解决', `状态：${CONFLICT_STATUS[r.status]?.label || r.status}`);
          await reload();
        } catch (e2) {
          if (e2 instanceof ApiError && e2.status === 409) toastErr(e2, '冲突不可重复解决');
          else toastErr(e2);
        }
      },
    });
  });

  return () => mq.removeEventListener('change', apply);
}

// ------------------------------------------------------------------ 页面入口
export default {
  title: '冲突',

  async render(mount, params) {
    if (params && params.id) return renderDetail(mount, params.id);

    mount.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">冲突治理</h1>
          <p class="page-desc">多智能体协作中出现的写冲突、分配冲突、资源冲突与意见冲突。
            同一问题只保留一条活跃冲突，检测幂等；解决后进入终态，不可重复解决。</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="btnReload">刷新</button>
          <button class="btn btn--primary" id="btnDetect">检测意见冲突</button>
        </div>
      </div>

      <div class="toolbar">
        <select class="select" id="status">
          ${STATUS_OPTIONS.map((o) => `<option value="${o.value}">${o.label}</option>`).join('')}
        </select>
        <div class="toolbar__spacer"></div>
        <span class="text-xs faint" id="summary"></span>
      </div>

      <div class="card card--flush">
        <div class="card__body" style="padding:0">${tableShell()}</div>
      </div>`;

    const sel = mount.querySelector('#status');
    syncSummary = () => {
      const el = mount.querySelector('#summary');
      if (el) {
        const active = state.items.filter((c) => !['resolved', 'dismissed'].includes(c.status)).length;
        el.textContent = `共 ${state.items.length} 条冲突 · 活跃 ${active} 条`;
      }
    };

    sel.addEventListener('change', () => { state.status = sel.value; state.page = 1; paintList(mount); });
    mount.querySelector('#btnDetect').addEventListener('click', () => openDetect(mount));
    mount.querySelector('#btnReload').addEventListener('click', async (e) => {
      setBusy(e.currentTarget, true, '刷新中');
      await load(mount);
      setBusy(e.currentTarget, false, '刷新');
    });

    await load(mount);

    bindRealtime(
      ['conflict.detected', 'conflict.resolved', 'conflict.escalated'],
      () => load(mount, true),
      { debounce: 300 },
    );
  },
};
