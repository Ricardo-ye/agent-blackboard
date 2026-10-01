/**
 * 页面级实时订阅管理
 *
 * 页面用 bindRealtime() 订阅事件，切换路由时由 app.js 统一清理，
 * 避免页面卸载后回调仍在跑（典型症状：已离开列表页还在无脑刷新）。
 */
import { realtime } from './api.js';

let pageSubs = [];

function push(fn) { pageSubs.push(fn); }

/** 清空当前页面的全部订阅 */
export function clearRealtime() {
  pageSubs.forEach((fn) => { try { fn(); } catch { /* 忽略已失效的订阅 */ } });
  pageSubs = [];
}

/**
 * 订阅一个或多个事件
 * @param {string|string[]} events 事件名，'*' 表示全部
 * @param {(payload)=>void} fn
 * @param {{debounce?: number}} opts 可选防抖（毫秒）
 */
export function bindRealtime(events, fn, opts = {}) {
  const list = Array.isArray(events) ? events : [events];
  let handler = fn;
  if (opts.debounce) handler = debounce(fn, opts.debounce);
  list.forEach((e) => push(realtime.on(e, handler)));
}

/** 订阅状态变化 */
export function bindStatus(fn) { push(realtime.onStatus(fn)); }

export function debounce(fn, wait = 250) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => { t = null; fn(...args); }, wait);
  };
}
