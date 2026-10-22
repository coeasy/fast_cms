import { $, api, debounce, esc, formatDate, toast } from '../../core.js';

let registry = { items: [], modules: [] };

export async function init() {
  $('#btn-refresh')?.addEventListener('click', () => load());
  $('#show-deprecated')?.addEventListener('change', () => load());
  $('#perm-search')?.addEventListener('input', debounce(render, 200));
  await load();
}

async function load() {
  const host = $('#perm-modules');
  host.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  try {
    const include = $('#show-deprecated')?.checked || false;
    registry = await api.get('/system/permissions', { include_deprecated: include });
    render();
  } catch (error) {
    host.innerHTML = `<div class="card"><div class="table-empty">${esc(error?.message || '加载失败')}</div></div>`;
    toast(error?.message || '权限清单加载失败', 'error');
  }
}

function render() {
  const host = $('#perm-modules');
  const keyword = String($('#perm-search')?.value || '').trim().toLowerCase();
  const match = (item) =>
    !keyword || item.code.toLowerCase().includes(keyword) || String(item.name).toLowerCase().includes(keyword);

  const modules = (registry.modules || [])
    .map((group) => ({ ...group, items: group.items.filter(match) }))
    .filter((group) => group.items.length);

  const total = modules.reduce((sum, group) => sum + group.items.length, 0);
  const summary = $('#perm-summary');
  if (summary) summary.textContent = `共 ${total} 项权限`;

  if (!modules.length) {
    host.innerHTML = '<div class="card"><div class="table-empty">没有匹配的权限码</div></div>';
    return;
  }

  host.innerHTML = modules
    .map(
      (group) => `
      <div class="card mb-16">
        <div class="card-head">
          <span class="card-title">${esc(group.module)}</span>
          <span class="card-extra tiny muted">${group.items.length} 项</span>
        </div>
        <div class="table-wrap">
          <table class="data compact">
            <thead>
              <tr><th style="width:280px">权限码</th><th style="width:180px">名称</th><th style="width:120px">类型</th><th>说明</th><th style="width:160px">废弃时间</th></tr>
            </thead>
            <tbody>
              ${group.items
                .map(
                  (item) => `<tr>
                    <td><span class="mono">${esc(item.code)}</span></td>
                    <td>${esc(item.name)}</td>
                    <td><span class="tag gray">${esc(item.type || '-')}</span></td>
                    <td class="muted">${esc(item.description || '-')}</td>
                    <td>${item.deprecated_at ? `<span class="tag orange">${esc(formatDate(item.deprecated_at))}</span>` : '-'}</td>
                  </tr>`,
                )
                .join('')}
            </tbody>
          </table>
        </div>
      </div>`,
    )
    .join('');
}
