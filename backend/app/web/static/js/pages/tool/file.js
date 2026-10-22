import {
  $, api, can, confirmDialog, download, esc, formatBytes, formatTime, serializeForm,
  toast,
} from '../../core.js';
import { DataTable, actionButtons } from '../../ui.js';

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/tool/files', params),
    columns: [
      { render: (row) => `<span class="break">${esc(row.original_name)}</span>` },
      { render: (row) => `<span class="tag gray">${esc(row.content_type || '-')}</span>` },
      { render: (row) => `<span class="num">${esc(formatBytes(row.size))}</span>` },
      { render: (row) => esc(row.uploader_name || '-') },
      { render: (row) => esc(formatTime(row.created_at)) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('tool:file:download') && { act: 'download', label: '下载' },
    can('tool:file:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());

  $('#btn-upload')?.addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    const button = $('#btn-upload');
    button.classList.add('loading');
    try {
      await api.upload('/tool/files/upload', form);
      toast(`已上传 ${file.name}`, 'success');
      table.reload();
    } catch {
      /* 请求层已提示 */
    } finally {
      button.classList.remove('loading');
      event.target.value = '';
    }
  });

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'download') {
      try {
        await download(`/tool/files/${row.id}/download`, row.original_name);
      } catch (error) {
        toast(error?.message || '下载失败', 'error');
      }
      return;
    }
    if (button.dataset.act === 'delete') {
      const ok = await confirmDialog({
        title: '删除文件',
        message: `确认删除「${row.original_name}」？引用该文件的头像等资源会失效。`,
        okText: '删除',
        danger: true,
      });
      if (!ok) return;
      try {
        await api.del(`/tool/files/${row.id}`);
        toast('文件已删除', 'success');
        table.reload();
      } catch {
        /* 请求层已提示 */
      }
    }
  });
}
