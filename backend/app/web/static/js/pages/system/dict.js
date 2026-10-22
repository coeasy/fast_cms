import {
  $, $$, api, can, closeModal, confirmDialog, download, esc, fillForm, openModal, resetForm,
  serializeForm, setLoading, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import { actionButtons, emptyState } from '../../ui.js';

let types = [];
let currentType = null;
let items = [];
let editingType = null;
let editingItem = null;

export async function init() {
  bind();
  await loadTypes();
}

function bind() {
  $('#btn-refresh')?.addEventListener('click', () => loadTypes(currentType?.id));
  $('#btn-export')?.addEventListener('click', exportDicts);
  $('#btn-create-type')?.addEventListener('click', () => openType(null));
  $('#btn-create-item')?.addEventListener('click', () => openItem(null));
  $('#btn-effective')?.addEventListener('click', showEffective);

  $('#type-list').addEventListener('click', async (event) => {
    const action = event.target.closest('[data-act]');
    const row = event.target.closest('[data-id]');
    if (action && row) {
      event.stopPropagation();
      const type = types.find((item) => item.id === Number(row.dataset.id));
      if (!type) return;
      if (action.dataset.act === 'edit') openType(type);
      else if (action.dataset.act === 'delete') removeType(type);
      return;
    }
    if (row) {
      const type = types.find((item) => item.id === Number(row.dataset.id));
      if (type) await selectType(type);
    }
  });

  $('#item-rows').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const item = items.find((entry) => entry.id === Number(button.closest('tr')?.dataset.id));
    if (!item) return;
    if (button.dataset.act === 'edit') openItem(item);
    else if (button.dataset.act === 'delete') removeItem(item);
  });

  $('#type-form').addEventListener('submit', submitType);
  $('#item-form').addEventListener('submit', submitItem);
}

/* -------------------------------- 字典类型 -------------------------------- */

async function loadTypes(selectId) {
  const host = $('#type-list');
  host.innerHTML = '<li><div class="loading-block"><span class="spinner"></span></div></li>';
  try {
    types = await api.get('/system/dict-types');
    renderTypes();
    const target = selectId ? types.find((item) => item.id === selectId) : types[0];
    if (target) await selectType(target);
    else {
      currentType = null;
      items = [];
      renderItems();
    }
  } catch (error) {
    host.innerHTML = `<li><div class="table-empty">${esc(error?.message || '加载失败')}</div></li>`;
  }
}

function renderTypes() {
  const host = $('#type-list');
  const count = $('#type-count');
  if (count) count.textContent = `${types.length} 个`;
  if (!types.length) {
    host.innerHTML = '<li><div class="table-empty">暂无字典类型</div></li>';
    return;
  }
  host.innerHTML = types
    .map(
      (type) => `
      <li data-id="${type.id}" class="${currentType?.id === type.id ? 'active' : ''}">
        <div class="grow" style="min-width:0">
          <div class="strong" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(type.name)}</div>
          <div class="tiny muted mono">${esc(type.code)} · ${type.item_count ?? 0} 项</div>
        </div>
        ${type.status === 'disabled' ? '<span class="tag gray">禁用</span>' : ''}
        <span class="btn-row">
          ${can('system:dict:update') ? '<button class="btn-link" data-act="edit">编辑</button>' : ''}
          ${can('system:dict:delete') ? '<button class="btn-link danger" data-act="delete">删除</button>' : ''}
        </span>
      </li>`,
    )
    .join('');
}

async function selectType(type) {
  currentType = type;
  $$('#type-list li').forEach((node) => node.classList.toggle('active', Number(node.dataset.id) === type.id));
  $('#item-title').textContent = `字典项 · ${type.name}`;
  $('#btn-create-item').disabled = false;
  $('#btn-effective').disabled = false;
  await loadItems();
}

async function loadItems() {
  if (!currentType) return;
  const tbody = $('#item-rows');
  tbody.innerHTML = `<tr><td colspan="7"><div class="loading-block"><span class="spinner"></span></div></td></tr>`;
  try {
    items = await api.get(`/system/dict-types/${currentType.id}/items`);
    renderItems();
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="7">${emptyState(error?.message || '加载失败')}</td></tr>`;
  }
}

function renderItems() {
  const tbody = $('#item-rows');
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="7">${emptyState('暂无字典项')}</td></tr>`;
    return;
  }
  tbody.innerHTML = items
    .map(
      (item) => `<tr data-id="${item.id}">
        <td>${item.tag_color ? `<span class="tag ${esc(item.tag_color)}">${esc(item.label)}</span>` : esc(item.label)}</td>
        <td class="mono">${esc(item.value)}</td>
        <td>${esc(item.tag_color || '-')}</td>
        <td class="num">${item.sort}</td>
        <td>${statusTag(item.status, STATUS_MAP)}</td>
        <td class="muted">${esc(item.remark || '-')}</td>
        <td class="actions">${actionButtons([
          can('system:dict:update') && { act: 'edit', label: '编辑' },
          can('system:dict:delete') && { act: 'delete', label: '删除', danger: true },
        ])}</td>
      </tr>`,
    )
    .join('');
}

function openType(type) {
  editingType = type;
  const form = $('#type-form');
  resetForm(form);
  $('#type-modal-title').textContent = type ? `编辑字典类型 ${type.name}` : '新建字典类型';
  $('#t-code').disabled = Boolean(type);
  if (type) fillForm(form, { name: type.name, code: type.code, status: type.status, remark: type.remark });
  else form.status.value = 'active';
  openModal('type-modal');
}

async function submitType(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#type-submit');
  setLoading(button, true);
  try {
    if (editingType) {
      delete values.code;
      await api.put(`/system/dict-types/${editingType.id}`, values);
      toast('字典类型已更新', 'success');
    } else {
      await api.post('/system/dict-types', values);
      toast('字典类型创建成功', 'success');
    }
    closeModal('type-modal');
    await loadTypes(editingType?.id);
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function removeType(type) {
  const ok = await confirmDialog({
    title: '删除字典类型',
    message: `确认删除「${type.name}」及其全部字典项？`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/dict-types/${type.id}`);
    toast('字典类型已删除', 'success');
    currentType = null;
    await loadTypes();
  } catch {
    /* 请求层已提示 */
  }
}

/* -------------------------------- 字典项 -------------------------------- */

function openItem(item) {
  if (!currentType) return;
  editingItem = item;
  const form = $('#item-form');
  resetForm(form);
  $('#item-modal-title').textContent = item ? `编辑字典项 ${item.label}` : '新建字典项';
  if (item) {
    fillForm(form, {
      label: item.label,
      value: item.value,
      tag_color: item.tag_color,
      sort: item.sort,
      status: item.status,
      remark: item.remark,
    });
  } else {
    form.status.value = 'active';
    form.sort.value = '0';
  }
  openModal('item-modal');
}

async function submitItem(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const button = $('#item-submit');
  setLoading(button, true);
  try {
    if (editingItem) {
      await api.put(`/system/dict-items/${editingItem.id}`, values);
      toast('字典项已更新', 'success');
    } else {
      await api.post(`/system/dict-types/${currentType.id}/items`, values);
      toast('字典项创建成功', 'success');
    }
    closeModal('item-modal');
    await loadItems();
    await refreshTypeCounts();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function refreshTypeCounts() {
  try {
    types = await api.get('/system/dict-types', null, { silent: true });
    renderTypes();
  } catch {
    /* 计数刷新失败不影响主流程 */
  }
}

async function removeItem(item) {
  const ok = await confirmDialog({
    title: '删除字典项',
    message: `确认删除字典项「${item.label}」？`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/dict-items/${item.id}`);
    toast('字典项已删除', 'success');
    await loadItems();
    await refreshTypeCounts();
  } catch {
    /* 请求层已提示 */
  }
}

/* ------------------------------ 消费方视角 ------------------------------ */

async function showEffective() {
  if (!currentType) return;
  const body = $('#effective-body');
  body.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  openModal('effective-modal');
  try {
    const data = await api.get(`/system/dicts/${encodeURIComponent(currentType.code)}`);
    body.innerHTML = `
      <div class="mb-16 muted small">${esc(data.name)}（${esc(data.code)}）· 仅含启用项，带 5 分钟缓存</div>
      ${
        data.items?.length
          ? `<div class="tag-row">${data.items
              .map((item) => `<span class="tag ${esc(item.tag_color || 'gray')}">${esc(item.label)} = ${esc(item.value)}</span>`)
              .join('')}</div>`
          : emptyState('该字典没有启用项')
      }`;
  } catch (error) {
    body.innerHTML = emptyState(error?.message || '加载失败');
  }
}

/** 字典页没有统一的筛选表单，导出就是全量（受后端 10000 行上限约束） */
async function exportDicts() {
  try {
    await download('/export/dicts', 'dicts.csv');
    toast('导出已开始', 'success');
  } catch (error) {
    toast(error?.message || '导出失败', 'error');
  }
}
