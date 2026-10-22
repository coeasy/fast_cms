import {
  $, $$, api, can, closeModal, confirmDialog, download, esc, fillForm, formatTime, openModal,
  resetForm, serializeForm, setLoading, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons } from '../../ui.js';

let table;
let editing = null;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/content/notices', params),
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
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('system:notice:update') && { act: 'edit', label: '编辑' },
    can('system:notice:publish') &&
      (row.status === 'published'
        ? { act: 'unpublish', label: '下线' }
        : { act: 'publish', label: '发布' }),
    can('system:notice:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-export')?.addEventListener('click', exportNotices);
  $('#btn-create')?.addEventListener('click', openCreate);

  $('table.data').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    const act = button.dataset.act;
    if (act === 'edit') openEdit(row);
    else if (act === 'publish') changeStatus(row, 'publish');
    else if (act === 'unpublish') changeStatus(row, 'unpublish');
    else if (act === 'delete') remove(row);
  });

  $('#notice-form').addEventListener('submit', submit);
}

function openCreate() {
  editing = null;
  const form = $('#notice-form');
  resetForm(form);
  $('#notice-modal-title').textContent = '新建公告';
  $$('[data-create-only]', form).forEach((node) => node.classList.remove('hidden'));
  form.elements.status.value = 'draft';
  openModal('notice-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#notice-form');
  resetForm(form);
  $('#notice-modal-title').textContent = `编辑公告 ${row.title}`;
  $$('[data-create-only]', form).forEach((node) => node.classList.add('hidden'));
  fillForm(form, { title: row.title, content: row.content, is_top: row.is_top });
  openModal('notice-modal');
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#notice-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/content/notices/${editing.id}`, {
        title: values.title,
        content: values.content,
        is_top: values.is_top,
      });
      toast('公告已更新', 'success');
    } else {
      await api.post('/content/notices', {
        title: values.title,
        content: values.content,
        status: values.status,
        is_top: values.is_top,
      });
      toast('公告已创建', 'success');
    }
    closeModal('notice-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
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
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除公告',
    message: `确认删除公告「${row.title}」？`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/content/notices/${row.id}`);
    toast('公告已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function exportNotices() {
  try {
    await download('/export/notices', 'notices.csv', { params: table.params });
    toast('导出已开始', 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}
