// @ts-check
/* ==========================================================================
   core/base.js —— 页面运行参数、DOM 工具、公共原语
   原生 ES Module，无构建、无第三方依赖。
   ========================================================================== */

/* 页面运行参数由 base.html 里的 <script type="application/json"> 注入。
   用数据块而不是内联脚本，响应头的 CSP 才能禁用 unsafe-inline。 */
function readConfig() {
  try {
    const node = document.getElementById('fastcms-config');
    if (!node) return {};
    return JSON.parse(node.textContent || '{}') || {};
  } catch (e) {
    return {};
  }
}

const CFG = readConfig();
export const BASE = CFG.base || '/admin/';
export const API_BASE = CFG.apiBase || '/api/v1';
export const PORTAL_BASE = CFG.portalBase || '/front/';
export const PAGE = CFG.page || '';

/* --------------------------------- DOM ---------------------------------- */

/**
 * 单元素选择器。``root`` 可以是 document，也可以是任意已定位到的元素
 * （表格内查 tbody、弹窗内查关闭按钮都是这么用的）。
 *
 * 返回类型用泛型：``$('#pw-old')`` 拿到 ``HTMLElement``（有 .value/.disabled），
 * 需要更具体类型时直接在调用点写 ``$('#x', HTMLSelectElement)`` 配合 JSDoc 断言。
 * @template {Element} [T=HTMLElement]
 * @param {string} sel CSS 选择器
 * @param {ParentNode} [root] 查找范围，默认整个文档
 * @returns {T | null}
 */
export const $ = (sel, root = document) => /** @type {T} */ (root.querySelector(sel));

/**
 * 多元素选择器，返回数组而不是 NodeList，调用方可以直接 map/filter。
 * @template {Element} [T=HTMLElement]
 * @param {string} sel CSS 选择器
 * @param {ParentNode} [root] 查找范围，默认整个文档
 * @returns {T[]}
 */
export const $$ = (sel, root = document) => /** @type {T[]} */ (Array.from(root.querySelectorAll(sel)));

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[ch]);
}

/** 只允许站内路径，避免菜单/字典里的值变成跳转或脚本入口 */
export function internalPath(path) {
  if (!path || typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return null;
  return path;
}

export function appUrl(path) {
  const suffix = String(path || '').replace(/^\/+/, '');
  return `${BASE}${suffix}`;
}

export function debounce(fn, wait = 300) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/** 跨模块共享的 localStorage 访问（禁用了 cookie 之外的持久化副作用） */
export function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 统一业务错误：code 与后端错误码保持一致，页面按 code 分支提示 */
export class ApiError extends Error {
  /**
   * @param {{ code?: string, message?: string, status?: number, requestId?: string, details?: any }} payload
   *        全部字段都有兜底，调用方只需要给出它知道的那些。
   */
  constructor({ code, message, status = 0, requestId, details }) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.code = code || 'ERROR';
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }
}
