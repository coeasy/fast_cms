/* ==========================================================================
   shell/admin.js —— 后台外壳
   登录守卫、侧边导航、顶栏、主题菜单、通知公告、心跳。
   前台页面不加载这一份；登录页用 shell/auth.js。
   ========================================================================== */

import {
  $, $$, api, appUrl, applyTheme, brandGradient, can, clearSession, esc, formatTime,
  getPreferredThemeCode, hasSessionHint, internalPath, openModal, PAGE, PORTAL_BASE,
  refreshAccessToken, setPreferredThemeCode, setSystemTimezone, state, themeState, toast,
  DEFAULT_THEME, DEFAULT_THEME_CODE,
} from '../core.js';
import { icon } from '../icons.js';

export const ROUTE_PERMISSIONS = {
  '/dashboard': 'dashboard:view',
  '/system/user': 'system:user:list',
  '/system/role': 'system:role:list',
  '/system/menu': 'system:menu:list',
  '/system/dept': 'system:dept:list',
  '/system/permission': 'system:permission:list',
  '/system/dict': 'system:dict:list',
  '/system/config': 'system:config:list',
  '/system/theme': 'system:theme:list',
  '/monitor/online': 'monitor:online:list',
  '/monitor/server': 'monitor:server:read',
  '/monitor/login-log': 'monitor:login-log:list',
  '/monitor/operation-log': 'monitor:operation-log:list',
  '/schedule/job': 'schedule:job:list',
  '/schedule/template': 'schedule:template:list',
  '/schedule/log': 'schedule:log:list',
  '/content/notice': 'system:notice:list',
  '/notify/channel': 'notify:channel:list',
  '/notify/record': 'notify:record:list',
  '/tool/file': 'tool:file:list',
  '/tool/codegen': 'tool:codegen:list',
};

const NON_ADMIN_PATHS = new Set(['/account/profile', '/account/password']);
const PUBLIC_PATHS = new Set(['/login', '/403', '/404']);

const MENU_ICONS = {
  DashboardOutlined: 'dashboard',
  SettingOutlined: 'setting',
  MonitorOutlined: 'monitor',
  ScheduleOutlined: 'schedule',
  SoundOutlined: 'sound',
  BellOutlined: 'bell',
  ToolOutlined: 'tool',
  UserOutlined: 'user',
};

let publicThemes = { active: null, items: [] };

/* ------------------------------- 登录守卫 ------------------------------- */

function redirect(path) {
  window.location.replace(appUrl(path));
}

async function loadPublicConfig() {
  try {
    state.publicConfig = (await api.get('/system/public-config', null, { silent: true })) || {};
  } catch {
    state.publicConfig = {};
  }
  try {
    const version = await api.get('/meta/version', null, { silent: true });
    if (version?.timezone) setSystemTimezone(version.timezone);
  } catch {
    /* 时区取不到时按浏览器本地时区展示 */
  }
}

async function ensureSession() {
  if (!hasSessionHint()) return null;
  try {
    return await api.get('/auth/me', null, { silent: true });
  } catch (error) {
    if (error?.status === 401) {
      const token = await refreshAccessToken({ redirect: false });
      if (token) {
        try {
          return await api.get('/auth/me', null, { silent: true });
        } catch {
          return null;
        }
      }
    }
    return null;
  }
}

/**
 * 后台外壳启动：
 * 1. 拉公开配置与时区；2. 校验会话；3. 权限与强制改密拦截；4. 渲染导航与顶栏。
 * 返回 false 表示已跳转，页面脚本不应继续执行。
 */
export async function bootAdmin() {
  const boot = document.getElementById('boot-mask');
  await loadPublicConfig();
  await loadThemes();

  const me = await ensureSession();
  if (!me) {
    if (PUBLIC_PATHS.has(PAGE)) {
      boot?.remove();
      return true;
    }
    redirect('login');
    return false;
  }

  state.me = me;
  state.user = me.user;
  state.permissions = me.permissions || [];
  state.isSuperuser = Boolean(me.is_superuser);
  state.isAdmin = Boolean(me.is_admin);

  if (me.user?.must_change_password && PAGE !== '/account/password') {
    redirect('account/password');
    return false;
  }
  if (!PUBLIC_PATHS.has(PAGE)) {
    if (!me.is_admin && !NON_ADMIN_PATHS.has(PAGE)) {
      redirect('403');
      return false;
    }
    const required = ROUTE_PERMISSIONS[PAGE];
    if (required && !can(required)) {
      redirect('403');
      return false;
    }
  }

  applyTheme(publicThemes.active?.config || DEFAULT_THEME, publicThemes.active?.code || DEFAULT_THEME_CODE);
  const preferred = getPreferredThemeCode();
  if (preferred) {
    const chosen = publicThemes.items.find((item) => item.code === preferred);
    if (chosen) applyTheme(chosen.config, chosen.code);
  }

  renderNav();
  renderHeader();
  bindShellEvents();
  startHeartbeat();
  boot?.remove();
  document.body.classList.add('ready');
  return true;
}

async function loadThemes() {
  try {
    publicThemes = (await api.get('/system/themes/public', null, { silent: true })) || publicThemes;
  } catch {
    publicThemes = { active: null, items: [] };
  }
}

/* -------------------------------- 导航 ---------------------------------- */

function visibleChildren(node) {
  return (node.children || []).filter(
    (child) => child.type !== 'button' && child.status === 'active' && child.visible !== false,
  );
}

function menuLink(node, depth = 0) {
  const path = internalPath(node.path);
  const active = path && path === PAGE;
  const ico = MENU_ICONS[node.icon] ? icon(MENU_ICONS[node.icon], 16) : '';
  const cls = `nav-item${active ? ' active' : ''}`;
  const label = esc(node.name);
  if (!path) return `<div class="${cls}" role="button" tabindex="0">${ico ? `<span class="ico">${ico}</span>` : ''}<span>${label}</span></div>`;
  return `<a class="${cls}" href="${appUrl(path)}" style="padding-left:${depth ? 36 : 10}px">${ico ? `<span class="ico">${ico}</span>` : ''}<span>${label}</span></a>`;
}

function renderNav() {
  const host = $('#nav');
  if (!host) return;
  const menus = (state.me?.menus || []).filter(
    (node) => node.type !== 'button' && node.status === 'active' && node.visible !== false,
  );

  const html = menus
    .map((node) => {
      const children = visibleChildren(node);
      if (node.type === 'directory') {
        if (!children.length) return '';
        const hasActive = children.some((child) => child.path === PAGE);
        const ico = MENU_ICONS[node.icon] ? icon(MENU_ICONS[node.icon], 16) : '';
        return `
          <div class="nav-group${hasActive ? ' open' : ''}">
            <div class="nav-item" data-group-toggle role="button" tabindex="0">
              ${ico ? `<span class="ico">${ico}</span>` : ''}<span>${esc(node.name)}</span>
              <span class="caret">${icon('chevron', 12)}</span>
            </div>
            <div class="nav-children">${children.map((child) => menuLink(child, 1)).join('')}</div>
          </div>`;
      }
      return menuLink(node);
    })
    .join('');

  host.innerHTML = html || '<div class="muted small" style="padding:16px">当前账号没有可用菜单</div>';

  $$('[data-group-toggle]', host).forEach((node) => {
    node.addEventListener('click', () => node.closest('.nav-group').classList.toggle('open'));
  });

  renderTopNav(menus);
}

function renderTopNav(menus) {
  const host = $('#topnav');
  if (!host) return;
  const items = menus.map((node) => {
    const children = visibleChildren(node);
    if (node.type === 'directory') {
      if (!children.length) return '';
      return `<div class="topnav-item" data-top-group="${esc(node.name)}">${esc(node.name)}${icon('down', 12)}</div>`;
    }
    const path = internalPath(node.path);
    return path
      ? `<a class="topnav-item${path === PAGE ? ' active' : ''}" href="${appUrl(path)}">${esc(node.name)}</a>`
      : '';
  });
  host.innerHTML = items.join('');
  host.querySelectorAll('[data-top-group]').forEach((node) => {
    node.addEventListener('click', (event) => {
      const name = node.dataset.topGroup;
      const menu = menus.find((item) => item.name === name);
      if (!menu) return;
      const links = visibleChildren(menu)
        .map((child) => {
          const path = internalPath(child.path);
          return path
            ? `<a class="pop-item${path === PAGE ? ' active' : ''}" href="${appUrl(path)}">${esc(child.name)}</a>`
            : '';
        })
        .join('');
      openPopover(node, links, event);
    });
  });
}

/* -------------------------------- 顶栏 ---------------------------------- */

function avatarHtml(size = 30) {
  const user = state.user;
  if (!user) return '';
  const name = user.nickname || user.username;
  const style = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.42)}px`;
  if (user.avatar_file_id) {
    return `<span class="avatar" style="${style}"><img data-avatar alt="" src=""></span>`;
  }
  return `<span class="avatar" style="${style}">${esc(name.slice(0, 1))}</span>`;
}

function renderHeader() {
  const user = state.user;
  if (!user) return;
  const name = user.nickname || user.username;
  const title = String(state.publicConfig['system.title'] || 'Fast CMS');

  const brand = $('#brand');
  if (brand) {
    brand.innerHTML = `
      <span class="brand-logo" style="background:${brandGradient(themeState.config.colorPrimary)}">${esc(title.slice(0, 1))}</span>
      <span class="brand-name">${esc(title)}</span>`;
  }

  const crumbs = $('#crumbs');
  if (crumbs) {
    const trail = breadcrumbTrail(PAGE);
    crumbs.innerHTML = trail
      .map((item, index) =>
        index === trail.length - 1
          ? `<span class="current">${esc(item)}</span>`
          : `<span>${esc(item)}</span><span class="sep">/</span>`,
      )
      .join('');
  }

  const actions = $('#header-actions');
  if (actions) {
    actions.innerHTML = `
      <button class="icon-btn" id="theme-btn" title="切换主题" aria-label="切换主题">${icon('palette', 17)}</button>
      <button class="icon-btn" id="bell-btn" title="通知公告" aria-label="通知公告">
        ${icon('bell', 18)}<span class="dot hidden" id="bell-dot">0</span>
      </button>
      <span class="vdivider"></span>
      <button class="user-chip" id="user-btn" aria-label="账号菜单">
        ${avatarHtml(30)}
        <span class="name">${esc(name)}</span>
        <span style="color:var(--text-3)">${icon('down', 12)}</span>
      </button>`;

    $('#theme-btn')?.addEventListener('click', (event) => openThemeMenu(event.currentTarget));
    $('#bell-btn')?.addEventListener('click', (event) => openNoticePanel(event.currentTarget));
    $('#user-btn')?.addEventListener('click', (event) => openUserMenu(event.currentTarget));
    loadAvatar();
    refreshUnread();
  }

  const portalLinks = $$('[data-portal-link]');
  portalLinks.forEach((node) => {
    node.href = PORTAL_BASE;
  });
}

function breadcrumbTrail(path) {
  const trail = [];
  const walk = (nodes, stack) => {
    for (const node of nodes || []) {
      const next = [...stack, node.name];
      if (node.path === path) {
        trail.push(...next);
        return true;
      }
      if (node.children?.length && walk(node.children, next)) return true;
    }
    return false;
  };
  walk(state.me?.menus || [], []);
  if (!trail.length) {
    const title = document.body.dataset.title;
    if (title) trail.push(title);
  }
  return trail.length ? trail : ['工作台'];
}

async function loadAvatar() {
  if (!state.user?.avatar_file_id) return;
  try {
    const { getBlob } = await import('../core.js');
    const blob = await getBlob('/auth/avatar');
    const url = URL.createObjectURL(blob);
    $$('[data-avatar]').forEach((img) => {
      img.src = url;
      img.alt = state.user.nickname || state.user.username;
    });
  } catch {
    /* 头像失败时保留首字母头像 */
  }
}

/* ------------------------------- 浮层菜单 -------------------------------- */

let activePopover = null;

function closePopover() {
  activePopover?.remove();
  activePopover = null;
}

function openPopover(anchor, html, event) {
  event?.stopPropagation();
  if (activePopover && activePopover._anchor === anchor) {
    closePopover();
    return;
  }
  closePopover();
  const node = document.createElement('div');
  node.className = 'popover';
  node._anchor = anchor;
  node.innerHTML = html;
  document.body.appendChild(node);
  const rect = anchor.getBoundingClientRect();
  const width = node.offsetWidth;
  let left = rect.right - width;
  if (left < 8) left = 8;
  node.style.left = `${left + window.scrollX}px`;
  node.style.top = `${rect.bottom + 6 + window.scrollY}px`;
  node.style.minWidth = `${Math.max(180, rect.width)}px`;
  activePopover = node;
  return node;
}

function openThemeMenu(anchor) {
  const items = publicThemes.items || [];
  const html = `
    <div class="pop-head">切换主题</div>
    ${items
      .map(
        (item) =>
          `<div class="pop-item" data-theme="${esc(item.code)}">
             ${item.code === (getPreferredThemeCode() || publicThemes.active?.code) ? icon('check', 14) : '<span style="width:14px"></span>'}
             <span>${esc(item.name)}</span>
           </div>`,
      )
      .join('')}
    ${items.length ? '<div class="pop-divider"></div>' : ''}
    <div class="pop-item" data-theme="">跟随系统默认</div>`;
  const node = openPopover(anchor, html);
  node?.querySelectorAll('[data-theme]').forEach((item) => {
    item.addEventListener('click', () => {
      const code = item.dataset.theme;
      if (!code) {
        setPreferredThemeCode(null);
        applyTheme(publicThemes.active?.config || DEFAULT_THEME, publicThemes.active?.code || DEFAULT_THEME_CODE);
      } else {
        const theme = items.find((entry) => entry.code === code);
        if (theme) {
          setPreferredThemeCode(code);
          applyTheme(theme.config, theme.code);
        }
      }
      closePopover();
      toast('主题已切换', 'success', 1600);
    });
  });
}

function openUserMenu(anchor) {
  const user = state.user;
  const html = `
    <div class="pop-head" style="display:flex;gap:10px;align-items:center">
      ${avatarHtml(36)}
      <div style="min-width:0">
        <div style="font-weight:600">${esc(user.nickname || user.username)}</div>
        <div class="tiny muted">@${esc(user.username)}${user.dept_name ? ` · ${esc(user.dept_name)}` : ''}</div>
      </div>
    </div>
    <div class="pop-divider"></div>
    <a class="pop-item" href="${appUrl('account/profile')}">${icon('user', 14)}个人中心</a>
    <a class="pop-item" href="${appUrl('account/password')}">${icon('key', 14)}修改密码</a>
    <a class="pop-item" href="${PORTAL_BASE}" target="_blank" rel="noopener noreferrer">${icon('global', 14)}访问前台</a>
    <div class="pop-divider"></div>
    <div class="pop-item danger" data-logout>${icon('logout', 14)}退出登录</div>`;

  const node = openPopover(anchor, html);
  node?.querySelector('[data-logout]')?.addEventListener('click', async () => {
    try {
      await api.post('/auth/logout', {}, { silent: true });
    } catch {
      /* 会话可能已失效，仍然清理本地状态 */
    }
    clearSession();
    redirect('login');
  });
}

/* -------------------------------- 通知 ---------------------------------- */

let unreadCount = 0;

async function refreshUnread() {
  try {
    const result = await api.get('/content/notices/unread-count', null, { silent: true });
    unreadCount = Number(result?.count || 0);
    const dot = $('#bell-dot');
    if (dot) {
      dot.textContent = unreadCount > 99 ? '99+' : String(unreadCount);
      dot.classList.toggle('hidden', unreadCount <= 0);
    }
  } catch {
    /* 无权限或网络异常时保持静默 */
  }
}

async function openNoticePanel(anchor) {
  let items = [];
  try {
    const result = await api.get('/content/notices/published', { page: 1, page_size: 8 }, { silent: true });
    items = result?.items || [];
  } catch {
    items = [];
  }
  const list = items.length
    ? items
        .map(
          (item) => `
          <div class="pop-item" data-notice="${item.id}" style="align-items:flex-start;flex-direction:column;gap:2px">
            <span style="font-weight:${item.read ? 400 : 600}">${esc(item.title)}</span>
            <span class="tiny muted">${esc(formatTime(item.published_at))}</span>
          </div>`,
        )
        .join('')
    : '<div class="pop-item muted">暂无公告</div>';

  const html = `
    <div class="pop-head" style="display:flex;align-items:center">
      <span>通知公告</span>
      <span style="margin-left:auto;font-weight:400;font-size:12px" class="muted">未读 ${unreadCount}</span>
    </div>
    <div class="pop-divider"></div>
    ${list}
    ${unreadCount ? '<div class="pop-divider"></div><div class="pop-item" data-read-all style="justify-content:center">全部标为已读</div>' : ''}`;

  const node = openPopover(anchor, html);
  node?.querySelectorAll('[data-notice]').forEach((item) => {
    item.addEventListener('click', async () => {
      const id = Number(item.dataset.notice);
      closePopover();
      await showNotice(id);
    });
  });
  node?.querySelector('[data-read-all]')?.addEventListener('click', async () => {
    try {
      await api.post('/content/notices/read-all', {});
      toast('已全部标为已读', 'success');
      await refreshUnread();
      closePopover();
    } catch {
      /* 请求层已提示 */
    }
  });
}

async function showNotice(id) {
  const body = $('#notice-modal-body');
  const title = $('#notice-modal-title');
  if (!body) return;
  title.textContent = '公告详情';
  body.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  openModal('notice-modal');
  try {
    const notice = await api.get(`/content/notices/${id}/published`);
    title.textContent = notice.title || '公告详情';
    body.innerHTML = `
      <div class="tiny muted mb-16">${esc(formatTime(notice.published_at))}</div>
      <div style="white-space:pre-wrap;line-height:1.8">${esc(notice.content || '')}</div>`;
    if (!notice.read) {
      await api.post(`/content/notices/${id}/read`, {}, { silent: true });
      await refreshUnread();
    }
  } catch (error) {
    body.innerHTML = `<div class="table-empty">${esc(error?.message || '公告加载失败')}</div>`;
  }
}

/* ------------------------------ 外壳交互 -------------------------------- */

function bindShellEvents() {
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.popover') && !event.target.closest('#header-actions') && !event.target.closest('#topnav')) {
      closePopover();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePopover();
  });
  $('#nav-toggle')?.addEventListener('click', () => {
    $('.app')?.classList.toggle('nav-open');
  });
  $('.app')?.addEventListener('click', (event) => {
    if (event.target.classList.contains('app')) $('.app')?.classList.remove('nav-open');
  });

  // 在线列表按 last_seen_at 判断活跃度，页面不动也要有心跳兜底
  setInterval(() => {
    if (document.visibilityState === 'visible') {
      api.post('/auth/heartbeat', {}, { silent: true }).catch(() => undefined);
    }
  }, 60_000);

  setInterval(() => refreshUnread(), 60_000);
}

function startHeartbeat() {
  if (document.visibilityState === 'visible') {
    api.post('/auth/heartbeat', {}, { silent: true }).catch(() => undefined);
  }
}
