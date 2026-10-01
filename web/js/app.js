/**
 * 应用外壳：侧边导航 + 顶栏 + 路由 + 主题 + 实时连接指示
 * 路由是 hash 模式（#/xxx），无需服务端配合即可前进/后退/刷新。
 */
import { api, realtime, apiKey, ALL_EVENTS } from './api.js';
import { clearRealtime } from './bus.js';
import { esc, toast, modal, setBusy } from './ui.js';

import dashboard from './pages/dashboard.js';
import agents from './pages/agents.js';
import entries from './pages/entries.js';
import tasks from './pages/tasks.js';
import conflicts from './pages/conflicts.js';
import rules from './pages/rules.js';

// ---------------------------------------------------------------- 图标
const svg = (inner) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;

const ICON = {
  grid: svg('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'),
  users: svg('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'),
  layers: svg('<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>'),
  check: svg('<polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
  alert: svg('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>'),
  sliders: svg('<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>'),
};

// ---------------------------------------------------------------- 路由表
const NAV = [
  {
    group: '概览',
    items: [{ path: '/dashboard', title: '仪表盘', icon: ICON.grid, page: dashboard }],
  },
  {
    group: '协作',
    items: [
      { path: '/agents',  title: '智能体',   icon: ICON.users,  page: agents,  countKey: 'total_agents' },
      { path: '/entries', title: '黑板条目', icon: ICON.layers, page: entries, countKey: 'total_entries' },
      { path: '/tasks',   title: '任务',     icon: ICON.check,  page: tasks,   countKey: 'pending_tasks' },
    ],
  },
  {
    group: '治理',
    items: [
      { path: '/conflicts', title: '冲突',     icon: ICON.alert,   page: conflicts, countKey: 'total_conflicts' },
      { path: '/rules',     title: '协作规则', icon: ICON.sliders, page: rules,     countKey: 'total_rules' },
    ],
  },
];

const ROUTES = [];
for (const g of NAV) {
  for (const it of g.items) {
    ROUTES.push({ ...it, nav: true });
    ROUTES.push({ ...it, path: `${it.path}/:id`, nav: false });
  }
}

// ---------------------------------------------------------------- 主题
const THEME_KEY = 'blackboard.theme';

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const btn = document.getElementById('themeBtn');
  if (btn) {
    btn.textContent = t === 'dark' ? '☀' : '☾';
    btn.title = t === 'dark' ? '切换到浅色主题' : '切换到深色主题';
    btn.setAttribute('aria-label', btn.title);
  }
}

function currentTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === 'dark' || saved === 'light') return saved;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light';
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

// ---------------------------------------------------------------- 外壳渲染
function buildShell(root) {
  root.innerHTML = `
    <div class="app">
      <aside class="sidebar" id="sidebar">
        <div class="sidebar__brand">
          <div class="sidebar__logo">BB</div>
          <div style="min-width:0">
            <div class="sidebar__title">黑板协作系统</div>
            <div class="sidebar__sub">Multi-Agent Console</div>
          </div>
        </div>
        <nav class="sidebar__nav" id="nav"></nav>
        <div class="sidebar__foot">
          <a class="btn btn--sm btn--subtle" href="/docs" target="_blank" rel="noopener"
             style="flex:1;justify-content:flex-start">API 文档 ↗</a>
        </div>
      </aside>
      <div class="sidebar-backdrop" id="sidebarBackdrop"></div>
      <div class="main">
        <header class="topbar">
          <button class="menu-btn" id="menuBtn" aria-label="打开菜单">☰</button>
          <div class="topbar__crumb" id="crumb">仪表盘</div>
          <div class="topbar__spacer"></div>
          <span class="conn" id="conn" title="WebSocket 实时通道">
            <span class="conn__dot"></span><span id="connText">未连接</span>
          </span>
          <button class="btn btn--sm btn--subtle" id="keyBtn" title="API Key 设置">🔑</button>
          <button class="btn btn--sm btn--subtle" id="themeBtn" title="切换主题">☾</button>
        </header>
        <main class="content">
          <div class="container" id="view"></div>
        </main>
      </div>
    </div>`;

  renderNav();

  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const closeNav = () => { sidebar.classList.remove('is-open'); backdrop.classList.remove('is-open'); };
  document.getElementById('menuBtn').addEventListener('click', () => {
    sidebar.classList.toggle('is-open');
    backdrop.classList.toggle('is-open');
  });
  backdrop.addEventListener('click', closeNav);
  document.getElementById('themeBtn').addEventListener('click', toggleTheme);
  document.getElementById('keyBtn').addEventListener('click', openKeyDialog);

  // 站内跳转后收起移动端抽屉
  document.getElementById('nav').addEventListener('click', (e) => {
    if (e.target.closest('a')) closeNav();
  });
}

function renderNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;
  let html = '';
  for (const g of NAV) {
    html += `<div class="nav-group__label">${esc(g.group)}</div>`;
    for (const it of g.items) {
      html += `<a class="nav-item" href="#${it.path}" data-path="${it.path}">
        <span class="nav-item__icon">${it.icon}</span>
        <span class="nav-item__text">${esc(it.title)}</span>
        <span class="nav-item__count" data-count="${it.countKey || ''}"></span>
      </a>`;
    }
  }
  nav.innerHTML = html;
}

function updateCounts(stats) {
  document.querySelectorAll('[data-count]').forEach((el) => {
    const k = el.getAttribute('data-count');
    if (!k) { el.style.display = 'none'; return; }
    const v = stats?.[k];
    if (v === undefined || v === null) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.textContent = String(v);
  });
}

function setActiveNav(path) {
  const base = '/' + String(path).split('/').filter(Boolean)[0];
  document.querySelectorAll('.nav-item').forEach((el) => {
    el.classList.toggle('is-active', el.getAttribute('data-path') === base);
  });
}

export function setCrumb(text) {
  const el = document.getElementById('crumb');
  if (el) el.textContent = text;
}

// ---------------------------------------------------------------- API Key
function openKeyDialog() {
  const body = document.createElement('div');
  body.className = 'stack';
  body.innerHTML = `
    <div class="alert alert--info">
      <span class="alert__icon">i</span>
      <div class="alert__body">
        <div class="alert__title">仅在服务端启用了鉴权时才需要</div>
        <div class="alert__desc">后端设置环境变量 BLACKBOARD_API_KEY 后，所有写操作（POST/PUT/PATCH/DELETE）
        都会校验请求头 X-API-Key。未启用时留空即可。</div>
      </div>
    </div>
    <div class="field">
      <label class="field__label" for="apiKeyInput">X-API-Key</label>
      <input class="input mono" id="apiKeyInput" type="password" placeholder="留空表示不使用鉴权" autocomplete="off">
      <div class="field__hint">保存在浏览器 localStorage，仅本机可见。</div>
    </div>`;

  const foot = document.createElement('div');
  foot.style.display = 'flex';
  foot.style.gap = 'var(--sp-2)';

  const clear = document.createElement('button');
  clear.className = 'btn btn--subtle';
  clear.textContent = '清除';
  clear.style.marginRight = 'auto';

  const save = document.createElement('button');
  save.className = 'btn btn--primary';
  save.textContent = '保存';

  foot.append(clear, save);
  const m = modal({ title: 'API Key 设置', body, footer: foot });

  const input = body.querySelector('#apiKeyInput');
  input.value = apiKey.get();

  clear.addEventListener('click', () => {
    apiKey.clear();
    toast('已清除本地 API Key', 'success');
    m.close();
  });
  save.addEventListener('click', () => {
    apiKey.set(input.value);
    toast(input.value ? 'API Key 已保存' : '已设置为无鉴权模式', 'success');
    m.close();
  });
}

// ---------------------------------------------------------------- 连接指示
function bindConn() {
  const el = document.getElementById('conn');
  const txt = document.getElementById('connText');
  const map = {
    idle:       ['', '未连接'],
    connecting: ['conn--connecting', '连接中'],
    live:       ['conn--live', '实时已连接'],
    down:       ['conn--down', '已断开'],
  };
  realtime.onStatus((s) => {
    const [cls, label] = map[s] || map.idle;
    el.className = `conn ${cls}`;
    txt.textContent = label;
    el.title = s === 'live' ? `已订阅 ${ALL_EVENTS.length} 类实时事件` : 'WebSocket 实时通道';
  });
}

// ---------------------------------------------------------------- 路由
function normalizeHash() {
  const h = (location.hash || '').replace(/^#/, '');
  if (!h || h === '/') return '/dashboard';
  return h.startsWith('/') ? h : `/${h}`;
}

function matchRoute(path) {
  const segs = path.split('/').filter(Boolean);
  for (const r of ROUTES) {
    const rs = r.path.split('/').filter(Boolean);
    if (rs.length !== segs.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < rs.length; i++) {
      if (rs[i].startsWith(':')) params[rs[i].slice(1)] = decodeURIComponent(segs[i]);
      else if (rs[i] !== segs[i]) { ok = false; break; }
    }
    if (ok) return { route: r, params };
  }
  return null;
}

let currentCleanup = null;

async function render() {
  const path = normalizeHash();
  const m = matchRoute(path);
  if (!m) { location.replace('#/dashboard'); return; }

  // 每次导航都换成全新的容器：让上一页持有的 mount 引用彻底脱离文档树。
  // 否则上一页残留的异步回调（例如实时事件触发的刷新）会查到 null 节点并崩溃
  // ——典型场景：在列表页有数据写入时立刻切到另一页。
  const old = document.getElementById('view');
  const view = document.createElement('div');
  view.id = 'view';
  view.className = 'container page-enter';
  if (old) old.replaceWith(view);
  else document.querySelector('.content').appendChild(view);

  setActiveNav(path);
  setCrumb(m.route.title);

  // 清理上一页的实时订阅
  clearRealtime();
  if (typeof currentCleanup === 'function') {
    try { currentCleanup(); } catch { /* 忽略 */ }
  }
  currentCleanup = null;

  try {
    const ret = await m.route.page.render(view, m.params);
    if (typeof ret === 'function') currentCleanup = ret;
  } catch (e) {
    view.innerHTML = `<div class="empty">
      <div class="empty__icon">!</div>
      <div class="empty__title">页面渲染失败</div>
      <div class="empty__desc">${esc(e?.message || e)}</div>
      <div class="empty__actions"><button class="btn" onclick="location.reload()">重新加载</button></div>
    </div>`;
  }
}

// ---------------------------------------------------------------- 统计轮询
let statsTimer = null;

async function refreshCounts() {
  try {
    const s = await api.stats();
    updateCounts(s);
  } catch { /* 统计失败不影响使用 */ }
}

function startStatsPolling() {
  refreshCounts();
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = setInterval(refreshCounts, 5000);
}

/** 供页面在数据变更后主动刷新导航计数 */
export function refreshNavCounts() { return refreshCounts(); }

/** 跳转到某页 */
export function navigate(path) {
  location.hash = path.startsWith('#') ? path : `#${path}`;
}

// ---------------------------------------------------------------- 启动
export function bootstrap() {
  applyTheme(currentTheme());
  buildShell(document.getElementById('root'));
  bindConn();

  window.addEventListener('hashchange', render);
  window.addEventListener('unhandledrejection', (e) => {
    console.error('[unhandledrejection]', e.reason);
  });

  render();
  realtime.connect();
  startStatsPolling();

  // 跟随系统主题变化（用户未手动选择过时）
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      if (!localStorage.getItem(THEME_KEY)) applyTheme(e.matches ? 'dark' : 'light');
    });
  }
}
