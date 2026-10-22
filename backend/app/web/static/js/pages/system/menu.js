import {
  $, api, can, closeModal, confirmDialog, esc, fillForm, openModal, resetForm,
  serializeForm, setLoading, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons, fillSelect, flattenTree, indentLabel } from '../../ui.js';

const TYPE_LABELS = {
  directory: { label: '目录', color: 'blue' },
  menu: { label: '菜单', color: 'green' },
  button: { label: '按钮', color: 'gray' },
};

let table;
let tree = [];
let editing = null;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    fetch: async () => {
      tree = await api.get('/system/menus');
      const rows = flattenTree(tree);
      return { items: rows, total: rows.length };
    },
    columns: [
      {
        render: (row) => indentLabel(row.name, row.__depth),
      },
      {
        render: (row) => {
          const type = TYPE_LABELS[row.type] || { label: row.type, color: 'gray' };
          return `<span class="tag ${type.color}">${esc(type.label)}</span>`;
        },
      },
      { render: (row) => (row.path ? `<span class="mono tiny">${esc(row.path)}</span>` : '-') },
      { render: (row) => (row.permission_code ? `<span class="mono tiny">${esc(row.permission_code)}</span>` : '-') },
      { render: (row) => esc(row.icon || '-') },
      { key: 'sort', className: 'num' },
      { render: (row) => (row.visible ? '<span class="tag green">显示</span>' : '<span class="tag gray">隐藏</span>') },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('system:menu:create') && { act: 'child', label: '子级' },
    can('system:menu:update') && { act: 'edit', label: '编辑' },
    can('system:menu:delete') && !row.is_builtin && { act: 'delete', label: '删除', danger: true },
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

  $('#menu-form').addEventListener('submit', submit);
}

function parentOptions(excludeId) {
  return flattenTree(tree)
    .filter((row) => row.type !== 'button' && row.id !== excludeId)
    .map((row) => ({ value: row.id, label: `${'　'.repeat(row.__depth)}${row.name}` }));
}

function openCreate(parent) {
  editing = null;
  const form = $('#menu-form');
  resetForm(form);
  $('#menu-modal-title').textContent = '新建菜单';
  fillSelect($('#m-parent'), parentOptions(null), { placeholder: '顶级', keep: false });
  if (parent) $('#m-parent').value = String(parent.id);
  form.type.value = parent ? 'menu' : 'directory';
  form.visible.checked = true;
  form.sort.value = '0';
  form.status.value = 'active';
  openModal('menu-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#menu-form');
  resetForm(form);
  $('#menu-modal-title').textContent = `编辑菜单 ${row.name}`;
  fillSelect($('#m-parent'), parentOptions(row.id), { placeholder: '顶级', keep: false });
  fillForm(form, {
    parent_id: row.parent_id,
    type: row.type,
    name: row.name,
    path: row.path,
    permission_code: row.permission_code,
    icon: row.icon,
    sort: row.sort,
    visible: row.visible,
    status: row.status,
    component_key: row.component_key,
    remark: row.remark,
  });
  openModal('menu-modal');
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#menu-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/system/menus/${editing.id}`, values);
      toast('菜单已更新', 'success');
    } else {
      await api.post('/system/menus', values);
      toast('菜单创建成功', 'success');
    }
    closeModal('menu-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除菜单',
    message: `确认删除「${row.name}」？内置菜单或已被引用的菜单会被停用而不是删除。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    const result = await api.del(`/system/menus/${row.id}`);
    if (result?.disabled) toast('该菜单已被引用，已改为停用', 'warning');
    else toast('菜单已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
