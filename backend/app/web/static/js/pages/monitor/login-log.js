import {
  $, api, closeModal, confirmDialog, download, endOfDayIso, esc, formatTime, openModal,
  serializeForm, setLoading, startOfDayIso, statusTag, STATUS_MAP, toast,
} from '../../core.js';
import { DataTable, actionButtons, bindBatchBar } from '../../ui.js';

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/monitor/login-logs', params),
    selectable: true,
    columns: [
      { render: (row) => `<span class="mono">${esc(row.username)}</span>` },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { render: (row) => `<span class="muted">${esc(row.reason || '-')}</span>` },
      { render: (row) => `<span class="mono tiny">${esc(row.ip || '-')}</span>` },
      { render: (row) => `<span class="tiny muted" title="${esc(row.user_agent || '')}">${esc(row.user_agent || '-')}</span>` },
      { render: (row) => esc(formatTime(row.created_at)) },
      { className: 'actions', render: () => actionButtons([{ act: 'detail', label: '详情' }]) },
    ],
  });

  await table.load(1);

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(query(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-export')?.addEventListener('click', exportCsv);
  $('#btn-cleanup')?.addEventListener('click', () => openModal('cleanup-modal'));

  bindBatchBar({
    table,
    bar: $('#login-log-batch-bar'),
    actions: [
      {
        act: 'delete',
        label: '批量删除',
        danger: true,
        confirm: '将删除选中的 {n} 条登录日志，操作不可撤销，确定继续？',
        // DELETE 带 body：裸 list[int] 会被 FastAPI 当成 query 参数，
        // 所以走 delBody 而不是 api.del
        run: (ids) => api.delBody('/monitor/login-logs/batch', { ids }),
      },
    ],
  });

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act="detail"]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (row) await showDetail(row.id);
  });

  $('#cleanup-form').addEventListener('submit', cleanup);
}

function query(form) {
  const values = serializeForm(form);
  return {
    keyword: values.keyword,
    status: values.status,
    start: startOfDayIso(values.start_date),
    end: endOfDayIso(values.end_date),
  };
}

async function showDetail(id) {
  const body = $('#detail-body');
  body.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  openModal('detail-modal');
  try {
    const row = await api.get(`/monitor/login-logs/${id}`);
    body.innerHTML = `<div class="desc-grid">
      ${desc('用户名', row.username)}
      ${desc('结果', row.status === 'success' ? '成功' : '失败')}
      ${desc('原因', row.reason || '-')}
      ${desc('IP', row.ip || '-')}
      ${desc('用户 ID', row.user_id ?? '-')}
      ${desc('时间', formatTime(row.created_at))}
      ${desc('客户端', row.user_agent || '-', true)}
    </div>`;
  } catch (error) {
    body.innerHTML = `<div class="table-empty">${esc(error?.message || '加载失败')}</div>`;
  }
}

function desc(key, value, full = false) {
  return `<div class="desc-item"${full ? ' style="grid-column:1/-1"' : ''}><div class="k">${esc(key)}</div><div class="v">${esc(value)}</div></div>`;
}

async function exportCsv() {
  try {
    await download('/monitor/login-logs/export', 'login-logs.csv', { params: query($('#filters')) });
    toast('导出已开始', 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}

async function cleanup(event) {
  event.preventDefault();
  const days = Number($('#cleanup-days').value || 90);
  if (!Number.isFinite(days) || days < 7 || days > 3650) {
    toast('保留天数需在 7-3650 之间', 'warning');
    return;
  }
  const ok = await confirmDialog({
    title: '清理登录日志',
    message: `将删除 ${days} 天之前的登录日志，操作不可撤销。`,
    okText: '清理',
    danger: true,
  });
  if (!ok) return;
  const button = $('#cleanup-submit');
  setLoading(button, true);
  try {
    const result = await api.del('/monitor/login-logs', { before_days: days });
    toast(`已清理 ${result?.deleted ?? 0} 条登录日志`, 'success');
    closeModal('cleanup-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}
