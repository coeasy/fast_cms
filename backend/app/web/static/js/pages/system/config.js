import { $, esc } from '../../core/base.js';
import { fillForm } from '../../core/form.js';
import { can } from '../../core/permission.js';
import { toast } from '../../core/dialog.js';
import { msg } from '../../core/messages.js';
import { actionButtons, createCrudPage, fillSelect } from '../../ui.js';

let page;

export async function init() {
  page = createCrudPage({
    endpoint: '/system/configs',
    filters: '#filters',
    onLoaded: (rows) => {
      // 分组下拉只填一次：每次加载都重填会把用户已选的分组冲掉
      const groups = [...new Set(rows.map((row) => row.group).filter(Boolean))].sort();
      const select = $('#f-group');
      if (select && select.options.length <= 1) {
        fillSelect(select, groups.map((group) => ({ value: group, label: group })), { placeholder: '全部分组' });
      }
    },
    columns: [
      { render: (row) => `<span class="mono">${esc(row.key)}</span>` },
      { key: 'name' },
      { render: (row) => `<span class="mono tiny break">${esc(row.value || '')}</span>` },
      { render: (row) => `<span class="tag gray">${esc(row.type)}</span>` },
      { key: 'group' },
      { render: (row) => (row.is_public ? '<span class="tag green">公开</span>' : '<span class="tag gray">私有</span>') },
      { render: (row) => (row.built_in ? '<span class="tag gold">内置</span>' : '-') },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
    form: {
      id: '#config-form',
      modal: 'config-modal',
      titleId: '#config-modal-title',
      submitId: '#config-submit',
      titleCreate: '新建参数',
      titleEdit: (row) => `编辑参数 ${row.key}`,
      onCreate: (form) => {
        // 参数键与类型决定读取方怎么解析，建好之后不再允许改
        $('#c-key').disabled = false;
        $('#c-type').disabled = false;
        form.type.value = 'string';
        form.group.value = 'basic';
      },
      onEdit: (form, row) => {
        $('#c-key').disabled = true;
        $('#c-type').disabled = Boolean(row.built_in);
        fillForm(form, {
          key: row.key,
          name: row.name,
          value: row.value,
          type: row.type,
          group: row.group,
          is_public: row.is_public,
          remark: row.remark,
        });
      },
      payload: (values, row) => {
        if (values.type === 'json' && values.value.trim()) {
          try {
            JSON.parse(values.value);
          } catch {
            toast(msg('config.jsonInvalid'), 'error');
            return false;
          }
        }
        if (row) {
          delete values.key;
          if (row.built_in) delete values.type;
        }
        return values;
      },
    },
    messages: { created: 'config.created', updated: 'config.updated', deleted: 'config.deleted' },
    confirm: {
      title: '删除参数',
      message: (row) => `确认删除参数 ${row.key}？依赖该参数的功能可能回落到默认值。`,
    },
  });

  await page.init();
}

function rowActions(row) {
  return actionButtons([
    can('system:config:update') && { act: 'edit', label: '编辑' },
    can('system:config:delete') && !row.built_in && { act: 'delete', label: '删除', danger: true },
  ]);
}
