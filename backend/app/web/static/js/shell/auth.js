/* ==========================================================================
   shell/auth.js —— 登录页外壳
   只有 pages/login.html 加载：主题预置、版本号、登录提交与跳转。
   ========================================================================== */

import {
  $, api, appUrl, applyTheme, heroGlow, heroGradient, internalPath,
  setAccessToken, setSystemTimezone, toast, DEFAULT_THEME, DEFAULT_THEME_CODE,
} from '../core.js';

export function initLoginPage() {
  const form = $('#login-form');
  if (!form) return;
  const errorBox = $('#login-error');
  const submit = $('#login-submit');

  api.get('/meta/version', null, { silent: true }).then((version) => {
    if (version?.timezone) setSystemTimezone(version.timezone);
    const node = $('#login-version');
    if (node && version) node.textContent = `${version.name} v${version.version}`;
  }).catch(() => undefined);

  applyLoginTheme();

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (errorBox) errorBox.classList.add('hidden');
    const username = form.username.value.trim();
    const password = form.password.value;
    if (!username || !password) {
      showLoginError('请输入用户名与密码');
      return;
    }
    submit.classList.add('loading');
    submit.disabled = true;
    try {
      const result = await api.post('/auth/login', { username, password }, { silent: true });
      setAccessToken(result.access_token);
      if (result.must_change_password) {
        window.location.replace(appUrl('account/password'));
        return;
      }
      const me = await api.get('/auth/me', null, { silent: true });
      if (!me.is_admin) {
        window.location.replace(appUrl('account/profile'));
        return;
      }
      const first = firstMenuPath(me.menus);
      window.location.replace(appUrl(first || 'dashboard'));
    } catch (error) {
      showLoginError(error?.message || '登录失败');
    } finally {
      submit.classList.remove('loading');
      submit.disabled = false;
    }
  });

  function showLoginError(message) {
    if (!errorBox) {
      toast(message, 'error');
      return;
    }
    errorBox.textContent = message;
    errorBox.classList.remove('hidden');
  }
}

function firstMenuPath(menus) {
  for (const node of menus || []) {
    if (node.type === 'menu') {
      const path = internalPath(node.path);
      if (path) return path;
    }
    if (node.children?.length) {
      const child = firstMenuPath(node.children);
      if (child) return child;
    }
  }
  return null;
}

function applyLoginTheme() {
  const snapshot = readCachedTheme();
  const config = snapshot.config;
  const dark = Boolean(config.darkAlgorithm);
  const hero = $('#login-hero');
  const page = $('#login-page');
  if (page) page.style.background = heroGradient(config.colorPrimary, dark);
  if (hero) {
    hero.style.background = heroGradient(config.colorPrimary, dark);
    const glow = hero.querySelector('.glow-b');
    if (glow) glow.style.background = heroGlow(config.colorPrimary);
  }
  applyTheme(config, snapshot.code);
}

function readCachedTheme() {
  try {
    const raw = localStorage.getItem('fastcms_theme_snapshot');
    if (raw) {
      const parsed = JSON.parse(raw);
      return { code: parsed.code || DEFAULT_THEME_CODE, config: { ...DEFAULT_THEME, ...(parsed.config || {}) } };
    }
  } catch {
    /* 缓存损坏时回落到默认主题 */
  }
  return { code: DEFAULT_THEME_CODE, config: DEFAULT_THEME };
}
