/**
 * 仪表盘 —— 全局态势 + 实时事件流 + 快捷操作
 */
import { api, realtime } from '../api.js';
import { bindRealtime } from '../bus.js';
import { seedDemoData } from '../demo.js';
import {
  esc, badge, fmtTime, fmtRelative, confirmDialog, modal, toast, toastOk, toastErr,
  loadingBlock, errorState, setBusy,
} from '../ui.js';

const EVENT_META = {
  'agent.registered':    { label: '智能体注册', tone: 'brand' },
  'agent.unregistered':  { label: '智能体注销', tone: 'neutral' },
  'agent.heartbeat':     { label: '心跳',       tone: 'neutral' },
  'agent.status_changed':{ label: '智能体状态变更', tone: 'info' },
  'agent.offline':       { label: '心跳超时离线', tone: 'warn' },
  'entry.created':       { label: '条目发布',   tone: 'brand' },
  'entry.updated':       { label: '条目更新',   tone: 'info' },
  'entry.deleted':       { label: '条目删除',   tone: 'neutral' },
  'entry.merged':        { label: '条目合并',   tone: 'info' },
  'task.created':        { label: '任务创建',   tone: 'brand' },
  'task.assigned':       { label: '任务分配',   tone: 'info' },
  'task.status_changed': { label: '任务状态变更', tone: 'info' },
  'task.unblocked':      { label: '依赖解锁',   tone: 'success' },
  'conflict.detected':   { label: '冲突发现',   tone: 'warn' },
  'conflict.resolved':   { label: '冲突解决',   tone: 'success' },
  'conflict.escalated':  { label: '冲突升级',   tone: 'danger' },
  'rule.created':        { label: '规则创建',   tone: 'neutral' },
  'rule.updated':        { label: '规则更新',   tone: 'neutral' },
  'rule.deleted':        { label: '规则删除',   tone: 'neutral' },
  'notification':        { label: '系统通知',   tone: 'info' },
};

function eventText(ev) {
  const d = ev.data || {};
  if (ev.event === 'notification') return d.message || '系统通知';
  const parts = [];
  for (const k of ['task_id', 'entry_id', 'conflict_id', 'agent_id', 'rule_id', 'topic']) {
    if (d[k]) parts.push(`${k}=${String(d[k]).slice(0, 8)}`);
  }
  if (d.new_status) parts.push(`${d.old_status || '?'} → ${d.new_status}`);
  if (d.assignee_id) parts.push(`→ ${String(d.assignee_id).slice(0, 8)}`);
  return parts.join(' · ') || '—';
}

export default {
  title: '仪表盘',

  async render(mount) {
    mount.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">仪表盘</h1>
          <p class="page-desc">黑板系统的全局态势：智能体在线情况、知识条目、任务流转与冲突治理；
            右侧事件流由 WebSocket 实时推送，无需刷新。</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="btnRefresh">刷新</button>
          <button class="btn" id="btnSeed">灌入演示数据</button>
          <button class="btn btn--primary" id="btnAgent">+ 注册智能体</button>
        </div>
      </div>

      <div class="section">
        <div class="stat-grid" id="stats">${loadingBlock('正在读取统计…')}</div>
      </div>

      <div class="section" style="display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:var(--sp-4)">
        <div class="card" id="streamCard">
          <div class="card__head">
            <div>
              <div class="card__title">实时事件流</div>
              <div class="card__desc">WebSocket 推送，最多保留最近 60 条</div>
            </div>
            <button class="btn btn--sm btn--subtle" id="btnClearStream">清空</button>
          </div>
          <div class="card__body"><div class="timeline" id="stream"></div></div>
        </div>

        <div class="stack">
          <div class="card">
            <div class="card__head"><div class="card__title">快捷操作</div></div>
            <div class="card__body stack-sm">
              <a class="btn" href="#/entries" style="justify-content:flex-start">发布知识条目</a>
              <a class="btn" href="#/tasks" style="justify-content:flex-start">创建协作任务</a>
              <a class="btn" href="#/conflicts" style="justify-content:flex-start">检测意见冲突</a>
              <a class="btn" href="#/rules" style="justify-content:flex-start">配置协作规则</a>
            </div>
          </div>

          <div class="card">
            <div class="card__head"><div class="card__title">系统与连接</div></div>
            <div class="card__body">
              <div class="kv" id="sysInfo">${loadingBlock()}</div>
            </div>
          </div>
        </div>
      </div>`;

    // 窄屏改为单列
    const grid = mount.querySelector('.section[style]');
    if (grid) grid.style.gridTemplateColumns = '';
    const mq = window.matchMedia('(max-width: 980px)');
    const applyCols = () => {
      grid.style.gridTemplateColumns = mq.matches ? 'minmax(0,1fr)' : 'minmax(0,1.15fr) minmax(0,1fr)';
    };
    applyCols();
    mq.addEventListener('change', applyCols);

    const statsEl = mount.querySelector('#stats');
    const streamEl = mount.querySelector('#stream');
    let stream = realtime.getBuffer().slice(0, 40);

    const paintStream = () => {
      if (!stream.length) {
        streamEl.innerHTML = `<div style="padding:var(--sp-6) 0;text-align:center">
          <div class="empty__desc">暂无事件。在页面上做一次写操作（注册智能体、创建任务）
          即可看到实时推送。</div></div>`;
        return;
      }
      streamEl.innerHTML = stream.map((ev) => {
        const meta = EVENT_META[ev.event] || { label: ev.event, tone: 'neutral' };
        return `<div class="timeline__item">
          <span class="timeline__dot timeline__dot--${meta.tone}"></span>
          <div class="timeline__body">
            <div class="timeline__title">${esc(meta.label)}</div>
            <div class="timeline__meta">
              <span>${esc(eventText(ev))}</span>
              <span>·</span>
              <span>${esc(fmtRelative(ev.timestamp))}</span>
            </div>
          </div>
        </div>`;
      }).join('');
    };
    paintStream();

    const loadStats = async () => {
      try {
        const s = await api.stats();
        const cards = [
          { label: '智能体',       value: s.online_agents, hint: `共 ${s.total_agents} 个 · 在线 ${s.online_agents} 个`, tone: 'success' },
          { label: '知识条目',     value: s.total_entries, hint: '黑板上的共享知识', tone: 'info' },
          { label: '待处理任务',   value: s.pending_tasks, hint: `共 ${s.total_tasks} 个 · 已完成 ${s.completed_tasks} 个`, tone: 'warn' },
          { label: '未完成冲突',   value: Math.max(0, s.total_conflicts - s.resolved_conflicts), hint: `累计 ${s.total_conflicts} 条 · 已解决 ${s.resolved_conflicts} 条`, tone: 'danger' },
          { label: '协作规则',     value: s.total_rules, hint: '热加载，改完即生效', tone: 'neutral' },
        ];
        statsEl.innerHTML = cards.map((c) => `
          <div class="stat">
            <div class="stat__label">${badge(c.label, c.tone)}${''}</div>
            <div class="stat__value">${c.value}</div>
            <div class="stat__hint">${esc(c.hint)}</div>
          </div>`).join('');
      } catch (e) {
        statsEl.innerHTML = errorState({ desc: e.message });
      }
    };

    const loadSys = async () => {
      const el = mount.querySelector('#sysInfo');
      try {
        const r = await api.root();
        el.innerHTML = `
          <div class="kv__k">服务名称</div><div class="kv__v">${esc(r.name)}</div>
          <div class="kv__k">版本</div><div class="kv__v">${esc(r.version)}</div>
          <div class="kv__k">运行状态</div><div class="kv__v">${badge(r.status === 'running' ? '运行中' : r.status, 'success', { dot: true, pulse: true })}</div>
          <div class="kv__k">接口文档</div><div class="kv__v"><a href="/docs" target="_blank" rel="noopener">Swagger UI ↗</a></div>
          <div class="kv__k">实时通道</div><div class="kv__v">WebSocket · 已订阅 ${realtime.getBuffer() ? '多类' : ''}事件</div>
          <div class="kv__k">统计更新</div><div class="kv__v faint">${esc(fmtTime(new Date().toISOString()))}</div>`;
      } catch (e) {
        el.innerHTML = `<div class="kv__k">状态</div><div class="kv__v">${esc(e.message)}</div>`;
      }
    };

    await Promise.all([loadStats(), loadSys()]);

    // --- 交互 ---
    mount.querySelector('#btnRefresh').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      setBusy(btn, true, '刷新中');
      await Promise.all([loadStats(), loadSys()]);
      setBusy(btn, false, '刷新');
      toastOk('已刷新');
    });

    mount.querySelector('#btnAgent').addEventListener('click', () => { location.hash = '#/agents'; });

    mount.querySelector('#btnClearStream').addEventListener('click', () => {
      stream = [];
      paintStream();
    });

    mount.querySelector('#btnSeed').addEventListener('click', () => runSeed());

    // 实时事件接入：新事件插到流顶部，并顺带刷新统计
    bindRealtime('*', (ev) => {
      stream.unshift(ev);
      if (stream.length > 60) stream.pop();
      paintStream();
      loadStats();
    });

    async function runSeed() {
      const ok = await confirmDialog({
        title: '灌入演示数据',
        message: '将创建 5 个智能体、6 条知识条目（含一对观点冲突）、6 个任务（含依赖链与一个无人可接任务），'
               + '并推进部分任务状态。已有数据不会被删除。',
        confirmText: '开始灌入',
      });
      if (!ok) return;

      const body = document.createElement('div');
      body.innerHTML = `<div class="stack-sm">
        <div class="loading-inline"><span class="spinner"></span><span id="seedMsg">准备中…</span></div>
      </div>`;
      const m = modal({ title: '正在灌入演示数据', body });
      const msg = body.querySelector('#seedMsg');

      try {
        const r = await seedDemoData((t) => { msg.textContent = t; });
        m.close();
        const parts = [`智能体 ${r.agents} 个`, `条目 ${r.entries} 条`, `任务 ${r.tasks} 个`, `冲突 ${r.conflicts} 条`];
        toastOk(parts.join(' · '), '演示数据已就绪');
        if (r.errors.length) {
          toastErr(new Error(`部分步骤失败：${r.errors.slice(0, 3).join('；')}`), '有失败项');
        }
        await Promise.all([loadStats(), loadSys()]);
      } catch (e) {
        m.close();
        toastErr(e, '灌入失败');
      }
    }

    return () => { mq.removeEventListener('change', applyCols); };
  },
};
