// @ts-check
/* ==========================================================================
   core/request.js —— 统一请求层
   统一处理 envelope、401 单飞刷新、下载与错误上抛。
   - 401 时自动用 HttpOnly refresh cookie 换一次 token 并重放；
   - 业务错误抛 ApiError，页面按需捕获，默认在调用处 toast。
   ========================================================================== */

import { API_BASE, appUrl, ApiError } from './base.js';
import { t } from './i18n.js';
import { setAccessToken, clearSession, getAccessToken } from './session.js';
import { toast } from './dialog.js';

let refreshing = null;

// 单次请求的硬超时。没有超时的话，后端卡住（连接建立但不返回）会让启动遮罩
// 永远转圈：页面既不报错也不可交互，用户只能刷新。
const REQUEST_TIMEOUT_MS = 20000;

function timeoutSignal(ms = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

function isAbort(error) {
  return error instanceof DOMException && error.name === 'AbortError';
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

/**
 * 用 refresh cookie 换一个 access token。
 *
 * 三种结果必须区分开，混在一起会把「临时故障」当成「登录过期」：
 * - 成功           → 返回 token；
 * - 凭证确实失效   → 返回 null（401/403），由调用方清理登录态并跳登录；
 * - 网络/服务端故障 → 抛 ApiError，**不清登录态**。后端 500 或代理抖动时
 *   把用户踢到登录页，是最让人莫名其妙的一种「被登出」。
 *
 * @param {{ redirect?: boolean }} [options]
 * @returns {Promise<string | null>}
 */
export async function refreshAccessToken({ redirect = true } = {}) {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    let response;
    try {
      response = await fetch(apiPath('/auth/refresh'), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        signal: timeoutSignal(),
      });
    } catch (error) {
      throw new ApiError({
        code: isAbort(error) ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
        message: isAbort(error)
          ? t('common.timeout', '请求超时，请稍后重试')
          : t('common.networkError', '网络连接失败，请检查后端服务'),
      });
    }
    const body = await response.json().catch(() => null);
    if (response.ok) {
      const token = body?.data?.access_token || null;
      setAccessToken(token);
      return token;
    }
    if (response.status === 401 || response.status === 403) return null;
    throw new ApiError({
      code: body?.code || `HTTP_${response.status}`,
      message: body?.message || t('common.serverError', '服务器内部错误，请稍后重试'),
      status: response.status,
      requestId: body?.request_id,
    });
  })().finally(() => {
    refreshing = null;
  });

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
 * 发起一次 API 请求：拼 URL、带凭证与 CSRF、解信封、把错误统一成 ApiError。
 * @param {string} path 相对 API 前缀的路径
 * @param {{ method?: string, params?: object, body?: any, form?: FormData,
 *           headers?: Record<string, string>, retry?: boolean, silent?: boolean }} [options]
 * @returns {Promise<any>} 信封里的 data 字段
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
  /** @type {Record<string, string>} */
  const requestHeaders = { Accept: 'application/json', ...headers };
  /** @type {RequestInit} */
  const init = {
    method,
    credentials: 'same-origin',
    headers: requestHeaders,
  };

  if (form instanceof FormData) {
    init.body = form;
  } else if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (getAccessToken()) requestHeaders.Authorization = `Bearer ${getAccessToken()}`;

  let response;
  try {
    response = await fetch(url, { ...init, signal: timeoutSignal() });
  } catch (error) {
    const apiError = new ApiError({
      code: isAbort(error) ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
      message: isAbort(error)
        ? t('common.timeout', '请求超时，请稍后重试')
        : t('common.networkError', '网络连接失败，请检查后端服务'),
    });
    if (!silent) toast(apiError.message, 'error');
    throw apiError;
  }

  if (response.status === 401 && retry && !String(path).includes('/auth/login') && !String(path).includes('/auth/refresh')) {
    let token = null;
    try {
      token = await refreshAccessToken();
    } catch (error) {
      // 刷新时的网络/5xx 故障：如实上抛，不当作登录失效处理
      if (!silent && error instanceof ApiError) toast(error.message, 'error');
      throw error;
    }
    if (token) return request(path, { ...options, retry: false });
  }

  try {
    return await parseEnvelope(response);
  } catch (error) {
    if (error instanceof ApiError && !silent) {
      if (error.status === 403) toast(error.message || t('common.forbidden', '没有权限执行该操作'), 'error');
      else if (error.status >= 500) toast(error.message || t('common.serverError', '服务器内部错误，请稍后重试'), 'error');
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

/**
 * 下载二进制资源（导出 CSV、附件）。
 * @param {string} path 相对 API 前缀的路径
 * @param {{ params?: object, method?: string, body?: any }} [options]
 * @returns {Promise<Blob>}
 */
export async function getBlob(path, { params, method = 'GET', body } = {}) {
  const url = `${apiPath(path)}${buildQuery(params)}`;
  /** @type {Record<string, string>} */
  const headers = { Accept: '*/*' };
  if (getAccessToken()) headers.Authorization = `Bearer ${getAccessToken()}`;
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
