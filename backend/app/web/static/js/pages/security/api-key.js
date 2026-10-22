import {
  $, api, closeModal, confirmDialog, copyText, esc, formatTime, openModal,
  serializeForm, setLoading, toast,
} from '../../core.js';
import { DataTable, actionButtons, tag } from '../../ui.js';

const STATUS_MAP = { active: ['启用', 'green'], disabled: ['停用', 'gray'] };

let table;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/security/api-keys', params),
    columns: [
      { render: (row) => esc(row.name) },
      // 只显示前缀：完整 key 创建后就不再可取，展示前缀足够定位是哪一把
      { render: (row) => `<span class="mono">${esc(row.key_prefix)}…</span>` },
      {
        render: (row) =>
          row.scopes?.length
            ? row.scopes.map((scope) => tag(scope, 'blue')).join(' ')
            // 空 scopes 不再是「继承创建者权限」：Key 的权限就等于它声明的范围，
            // 留空意味着一把什么都不能调的凭证，必须如实告诉用户
            : '<span class="muted">未声明（无任何权限）</span>',
      },
      { render: (row) => statusTag(row.status) },
      {
        render: (row) =>
          row.last_used_at ? esc(formatTime(row.last_used_at)) : '<span class="muted">从未使用</span>',
      },
      { render: (row) => `<span class="mono tiny">${esc(row.last_used_ip || '-')}</span>` },
      { render: (row) => esc(row.created_by_name || '-') },
      {
        className: 'actions',
        render: (row) =>
          actionButtons([
            { act: 'toggle', label: row.status === 'active' ? '停用' : '启用' },
            { act: 'delete', label: '删除', danger: true },
          ]),
      },
    ],
  });

  await table.load(1);

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search({ keyword: serializeForm(event.currentTarget).keyword });
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', () => {
    $('#key-form').reset();
    openModal('key-modal');
  });
  $('#key-form').addEventListener('submit', submitKey);
  $('#secret-copy')?.addEventListener('click', copySecret);

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'toggle') await toggleKey(row);
    if (button.dataset.act === 'delete') await removeKey(row);
  });
}

function statusTag(value) {
  const [text, color] = STATUS_MAP[value] || [value, 'gray'];
  return tag(text, color);
}

async function submitKey(event) {
  event.preventDefault();
  const values = serializeForm(event.currentTarget);
  if (!values.name) {
    toast('请填写名称', 'warning');
    return;
  }
  const button = $('#key-submit');
  setLoading(button, true);
  try {
    const created = await api.post('/security/api-keys', {
      name: values.name,
      scopes: values.scopes || '',
      expires_at: values.expires_at ? new Date(values.expires_at).toISOString() : null,
      remark: values.remark || null,
    });
    closeModal('key-modal');
    showSecret(created.key);
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

/** 明文只在创建响应里出现一次，这里必须原样展示并引导用户立即复制 */
function showSecret(key) {
  $('#secret-value').textContent = key;
  openModal('secret-modal');
  $('#secret-modal').querySelector('[data-modal-close]')?.focus();
}

/**
 * 复制失败（非安全上下文、权限被拒）不能静默——用户会以为已经复制到了。
 * copyText 成功时自己会 toast，这里只补失败分支。
 */
async function copySecret() {
  const key = $('#secret-value')?.textContent || '';
  if (!key) return;
  try {
    await copyText(key);
  } catch {
    toast('复制失败，请手动选择文本复制', 'warning');
  }
}

async function toggleKey(row) {
  const next = row.status === 'active' ? 'disabled' : 'active';
  try {
    await api.post(`/security/api-keys/${row.id}/status?status=${next}`);
    toast(next === 'active' ? '已启用' : '已停用', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function removeKey(row) {
  const ok = await confirmDialog({
    title: '删除 API Key',
    message: `将删除 ${row.name}，使用该 Key 的集成会立即失效。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/security/api-keys/${row.id}`);
    toast('API Key 已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
