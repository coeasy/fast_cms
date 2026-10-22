import {
  $, api, download, esc, formatMs, formatTime, openModal, serializeForm, statusTag, STATUS_MAP,
} from '../../core.js';
import { DataTable, actionButtons } from '../../ui.js';

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
  body.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
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
    body.innerHTML = `<div class="table-empty">${esc(error?.message || '加载失败')}</div>`;
  }
}

function desc(key, value) {
  return `<div class="desc-item"><div class="k">${esc(key)}</div><div class="v break">${esc(value || '-')}</div></div>`;
}

async function exportJobLogs() {
  try {
    await download('/export/job-logs', 'job-logs.csv', { params: table.params });
    toast('导出已开始', 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}
