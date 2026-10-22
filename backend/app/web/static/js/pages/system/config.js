import {
  $, api, can, closeModal, confirmDialog, esc, fillForm, openModal, resetForm,
  serializeForm, setLoading, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons, fillSelect } from '../../ui.js';

let table;
let rows = [];
let editing = null;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    fetch: async (params) => {
      rows = await api.get('/system/configs', params);
      return { items: rows, total: rows.length };
    },
    onLoaded: () => {
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
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('system:config:update') && { act: 'edit', label: '编辑' },
    can('system:config:delete') && !row.built_in && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', () => openCreate());

  $('table.data').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'edit') openEdit(row);
    else if (button.dataset.act === 'delete') remove(row);
  });

  $('#config-form').addEventListener('submit', submit);
}

function openCreate() {
  editing = null;
  const form = $('#config-form');
  resetForm(form);
  $('#config-modal-title').textContent = '新建参数';
  $('#c-key').disabled = false;
  $('#c-type').disabled = false;
  form.type.value = 'string';
  form.group.value = 'basic';
  openModal('config-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#config-form');
  resetForm(form);
  $('#config-modal-title').textContent = `编辑参数 ${row.key}`;
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
  openModal('config-modal');
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  if (values.type === 'json' && values.value.trim()) {
    try {
      JSON.parse(values.value);
    } catch {
      toast('JSON 类型的值必须是合法 JSON', 'error');
      return;
    }
  }
  const button = $('#config-submit');
  setLoading(button, true);
  try {
    if (editing) {
      delete values.key;
      if (editing.built_in) delete values.type;
      await api.put(`/system/configs/${editing.id}`, values);
      toast('参数已更新', 'success');
    } else {
      await api.post('/system/configs', values);
      toast('参数创建成功', 'success');
    }
    closeModal('config-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除参数',
    message: `确认删除参数 ${row.key}？依赖该参数的功能可能回落到默认值。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/configs/${row.id}`);
    toast('参数已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
