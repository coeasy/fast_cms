import { $, esc } from '../../core/base.js';
import { confirmDialog, toast } from '../../core/dialog.js';
import { serializeForm } from '../../core/form.js';
import { msg } from '../../core/messages.js';
import { formatTime } from '../../core/time.js';
import { api } from '../../core/request.js';
import { can } from '../../core/permission.js';
import { actionButtons, createCrudPage, tag } from '../../ui.js';

let page;

export async function init() {
  page = createCrudPage({
    endpoint: '/security/banned-users',
    pagerSelector: '.pager',
    filters: '#filters',
    // 只读列表：这里只做「解封 / 封禁其常用 IP」，没有新建与编辑表单
    columns: [
      { render: (row) => `<span class="mono">${esc(row.username)}</span>` },
      { render: (row) => esc(row.nickname || '-') },
      { render: (row) => (row.banned_until ? tag('临时封禁', 'orange') : tag('永久封禁', 'red')) },
      {
        render: (row) =>
          `<span class="muted" title="${esc(row.ban_reason || '')}">${esc(row.ban_reason || '-')}</span>`,
      },
      {
        render: (row) => (row.banned_until ? esc(formatTime(row.banned_until)) : '<span class="muted">永不</span>'),
      },
      { render: (row) => esc(formatTime(row.banned_at)) },
      {
        className: 'actions',
        render: (row) =>
          actionButtons([
            can('security:user-ban:ban') && { act: 'unban', label: '解封' },
            can('security:user-ban:ban') && { act: 'ban-ip', label: '封禁 IP' },
          ]),
      },
    ],
    actions: [
      { act: 'unban', run: (row) => unban(row) },
      { act: 'ban-ip', run: (row) => banIp(row) },
    ],
  });

  await page.init();
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
    toast(msg('user.unbanned'), 'success');
    page.table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function banIp(row) {
  const ip = row.last_login_ip;
  if (!ip) {
    toast(msg('ban.noLoginIp'), 'warning');
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
    toast(msg('ban.bannedIp'), 'success');
  } catch {
    /* 请求层已提示 */
  }
}

/**
 * 本页只管理「已经封禁」的账号：解封、封禁其常用 IP。
 * 「封禁」动作本身在用户列表页——封禁的前提是先看到那个人，
 * 在这里再放一个入口只会出现两个行为不一致的入口。
 */
