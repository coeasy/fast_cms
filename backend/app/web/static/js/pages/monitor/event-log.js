import {
  $,
  esc
} from '../../core/base.js';
import {
  api
} from '../../core/request.js';
import {
  formatTime
} from '../../core/time.js';
import {
  formatMs
} from '../../core/format.js';
import { DataTable } from '../../ui.js';

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/monitor/event-logs', params),
    selectable: false,
    columns: [
      { render: (row) => `<span class="mono">${esc(row.event)}</span>` },
      { render: (row) => `<span class="mono tiny">${esc(row.subscriber)}</span>` },
      {
        render: (row) =>
          row.success
            ? '<span class="tag green">成功</span>'
            : '<span class="tag red">失败</span>',
      },
      { render: (row) => `<span class="num">${esc(formatMs(row.duration_ms))}</span>` },
      { render: (row) => `<span class="muted tiny" title="${esc(row.error || '')}">${esc(row.error || '-')}</span>` },
      { render: (row) => esc(formatTime(row.created_at)) },
    ],
  });

  await table.load(1);

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(query(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
}

function query(form) {
  const data = new FormData(form);
  const params = {};
  const event = data.get('event');
  const success = data.get('success');
  const startDate = data.get('start_date');
  const endDate = data.get('end_date');
  if (event) params.event = event;
  if (success) params.success = success === 'true';
  if (startDate) params.start = `${startDate}T00:00:00`;
  if (endDate) params.end = `${endDate}T23:59:59`;
  return params;
}
