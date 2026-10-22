import {
  $, api, confirmDialog, esc, fillForm, formatTime, getBlob, serializeForm, setLoading,
  state, toast, validateForm,
} from '../../core.js';

export async function init() {
  renderAccount();
  renderAvatar();
  fillProfile();
  await loadSessions();

  $('#btn-avatar')?.addEventListener('click', () => $('#avatar-input').click());
  $('#avatar-input').addEventListener('change', uploadAvatar);
  $('#profile-form').addEventListener('submit', submitProfile);
  $('#btn-revoke-others')?.addEventListener('click', revokeOthers);
  $('#session-rows').addEventListener('click', revokeOne);
}

function renderAccount() {
  const user = state.user || {};
  const roles = user.is_superuser
    ? '<span class="tag gold">超级管理员</span>'
    : (user.roles || []).map((role) => `<span class="tag blue">${esc(role.name)}</span>`).join('') || '-';
  $('#account-desc').innerHTML = `
    ${desc('用户名', user.username)}
    ${desc('部门', user.dept_name || '-')}
    ${desc('角色', roles, true)}
    ${desc('最近登录', formatTime(user.last_login_at))}
    ${desc('最近登录 IP', user.last_login_ip || '-')}
    ${desc('创建时间', formatTime(user.created_at))}
  `;
}

function desc(key, value, raw = false) {
  return `<div class="desc-item"><div class="k">${esc(key)}</div><div class="v">${raw ? value : esc(value || '-')}</div></div>`;
}

function fillProfile() {
  const user = state.user || {};
  fillForm($('#profile-form'), {
    nickname: user.nickname,
    email: user.email,
    phone: user.phone,
  });
}

async function renderAvatar() {
  const node = $('#profile-avatar');
  const user = state.user || {};
  const name = user.nickname || user.username || '?';
  if (!node) return;
  node.textContent = name.slice(0, 1);
  if (!user.avatar_file_id) return;
  try {
    const blob = await getBlob('/auth/avatar');
    const url = URL.createObjectURL(blob);
    node.innerHTML = `<img src="${url}" alt="${esc(name)}">`;
  } catch {
    /* 保留首字母头像 */
  }
}

async function submitProfile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#profile-submit');
  setLoading(button, true);
  try {
    const user = await api.put('/auth/profile', values);
    state.user = user;
    state.me.user = user;
    toast('资料已保存', 'success');
    renderAccount();
    renderAvatar();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function uploadAvatar(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    toast('头像大小不能超过 2 MB', 'warning');
    event.target.value = '';
    return;
  }
  const form = new FormData();
  form.append('file', file);
  const button = $('#btn-avatar');
  setLoading(button, true);
  try {
    const user = await api.upload('/auth/avatar', form);
    state.user = user;
    state.me.user = user;
    toast('头像已更新', 'success');
    await renderAvatar();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
    event.target.value = '';
  }
}

async function loadSessions() {
  const tbody = $('#session-rows');
  try {
    const sessions = await api.get('/auth/sessions');
    if (!sessions.length) {
      tbody.innerHTML = '<tr><td colspan="4"><div class="table-empty">暂无登录设备</div></td></tr>';
      return;
    }
    tbody.innerHTML = sessions
      .map(
        (session) => `<tr data-id="${esc(session.id)}">
          <td class="mono tiny">${esc(session.ip || '-')}</td>
          <td class="tiny muted break">${esc(shortAgent(session.user_agent))}</td>
          <td>${esc(formatTime(session.last_seen_at))}</td>
          <td class="actions">${
            session.current
              ? '<span class="tag green">当前</span>'
              : '<button class="btn-link danger" data-act="revoke">下线</button>'
          }</td>
        </tr>`,
      )
      .join('');
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="4"><div class="table-empty">${esc(error?.message || '加载失败')}</div></td></tr>`;
  }
}

function shortAgent(agent) {
  if (!agent) return '-';
  const text = String(agent);
  const browser = /Edg\//.test(text) ? 'Edge' : /Chrome\//.test(text) ? 'Chrome' : /Firefox\//.test(text) ? 'Firefox' : /Safari\//.test(text) ? 'Safari' : '浏览器';
  const os = /Windows/.test(text) ? 'Windows' : /Mac OS/.test(text) ? 'macOS' : /Linux/.test(text) ? 'Linux' : /Android/.test(text) ? 'Android' : /iPhone|iPad/.test(text) ? 'iOS' : '';
  return os ? `${browser} · ${os}` : browser;
}

async function revokeOne(event) {
  const button = event.target.closest('[data-act="revoke"]');
  if (!button) return;
  const sessionId = button.closest('tr')?.dataset.id;
  if (!sessionId) return;
  const ok = await confirmDialog({
    title: '下线设备',
    message: '确认让该设备的登录会话立即失效？',
    okText: '下线',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/auth/sessions/${encodeURIComponent(sessionId)}`);
    toast('该设备已下线', 'success');
    await loadSessions();
  } catch {
    /* 请求层已提示 */
  }
}

async function revokeOthers() {
  const ok = await confirmDialog({
    title: '下线其他设备',
    message: '除当前设备外的所有登录会话都会失效，确认继续？',
    okText: '全部下线',
    danger: true,
  });
  if (!ok) return;
  try {
    const result = await api.del('/auth/sessions');
    toast(`已下线 ${result?.revoked ?? 0} 个会话`, 'success');
    await loadSessions();
  } catch {
    /* 请求层已提示 */
  }
}
