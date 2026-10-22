import {
  $, $$, api, closeModal, confirmDialog, download, esc, openModal, toast,
} from '../../core.js';

let tables = [];
let current = null;
let previewFiles = [];

export async function init() {
  $('#btn-refresh')?.addEventListener('click', () => loadTables());
  $('#table-search')?.addEventListener('input', renderTables);
  $('#table-list').addEventListener('click', (event) => {
    const row = event.target.closest('[data-table]');
    if (!row) return;
    const table = tables.find((item) => item.table === row.dataset.table);
    if (table) selectTable(table);
  });

  $('#btn-preview')?.addEventListener('click', preview);
  $('#btn-download')?.addEventListener('click', downloadZip);
  $('#btn-write')?.addEventListener('click', writeFiles);

  await loadTables();
}

async function loadTables() {
  const host = $('#table-list');
  host.innerHTML = '<li><div class="loading-block"><span class="spinner"></span></div></li>';
  try {
    tables = await api.get('/tool/codegen/tables');
    renderTables();
  } catch (error) {
    host.innerHTML = `<li><div class="table-empty">${esc(error?.message || '加载失败')}</div></li>`;
  }
}

function renderTables() {
  const host = $('#table-list');
  const keyword = String($('#table-search')?.value || '').trim().toLowerCase();
  const items = tables.filter((item) => !keyword || item.table.toLowerCase().includes(keyword));
  $('#table-count').textContent = `${tables.length} 张`;
  if (!items.length) {
    host.innerHTML = '<li><div class="table-empty">没有匹配的数据表</div></li>';
    return;
  }
  host.innerHTML = items
    .map(
      (item) => `
      <li data-table="${esc(item.table)}" class="${current?.table === item.table ? 'active' : ''}">
        <div class="grow" style="min-width:0">
          <div class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(item.table)}</div>
          <div class="tiny muted">${item.column_count} 列${item.is_system ? ' · 系统表' : ''}</div>
        </div>
        ${item.codegen_enabled ? '' : '<span class="tag gray">未启用</span>'}
      </li>`,
    )
    .join('');
}

async function selectTable(table) {
  current = table;
  renderTables();
  $('#detail-title').textContent = `生成配置 · ${table.table}`;
  ['btn-preview', 'btn-download', 'btn-write'].forEach((id) => {
    const button = $(`#${id}`);
    if (button) button.disabled = false;
  });

  const moduleInput = $('#g-module');
  if (!moduleInput.value) {
    const prefix = table.table.split('_')[0];
    moduleInput.value = prefix || 'app';
  }
  const resourceInput = $('#g-resource');
  if (!resourceInput.value) {
    const parts = table.table.split('_');
    resourceInput.value = parts.length > 1 ? parts.slice(1).join('_') : table.table;
  }

  const tbody = $('#column-rows');
  tbody.innerHTML = '<tr><td colspan="5"><div class="loading-block"><span class="spinner"></span></div></td></tr>';
  try {
    const columns = await api.get(`/tool/codegen/tables/${encodeURIComponent(table.table)}/columns`);
    $('#columns-title').textContent = `${table.table} · ${columns.length} 个字段`;
    tbody.innerHTML = columns
      .map(
        (column) => `<tr>
          <td class="mono">${esc(column.name)}</td>
          <td class="mono tiny">${esc(column.type)}</td>
          <td>${column.nullable ? '是' : '否'}</td>
          <td>${column.primary_key ? `<span class="tag blue">主键${column.autoincrement ? ' · 自增' : ''}</span>` : '-'}</td>
          <td class="mono tiny">${esc(column.default ?? '-')}</td>
        </tr>`,
      )
      .join('');
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="5"><div class="table-empty">${esc(error?.message || '字段加载失败')}</div></td></tr>`;
  }
}

function payload() {
  const module = $('#g-module').value.trim();
  const resource = $('#g-resource').value.trim();
  const title = $('#g-title').value.trim();
  if (!module) {
    toast('请填写模块名', 'warning');
    return null;
  }
  if (!current) {
    toast('请选择数据表', 'warning');
    return null;
  }
  return {
    table: current.table,
    module,
    resource: resource || undefined,
    title: title || undefined,
  };
}

async function preview() {
  const data = payload();
  if (!data) return;
  const body = $('#preview-code');
  const tabs = $('#preview-tabs');
  body.textContent = '生成中...';
  tabs.innerHTML = '';
  openModal('preview-modal');
  try {
    const result = await api.post('/tool/codegen/preview', data);
    previewFiles = result?.files || [];
    if (!previewFiles.length) {
      body.textContent = '没有生成任何文件';
      return;
    }
    tabs.innerHTML = previewFiles
      .map((file, index) => `<button class="tab${index === 0 ? ' active' : ''}" data-index="${index}">${esc(file.path)}</button>`)
      .join('');
    body.textContent = previewFiles[0].content;
    tabs.querySelectorAll('.tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.querySelectorAll('.tab').forEach((node) => node.classList.remove('active'));
        tab.classList.add('active');
        body.textContent = previewFiles[Number(tab.dataset.index)].content;
      });
    });
  } catch (error) {
    body.textContent = error?.message || '生成失败';
  }
}

async function downloadZip() {
  const data = payload();
  if (!data) return;
  try {
    await download('/tool/codegen/download', `${data.module}-${data.resource || data.table}.zip`, {
      method: 'POST',
      body: data,
    });
    toast('下载已开始', 'success');
  } catch (error) {
    toast(error?.message || '下载失败', 'error');
  }
}

async function writeFiles() {
  const data = payload();
  if (!data) return;
  const ok = await confirmDialog({
    title: '写入项目',
    message: '生成结果会写入 backend/generated/ 目录，已存在的文件不会被覆盖。确认继续？',
    okText: '写入',
  });
  if (!ok) return;
  try {
    const result = await api.post('/tool/codegen/write', data);
    const written = result?.written || [];
    toast(written.length ? `已写入 ${written.length} 个文件` : '没有新增文件（已存在）', 'success');
  } catch {
    /* 请求层已提示 */
  }
}
