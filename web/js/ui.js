/**
 * UI 组件层 —— 与业务无关的通用控件与状态展示。
 * 页面只负责取数 + 组装，所有交互与边界态（加载 / 空 / 错误）都走这里的原语。
 */
import { ApiError } from './api.js';

// ---------------------------------------------------------------- 基础工具
export function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function shortId(id, n = 8) {
  if (!id) return '-';
  return String(id).slice(0, n);
}

export function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function fmtRelative(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const diff = Date.now() - d.getTime();
  const s = Math.floor(diff / 1000);
  if (s < 5) return '刚刚';
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const day = Math.floor(h / 24);
  if (day < 30) return `${day} 天前`;
  return d.toLocaleDateString('zh-CN');
}

/** 稳定的取色，用于智能体头像 */
export function hashHue(str) {
  let h = 0;
  const s = String(str || '');
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}

export function initials(name) {
  const s = String(name || '?').trim();
  if (!s) return '?';
  // 中文取首字，英文取首字母
  if (/[一-龥]/.test(s[0])) return s.slice(0, 1);
  return s.slice(0, 2).toUpperCase();
}

/** 内容字段可能是任意 JSON，也可能是纯文本；宽松解析 */
export function parseLoose(text) {
  const t = String(text ?? '').trim();
  if (!t) return null;
  try { return JSON.parse(t); } catch { return t; }
}

export function stringifyContent(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

// ---------------------------------------------------------------- 业务枚举的中文文案与配色
export const AGENT_STATUS = {
  online:  { label: '在线',   tone: 'success', dot: true, pulse: true },
  busy:    { label: '忙碌',   tone: 'warn' },
  offline: { label: '离线',   tone: 'neutral' },
  error:   { label: '异常',   tone: 'danger' },
};

export const PRIORITY = {
  low:      { label: '低',     tone: 'neutral' },
  normal:   { label: '普通',   tone: 'info' },
  high:     { label: '高',     tone: 'warn' },
  critical: { label: '紧急',   tone: 'danger' },
};

export const TASK_STATUS = {
  pending:     { label: '待分配', tone: 'neutral' },
  assigned:    { label: '已分配', tone: 'info' },
  in_progress: { label: '进行中', tone: 'brand' },
  review:      { label: '待评审', tone: 'brand' },
  done:        { label: '已完成', tone: 'success' },
  blocked:     { label: '已阻塞', tone: 'danger' },
  failed:      { label: '失败',   tone: 'danger' },
};

export const CONFLICT_STATUS = {
  detected:  { label: '已发现', tone: 'warn' },
  resolving: { label: '解决中', tone: 'info' },
  resolved:  { label: '已解决', tone: 'success' },
  escalated: { label: '已升级', tone: 'danger' },
  dismissed: { label: '已忽略', tone: 'neutral' },
};

export const CONFLICT_TYPE = {
  write_conflict:     '写冲突',
  task_assignment:    '任务分配冲突',
  resource_conflict:  '资源冲突',
  opinion_conflict:   '意见冲突',
};

export const RULE_TRIGGER = {
  on_task_created:        '任务创建时',
  on_task_status_changed: '任务状态变更时',
  on_conflict_detected:   '冲突发现时',
  on_agent_registered:    '智能体注册时',
  on_entry_created:       '条目创建时',
  on_entry_updated:       '条目更新时',
};

export const RULE_ACTION = {
  auto_assign:     '自动分配任务',
  escalate:        '升级冲突',
  notify:          '发送通知',
  set_priority:    '设置优先级',
  block_task:      '阻塞任务',
  merge_entries:   '合并条目',
  vote_resolution: '投票裁决',
};

// ---------------------------------------------------------------- 徽章
export function badge(text, tone = 'neutral', opts = {}) {
  const dot = opts.dot
    ? `<span class="badge__dot${opts.pulse ? ' badge__dot--pulse' : ''}"></span>`
    : '';
  return `<span class="badge badge--${esc(tone)}">${dot}${esc(text)}</span>`;
}

export function enumBadge(value, map) {
  const meta = map[value] || { label: value || '-', tone: 'neutral' };
  return badge(meta.label, meta.tone, { dot: meta.dot, pulse: meta.pulse });
}

export function chips(list, tone = '') {
  if (!Array.isArray(list) || !list.length) {
    return '<span class="faint text-xs">—</span>';
  }
  return list
    .map((c) => `<span class="chip${tone ? ` chip--${esc(tone)}` : ''}">${esc(c)}</span>`)
    .join(' ');
}

// ---------------------------------------------------------------- Toast
let toastStack = null;

export function toast(message, type = 'info', title = '') {
  if (!toastStack) {
    toastStack = document.createElement('div');
    toastStack.className = 'toast-stack';
    document.body.appendChild(toastStack);
  }
  const icons = { success: '✓', error: '✕', warn: '!', info: 'i' };
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  el.innerHTML = `
    <span class="toast__icon">${icons[type] || 'i'}</span>
    <div class="toast__body">
      ${title ? `<div class="toast__title">${esc(title)}</div>` : ''}
      <div class="toast__msg">${esc(message)}</div>
    </div>`;

  const remove = () => {
    el.classList.add('is-leaving');
    setTimeout(() => el.remove(), 220);
  };
  el.addEventListener('click', remove);
  toastStack.appendChild(el);

  const ttl = type === 'error' ? 6000 : 3200;
  setTimeout(remove, ttl);
  return remove;
}

export function toastOk(msg, title = '操作成功') { return toast(msg, 'success', title); }
export function toastErr(err, title = '操作失败') {
  const msg = err instanceof Error ? err.message : String(err);
  return toast(msg, 'error', title);
}

// ---------------------------------------------------------------- 模态框
export function modal({ title, desc = '', body, footer, wide = false, onClose } = {}) {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';

  const box = document.createElement('div');
  box.className = `modal${wide ? ' modal--wide' : ''}`;

  box.innerHTML = `
    <div class="modal__head">
      <div style="min-width:0">
        <div class="modal__title">${esc(title)}</div>
        ${desc ? `<div class="modal__desc">${esc(desc)}</div>` : ''}
      </div>
      <button class="modal__close" type="button" aria-label="关闭">✕</button>
    </div>`;

  const bodyWrap = document.createElement('div');
  bodyWrap.className = 'modal__body';
  if (typeof body === 'string') bodyWrap.innerHTML = body;
  else if (body) bodyWrap.appendChild(body);
  box.appendChild(bodyWrap);

  if (footer) {
    const footWrap = document.createElement('div');
    footWrap.className = 'modal__foot';
    if (typeof footer === 'string') footWrap.innerHTML = footer;
    else footWrap.appendChild(footer);
    box.appendChild(footWrap);
  }

  backdrop.appendChild(box);
  document.body.appendChild(backdrop);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey);
    backdrop.remove();
    onClose && onClose();
  };

  function onKey(e) { if (e.key === 'Escape') close(); }
  document.addEventListener('keydown', onKey);
  backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) close(); });
  box.querySelector('.modal__close').addEventListener('click', close);

  // 首帧聚焦第一个可输入元素
  requestAnimationFrame(() => {
    const f = box.querySelector('input, textarea, select');
    if (f && !f.disabled) f.focus();
  });

  return { root: box, backdrop, close };
}

// ---------------------------------------------------------------- 确认框
export function confirmDialog({ title = '确认操作', message = '', confirmText = '确认', danger = false } = {}) {
  return new Promise((resolve) => {
    const foot = document.createElement('div');
    foot.style.display = 'flex';
    foot.style.gap = 'var(--sp-2)';

    const cancel = document.createElement('button');
    cancel.className = 'btn';
    cancel.type = 'button';
    cancel.textContent = '取消';

    const ok = document.createElement('button');
    ok.className = `btn ${danger ? 'btn--danger' : 'btn--primary'}`;
    ok.type = 'button';
    ok.textContent = confirmText;

    foot.append(cancel, ok);

    let settled = false;
    const m = modal({
      title,
      body: `<p class="text-sm muted" style="line-height:var(--lh-normal)">${esc(message)}</p>`,
      footer: foot,
      onClose: () => { if (!settled) { settled = true; resolve(false); } },
    });

    cancel.addEventListener('click', () => { settled = true; m.close(); resolve(false); });
    ok.addEventListener('click', () => { settled = true; m.close(); resolve(true); });
  });
}

// ---------------------------------------------------------------- 通用表单模态
/**
 * fields: [{ name, label, type: text|textarea|number|select|switch|tags|json,
 *            value, placeholder, hint, required, options: [{value,label}],
 *            min, max, step, rows, loose }]
 * onSubmit(values) 抛出 ApiError 时，错误会显示在模态内且不关闭。
 */
export function openForm({ title, desc = '', submitText = '提交', fields = [], onSubmit, wide = false }) {
  const form = document.createElement('form');
  form.className = 'stack';
  form.noValidate = true;

  const getters = new Map();

  for (const f of fields) {
    const wrap = document.createElement('div');
    wrap.className = 'field';

    const label = document.createElement('label');
    label.className = 'field__label';
    label.htmlFor = `f_${f.name}`;
    label.innerHTML = `${esc(f.label)}${f.required ? '<span class="req">*</span>' : ''}`;
    wrap.appendChild(label);

    let input;
    const common = { id: `f_${f.name}`, name: f.name };

    if (f.type === 'textarea' || f.type === 'json') {
      input = document.createElement('textarea');
      input.className = 'textarea';
      input.rows = f.rows || (f.type === 'json' ? 6 : 3);
      if (f.type === 'json') input.style.fontSize = 'var(--fs-xs)';
      input.value = f.type === 'json' ? stringifyContent(f.value) : (f.value ?? '');
    } else if (f.type === 'select') {
      input = document.createElement('select');
      input.className = 'select';
      for (const o of f.options || []) {
        const opt = document.createElement('option');
        opt.value = o.value;
        opt.textContent = o.label;
        if (o.value === f.value) opt.selected = true;
        input.appendChild(opt);
      }
    } else if (f.type === 'multi') {
      // 多选：渲染成可切换的 chip 组（任务依赖等场景）
      input = document.createElement('div');
      input.className = 'multi-picker';
      const selected = new Set(Array.isArray(f.value) ? f.value : []);
      input._selected = selected;
      const opts = f.options || [];
      if (!opts.length) {
        const tip = document.createElement('span');
        tip.className = 'faint text-xs';
        tip.textContent = f.emptyHint || '暂无可选项';
        input.appendChild(tip);
      }
      for (const o of opts) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `chip${selected.has(o.value) ? ' chip--brand' : ''}`;
        b.textContent = o.label;
        b.addEventListener('click', (ev) => {
          ev.preventDefault();
          if (selected.has(o.value)) {
            selected.delete(o.value);
            b.classList.remove('chip--brand');
          } else {
            selected.add(o.value);
            b.classList.add('chip--brand');
          }
        });
        input.appendChild(b);
      }
    } else if (f.type === 'number') {
      input = document.createElement('input');
      input.className = 'input';
      input.type = 'number';
      if (f.min !== undefined) input.min = f.min;
      if (f.max !== undefined) input.max = f.max;
      if (f.step !== undefined) input.step = f.step;
      input.value = f.value ?? '';
    } else if (f.type === 'switch') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!f.value;
      input.style.position = 'absolute';
      input.style.opacity = '0';
    } else {
      input = document.createElement('input');
      input.className = 'input';
      input.type = 'text';
      input.value = Array.isArray(f.value) ? f.value.join(', ') : (f.value ?? '');
    }

    if (f.placeholder) input.placeholder = f.placeholder;
    if (f.disabled) input.disabled = true;

    if (f.type === 'switch') {
      const sw = document.createElement('label');
      sw.className = 'switch';
      const track = document.createElement('span');
      track.className = 'switch__track';
      const txt = document.createElement('span');
      txt.className = 'switch__label';
      txt.textContent = f.switchLabel || '启用';
      sw.append(input, track, txt);
      wrap.appendChild(sw);
    } else {
      Object.assign(input, common);
      wrap.appendChild(input);
    }

    if (f.hint) {
      const hint = document.createElement('div');
      hint.className = 'field__hint';
      hint.textContent = f.hint;
      wrap.appendChild(hint);
    }

    const err = document.createElement('div');
    err.className = 'field__error';
    err.style.display = 'none';
    wrap.appendChild(err);

    form.appendChild(wrap);

    getters.set(f.name, {
      field: f,
      input,
      errorEl: err,
      read() {
        if (f.type === 'switch') return !!input.checked;
        if (f.type === 'multi') return [...(input._selected || [])];
        if (f.type === 'number') {
          const n = input.value === '' ? undefined : Number(input.value);
          return n;
        }
        if (f.type === 'tags') {
          return String(input.value || '')
            .split(/[,，\s]+/)
            .map((s) => s.trim())
            .filter(Boolean);
        }
        if (f.type === 'json') return parseLoose(input.value);
        return String(input.value ?? '').trim();
      },
      setError(msg) {
        if (msg) {
          err.textContent = msg;
          err.style.display = 'flex';
          input.classList && input.classList.add('is-invalid');
        } else {
          err.textContent = '';
          err.style.display = 'none';
          input.classList && input.classList.remove('is-invalid');
        }
      },
    });
  }

  const topErr = document.createElement('div');
  topErr.style.display = 'none';
  form.prepend(topErr);

  const foot = document.createElement('div');
  foot.style.display = 'flex';
  foot.style.gap = 'var(--sp-2)';

  const cancel = document.createElement('button');
  cancel.className = 'btn';
  cancel.type = 'button';
  cancel.textContent = '取消';

  const submit = document.createElement('button');
  submit.className = 'btn btn--primary';
  submit.type = 'submit';
  submit.textContent = submitText;

  // 按钮放在 modal 页脚（而不是表单内部），用 HTML5 form 属性保持关联，
  // 这样既能触发 submit 事件，又能统一页脚的视觉分隔。
  form.id = form.id || 'uiForm';
  submit.setAttribute('form', form.id);

  foot.append(cancel, submit);

  const m = modal({ title, desc, body: form, footer: foot, wide });

  cancel.addEventListener('click', () => m.close());

  const showTop = (msg) => {
    if (!msg) { topErr.style.display = 'none'; topErr.innerHTML = ''; return; }
    topErr.style.display = 'block';
    topErr.innerHTML = `<div class="alert alert--error"><span class="alert__icon">✕</span>
      <div class="alert__body"><div class="alert__title">${esc(msg)}</div></div></div>`;
  };

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    showTop('');

    const values = {};
    let firstInvalid = null;

    for (const [name, g] of getters) {
      g.setError('');
      let v;
      try {
        v = g.read();
      } catch (err2) {
        g.setError(String(err2.message || err2));
        firstInvalid = firstInvalid || g;
        continue;
      }
      if (g.field.required) {
        const empty = v === undefined || v === null || v === '' ||
                      (Array.isArray(v) && v.length === 0);
        if (empty) {
          g.setError('此项为必填');
          firstInvalid = firstInvalid || g;
          continue;
        }
      }
      values[name] = v;
    }

    if (firstInvalid) { firstInvalid.input.focus && firstInvalid.input.focus(); return; }

    setBusy(submit, true, submitText);
    try {
      await onSubmit(values);
      m.close();
    } catch (err3) {
      if (err3 instanceof ApiError) showTop(err3.message);
      else showTop(String(err3?.message || err3));
    } finally {
      setBusy(submit, false, submitText);
    }
  });

  return m;
}

// ---------------------------------------------------------------- 按钮加载态
export function setBusy(btn, busy, originalText = '') {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset._text) btn.dataset._text = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = `<span class="btn__spinner"></span>${esc(originalText || '处理中')}`;
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset._text || originalText || '提交';
    delete btn.dataset._text;
  }
}

// ---------------------------------------------------------------- 边界态
export function skeletonRows(n = 4, cols = 4) {
  let html = '';
  for (let i = 0; i < n; i++) {
    let cells = '<div class="skeleton skeleton--avatar"></div><div style="flex:1">';
    cells += '<div class="skeleton skeleton--text" style="width:38%"></div>';
    cells += '<div class="skeleton skeleton--text" style="width:22%"></div></div>';
    for (let c = 2; c < cols; c++) {
      cells += '<div class="skeleton skeleton--text" style="width:60px"></div>';
    }
    html += `<div class="skeleton-row">${cells}</div>`;
  }
  return html;
}

export function loadingBlock(text = '加载中…') {
  return `<div class="empty"><span class="spinner" style="width:22px;height:22px"></span>
    <div class="empty__desc">${esc(text)}</div></div>`;
}

export function emptyState({ icon = '◻', title = '暂无数据', desc = '', actionHtml = '' } = {}) {
  return `<div class="empty">
    <div class="empty__icon">${esc(icon)}</div>
    <div class="empty__title">${esc(title)}</div>
    ${desc ? `<div class="empty__desc">${esc(desc)}</div>` : ''}
    ${actionHtml ? `<div class="empty__actions">${actionHtml}</div>` : ''}
  </div>`;
}

export function errorState({ title = '加载失败', desc = '', retryId = '' } = {}) {
  return `<div class="empty">
    <div class="empty__icon">!</div>
    <div class="empty__title">${esc(title)}</div>
    <div class="empty__desc">${esc(desc)}</div>
    ${retryId ? `<div class="empty__actions"><button class="btn" data-retry="${esc(retryId)}">重试</button></div>` : ''}
  </div>`;
}

/** 统一的列表渲染：处理 loading / error / empty 三态 */
export function renderStates(container, { loading, error, empty, skeleton, emptyHtml, onRetry }) {
  // 容器已随页面切换而消失（宿主节点被替换），放弃本次渲染
  if (!container) return true;
  if (loading) { container.innerHTML = skeleton || loadingBlock(); return true; }
  if (error) {
    container.innerHTML = errorState({ desc: error instanceof Error ? error.message : String(error) });
    const btn = container.querySelector('[data-retry]');
    if (btn && onRetry) btn.addEventListener('click', onRetry);
    return true;
  }
  if (empty) { container.innerHTML = emptyHtml; return true; }
  return false;
}

// ---------------------------------------------------------------- 分页（后端未提供，前端本地分页）
export function paginate(items, page, size) {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(Math.max(1, page), pages);
  const start = (p - 1) * size;
  return { rows: items.slice(start, start + size), page: p, pages, total, start };
}

export function pagerHtml({ page, pages, total, size }) {
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(page * size, total);
  return `<div class="pager">
    <button class="btn btn--sm" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>
    <span class="pager__info">${from}–${to} / 共 ${total} 条 · 第 ${page}/${pages} 页</span>
    <button class="btn btn--sm" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>下一页</button>
  </div>`;
}

// ---------------------------------------------------------------- 复制
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(String(text));
    toast('已复制到剪贴板', 'success');
  } catch {
    toast('当前浏览器不支持自动复制', 'warn');
  }
}
