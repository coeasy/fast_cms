import {
  $, api, can, closeModal, confirmDialog, esc, fillForm, openModal, resetForm,
  serializeForm, setLoading, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons, fillSelect, flattenTree, indentLabel } from '../../ui.js';

let table;
let tree = [];
let editing = null;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    fetch: async () => {
      tree = await api.get('/system/depts');
      const rows = flattenTree(tree);
      return { items: rows, total: rows.length };
    },
    columns: [
      { render: (row) => indentLabel(row.name, row.__depth) },
      { key: 'leader', render: (row) => esc(row.leader || '-') },
      { key: 'phone', render: (row) => esc(row.phone || '-') },
      { key: 'sort', className: 'num' },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { key: 'remark', render: (row) => esc(row.remark || '-') },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('system:dept:create') && { act: 'child', label: '子部门' },
    can('system:dept:update') && { act: 'edit', label: '编辑' },
    can('system:dept:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', () => openCreate(null));

  $('table.data').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    const act = button.dataset.act;
    if (act === 'edit') openEdit(row);
    else if (act === 'child') openCreate(row);
    else if (act === 'delete') remove(row);
  });

  $('#dept-form').addEventListener('submit', submit);
}

function parentOptions(excludeId) {
  return flattenTree(tree)
    .filter((row) => row.id !== excludeId)
    .map((row) => ({ value: row.id, label: `${'　'.repeat(row.__depth)}${row.name}` }));
}

function openCreate(parent) {
  editing = null;
  const form = $('#dept-form');
  resetForm(form);
  $('#dept-modal-title').textContent = '新建部门';
  fillSelect($('#d-parent'), parentOptions(null), { placeholder: '顶级', keep: false });
  if (parent) $('#d-parent').value = String(parent.id);
  form.sort.value = '0';
  form.status.value = 'active';
  openModal('dept-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#dept-form');
  resetForm(form);
  $('#dept-modal-title').textContent = `编辑部门 ${row.name}`;
  fillSelect($('#d-parent'), parentOptions(row.id), { placeholder: '顶级', keep: false });
  fillForm(form, {
    parent_id: row.parent_id,
    name: row.name,
    leader: row.leader,
    phone: row.phone,
    sort: row.sort,
    status: row.status,
    remark: row.remark,
  });
  openModal('dept-modal');
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#dept-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/system/depts/${editing.id}`, values);
      toast('部门已更新', 'success');
    } else {
      await api.post('/system/depts', values);
      toast('部门创建成功', 'success');
    }
    closeModal('dept-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除部门',
    message: `确认删除部门「${row.name}」？存在下级部门或用户时无法删除。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/depts/${row.id}`);
    toast('部门已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
