import {
  $, api, can, closeModal, confirmDialog, esc, formatTime, openModal, serializeForm,
  setLoading, statusTag, STATUS_MAP, toast,
} from '../../core.js';
import { DataTable, actionButtons } from '../../ui.js';

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/notify/records', params),
    columns: [
      { render: (row) => `${esc(row.channel_name || '-')}<div class="tiny muted">${esc(row.channel_type || '')}</div>` },
      { render: (row) => `<span class="tag gray">${esc(row.event)}</span>` },
      { render: (row) => `<span class="break">${esc(row.title || '-')}</span>` },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { render: (row) => String(row.attempts ?? 0) },
      { render: (row) => (row.response_status ? String(row.response_status) : '-') },
      { render: (row) => esc(formatTime(row.sent_at)) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    { act: 'detail', label: '详情' },
    can('notify:record:retry') && { act: 'retry', label: '重试' },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-cleanup')?.addEventListener('click', () => openModal('cleanup-modal'));

  $('table.data').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'detail') showDetail(row.id);
    else if (button.dataset.act === 'retry') retry(row);
  });

  $('#cleanup-form').addEventListener('submit', cleanup);
}

async function showDetail(id) {
  const body = $('#detail-body');
  body.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  openModal('detail-modal');
  try {
    const row = await api.get(`/notify/records/${id}`);
    body.innerHTML = `
      <div class="desc-grid mb-16">
        ${desc('渠道', row.channel_name)}
        ${desc('类型', row.channel_type)}
        ${desc('事件', row.event)}
        ${desc('状态', row.status)}
        ${desc('尝试次数', String(row.attempts ?? 0))}
        ${desc('HTTP 状态', row.response_status ? String(row.response_status) : '-')}
        ${desc('发送时间', formatTime(row.sent_at))}
        ${desc('下次重试', formatTime(row.next_retry_at))}
        ${desc('去重键', row.dedupe_key, true)}
      </div>
      ${
        row.last_error
          ? `<div class="mb-16"><div class="tiny muted mb-8">最近错误</div><pre class="code" style="border-color:var(--danger);color:var(--danger)">${esc(row.last_error)}</pre></div>`
          : ''
      }
      <div class="tiny muted mb-8">消息正文</div>
      <pre class="code">${esc(row.content || '（无正文）')}</pre>`;
  } catch (error) {
    body.innerHTML = `<div class="table-empty">${esc(error?.message || '加载失败')}</div>`;
  }
}

function desc(key, value, full = false) {
  return `<div class="desc-item"${full ? ' style="grid-column:1/-1"' : ''}><div class="k">${esc(key)}</div><div class="v break">${esc(value || '-')}</div></div>`;
}

async function retry(row) {
  const ok = await confirmDialog({
    title: '重试发送',
    message: `确认重新投递「${row.title || row.event}」到 ${row.channel_name}？`,
    okText: '重试',
  });
  if (!ok) return;
  try {
    const record = await api.post(`/notify/records/${row.id}/retry`);
    toast(record?.status === 'success' ? '重试成功' : `重试结果：${record?.status || '未知'}`, record?.status === 'success' ? 'success' : 'warning');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function cleanup(event) {
  event.preventDefault();
  const days = Number($('#cleanup-days').value || 30);
  if (!Number.isFinite(days) || days < 1) {
    toast('保留天数必须大于 0', 'warning');
    return;
  }
  const ok = await confirmDialog({
    title: '清理发送记录',
    message: `将删除 ${days} 天之前的发送记录，操作不可撤销。`,
    okText: '清理',
    danger: true,
  });
  if (!ok) return;
  const button = $('#cleanup-submit');
  setLoading(button, true);
  try {
    const result = await api.del('/notify/records', { before_days: days });
    toast(`已清理 ${result?.deleted ?? 0} 条记录`, 'success');
    closeModal('cleanup-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}
