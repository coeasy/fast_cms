import { $, esc } from '../../core/base.js';
import { confirmDialog, toast } from '../../core/dialog.js';
import { fillForm } from '../../core/form.js';
import { msg } from '../../core/messages.js';
import { STATUS_MAP, statusTag } from '../../core/other.js';
import { can } from '../../core/permission.js';
import { api, download } from '../../core/request.js';
import { formatTime } from '../../core/time.js';
import { actionButtons, createCrudPage } from '../../ui.js';

let page;

export async function init() {
  page = createCrudPage({
    endpoint: '/content/notices',
    pagerSelector: '.pager',
    filters: '#filters',
    columns: [
      {
        render: (row) =>
          `<span class="strong">${esc(row.title)}</span>${row.is_top ? ' <span class="tag red">置顶</span>' : ''}`,
      },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { render: (row) => (row.is_top ? '<span class="tag red">是</span>' : '<span class="tag gray">否</span>') },
      { render: (row) => esc(formatTime(row.published_at)) },
      { render: (row) => esc(formatTime(row.updated_at)) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
    form: {
      id: '#notice-form',
      modal: 'notice-modal',
      titleId: '#notice-modal-title',
      submitId: '#notice-submit',
      titleCreate: '新建公告',
      titleEdit: (row) => `编辑公告 ${row.title}`,
      onCreate: (form) => {
        // 只有「新建」才需要选状态；编辑不改状态（发布/下线走行内按钮，走独立确认）
        form.querySelectorAll('[data-create-only]').forEach((node) => node.classList.remove('hidden'));
        form.elements.status.value = 'draft';
      },
      onEdit: (form, row) => {
        form.querySelectorAll('[data-create-only]').forEach((node) => node.classList.add('hidden'));
        fillForm(form, { title: row.title, content: row.content, is_top: row.is_top });
      },
      payload: (values, row) =>
        row
          ? { title: values.title, content: values.content, is_top: values.is_top }
          : {
              title: values.title,
              content: values.content,
              status: values.status,
              is_top: values.is_top,
            },
    },
    messages: { created: 'notice.created', updated: 'notice.updated', deleted: 'notice.deleted' },
    confirm: {
      title: '删除公告',
      message: (row) => `确认删除公告「${row.title}」？`,
    },
    actions: [
      { act: 'publish', run: (row) => changeStatus(row, 'publish') },
      { act: 'unpublish', run: (row) => changeStatus(row, 'unpublish') },
    ],
  });

  await page.init();
  $('#btn-export')?.addEventListener('click', exportNotices);
}

function rowActions(row) {
  return actionButtons([
    can('system:notice:update') && { act: 'edit', label: '编辑' },
    can('system:notice:publish') &&
      (row.status === 'published' ? { act: 'unpublish', label: '下线' } : { act: 'publish', label: '发布' }),
    can('system:notice:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

async function changeStatus(row, action) {
  const ok = await confirmDialog({
    title: action === 'publish' ? '发布公告' : '下线公告',
    message:
      action === 'publish'
        ? `确认发布「${row.title}」？发布后会推送给订阅了公告事件的通知渠道。`
        : `确认下线「${row.title}」？下线后用户端不再展示。`,
    okText: action === 'publish' ? '发布' : '下线',
  });
  if (!ok) return;
  try {
    await api.post(`/content/notices/${row.id}/${action}`);
    toast(action === 'publish' ? '公告已发布' : '公告已下线', 'success');
    page.table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

/** 导出要带上当前筛选条件，否则「导出的和看到的不一致」 */
async function exportNotices() {
  try {
    await download('/export/notices', 'notices.csv', { params: page.table.params });
    toast(msg('common.exportStarted'), 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}
