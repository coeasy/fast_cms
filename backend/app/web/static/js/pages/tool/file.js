import { $, esc } from '../../core/base.js';
import { toast } from '../../core/dialog.js';
import { serializeForm } from '../../core/form.js';
import { formatBytes } from '../../core/format.js';
import { msg } from '../../core/messages.js';
import { can } from '../../core/permission.js';
import { api, download } from '../../core/request.js';
import { formatTime } from '../../core/time.js';
import { actionButtons, createCrudPage } from '../../ui.js';

let page;

export async function init() {
  page = createCrudPage({
    endpoint: '/tool/files',
    pagerSelector: '.pager',
    filters: '#filters',
    columns: [
      { render: (row) => `<span class="break">${esc(row.original_name)}</span>` },
      { render: (row) => `<span class="tag gray">${esc(row.content_type || '-')}</span>` },
      { render: (row) => `<span class="num">${esc(formatBytes(row.size))}</span>` },
      { render: (row) => esc(row.uploader_name || '-') },
      { render: (row) => esc(formatTime(row.created_at)) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
    messages: { deleted: 'file.deleted' },
    confirm: {
      title: '删除文件',
      message: (row) => `确认删除「${row.original_name}」？引用该文件的头像等资源会失效。`,
    },
    actions: [
      {
        act: 'download',
        run: async (row) => {
          try {
            await download(`/tool/files/${row.id}/download`, row.original_name);
          } catch (error) {
            toast(error?.message || '下载失败', 'error');
          }
        },
      },
    ],
  });

  await page.init();
  bindUpload();
}

function rowActions(row) {
  return actionButtons([
    can('tool:file:download') && { act: 'download', label: '下载' },
    can('tool:file:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

/** 上传不走弹窗表单：选完文件立即传，成功后刷新列表 */
function bindUpload() {
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
      page.table.reload();
    } catch {
      /* 请求层已提示 */
    } finally {
      button.classList.remove('loading');
      event.target.value = '';
    }
  });
}
