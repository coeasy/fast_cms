import {
  $, api, confirmDialog, esc, formatTime, toast,
} from '../../core.js';
import { DataTable, actionButtons, tag } from '../../ui.js';

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/security/banned-users', params),
    columns: [
      { render: (row) => `<span class="mono">${esc(row.username)}</span>` },
      { render: (row) => esc(row.nickname || '-') },
      {
        render: (row) =>
          row.banned_until ? tag('临时封禁', 'orange') : tag('永久封禁', 'red'),
      },
      {
        render: (row) =>
          `<span class="muted" title="${esc(row.ban_reason || '')}">${esc(row.ban_reason || '-')}</span>`,
      },
      {
        render: (row) =>
          row.banned_until ? esc(formatTime(row.banned_until)) : '<span class="muted">永不</span>',
      },
      { render: (row) => esc(formatTime(row.banned_at)) },
      {
        className: 'actions',
        render: (row) =>
          actionButtons([
            { act: 'unban', label: '解封' },
            { act: 'ban-ip', label: '封禁 IP' },
          ]),
      },
    ],
  });

  await table.load(1);

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search({ keyword: serializeForm(event.currentTarget).keyword });
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'unban') await unban(row);
    if (button.dataset.act === 'ban-ip') await banIp(row);
  });
}

async function unban(row) {
  const ok = await confirmDialog({
    title: '解封用户',
    message: `将解除 ${row.username} 的封禁，该账号可以立即重新登录。`,
    okText: '解封',
  });
  if (!ok) return;
  try {
    await api.post(`/security/users/${row.id}/unban`);
    toast('用户已解封', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function banIp(row) {
  const ip = row.last_login_ip;
  if (!ip) {
    toast('该用户没有记录到登录 IP', 'warning');
    return;
  }
  const ok = await confirmDialog({
    title: '封禁 IP',
    message: `将封禁 ${row.username} 最近使用的 IP ${ip}。`,
    okText: '封禁',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.post('/security/ip-rules/block-now', {
      ip,
      scope: 'all',
      reason: `由封禁用户 ${row.username} 触发`,
      minutes: 0,
    });
    toast('已封禁该 IP', 'success');
  } catch {
    /* 请求层已提示 */
  }
}

/**
 * 本页只管理「已经封禁」的账号：解封、封禁其常用 IP。
 * 「封禁」动作本身在用户列表页——封禁的前提是先看到那个人，
 * 在这里再放一个入口只会出现两个行为不一致的入口。
 */

