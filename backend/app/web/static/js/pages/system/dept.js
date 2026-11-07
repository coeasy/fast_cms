import { $, esc } from '../../core/base.js';
import { fillForm } from '../../core/form.js';
import { STATUS_MAP, statusTag } from '../../core/other.js';
import { can } from '../../core/permission.js';
import { api } from '../../core/request.js';
import { actionButtons, createCrudPage, fillSelect, flattenTree, indentLabel } from '../../ui.js';

let tree = [];
let page;

export async function init() {
  page = createCrudPage({
    endpoint: '/system/depts',
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
    form: {
      id: '#dept-form',
      modal: 'dept-modal',
      titleId: '#dept-modal-title',
      submitId: '#dept-submit',
      titleCreate: '新建部门',
      titleEdit: (row) => `编辑部门 ${row.name}`,
      onCreate: (form, _page, parent) => {
        fillSelect($('#d-parent'), parentOptions(null), { placeholder: '顶级', keep: false });
        if (parent) $('#d-parent').value = String(parent.id);
        form.sort.value = '0';
        form.status.value = 'active';
      },
      onEdit: (form, row) => {
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
      },
    },
    messages: { created: 'dept.created', updated: 'dept.updated', deleted: 'dept.deleted' },
    confirm: {
      title: '删除部门',
      message: (row) => `确认删除部门「${row.name}」？存在下级部门或用户时无法删除。`,
    },
    actions: [{ act: 'child', run: (row) => page.openCreate(row) }],
  });

  await page.init();
}

function rowActions(row) {
  return actionButtons([
    can('system:dept:create') && { act: 'child', label: '子部门' },
    can('system:dept:update') && { act: 'edit', label: '编辑' },
    can('system:dept:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

function parentOptions(excludeId) {
  return flattenTree(tree)
    .filter((row) => row.id !== excludeId)
    .map((row) => ({ value: row.id, label: `${'　'.repeat(row.__depth)}${row.name}` }));
}
