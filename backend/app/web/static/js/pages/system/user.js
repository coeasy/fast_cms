import {
  $, $$, api, can, closeModal, confirmDialog, download, esc, fillForm, formatTime, openModal,
  resetForm, serializeForm, setFieldError, setLoading, state, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import {
  DataTable, actionButtons, bindBatchBar, fillMultiSelect, fillSelect, selectedValues, tag, treeOptions,
} from '../../ui.js';

let table;
let depts = [];
let roles = [];
let editing = null;
let resetTarget = null;
let roleTarget = null;
let banTarget = null;

export async function init() {
  await loadOptions();

  table = new DataTable({
    table: $('#filters').closest('.card').querySelector('table.data'),
    pager: $('#filters').closest('.card').querySelector('.pager'),
    fetch: (params) => api.get('/system/users', params),
    selectable: true,
    columns: [
      { key: 'username', render: (row) => `<span class="mono">${esc(row.username)}</span>` },
      { key: 'nickname' },
      { key: 'dept_name', render: (row) => esc(row.dept_name || '-') },
      {
        render: (row) =>
          row.roles?.length
            ? `<span class="tag-row">${row.roles.map((role) => `<span class="tag blue">${esc(role.name)}</span>`).join('')}</span>`
            : '-',
      },
      { render: (row) => statusCell(row) },
      { render: (row) => esc(formatTime(row.last_login_at)) },
      { render: (row) => esc(formatTime(row.created_at)) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

async function loadOptions() {
  try {
    depts = await api.get('/system/depts/options', null, { silent: true });
  } catch {
    depts = [];
  }
  fillSelect($('#f-dept'), treeOptions(depts), { keep: true });
  fillSelect($('#u-dept'), treeOptions(depts), { placeholder: '未分配', keep: true });

  if (can('system:role:list')) {
    try {
      roles = await api.get('/system/roles/options', null, { silent: true });
    } catch {
      roles = [];
    }
    fillSelect($('#f-role'), roles.map((role) => ({ value: role.id, label: role.name })), { keep: true });
    const options = roles.map((role) => ({ value: role.id, label: role.name }));
    fillMultiSelect($('#u-roles'), options);
    fillMultiSelect($('#assign-roles'), options);
  } else {
    $('#f-role')?.closest('.field')?.classList.add('hidden');
    $('#u-roles')?.closest('.field')?.classList.add('hidden');
  }
}

function statusCell(row) {
  const disabled = !can('system:user:status') || row.is_superuser;
  const locked = isLocked(row);
  return `<span class="row" style="gap:6px">
    <label class="switch">
      <input type="checkbox" data-act="toggle" ${row.status === 'active' ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
      <span class="track"></span>
    </label>
    ${locked ? '<span class="tag red">已锁定</span>' : ''}
  </span>`;
}

function isLocked(row) {
  if (!row.locked_until) return false;
  const until = new Date(row.locked_until).getTime();
  return Number.isFinite(until) && until > Date.now();
}

function rowActions(row) {
  return actionButtons([
    can('system:user:update') && { act: 'edit', label: '编辑' },
    can('system:user:assign-role') && { act: 'roles', label: '角色' },
    can('system:user:reset-password') && { act: 'reset', label: '重置密码' },
    can('security:user-ban:ban') && !row.is_superuser && { act: 'ban', label: '封禁', danger: true },
    can('system:user:delete') && !row.is_superuser && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', () => openCreate());
  $('#btn-export')?.addEventListener('click', exportUsers);
  $('#btn-import')?.addEventListener('click', () => {
    $('#import-form').reset();
    openModal('import-modal');
  });
  $('#import-form').addEventListener('submit', submitImport);

  bindBatchBar({
    table,
    bar: $('#user-batch-bar'),
    actions: [
      {
        act: 'enable',
        label: '批量启用',
        confirm: '将启用选中的 {n} 个用户，确定继续？',
        run: (ids) => api.post('/system/users/batch', { action: 'enable', ids }),
      },
      {
        act: 'disable',
        label: '批量禁用',
        confirm: '将禁用选中的 {n} 个用户，被禁用的账号将无法登录，确定继续？',
        run: (ids) => api.post('/system/users/batch', { action: 'disable', ids }),
      },
      {
        act: 'delete',
        label: '批量删除',
        danger: true,
        confirm: '将删除选中的 {n} 个用户，该操作不可撤销，确定继续？',
        run: (ids) => api.post('/system/users/batch', { action: 'delete', ids }),
      },
    ],
  });

  const card = $('#filters').closest('.card');
  card.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const id = Number(button.closest('tr')?.dataset.id);
    const row = table.find(id);
    if (!row) return;
    const act = button.dataset.act;
    if (act === 'edit') openEdit(row);
    else if (act === 'roles') openRoles(row);
    else if (act === 'reset') openReset(row);
    else if (act === 'ban') openBan(row);
    else if (act === 'delete') remove(row);
  });

  card.addEventListener('change', async (event) => {
    const input = event.target.closest('[data-act="toggle"]');
    if (!input) return;
    const row = table.find(Number(input.closest('tr')?.dataset.id));
    if (!row) return;
    try {
      await api.patch(`/system/users/${row.id}/status`, { status: input.checked ? 'active' : 'disabled' });
      toast('状态已更新', 'success');
      table.reload();
    } catch {
      input.checked = !input.checked;
    }
  });

  $('#user-form').addEventListener('submit', submitUser);
  $('#reset-form').addEventListener('submit', submitReset);
  $('#role-form').addEventListener('submit', submitRoles);
  $('#ban-form').addEventListener('submit', submitBan);
}

/* -------------------------------- 新建/编辑 -------------------------------- */

function openCreate() {
  editing = null;
  const form = $('#user-form');
  resetForm(form);
  $('#user-modal-title').textContent = '新建用户';
  $$('[data-new-only]', form).forEach((node) => node.classList.remove('hidden'));
  $('#u-password').required = true;
  $('#u-username').disabled = false;
  $('#u-super').disabled = !state.isSuperuser;
  $('#u-super-hint').textContent = state.isSuperuser ? '授予后可访问全部数据' : '仅超级管理员可以授予该身份';
  form.status.value = 'active';
  form.must_change_password.checked = true;
  applyPasswordHelp();
  openModal('user-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#user-form');
  resetForm(form);
  $('#user-modal-title').textContent = `编辑用户 ${row.username}`;
  $$('[data-new-only]', form).forEach((node) => node.classList.add('hidden'));
  $('#u-password').required = false;
  $('#u-username').disabled = true;
  $('#u-super').disabled = !state.isSuperuser;
  $('#u-super-hint').textContent = state.isSuperuser ? '授予后可访问全部数据' : '仅超级管理员可以授予该身份';
  fillForm(form, {
    username: row.username,
    nickname: row.nickname,
    dept_id: row.dept_id,
    email: row.email,
    phone: row.phone,
    status: row.status,
    is_superuser: row.is_superuser,
    remark: row.remark,
  });
  const select = $('#u-roles select');
  if (select) {
    const ids = new Set((row.roles || []).map((role) => String(role.id)));
    Array.from(select.options).forEach((option) => {
      option.selected = ids.has(option.value);
    });
    select.dispatchEvent(new Event('change'));
  }
  if (!can('system:user:assign-role')) {
    $('#u-roles')?.closest('.field')?.classList.add('hidden');
  }
  openModal('user-modal');
}

function applyPasswordHelp() {
  const policy = passwordPolicy();
  const help = $('#password-help');
  if (help) help.textContent = policy.description;
  const resetHelp = $('#reset-help');
  if (resetHelp) resetHelp.textContent = policy.description;
}

function passwordPolicy() {
  const raw = Number(state.publicConfig?.['auth.password_min_length']);
  const minLength = Number.isFinite(raw) && raw >= 8 ? Math.min(raw, 64) : 8;
  const description = String(
    state.publicConfig?.['auth.password_rule'] || `至少 ${minLength} 位，必须同时包含字母和数字，且不能包含用户名`,
  );
  return { minLength, description };
}

function checkPassword(field, username) {
  const policy = passwordPolicy();
  const value = field.value;
  let message = '';
  if (!value) message = '请输入密码';
  else if (value.length < policy.minLength) message = `密码至少 ${policy.minLength} 位`;
  else if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) message = '密码必须同时包含字母和数字';
  else if (username && value.toLowerCase().includes(String(username).toLowerCase())) message = '密码不能包含用户名';
  setFieldError(field, message);
  return !message;
}

async function submitUser(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const username = form.username.value.trim();
  if (!editing) {
    if (!checkPassword($('#u-password'), username)) return;
  }
  const values = serializeForm(form);
  delete values.password;
  delete values.must_change_password;
  if (editing) delete values.username;

  const payload = { ...values };
  const roleIds = selectedValues($('#u-roles select'));
  if (can('system:user:assign-role')) payload.role_ids = roleIds;
  if (!state.isSuperuser) delete payload.is_superuser;

  const button = $('#user-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/system/users/${editing.id}`, payload);
      toast('用户已更新', 'success');
    } else {
      payload.password = $('#u-password').value;
      payload.must_change_password = form.must_change_password.checked;
      await api.post('/system/users', payload);
      toast('用户创建成功', 'success');
    }
    closeModal('user-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

/* -------------------------------- 重置密码 -------------------------------- */

function openReset(row) {
  resetTarget = row;
  const form = $('#reset-form');
  resetForm(form);
  $('#reset-modal-title').textContent = `重置密码：${row.username}`;
  form.must_change_password.checked = true;
  const alert = $('#reset-alert');
  if (alert) {
    alert.classList.toggle('hidden', !isLocked(row));
    alert.textContent = '该账号因连续登录失败已被临时锁定，重置密码会同时解除锁定。';
  }
  applyPasswordHelp();
  openModal('reset-modal');
}

async function submitReset(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!checkPassword($('#r-password'), resetTarget?.username)) return;
  const button = $('#reset-submit');
  setLoading(button, true);
  try {
    await api.post(`/system/users/${resetTarget.id}/reset-password`, {
      new_password: form.new_password.value,
      must_change_password: form.must_change_password.checked,
    });
    toast('密码已重置，该用户所有会话已失效', 'success');
    closeModal('reset-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

/* -------------------------------- 分配角色 -------------------------------- */

function openRoles(row) {
  roleTarget = row;
  const form = $('#role-form');
  resetForm(form);
  $('#role-modal-title').textContent = `分配角色：${row.username}`;
  const select = $('#assign-roles select');
  const ids = new Set((row.roles || []).map((role) => String(role.id)));
  Array.from(select.options).forEach((option) => {
    option.selected = ids.has(option.value);
  });
  select.dispatchEvent(new Event('change'));
  openModal('role-modal');
}

async function submitRoles(event) {
  event.preventDefault();
  const button = $('#role-submit');
  setLoading(button, true);
  try {
    await api.put(`/system/users/${roleTarget.id}/roles`, { role_ids: selectedValues($('#assign-roles select')) });
    toast('角色已更新', 'success');
    closeModal('role-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除用户',
    message: `确认删除用户 ${row.username}？该操作不可撤销。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/users/${row.id}`);
    toast('用户已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

/* --------------------------------- 导出 --------------------------------- */

async function exportUsers() {
  try {
    await download('/export/users', 'users.csv', { params: table.params });
    toast('导出已开始', 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}

/* --------------------------------- 导入 --------------------------------- */

/**
 * 导入结果分两段展示：汇总一行 + 逐行失败明细。
 * 失败行必须给出行号——用户是靠「第 37 行密码太弱」去改 CSV 的，
 * 只说「有 1 条失败」等于让他重传整包再猜。
 */
async function submitImport(event) {
  event.preventDefault();
  const file = $('#import-file').files?.[0];
  if (!file) {
    toast('请先选择 CSV 文件', 'warning');
    return;
  }
  const dryRun = $('#import-dry').checked;
  const payload = new FormData();
  payload.append('file', file);
  const button = $('#import-submit');
  setLoading(button, true);
  try {
    const result = await api.upload(`/system/users/import?dry_run=${dryRun ? 'true' : 'false'}`, payload);
    closeModal('import-modal');
    renderImportResult(result, dryRun);
    if (!dryRun) table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

function renderImportResult(result, dryRun) {
  const ok = result.succeeded?.length || 0;
  const failed = result.failed || [];
  const head = `
    <div class="import-summary">
      ${tag(`${dryRun ? '预检' : '导入'}完成：共 ${result.total} 行`, 'blue')}
      ${tag(`可导入 ${ok} 行`, 'green')}
      ${failed.length ? tag(`${failed.length} 行有问题`, 'red') : tag('无失败', 'gray')}
    </div>`;

  const rows = failed
    .map(
      (item) => `<tr>
        <td class="mono">${esc(item.row)}</td>
        <td class="mono">${esc(item.username || '-')}</td>
        <td class="mono">${esc(item.code)}</td>
        <td>${esc(item.message)}</td>
      </tr>`,
    )
    .join('');

  const body = failed.length
    ? `<div class="import-failures">
        <table>
          <thead><tr><th>CSV 行号</th><th>用户名</th><th>错误码</th><th>原因</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`
    : '<div class="muted">全部通过，可以正式导入。</div>';

  $('#import-result-body').innerHTML = head + body;
  $('#import-result-modal-title').textContent = dryRun ? '导入预检结果' : '导入结果';
  openModal('import-result-modal');
}

/* --------------------------------- 封禁 --------------------------------- */

function openBan(row) {
  banTarget = row;
  $('#ban-target').innerHTML = `即将封禁：<span class="mono">${esc(row.username)}</span>`;
  const form = $('#ban-form');
  form.reset();
  $('#ban-ip-minutes').value = '0';
  openModal('ban-modal');
}

async function submitBan(event) {
  event.preventDefault();
  if (!banTarget?.id) return;
  const values = serializeForm(event.currentTarget);
  const button = $('#ban-submit');
  setLoading(button, true);
  try {
    await api.post(`/security/users/${banTarget.id}/ban`, {
      reason: values.reason || null,
      until: values.until ? new Date(values.until).toISOString() : null,
      ban_ip: $('#ban_ip').checked,
      ip_minutes: Number(values.ip_minutes || 0),
    });
    toast('用户已封禁', 'success');
    closeModal('ban-modal');
    banTarget = null;
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}
