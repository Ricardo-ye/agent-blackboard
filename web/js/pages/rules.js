/**
 * 协作规则 —— 列表 + 条件构造器 + 热加载开关
 * 对应后端 RuleEngine：触发器 → 条件匹配 → 动作执行，改完即生效。
 *
 * 注意：rule_id / trigger / action 属于规则骨架，创建后不可修改
 * （后端 PATCH 白名单只允许 name / condition / action_params / enabled / priority），
 * 所以编辑表单里不提供这几项，避免 422。
 */
import { api, ApiError } from '../api.js';
import { bindRealtime } from '../bus.js';
import {
  esc, badge, chips, RULE_TRIGGER, RULE_ACTION, fmtTime, fmtRelative,
  openForm, confirmDialog, toast, toastOk, toastErr, skeletonRows, emptyState,
  renderStates, paginate, pagerHtml, setBusy, stringifyContent,
} from '../ui.js';

const state = { items: [], loading: true, error: null, q: '', page: 1, size: 10 };

let syncSummary = () => {};

const TRIGGER_OPTIONS = Object.entries(RULE_TRIGGER).map(([v, l]) => ({ value: v, label: l }));
const ACTION_OPTIONS = Object.entries(RULE_ACTION).map(([v, l]) => ({ value: v, label: l }));

const OPERATOR_OPTIONS = [
  { value: 'eq', label: 'eq 等于' },
  { value: 'ne', label: 'ne 不等于' },
  { value: 'gt', label: 'gt 大于' },
  { value: 'lt', label: 'lt 小于' },
  { value: 'gte', label: 'gte 大于等于' },
  { value: 'lte', label: 'lte 小于等于' },
  { value: 'in', label: 'in 属于列表' },
  { value: 'nin', label: 'nin 不属于列表' },
  { value: 'contains', label: 'contains 包含' },
];

function filtered() {
  const q = state.q.trim().toLowerCase();
  if (!q) return state.items;
  return state.items.filter((r) => (r.name || '').toLowerCase().includes(q)
    || (r.trigger || '').toLowerCase().includes(q)
    || (r.action || '').toLowerCase().includes(q));
}

function condText(c) {
  if (!c || !Object.keys(c).length) return '<span class="faint">无条件（总是匹配）</span>';
  if (c.and || c.or) return `<span class="mono">复合条件 ${c.and ? 'AND' : 'OR'}（${(c.and || c.or).length} 项）</span>`;
  if (c.field) {
    return `<span class="mono">${esc(c.field)} ${esc(c.operator || 'eq')} ${esc(stringifyContent(c.value))}</span>`;
  }
  return `<span class="mono">${esc(JSON.stringify(c))}</span>`;
}

function rowsHtml(rows) {
  return rows.map((r) => `<tr>
    <td>
      <div class="font-medium">${esc(r.name)}</div>
      <div class="table__id">${esc(r.rule_id)}</div>
    </td>
    <td>${badge(RULE_TRIGGER[r.trigger] || r.trigger, 'info')}</td>
    <td style="max-width:220px">${condText(r.condition)}</td>
    <td>${badge(RULE_ACTION[r.action] || r.action, 'brand')}</td>
    <td class="num">${r.priority ?? 0}</td>
    <td>${r.enabled
      ? badge('已启用', 'success', { dot: true, pulse: true })
      : badge('已停用', 'neutral')}</td>
    <td class="faint text-xs" title="${esc(fmtTime(r.created_at))}">${esc(fmtRelative(r.created_at))}</td>
    <td class="text-right">
      <div class="btn-group" style="justify-content:flex-end">
        <button class="btn btn--sm" data-toggle="${esc(r.rule_id)}">${r.enabled ? '停用' : '启用'}</button>
        <button class="btn btn--sm" data-edit="${esc(r.rule_id)}">编辑</button>
        <button class="btn btn--sm btn--outline-danger" data-del="${esc(r.rule_id)}" data-name="${esc(r.name)}">删除</button>
      </div>
    </td>
  </tr>`).join('');
}

function tableShell() {
  return `<div class="table-wrap"><table class="table">
    <thead><tr>
      <th>规则</th><th>触发时机</th><th>匹配条件</th><th>执行动作</th>
      <th class="num">优先级</th><th>状态</th><th>创建时间</th><th></th>
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
    skeleton: skeletonRows(4, 5),
    emptyHtml: emptyState({
      icon: '⚙',
      title: state.q ? '没有匹配的规则' : '还没有配置规则',
      desc: state.q
        ? '换个关键词试试。'
        : '规则在事件触发时按优先级从高到低匹配，命中即执行动作。系统初始化会内置 4 条默认规则。',
      actionHtml: state.q
        ? '<button class="btn" data-act="reset">清空筛选</button>'
        : '<button class="btn btn--primary" data-act="create">+ 新建规则</button>',
    }),
  })) {
    pager.innerHTML = '';
    const resetBtn = body.querySelector('[data-act="reset"]');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.q = ''; state.page = 1;
      mount.querySelector('#q').value = '';
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

  body.querySelectorAll('[data-toggle]').forEach((b) => {
    b.addEventListener('click', async (e) => {
      const id = b.getAttribute('data-toggle');
      const r = state.items.find((x) => x.rule_id === id);
      setBusy(e.currentTarget, true, '…');
      try {
        await api.updateRule(id, { enabled: !r.enabled });
        toastOk(r.enabled ? '规则已停用' : '规则已启用');
        await load(mount);
      } catch (err) { toastErr(err); }
      finally { setBusy(e.currentTarget, false, r.enabled ? '停用' : '启用'); }
    });
  });
  body.querySelectorAll('[data-edit]').forEach((b) => {
    b.addEventListener('click', () => {
      const id = b.getAttribute('data-edit');
      openEdit(mount, state.items.find((r) => r.rule_id === id));
    });
  });
  body.querySelectorAll('[data-del]').forEach((b) => {
    b.addEventListener('click', async () => {
      const id = b.getAttribute('data-del');
      const name = b.getAttribute('data-name');
      const ok = await confirmDialog({
        title: '删除规则', message: `确定删除规则「${name}」吗？`, confirmText: '删除', danger: true,
      });
      if (!ok) return;
      try { await api.deleteRule(id); toastOk('规则已删除'); await load(mount); }
      catch (err) { toastErr(err); }
    });
  });

  syncSummary();
}

async function load(mount, quiet = false) {
  if (!quiet) { state.loading = true; paintList(mount); }
  try {
    state.items = await api.listRules();
    state.error = null;
  } catch (e) {
    state.error = e;
  } finally {
    state.loading = false;
    paintList(mount);
  }
}

/** 条件值：能解析成 JSON 就用 JSON，否则按字符串 */
function condValue(raw) {
  const t = String(raw ?? '').trim();
  if (!t) return null;
  try { return JSON.parse(t); } catch { return t; }
}

function buildCondition(v) {
  if (!v.cond_field) return {};
  return { field: v.cond_field, operator: v.cond_operator || 'eq', value: condValue(v.cond_value) };
}

function openCreate(mount) {
  openForm({
    title: '新建协作规则',
    desc: '事件触发时，按优先级从高到低匹配条件，命中即执行动作。规则热加载，保存后立即生效。',
    submitText: '创建',
    wide: true,
    fields: [
      { name: 'name', label: '规则名称', required: true, placeholder: '例如：紧急任务立即分配' },
      { name: 'trigger', label: '触发时机', type: 'select', required: true, options: TRIGGER_OPTIONS },
      { name: 'action', label: '执行动作', type: 'select', required: true, options: ACTION_OPTIONS },
      { name: 'cond_field', label: '条件字段（留空表示无条件匹配）',
        placeholder: '例如：priority / topic / status',
        hint: '支持点号访问嵌套字段，如 task.priority' },
      { name: 'cond_operator', label: '比较运算符', type: 'select', value: 'eq', options: OPERATOR_OPTIONS },
      { name: 'cond_value', label: '条件值', placeholder: '例如：high',
        hint: '需要数字/布尔/数组时写成 JSON，如 3 或 ["a","b"]' },
      { name: 'action_params', label: '动作参数', type: 'json', rows: 3, value: {},
        hint: 'notify 可用 {"channel":"system","message":"..."}；set_priority 可用 {"priority":"high"}' },
      { name: 'priority', label: '优先级', type: 'number', value: 0, hint: '数字越大越先匹配' },
      { name: 'enabled', label: '立即启用', type: 'switch', value: true, switchLabel: '启用' },
    ],
    onSubmit: async (v) => {
      await api.createRule({
        name: v.name,
        trigger: v.trigger,
        condition: buildCondition(v),
        action: v.action,
        action_params: v.action_params || {},
        enabled: !!v.enabled,
        priority: v.priority ?? 0,
      });
      toastOk(`规则「${v.name}」已创建`);
      await load(mount);
    },
  });
}

function openEdit(mount, rule) {
  if (!rule) return;
  const c = rule.condition || {};
  openForm({
    title: '编辑规则',
    desc: `触发时机「${RULE_TRIGGER[rule.trigger] || rule.trigger}」与动作「${RULE_ACTION[rule.action] || rule.action}」`
        + '属于规则骨架，创建后不可修改。',
    submitText: '保存',
    wide: true,
    fields: [
      { name: 'name', label: '规则名称', value: rule.name, required: true },
      { name: 'cond_field', label: '条件字段（留空表示无条件匹配）', value: c.field || '' },
      { name: 'cond_operator', label: '比较运算符', type: 'select', value: c.operator || 'eq', options: OPERATOR_OPTIONS },
      { name: 'cond_value', label: '条件值', value: c.value === undefined || c.value === null ? '' : stringifyContent(c.value) },
      { name: 'action_params', label: '动作参数', type: 'json', rows: 3, value: rule.action_params || {} },
      { name: 'priority', label: '优先级', type: 'number', value: rule.priority ?? 0 },
      { name: 'enabled', label: '启用状态', type: 'switch', value: rule.enabled, switchLabel: '启用' },
    ],
    onSubmit: async (v) => {
      try {
        await api.updateRule(rule.rule_id, {
          name: v.name,
          condition: buildCondition(v),
          action_params: v.action_params || {},
          enabled: !!v.enabled,
          priority: v.priority ?? 0,
        });
        toastOk('规则已更新并热加载');
        await load(mount);
      } catch (e) {
        if (e instanceof ApiError && e.status === 422) toastErr(e, '字段不被允许修改');
        else toastErr(e);
      }
    },
  });
}

// ------------------------------------------------------------------ 详情页
async function renderDetail(mount, id) {
  mount.innerHTML = `<div class="empty"><span class="spinner" style="width:22px;height:22px"></span>
    <div class="empty__desc">加载规则详情…</div></div>`;

  let rule;
  try {
    rule = await api.getRule(id);
  } catch (e) {
    mount.innerHTML = emptyState({
      icon: '!', title: '加载失败或规则不存在', desc: e.message,
      actionHtml: '<a class="btn" href="#/rules">返回列表</a>',
    });
    return;
  }

  mount.innerHTML = `
    <div class="page-head">
      <div style="min-width:0">
        <h1 class="page-title">${esc(rule.name)}</h1>
        <p class="page-desc mono">${esc(rule.rule_id)}</p>
      </div>
      <div class="page-actions">
        <a class="btn" href="#/rules">← 返回</a>
        <button class="btn" id="dToggle">${rule.enabled ? '停用' : '启用'}</button>
        <button class="btn btn--primary" id="dEdit">编辑</button>
      </div>
    </div>

    <div class="section" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--sp-4)" id="dGrid">
      <div class="card">
        <div class="card__head"><div class="card__title">规则定义</div></div>
        <div class="card__body">
          <div class="kv">
            <div class="kv__k">触发时机</div><div class="kv__v">${badge(RULE_TRIGGER[rule.trigger] || rule.trigger, 'info')}</div>
            <div class="kv__k">执行动作</div><div class="kv__v">${badge(RULE_ACTION[rule.action] || rule.action, 'brand')}</div>
            <div class="kv__k">优先级</div><div class="kv__v">${rule.priority ?? 0}</div>
            <div class="kv__k">状态</div><div class="kv__v">${rule.enabled
              ? badge('已启用', 'success', { dot: true, pulse: true }) : badge('已停用', 'neutral')}</div>
            <div class="kv__k">创建时间</div><div class="kv__v">${esc(fmtTime(rule.created_at))}</div>
          </div>
          <div class="mt-4">
            <div class="field__label">匹配条件</div>
            <pre class="code mt-2">${esc(JSON.stringify(rule.condition || {}, null, 2))}</pre>
          </div>
          <div class="mt-4">
            <div class="field__label">动作参数</div>
            <pre class="code mt-2">${esc(JSON.stringify(rule.action_params || {}, null, 2))}</pre>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card__head"><div class="card__title">支持的语法</div></div>
        <div class="card__body stack-sm">
          <div>
            <div class="field__label">条件运算符</div>
            <div class="mt-2">${chips(['eq', 'ne', 'gt', 'lt', 'gte', 'lte', 'in', 'nin', 'contains'])}</div>
          </div>
          <div>
            <div class="field__label">可用动作</div>
            <div class="mt-2">${chips(Object.values(RULE_ACTION))}</div>
          </div>
          <div class="alert alert--info mt-2">
            <span class="alert__icon">i</span>
            <div class="alert__body">
              <div class="alert__desc">条件字段支持点号访问嵌套数据；留空条件表示无条件匹配。
                规则变更后通过事件总线通知，无需重启服务。</div>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  const mq = window.matchMedia('(max-width: 980px)');
  const grid = mount.querySelector('#dGrid');
  const apply = () => { grid.style.gridTemplateColumns = mq.matches ? 'minmax(0,1fr)' : 'minmax(0,1fr) minmax(0,1fr)'; };
  apply();
  mq.addEventListener('change', apply);

  const reload = async () => { mount.innerHTML = ''; await renderDetail(mount, id); };

  mount.querySelector('#dToggle').addEventListener('click', async (e) => {
    setBusy(e.currentTarget, true, '…');
    try {
      await api.updateRule(id, { enabled: !rule.enabled });
      toastOk(rule.enabled ? '已停用' : '已启用');
      await reload();
    } catch (err) { toastErr(err); }
    finally { setBusy(e.currentTarget, false, rule.enabled ? '停用' : '启用'); }
  });
  mount.querySelector('#dEdit').addEventListener('click', () => openEdit(mount, rule));

  return () => mq.removeEventListener('change', apply);
}

// ------------------------------------------------------------------ 页面入口
export default {
  title: '协作规则',

  async render(mount, params) {
    if (params && params.id) return renderDetail(mount, params.id);

    mount.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">协作规则</h1>
          <p class="page-desc">规则引擎在事件触发时按优先级匹配条件并执行动作，支持热加载。
            数字越大越先匹配；条件留空表示无条件命中。</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="btnReload">刷新</button>
          <button class="btn btn--primary" id="btnCreate">+ 新建规则</button>
        </div>
      </div>

      <div class="toolbar">
        <input class="input" id="q" placeholder="搜索名称 / 触发器 / 动作…" style="min-width:240px">
        <div class="toolbar__spacer"></div>
        <span class="text-xs faint" id="summary"></span>
      </div>

      <div class="card card--flush">
        <div class="card__body" style="padding:0">${tableShell()}</div>
      </div>`;

    const q = mount.querySelector('#q');
    syncSummary = () => {
      const el = mount.querySelector('#summary');
      if (el) {
        const on = state.items.filter((r) => r.enabled).length;
        el.textContent = `共 ${state.items.length} 条规则 · 启用中 ${on} 条`;
      }
    };

    q.addEventListener('input', () => { state.q = q.value; state.page = 1; paintList(mount); });
    mount.querySelector('#btnCreate').addEventListener('click', () => openCreate(mount));
    mount.querySelector('#btnReload').addEventListener('click', async (e) => {
      setBusy(e.currentTarget, true, '刷新中');
      await load(mount);
      setBusy(e.currentTarget, false, '刷新');
    });

    await load(mount);

    bindRealtime(
      ['rule.created', 'rule.updated', 'rule.deleted'],
      () => load(mount, true),
      { debounce: 300 },
    );
  },
};
