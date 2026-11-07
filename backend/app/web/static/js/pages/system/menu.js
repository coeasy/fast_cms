import { $, esc } from '../../core/base.js';
import { toast } from '../../core/dialog.js';
import { fillForm } from '../../core/form.js';
import { msg } from '../../core/messages.js';
import { STATUS_MAP, statusTag } from '../../core/other.js';
import { can } from '../../core/permission.js';
import { api } from '../../core/request.js';
import { actionButtons, createCrudPage, fillSelect, flattenTree, indentLabel } from '../../ui.js';

const TYPE_LABELS = {
  directory: { label: '目录', color: 'blue' },
  menu: { label: '菜单', color: 'green' },
  button: { label: '按钮', color: 'gray' },
};

let tree = [];
let page;

export async function init() {
  page = createCrudPage({
    endpoint: '/system/menus',
    fetch: async () => {
      tree = await api.get('/system/menus');
      const rows = flattenTree(tree);
      return { items: rows, total: rows.length };
    },
    columns: [
      { render: (row) => indentLabel(row.name, row.__depth) },
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
    form: {
      id: '#menu-form',
      modal: 'menu-modal',
      titleId: '#menu-modal-title',
      submitId: '#menu-submit',
      titleCreate: '新建菜单',
      titleEdit: (row) => `编辑菜单 ${row.name}`,
      onCreate: (form, _page, parent) => {
        fillSelect($('#m-parent'), parentOptions(null), { placeholder: '顶级', keep: false });
        if (parent) $('#m-parent').value = String(parent.id);
        // 从「子级」进来的几乎都是要建菜单项，从顶栏进来的通常是新建目录
        form.type.value = parent ? 'menu' : 'directory';
        form.visible.checked = true;
        form.sort.value = '0';
        form.status.value = 'active';
      },
      onEdit: (form, row) => {
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
      },
    },
    messages: { created: 'menu.created', updated: 'menu.updated' },
    confirm: {
      title: '删除菜单',
      message: (row) => `确认删除「${row.name}」？内置菜单或已被引用的菜单会被停用而不是删除。`,
      afterDelete: (result) => {
        if (result?.disabled) toast(msg('menu.referenced'), 'warning');
        else toast(msg('menu.deleted'), 'success');
      },
    },
    actions: [{ act: 'child', run: (row) => page.openCreate(row) }],
  });

  await page.init();
}

function rowActions(row) {
  return actionButtons([
    can('system:menu:create') && { act: 'child', label: '子级' },
    can('system:menu:update') && { act: 'edit', label: '编辑' },
    can('system:menu:delete') && !row.is_builtin && { act: 'delete', label: '删除', danger: true },
  ]);
}

function parentOptions(excludeId) {
  return flattenTree(tree)
    .filter((row) => row.type !== 'button' && row.id !== excludeId)
    .map((row) => ({ value: row.id, label: `${'　'.repeat(row.__depth)}${row.name}` }));
}
