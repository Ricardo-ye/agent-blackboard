/**
 * API 客户端 —— 严格对齐后端真实契约（契约由 tests/ui_contract_probe.py 实测锁定）
 *
 * 几个容易踩坑的点，这里集中封装掉：
 *  - 条目写操作必须带 author_id 查询参数（不在 body 里）
 *  - 任务更新是 PATCH 且必须带 updater_id 查询参数
 *  - 规则 PATCH 只允许白名单字段，多传会被 422 拒绝
 *  - 冲突检测/解决走查询参数
 *  - 启用 BLACKBOARD_API_KEY 后所有写操作需要 X-API-Key 头
 */

// ---------------------------------------------------------------- 错误类型
export class ApiError extends Error {
  constructor(status, detail, method, url) {
    super(ApiError.toMessage(status, detail));
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.method = method;
    this.url = url;
  }

  static toMessage(status, detail) {
    // 422 的 detail 是校验错误数组
    if (Array.isArray(detail)) {
      const first = detail[0];
      const field = Array.isArray(first?.loc) ? first.loc.slice(-1)[0] : '参数';
      return `${field}：${first?.msg || '校验失败'}`;
    }
    if (typeof detail === 'string' && detail) return detail;

    switch (status) {
      case 0:   return '无法连接服务，请确认后端已启动';
      case 401: return 'API Key 无效或缺失，请在右上角设置中填写';
      case 403: return '没有权限执行该操作（条目仅作者可删除）';
      case 404: return '资源不存在，可能已被删除';
      case 409: return '状态冲突：版本已过期、依赖未就绪或资源已处于终态';
      case 422: return '参数校验失败，请检查输入';
      case 500: return '服务端内部错误';
      default:  return `请求失败（HTTP ${status}）`;
    }
  }
}

// ---------------------------------------------------------------- API Key
const KEY_STORAGE = 'blackboard.apiKey';

export const apiKey = {
  get: () => localStorage.getItem(KEY_STORAGE) || '',
  set: (v) => localStorage.setItem(KEY_STORAGE, (v || '').trim()),
  clear: () => localStorage.removeItem(KEY_STORAGE),
};

// ---------------------------------------------------------------- 请求核心
async function request(method, path, { params, body } = {}) {
  let url = path;
  if (params && Object.keys(params).length) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') qs.append(k, v);
    }
    const s = qs.toString();
    if (s) url += `?${s}`;
  }

  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const key = apiKey.get();
  if (key) headers['X-API-Key'] = key;

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, String(e && e.message ? e.message : e), method, url);
  }

  // 204 / 无内容
  if (res.status === 204) return null;

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }

  if (!res.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
    throw new ApiError(res.status, detail, method, url);
  }
  return data;
}

// ---------------------------------------------------------------- 资源方法
export const api = {
  // --- 系统 ---
  root: () => request('GET', '/'),
  stats: () => request('GET', '/api/stats'),

  // --- 智能体 ---
  listAgents: () => request('GET', '/api/agents'),
  getAgent: (id) => request('GET', `/api/agents/${encodeURIComponent(id)}`),
  createAgent: (body) => request('POST', '/api/agents', { body }),
  deleteAgent: (id) => request('DELETE', `/api/agents/${encodeURIComponent(id)}`),
  heartbeat: (id) => request('POST', `/api/agents/${encodeURIComponent(id)}/heartbeat`),
  setAgentStatus: (id, status) =>
    request('PATCH', `/api/agents/${encodeURIComponent(id)}/status`, { params: { status } }),
  agentTasks: (id) => request('GET', `/api/agents/${encodeURIComponent(id)}/tasks`),

  // --- 黑板条目 ---
  listEntries: (topic) => request('GET', '/api/entries', { params: { topic } }),
  getEntry: (id) => request('GET', `/api/entries/${encodeURIComponent(id)}`),
  createEntry: (authorId, body) =>
    request('POST', '/api/entries', { params: { author_id: authorId }, body }),
  updateEntry: (id, authorId, body) =>
    request('PUT', `/api/entries/${encodeURIComponent(id)}`, {
      params: { author_id: authorId }, body,
    }),
  deleteEntry: (id, authorId) =>
    request('DELETE', `/api/entries/${encodeURIComponent(id)}`, { params: { author_id: authorId } }),
  mergeEntries: (authorId, sourceIds, targetTopic) =>
    request('POST', '/api/entries/merge', {
      params: { author_id: authorId },
      body: { source_ids: sourceIds, target_topic: targetTopic },
    }),

  // --- 任务 ---
  listTasks: (status) => request('GET', '/api/tasks', { params: { status } }),
  getTask: (id) => request('GET', `/api/tasks/${encodeURIComponent(id)}`),
  createTask: (body) => request('POST', '/api/tasks', { body }),
  updateTask: (id, updaterId, body) =>
    request('PATCH', `/api/tasks/${encodeURIComponent(id)}`, {
      params: { updater_id: updaterId }, body,
    }),
  assignTask: (id, assigneeId) =>
    request('POST', `/api/tasks/${encodeURIComponent(id)}/assign`, { params: { assignee_id: assigneeId } }),
  autoAssignTask: (id) => request('POST', `/api/tasks/${encodeURIComponent(id)}/auto-assign`),

  // --- 冲突 ---
  listConflicts: (status) => request('GET', '/api/conflicts', { params: { status } }),
  getConflict: (id) => request('GET', `/api/conflicts/${encodeURIComponent(id)}`),
  detectOpinion: (topic) => request('POST', '/api/conflicts/detect/opinion', { params: { topic } }),
  resolveConflict: (id, strategy, resolvedBy) =>
    request('POST', `/api/conflicts/${encodeURIComponent(id)}/resolve`, {
      params: { strategy, resolved_by: resolvedBy },
    }),

  // --- 规则 ---
  listRules: () => request('GET', '/api/rules'),
  getRule: (id) => request('GET', `/api/rules/${encodeURIComponent(id)}`),
  createRule: (body) => request('POST', '/api/rules', { body }),
  updateRule: (id, body) => request('PATCH', `/api/rules/${encodeURIComponent(id)}`, { body }),
  deleteRule: (id) => request('DELETE', `/api/rules/${encodeURIComponent(id)}`),
};

// ---------------------------------------------------------------- 实时通道
/**
 * 后端 event_bus 的频道就是精确的事件名，没有通配符，
 * 所以这里把系统里所有事件名枚举全量订阅。
 */
export const ALL_EVENTS = [
  'agent.registered', 'agent.unregistered', 'agent.heartbeat',
  'agent.status_changed', 'agent.offline',
  'entry.created', 'entry.updated', 'entry.deleted', 'entry.merged',
  'task.created', 'task.assigned', 'task.status_changed', 'task.unblocked',
  'conflict.detected', 'conflict.resolved', 'conflict.escalated',
  'rule.created', 'rule.updated', 'rule.deleted',
  'notification',
];

class Realtime {
  constructor() {
    this.ws = null;
    this.status = 'idle';        // idle | connecting | live | down
    this.handlers = new Map();   // event -> Set<fn>
    this.statusHandlers = new Set();
    this.buffer = [];            // 最近的事件，供仪表盘回放
    this.bufferLimit = 60;
    this._retry = 0;
    this._timer = null;
    this.enabled = true;
  }

  onStatus(fn) {
    this.statusHandlers.add(fn);
    fn(this.status);
    return () => this.statusHandlers.delete(fn);
  }

  /** 订阅某类事件；返回取消函数 */
  on(event, fn) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event).add(fn);
    return () => {
      const s = this.handlers.get(event);
      if (s) s.delete(fn);
    };
  }

  /** 订阅任意事件 */
  onAny(fn) {
    return this.on('*', fn);
  }

  getBuffer() {
    return this.buffer.slice();
  }

  _setStatus(s) {
    if (this.status === s) return;
    this.status = s;
    this.statusHandlers.forEach((fn) => fn(s));
  }

  connect() {
    if (!this.enabled) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;

    this._setStatus('connecting');
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let ws;
    try {
      ws = new WebSocket(`${proto}://${location.host}/ws`);
    } catch {
      this._setStatus('down');
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this._retry = 0;
      this._setStatus('live');
      ws.send(JSON.stringify({ action: 'subscribe', channels: ALL_EVENTS }));
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (!msg || !msg.event) return;
      if (msg.event === 'pong') return;

      const payload = { event: msg.event, data: msg.data || {}, timestamp: msg.timestamp || new Date().toISOString() };
      this.buffer.unshift(payload);
      if (this.buffer.length > this.bufferLimit) this.buffer.pop();

      const specific = this.handlers.get(msg.event);
      if (specific) specific.forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } });
      const any = this.handlers.get('*');
      if (any) any.forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } });
    };

    ws.onclose = () => {
      this._setStatus('down');
      this._scheduleReconnect();
    };

    ws.onerror = () => { /* onclose 会接着触发，这里不重复处理 */ };
  }

  _scheduleReconnect() {
    if (!this.enabled) return;
    if (this._timer) return;
    const delay = Math.min(1000 * 2 ** this._retry, 15000);
    this._retry += 1;
    this._timer = setTimeout(() => {
      this._timer = null;
      this.connect();
    }, delay);
  }

  disconnect() {
    this.enabled = false;
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
    this._setStatus('down');
  }
}

export const realtime = new Realtime();
