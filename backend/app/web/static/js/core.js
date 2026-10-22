/* ==========================================================================
   core.js —— 请求层、会话、格式化、弹层、主题
   原生 ES Module，无构建、无第三方依赖。
   ========================================================================== */

import { icon } from './icons.js';

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

export const CFG = readConfig();
export const BASE = CFG.base || '/admin/';
export const API_BASE = CFG.apiBase || '/api/v1';
export const PORTAL_BASE = CFG.portalBase || '/front/';
export const PAGE = CFG.page || '';
export const APP_VERSION = CFG.version || '';

/* --------------------------------- DOM ---------------------------------- */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

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

/* ------------------------------- 会话状态 -------------------------------- */

const HINT_KEY = 'fastcms_session_hint';
const THEME_CACHE = 'fastcms_theme_snapshot';
const THEME_PREF = 'fastcms_theme_code';

export const state = {
  me: null,
  user: null,
  permissions: [],
  isSuperuser: false,
  isAdmin: false,
  publicConfig: {},
  timezone: undefined,
};

let accessToken = null;
let refreshing = null;

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function hasSessionHint() {
  return storage()?.getItem(HINT_KEY) === '1';
}

export function setAccessToken(token) {
  accessToken = token || null;
  const store = storage();
  if (!store) return;
  if (token) store.setItem(HINT_KEY, '1');
  else store.removeItem(HINT_KEY);
}

export function getAccessToken() {
  return accessToken;
}

export function clearSession() {
  setAccessToken(null);
  state.me = null;
  state.user = null;
  state.permissions = [];
  state.isSuperuser = false;
  state.isAdmin = false;
}

/* -------------------------------- 请求层 -------------------------------- */

export class ApiError extends Error {
  constructor({ code, message, status = 0, requestId, details }) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.code = code || 'ERROR';
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }
}

function apiPath(suffix) {
  const tail = String(suffix || '').startsWith('/') ? suffix : `/${suffix}`;
  return `${API_BASE}${tail}`;
}

function buildQuery(params) {
  if (!params) return '';
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item));
    else search.append(key, value);
  });
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

export async function refreshAccessToken({ redirect = true } = {}) {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      const response = await fetch(apiPath('/auth/refresh'), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const body = await response.json().catch(() => null);
      const token = body?.data?.access_token || null;
      setAccessToken(token);
      return token;
    } catch {
      return null;
    } finally {
      refreshing = null;
    }
  })();

  const token = await refreshing;
  if (!token && redirect) {
    clearSession();
    const login = appUrl('login');
    if (window.location.pathname.replace(/\/+$/, '') !== login.replace(/\/+$/, '')) {
      window.location.replace(login);
    }
  }
  return token;
}

async function parseEnvelope(response) {
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const payload = body && typeof body === 'object' ? body : {};
    throw new ApiError({
      code: payload.code || `HTTP_${response.status}`,
      message: payload.message || `请求失败（HTTP ${response.status}）`,
      status: response.status,
      requestId: payload.request_id,
      details: payload.details,
    });
  }
  if (!body || typeof body !== 'object') {
    throw new ApiError({ code: 'INVALID_RESPONSE', message: '响应格式错误' });
  }
  if (body.code !== 'OK') {
    throw new ApiError({
      code: body.code || 'ERROR',
      message: body.message || '请求失败',
      requestId: body.request_id,
      details: body.details,
    });
  }
  return body.data;
}

/**
 * 统一请求入口。
 * - 401 时自动用 HttpOnly refresh cookie 换一次 token 并重放；
 * - 业务错误抛 ApiError，页面按需捕获，默认在调用处 toast。
 */
export async function request(path, options = {}) {
  const {
    method = 'GET',
    params,
    body,
    form,
    headers = {},
    retry = true,
    silent = false,
  } = options;

  const url = `${apiPath(path)}${buildQuery(params)}`;
  const init = {
    method,
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...headers },
  };

  if (form instanceof FormData) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (accessToken) init.headers.Authorization = `Bearer ${accessToken}`;

  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    const apiError = new ApiError({ code: 'NETWORK_ERROR', message: '网络连接失败，请检查后端服务' });
    if (!silent) toast(apiError.message, 'error');
    throw apiError;
  }

  if (response.status === 401 && retry && !String(path).includes('/auth/login') && !String(path).includes('/auth/refresh')) {
    const token = await refreshAccessToken();
    if (token) return request(path, { ...options, retry: false });
  }

  try {
    return await parseEnvelope(response);
  } catch (error) {
    if (error instanceof ApiError && !silent) {
      if (error.status === 403) toast(error.message || '没有权限执行该操作', 'error');
      else if (error.status >= 500) toast(error.message || '服务器内部错误，请稍后重试', 'error');
      else toast(error.message, 'error');
    }
    throw error;
  }
}

export const api = {
  get: (path, params, options) => request(path, { ...options, params }),
  post: (path, body, options) => request(path, { ...options, method: 'POST', body }),
  put: (path, body, options) => request(path, { ...options, method: 'PUT', body }),
  patch: (path, body, options) => request(path, { ...options, method: 'PATCH', body }),
  del: (path, params, options) => request(path, { ...options, method: 'DELETE', params }),
  // DELETE 带请求体：批量删除必须传 ids。FastAPI 不会把裸 list[int] 从
  // DELETE 的 body 里解出来（会被当成 query 参数），所以单独开一个入口，
  // 免得每个页面都去裸调 request。
  delBody: (path, body, options) => request(path, { ...options, method: 'DELETE', body }),
  upload: (path, formData, options) => request(path, { ...options, method: 'POST', form: formData }),
};

async function toBlob(response, fallbackMessage) {
  const type = String(response.headers.get('content-type') || '');
  if (type.includes('application/json')) {
    const text = await response.text();
    try {
      const body = JSON.parse(text);
      throw new ApiError({ code: body.code, message: body.message, requestId: body.request_id });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError({ code: 'DOWNLOAD_FAILED', message: fallbackMessage });
    }
  }
  if (!response.ok) throw new ApiError({ code: `HTTP_${response.status}`, message: fallbackMessage });
  return response.blob();
}

export async function getBlob(path, { params, method = 'GET', body } = {}) {
  const url = `${apiPath(path)}${buildQuery(params)}`;
  const headers = { Accept: '*/*' };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) {
    const token = await refreshAccessToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
      response = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    }
  }
  return toBlob(response, '文件读取失败');
}

export async function download(path, filename, options = {}) {
  const blob = await getBlob(path, options);
  saveBlob(blob, filename || 'download.bin');
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* -------------------------------- 权限 ---------------------------------- */

export function can(code) {
  if (!code) return true;
  if (state.isSuperuser) return true;
  return state.permissions.includes(code);
}

export function guard(code) {
  return can(code);
}

/** 模板里用 data-perm="code" 标记需要权限的元素，启动后统一隐藏无权项 */
export function applyPermissions(root = document) {
  $$('[data-perm]', root).forEach((node) => {
    if (!can(node.dataset.perm)) node.hidden = true;
  });
}

/* -------------------------------- Toast --------------------------------- */

function toastHost() {
  let host = $('.toasts');
  if (!host) {
    host = document.createElement('div');
    host.className = 'toasts';
    document.body.appendChild(host);
  }
  return host;
}

const TOAST_ICON = { success: 'check', error: 'close', warning: 'alert', info: 'info' };

export function toast(message, type = 'info', duration = 3200) {
  const host = toastHost();
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.innerHTML = `<span class="ico">${icon(TOAST_ICON[type] || 'info', 15)}</span><span>${esc(message)}</span>`;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .2s';
    setTimeout(() => node.remove(), 220);
  }, duration);
  return node;
}

/* -------------------------------- 弹层 ---------------------------------- */

/**
 * 弹层一律写在 HTML 里（<div class="mask" data-modal="user-form" hidden>），
 * 这里只负责显隐与焦点管理，避免用 JS 拼 DOM。
 */
export function openModal(id) {
  const node = document.getElementById(id);
  if (!node) return null;
  node.hidden = false;
  document.body.style.overflow = 'hidden';
  const focusable = node.querySelector('input:not([type=hidden]), select, textarea, button');
  if (focusable) setTimeout(() => focusable.focus(), 40);
  return node;
}

export function closeModal(id) {
  const node = typeof id === 'string' ? document.getElementById(id) : id;
  if (!node) return;
  node.hidden = true;
  if (!$('.mask:not([hidden])')) document.body.style.overflow = '';
}

export function closeTopModal() {
  const masks = $$('.mask').filter((node) => !node.hidden);
  if (masks.length) closeModal(masks[masks.length - 1]);
}

export function confirmDialog({ title = '确认操作', message = '', okText = '确定', cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    const mask = document.createElement('div');
    mask.className = 'mask';
    mask.style.zIndex = '200';
    mask.innerHTML = `
      <div class="modal" style="width:420px">
        <div class="modal-head">${esc(title)}</div>
        <div class="modal-body"><div style="color:var(--text-2)">${esc(message)}</div></div>
        <div class="modal-foot">
          <button class="btn" data-act="cancel">${esc(cancelText)}</button>
          <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${esc(okText)}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    const done = (value) => {
      mask.remove();
      if (!$('.mask:not([hidden])')) document.body.style.overflow = '';
      resolve(value);
    };
    mask.addEventListener('click', (event) => {
      const act = event.target.closest('[data-act]')?.dataset.act;
      if (act === 'ok') done(true);
      else if (act === 'cancel' || event.target === mask) done(false);
    });
    setTimeout(() => mask.querySelector('[data-act=ok]')?.focus(), 40);
  });
}

/** 通用弹层开合：所有带 data-modal-open / data-modal-close 的元素自动生效 */
export function bindModalTriggers(root = document) {
  root.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-modal-open]');
    if (opener) {
      event.preventDefault();
      openModal(opener.dataset.modalOpen);
      return;
    }
    const closer = event.target.closest('[data-modal-close]');
    if (closer) {
      event.preventDefault();
      closeModal(closer.dataset.modalClose || closer.closest('.mask')?.id);
      return;
    }
    if (event.target.classList?.contains('mask')) closeModal(event.target);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeTopModal();
  });
}

/* -------------------------------- 表单 ---------------------------------- */

/**
 * 读取表单值：按 data-type 做类型转换，跳过 disabled 与未命名控件。
 * checkbox 视为布尔，multiple select 视为数组。
 */
export function serializeForm(form) {
  const data = {};
  $$('[name]', form).forEach((field) => {
    const name = field.name;
    if (!name || field.disabled) return;
    const type = field.dataset.type || '';
    if (field.type === 'checkbox') {
      data[name] = field.checked;
      return;
    }
    if (field.tagName === 'SELECT' && field.multiple) {
      data[name] = Array.from(field.selectedOptions).map((opt) => coerce(opt.value, type));
      return;
    }
    let value = field.value;
    if (type === 'number') {
      data[name] = value === '' ? null : Number(value);
      return;
    }
    if (type === 'bool') {
      data[name] = value === 'true';
      return;
    }
    if (type === 'json') {
      if (value.trim() === '') {
        data[name] = {};
        return;
      }
      try {
        data[name] = JSON.parse(value);
      } catch {
        throw new ApiError({ code: 'INVALID_JSON', message: `${field.dataset.label || name} 不是合法的 JSON` });
      }
      return;
    }
    if (type === 'array') {
      data[name] = value === '' ? [] : value.split(',').map((item) => item.trim()).filter(Boolean);
      return;
    }
    if (type === 'nullable') {
      data[name] = value === '' ? null : value;
      return;
    }
    data[name] = value;
  });
  return data;
}

function coerce(value, type) {
  if (type === 'number') return Number(value);
  return value;
}

export function fillForm(form, values = {}) {
  $$('[name]', form).forEach((field) => {
    const name = field.name;
    if (!name) return;
    const value = values[name];
    if (field.type === 'checkbox') {
      field.checked = Boolean(value);
      return;
    }
    if (field.tagName === 'SELECT' && field.multiple) {
      const list = Array.isArray(value) ? value.map(String) : [];
      Array.from(field.options).forEach((opt) => {
        opt.selected = list.includes(opt.value);
      });
      return;
    }
    if (field.dataset.type === 'json') {
      field.value = value === undefined || value === null ? '' : JSON.stringify(value, null, 2);
      return;
    }
    field.value = value === undefined || value === null ? '' : String(value);
  });
  syncMultiSelects(form);
}

export function resetForm(form) {
  form.reset();
  $$('.field.invalid', form).forEach((node) => node.classList.remove('invalid'));
  $$('[name]', form).forEach((field) => {
    if (field.type === 'checkbox') field.checked = field.defaultChecked;
  });
  syncMultiSelects(form);
}

export function setFieldError(field, message) {
  const wrapper = field.closest('.field');
  if (!wrapper) return;
  wrapper.classList.toggle('invalid', Boolean(message));
  const err = wrapper.querySelector('.err');
  if (err) err.textContent = message || '';
}

/** 基于 required / minlength / pattern 的原生校验，错误就地展示 */
export function validateForm(form) {
  let firstInvalid = null;
  $$('[name]', form).forEach((field) => {
    if (field.disabled || field.type === 'hidden') return;
    let message = '';
    const value = String(field.value ?? '').trim();
    if (field.required && field.type !== 'checkbox' && !value) {
      message = `${field.dataset.label || '该项'}不能为空`;
    } else if (field.type === 'checkbox' && field.required && !field.checked) {
      message = `请勾选${field.dataset.label || '该项'}`;
    } else if (value && field.minLength > 0 && value.length < field.minLength) {
      message = `${field.dataset.label || '该项'}至少 ${field.minLength} 位`;
    } else if (value && field.pattern && !new RegExp(`^(?:${field.pattern})$`).test(value)) {
      message = field.dataset.patternMessage || `${field.dataset.label || '该项'}格式不正确`;
    } else if (value && field.type === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      message = '邮箱格式不正确';
    }
    setFieldError(field, message);
    if (message && !firstInvalid) firstInvalid = field;
  });
  if (firstInvalid) {
    firstInvalid.focus();
    return false;
  }
  return true;
}

export function setLoading(button, loading) {
  if (!button) return;
  button.classList.toggle('loading', Boolean(loading));
  button.disabled = Boolean(loading);
}

/* ------------------------- 多选（原生 select multiple） ------------------------- */

export function syncMultiSelects(root = document) {
  $$('.multi-select', root).forEach((wrapper) => {
    const select = wrapper.querySelector('select[multiple]');
    const box = wrapper.querySelector('.ms-box');
    if (!select || !box) return;
    const selected = Array.from(select.selectedOptions);
    box.innerHTML = selected.length
      ? selected.map((opt) => `<span class="tag blue">${esc(opt.text)}</span>`).join('')
      : `<span class="placeholder">${esc(wrapper.dataset.placeholder || '请选择')}</span>`;
    const chosen = new Set(selected.map((opt) => opt.value));
    wrapper.querySelectorAll('.ms-option').forEach((node) => {
      node.classList.toggle('active', chosen.has(node.dataset.value));
    });
  });
}

export function bindMultiSelects(root = document) {
  root.addEventListener('click', (event) => {
    const wrapper = event.target.closest('.multi-select');
    if (!wrapper) {
      $$('.multi-select.open').forEach((node) => node.classList.remove('open'));
      return;
    }
    const option = event.target.closest('.ms-option');
    const select = wrapper.querySelector('select[multiple]');
    if (option && select) {
      const value = option.dataset.value;
      const target = Array.from(select.options).find((opt) => opt.value === value);
      if (target) target.selected = !target.selected;
      option.classList.toggle('active', Boolean(target?.selected));
      syncMultiSelects(wrapper.parentElement || document);
      select.dispatchEvent(new Event('change', { bubbles: true }));
      event.stopPropagation();
      return;
    }
    if (event.target.closest('.ms-box')) {
      wrapper.classList.toggle('open');
      event.stopPropagation();
    }
  });
}

/* -------------------------------- 时间 ---------------------------------- */

let systemTimezone;

export function setSystemTimezone(name) {
  if (!name) return;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name });
    systemTimezone = name;
  } catch {
    systemTimezone = undefined;
  }
}

export function getSystemTimezone() {
  return systemTimezone;
}

function parts(date, options) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: systemTimezone,
    hour12: false,
    ...options,
  });
  const out = {};
  formatter.formatToParts(date).forEach((part) => {
    if (part.type !== 'literal') out[part.type] = part.value;
  });
  return out;
}

const pad = (value) => String(value).padStart(2, '0');

export function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const p = parts(date, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  return `${p.year}-${p.month}-${p.day} ${pad(Number(p.hour) % 24)}:${p.minute}:${p.second}`;
}

export function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const p = parts(date, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}-${p.month}-${p.day}`;
}

export function formatRecentTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const p = parts(date, {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
  return `${p.month}-${p.day} ${pad(Number(p.hour) % 24)}:${p.minute}`;
}

/** 墙上时间（datetime-local 的值）→ 系统时区对应的 UTC ISO */
export function wallToUtcIso(local) {
  if (!local) return null;
  const [datePart, timePart = '00:00'] = String(local).split('T');
  const [y, m, d] = datePart.split('-').map(Number);
  const [hh, mm, ss = 0] = timePart.split(':').map(Number);
  if (!y || !m || !d) return null;
  const guess = Date.UTC(y, m - 1, d, hh || 0, mm || 0, ss);
  const zone = systemTimezone;
  if (!zone) return new Date(guess).toISOString();
  const p = parts(new Date(guess), {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  const asUtc = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour) % 24, Number(p.minute), Number(p.second));
  return new Date(guess - (asUtc - guess)).toISOString();
}

/** UTC ISO → 系统时区的 datetime-local 值 */
export function utcIsoToWall(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const p = parts(date, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  return `${p.year}-${p.month}-${p.day}T${pad(Number(p.hour) % 24)}:${p.minute}`;
}

/** 日期筛选按展示时区取整天边界，否则跨时区会漏掉首尾两天的记录 */
export function startOfDayIso(dateStr) {
  return dateStr ? wallToUtcIso(`${dateStr}T00:00:00`) : undefined;
}

export function endOfDayIso(dateStr) {
  return dateStr ? wallToUtcIso(`${dateStr}T23:59:59`) : undefined;
}

export function formatBytes(value) {
  const size = Number(value) || 0;
  if (size <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / 1024 ** index).toFixed(index === 0 ? 0 : 2)} ${units[index]}`;
}

export function formatUptime(seconds) {
  if (!seconds || seconds <= 0) return '-';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${days} 天 ${hours} 小时 ${minutes} 分`;
}

export function formatNumber(value) {
  return Number(value || 0).toLocaleString('zh-CN');
}

export function formatMs(value) {
  const ms = Number(value);
  if (!Number.isFinite(ms)) return '-';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/* -------------------------------- 主题 ---------------------------------- */

export const DEFAULT_THEME = {
  colorPrimary: '#1677ff',
  colorSuccess: '#52c41a',
  colorWarning: '#faad14',
  colorError: '#ff4d4f',
  borderRadius: 6,
  navTheme: 'light',
  layout: 'mix',
  darkAlgorithm: false,
  compactAlgorithm: false,
  fixedHeader: true,
  splitMenus: false,
  colorWeak: false,
  fontSize: 14,
  controlHeight: 32,
  componentSize: 'middle',
  contentPadding: 24,
  siderWidth: 208,
  showBreadcrumb: true,
  tableCompact: false,
  watermark: '',
};

export const DEFAULT_THEME_CODE = 'classic-blue';

function isHex(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
}

function lighten(hex, ratio) {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift) => {
    const raw = (value >> shift) & 255;
    return Math.round(raw + (255 - raw) * ratio);
  };
  return `#${[channel(16), channel(8), channel(0)].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
}

function darken(hex, ratio) {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift) => Math.round(((value >> shift) & 255) * (1 - ratio));
  return `#${[channel(16), channel(8), channel(0)].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
}

export function isDarkSurface(hex) {
  if (!isHex(hex)) return false;
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift) => ((value >> shift) & 255) / 255;
  return 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0) < 0.45;
}

export function brandGradient(hex) {
  const base = isHex(hex) ? hex : DEFAULT_THEME.colorPrimary;
  return `linear-gradient(135deg, ${base} 0%, ${lighten(base, 0.3)} 100%)`;
}

export function heroGradient(hex, dark = false) {
  const base = isHex(hex) ? hex : DEFAULT_THEME.colorPrimary;
  const from = darken(base, dark ? 0.78 : 0.55);
  const mid = darken(base, dark ? 0.4 : 0.22);
  return `linear-gradient(135deg, ${from} 0%, ${mid} 55%, ${lighten(base, 0.12)} 100%)`;
}

export function heroGlow(hex, alpha = 0.28) {
  const base = isHex(hex) ? hex : DEFAULT_THEME.colorPrimary;
  const value = Number.parseInt(base.slice(1), 16);
  const channel = (shift) => (value >> shift) & 255;
  return `rgba(${channel(16)}, ${channel(8)}, ${channel(0)}, ${alpha})`;
}

export const themeState = { code: DEFAULT_THEME_CODE, config: { ...DEFAULT_THEME } };

export function readThemeCache() {
  const store = storage();
  if (!store) return { code: DEFAULT_THEME_CODE, config: { ...DEFAULT_THEME } };
  try {
    const raw = store.getItem(THEME_CACHE);
    if (!raw) return { code: DEFAULT_THEME_CODE, config: { ...DEFAULT_THEME } };
    const parsed = JSON.parse(raw);
    return {
      code: parsed.code || DEFAULT_THEME_CODE,
      config: { ...DEFAULT_THEME, ...(parsed.config || {}) },
    };
  } catch {
    return { code: DEFAULT_THEME_CODE, config: { ...DEFAULT_THEME } };
  }
}

export function getPreferredThemeCode() {
  return storage()?.getItem(THEME_PREF) || null;
}

export function setPreferredThemeCode(code) {
  const store = storage();
  if (!store) return;
  if (code) store.setItem(THEME_PREF, code);
  else store.removeItem(THEME_PREF);
}

/**
 * 把主题配置映射成 CSS 变量。主题模板里的每一项都对应一个变量，
 * 未配置的项保持样式表默认值。
 */
export function applyTheme(config, code = DEFAULT_THEME_CODE, { persist = false } = {}) {
  const merged = { ...DEFAULT_THEME, ...(config || {}) };
  themeState.code = code || DEFAULT_THEME_CODE;
  themeState.config = merged;

  const root = document.documentElement;
  const dark = Boolean(merged.darkAlgorithm);
  root.dataset.dark = dark ? '1' : '0';

  const set = (name, value) => {
    if (value === undefined || value === null || value === '') root.style.removeProperty(name);
    else root.style.setProperty(name, String(value));
  };

  set('--primary', merged.colorPrimary);
  set('--success', merged.colorSuccess);
  set('--warning', merged.colorWarning);
  set('--danger', merged.colorError);
  set('--primary-weak', `${isHex(merged.colorPrimary) ? merged.colorPrimary : DEFAULT_THEME.colorPrimary}1f`);
  set('--radius', `${merged.borderRadius}px`);
  set('--radius-lg', `${merged.borderRadius + 4}px`);
  set('--radius-sm', `${Math.max(2, merged.borderRadius - 2)}px`);
  set('--font-size', `${merged.fontSize}px`);
  set('--font-size-sm', `${Math.max(11, merged.fontSize - 1)}px`);
  set('--control-h', `${merged.controlHeight}px`);
  set('--sider-w', `${merged.siderWidth}px`);
  set('--content-pad', `${merged.contentPadding}px`);
  set('--brand-gradient', brandGradient(merged.colorPrimary));
  set('--table-head-bg', merged.tableHeaderBg || (dark ? '#1a1e24' : '#fafbfd'));
  set('--table-cell-y', merged.tableCompact ? '7px' : '12px');

  if (merged.colorBgLayout) set('--bg-layout', merged.colorBgLayout);
  else root.style.removeProperty('--bg-layout');
  if (merged.colorBgContainer) set('--bg-container', merged.colorBgContainer);
  else root.style.removeProperty('--bg-container');
  if (merged.colorBgSider) set('--bg-sider', merged.colorBgSider);
  else root.style.removeProperty('--bg-sider');
  if (merged.colorBgHeader) set('--bg-header', merged.colorBgHeader);
  else root.style.removeProperty('--bg-header');
  if (merged.colorLink) set('--link', merged.colorLink);
  else root.style.removeProperty('--link');

  root.dataset.layout = merged.layout || 'mix';
  root.dataset.navTheme = merged.navTheme || 'light';
  root.dataset.colorWeak = merged.colorWeak ? '1' : '0';

  applyWatermark(merged.watermark);
  document.dispatchEvent(new CustomEvent('theme:change', { detail: themeState }));

  const store = storage();
  if (store) {
    try {
      store.setItem(THEME_CACHE, JSON.stringify(themeState));
      if (persist) store.setItem(THEME_PREF, themeState.code);
    } catch {
      /* 主题写缓存失败不影响渲染 */
    }
  }
}

function applyWatermark(text) {
  let node = document.getElementById('fc-watermark');
  if (!text) {
    node?.remove();
    return;
  }
  if (!node) {
    node = document.createElement('div');
    node.id = 'fc-watermark';
    node.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:1;overflow:hidden;';
    document.body.appendChild(node);
  }
  const rows = 12;
  const cols = 8;
  let html = '';
  for (let r = 0; r < rows; r += 1) {
    html += '<div style="display:flex">';
    for (let c = 0; c < cols; c += 1) {
      html += `<span style="display:inline-block;width:220px;padding:40px 0 0 30px;transform:rotate(-22deg);color:var(--text-4);opacity:.28;font-size:13px;white-space:nowrap">${esc(text)}</span>`;
    }
    html += '</div>';
  }
  node.innerHTML = html;
}

/* -------------------------------- 其它 ---------------------------------- */

export function copyText(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text).then(() => toast('已复制', 'success'));
  }
  const input = document.createElement('textarea');
  input.value = text;
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  input.remove();
  toast('已复制', 'success');
  return Promise.resolve();
}

export function emptyRow(colspan, text = '暂无数据') {
  return `<tr><td colspan="${colspan}"><div class="table-empty"><div class="big">∅</div>${esc(text)}</div></td></tr>`;
}

export function statusTag(status, map = {}) {
  const meta = map[status] || { label: status || '-', color: 'gray' };
  return `<span class="tag ${meta.color}">${esc(meta.label)}</span>`;
}

export const STATUS_MAP = {
  active: { label: '启用', color: 'green' },
  disabled: { label: '禁用', color: 'gray' },
  enabled: { label: '启用', color: 'green' },
  paused: { label: '暂停', color: 'orange' },
  draft: { label: '草稿', color: 'gray' },
  published: { label: '已发布', color: 'green' },
  offline: { label: '已下线', color: 'orange' },
  success: { label: '成功', color: 'green' },
  failure: { label: '失败', color: 'red' },
  failed: { label: '失败', color: 'red' },
  running: { label: '运行中', color: 'blue' },
  skipped: { label: '跳过', color: 'gray' },
  interrupted: { label: '中断', color: 'orange' },
  pending: { label: '待发送', color: 'blue' },
  sending: { label: '发送中', color: 'cyan' },
};
