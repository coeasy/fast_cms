import {
  $,
  esc
} from '../../core/base.js';
import {
  confirmDialog,
  toast
} from '../../core/dialog.js';
import {
  api
} from '../../core/request.js';
import {
  can
} from '../../core/permission.js';
import {
  serializeForm
} from '../../core/form.js';
import {
  formatTime
} from '../../core/time.js';
import { DataTable } from '../../ui.js';
import { msg } from '../../core/messages.js';

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    fetch: async (params) => {
      const items = await api.get('/monitor/online', params);
      return { items, total: items.length };
    },
    columns: [
      { render: (row) => `<span class="mono">${esc(row.username)}</span>${row.current ? ' <span class="tag blue">当前</span>' : ''}` },
      { key: 'nickname' },
      { render: (row) => esc(row.dept_name || '-') },
      { render: (row) => `<span class="mono tiny">${esc(row.ip || '-')}</span>` },
      { render: (row) => `<span class="tiny muted" title="${esc(row.user_agent || '')}">${esc(shortAgent(row.user_agent))}</span>` },
      { render: (row) => esc(formatTime(row.last_seen_at)) },
      { render: (row) => esc(formatTime(row.created_at)) },
      {
        className: 'actions',
        render: (row) =>
          can('monitor:online:kick') && !row.current
            ? '<button class="btn-link danger" data-act="kick">强制下线</button>'
            : '-',
      },
    ],
  });

  await table.load(1);

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act="kick"]');
    if (!button) return;
    // 会话 id 是字符串（UUID 形态），不能转 Number：转完 table.find 永远找不到行
    const row = table.find(button.closest('tr')?.dataset.id);
    if (!row) return;
    const ok = await confirmDialog({
      title: '强制下线',
      message: `确认让 ${row.username} 的该会话立即失效？`,
      okText: '下线',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del(`/monitor/online/${encodeURIComponent(row.id)}`);
      toast(msg('online.kicked'), 'success');
      table.reload();
    } catch {
      /* 请求层已提示 */
    }
  });
}

function shortAgent(agent) {
  if (!agent) return '-';
  const text = String(agent);
  const browser = /Edg\//.test(text) ? 'Edge' : /Chrome\//.test(text) ? 'Chrome' : /Firefox\//.test(text) ? 'Firefox' : /Safari\//.test(text) ? 'Safari' : '浏览器';
  const os = /Windows/.test(text) ? 'Windows' : /Mac OS/.test(text) ? 'macOS' : /Linux/.test(text) ? 'Linux' : /Android/.test(text) ? 'Android' : /iPhone|iPad/.test(text) ? 'iOS' : '';
  return os ? `${browser} · ${os}` : browser;
}
