import {
  $,
  esc
} from '../../core/base.js';
import {
  openModal
} from '../../core/dialog.js';
import {
  api,
  download
} from '../../core/request.js';
import {
  serializeForm
} from '../../core/form.js';
import {
  formatTime
} from '../../core/time.js';
import {
  formatMs
} from '../../core/format.js';
import {
  renderState,
  statusTag,
  STATUS_MAP
} from '../../core/other.js';
import { DataTable, actionButtons } from '../../ui.js';
import { msg } from '../../core/messages.js';

let table;

export async function init() {
  const jobId = new URLSearchParams(window.location.search).get('job_id');

  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/schedule/job-logs', params),
    columns: [
      { render: (row) => `<span class="strong">${esc(row.job_name || '-')}</span><div class="tiny muted mono">${esc(row.job_code || '')}</div>` },
      { render: (row) => `<span class="tag gray">${esc(row.job_type || '-')}</span>` },
      { render: (row) => `<span class="tiny">${esc(row.trigger_type || '-')}</span>` },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { render: (row) => esc(formatTime(row.started_at)) },
      { render: (row) => `<span class="num">${esc(formatMs(row.duration_ms))}</span>` },
      { className: 'actions', render: () => actionButtons([{ act: 'detail', label: '详情' }]) },
    ],
  });

  await table.load(1);

  if (jobId) {
    $('#f-job').value = jobId;
    await table.search({ job_id: Number(jobId) });
  }

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-export')?.addEventListener('click', exportJobLogs);

  $('table.data').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act="detail"]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (row) showDetail(row.id);
  });
}

async function showDetail(id) {
  const body = $('#detail-body');
  renderState(body, { state: 'loading' });
  openModal('detail-modal');
  try {
    const row = await api.get(`/schedule/job-logs/${id}`);
    body.innerHTML = `
      <div class="desc-grid mb-16">
        ${desc('任务', row.job_name)}
        ${desc('编码', row.job_code)}
        ${desc('状态', row.status)}
        ${desc('触发', row.trigger_type)}
        ${desc('开始时间', formatTime(row.started_at))}
        ${desc('结束时间', formatTime(row.finished_at))}
        ${desc('耗时', formatMs(row.duration_ms))}
        ${desc('脚本指纹', row.code_hash || '-')}
      </div>
      ${
        row.error
          ? `<div class="mb-16"><div class="tiny muted mb-8">错误信息</div><pre class="code" style="border-color:var(--danger);color:var(--danger)">${esc(row.error)}</pre></div>`
          : ''
      }
      <div class="tiny muted mb-8">执行输出</div>
      <pre class="code">${esc(row.output || '（无输出）')}</pre>`;
  } catch (error) {
    renderState(body, { state: 'error', message: error?.message });
  }
}

function desc(key, value) {
  return `<div class="desc-item"><div class="k">${esc(key)}</div><div class="v break">${esc(value || '-')}</div></div>`;
}

async function exportJobLogs() {
  try {
    await download('/export/job-logs', 'job-logs.csv', { params: table.params });
    toast(msg('common.exportStarted'), 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}
