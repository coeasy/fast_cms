import {
  $, $$, api, can, closeModal, confirmDialog, download, esc, fillForm, openModal, resetForm,
  serializeForm, setLoading, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons, fillMultiSelect, selectedValues, treeOptions } from '../../ui.js';

const SCOPE_LABELS = {
  all: { label: '全部数据', color: 'blue' },
  dept: { label: '本部门', color: 'cyan' },
  dept_and_below: { label: '本部门及以下', color: 'purple' },
  self: { label: '仅自己', color: 'gray' },
  custom: { label: '指定部门', color: 'orange' },
};

let table;
let editing = null;
let permTarget = null;
let scopeTarget = null;
let registry = { items: [], modules: [] };
let depts = [];

export async function init() {
  table = new DataTable({
    table: $('#filters').closest('.card').querySelector('table.data'),
    fetch: async (params) => {
      const items = await api.get('/system/roles', params);
      return { items, total: items.length };
    },
    columns: [
      { key: 'name', render: (row) => `<span class="strong">${esc(row.name)}</span>` },
      { key: 'code', render: (row) => `<span class="mono">${esc(row.code)}</span>` },
      {
        render: (row) => {
          const meta = SCOPE_LABELS[row.data_scope] || { label: row.data_scope, color: 'gray' };
          return `<span class="tag ${meta.color}">${esc(meta.label)}</span>`;
        },
      },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { key: 'sort', className: 'num' },
      { render: (row) => (row.user_count ?? '-') },
      { render: (row) => (row.is_builtin ? '<span class="tag gold">内置</span>' : '-') },
      { key: 'remark', render: (row) => esc(row.remark || '-') },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('system:role:update') && { act: 'edit', label: '编辑' },
    can('system:role:assign-permission') && { act: 'perm', label: '权限' },
    can('system:role:data-scope') && { act: 'scope', label: '数据范围' },
    can('system:role:delete') && !row.is_builtin && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-export')?.addEventListener('click', exportRoles);
  $('#btn-create')?.addEventListener('click', openCreate);

  const card = $('#filters').closest('.card');
  card.addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    const act = button.dataset.act;
    if (act === 'edit') openEdit(row);
    else if (act === 'perm') openPerm(row);
    else if (act === 'scope') openScope(row);
    else if (act === 'delete') remove(row);
  });

  $('#role-form').addEventListener('submit', submitRole);
  $('#perm-submit').addEventListener('click', submitPerm);
  $('#scope-form').addEventListener('submit', submitScope);
  $('#s-scope').addEventListener('change', (event) => {
    $('#scope-depts-field')?.classList.toggle('hidden', event.target.value !== 'custom');
  });
}

/* -------------------------------- 新建/编辑 -------------------------------- */

function openCreate() {
  editing = null;
  const form = $('#role-form');
  resetForm(form);
  $('#role-modal-title').textContent = '新建角色';
  $('#r-code').disabled = false;
  form.status.value = 'active';
  form.data_scope.value = 'all';
  form.sort.value = '0';
  openModal('role-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#role-form');
  resetForm(form);
  $('#role-modal-title').textContent = `编辑角色 ${row.name}`;
  $('#r-code').disabled = true;
  fillForm(form, {
    name: row.name,
    code: row.code,
    data_scope: row.data_scope,
    status: row.status,
    sort: row.sort,
    remark: row.remark,
  });
  openModal('role-modal');
}

async function submitRole(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#role-submit');
  setLoading(button, true);
  try {
    if (editing) {
      delete values.code;
      await api.put(`/system/roles/${editing.id}`, values);
      toast('角色已更新', 'success');
    } else {
      await api.post('/system/roles', values);
      toast('角色创建成功', 'success');
    }
    closeModal('role-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

/* -------------------------------- 权限分配 -------------------------------- */

async function openPerm(row) {
  permTarget = row;
  $('#perm-modal-title').textContent = `分配权限：${row.name}`;
  const host = $('#perm-tree');
  host.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  openModal('perm-modal');
  try {
    if (!registry.items?.length) {
      registry = await api.get('/system/permissions');
    }
    const assigned = new Set(row.permission_codes || []);
    host.innerHTML = (registry.modules || [])
      .map(
        (group) => `
        <div class="card" style="margin-bottom:10px;box-shadow:none">
          <div class="card-head" style="padding:10px 14px;min-height:auto">
            <label class="checkbox">
              <input type="checkbox" data-module="${esc(group.module)}">
              <span class="strong">${esc(group.module)}</span>
            </label>
            <span class="card-extra tiny muted">${group.items.length} 项</span>
          </div>
          <div class="card-body" style="padding:10px 14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px">
            ${group.items
              .map(
                (item) => `
                <label class="checkbox" title="${esc(item.description || item.code)}">
                  <input type="checkbox" data-perm-code="${esc(item.code)}" ${assigned.has(item.code) ? 'checked' : ''}>
                  <span>${esc(item.name)}<span class="tiny muted" style="margin-left:6px">${esc(item.code)}</span></span>
                </label>`,
              )
              .join('')}
          </div>
        </div>`,
      )
      .join('');

    host.querySelectorAll('[data-module]').forEach((moduleBox) => {
      const group = moduleBox.closest('.card');
      const boxes = $$('[data-perm-code]', group);
      const sync = () => {
        const checked = boxes.filter((box) => box.checked).length;
        moduleBox.checked = checked === boxes.length && boxes.length > 0;
        moduleBox.indeterminate = checked > 0 && checked < boxes.length;
        updateCount();
      };
      moduleBox.addEventListener('change', () => {
        boxes.forEach((box) => {
          box.checked = moduleBox.checked;
        });
        updateCount();
      });
      boxes.forEach((box) => box.addEventListener('change', sync));
      sync();
    });
    updateCount();
  } catch (error) {
    host.innerHTML = `<div class="table-empty">${esc(error?.message || '权限清单加载失败')}</div>`;
  }
}

function updateCount() {
  const total = $$('#perm-tree [data-perm-code]').filter((box) => box.checked).length;
  const node = $('#perm-count');
  if (node) node.textContent = String(total);
}

async function submitPerm() {
  const codes = $$('#perm-tree [data-perm-code]')
    .filter((box) => box.checked)
    .map((box) => box.dataset.permCode);
  const button = $('#perm-submit');
  setLoading(button, true);
  try {
    await api.put(`/system/roles/${permTarget.id}/permissions`, { permission_codes: codes });
    toast('权限已保存', 'success');
    closeModal('perm-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

/* -------------------------------- 数据范围 -------------------------------- */

async function openScope(row) {
  scopeTarget = row;
  const form = $('#scope-form');
  resetForm(form);
  $('#scope-modal-title').textContent = `数据范围：${row.name}`;
  form.data_scope.value = row.data_scope || 'all';
  if (!depts.length) {
    try {
      depts = await api.get('/system/depts/options', null, { silent: true });
    } catch {
      depts = [];
    }
  }
  fillMultiSelect($('#scope-depts'), treeOptions(depts));
  const select = $('#scope-depts select');
  const ids = new Set((row.dept_ids || []).map(String));
  Array.from(select.options).forEach((option) => {
    option.selected = ids.has(option.value);
  });
  select.dispatchEvent(new Event('change'));
  $('#scope-depts-field')?.classList.toggle('hidden', row.data_scope !== 'custom');
  openModal('scope-modal');
}

async function submitScope(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const scope = form.data_scope.value;
  const payload = {
    data_scope: scope,
    dept_ids: scope === 'custom' ? selectedValues($('#scope-depts select')) : [],
  };
  const button = $('#scope-submit');
  setLoading(button, true);
  try {
    await api.put(`/system/roles/${scopeTarget.id}/data-scope`, payload);
    toast('数据范围已更新', 'success');
    closeModal('scope-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除角色',
    message: `确认删除角色 ${row.name}？已分配给用户的角色无法删除。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/roles/${row.id}`);
    toast('角色已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function exportRoles() {
  try {
    await download('/export/roles', 'roles.csv', { params: table.params });
    toast('导出已开始', 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}
