// @ts-check
/* ==========================================================================
   core/session.js —— 会话状态与访问令牌
   令牌只存内存，刷新依赖 HttpOnly cookie；本地只留「是否有会话」提示位。
   ========================================================================== */

import { storage } from './base.js';

const HINT_KEY = 'fastcms_session_hint';

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
