import {
  $, api, closeModal, confirmDialog, esc, formatTime, openModal,
  serializeForm, setLoading, toast,
} from '../../core.js';
import { DataTable, actionButtons, tag } from '../../ui.js';

const KIND_MAP = { block: ['封禁', 'red'], allow: ['放行', 'green'] };
const SCOPE_MAP = { all: '全部', api: '接口', admin: '后台', portal: '前台' };
const STATUS_MAP = { active: ['生效中', 'green'], disabled: ['已停用', 'gray'], expired: ['已过期', 'gray'] };

let table;
let editingId = 0;

export async function init() {
  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/security/ip-rules', params),
    columns: [
      {
        render: (row) =>
          `<span class="mono">${esc(row.target)}</span>` +
          (row.auto ? ' ' + tag('自动', 'orange') : ''),
      },
      { render: (row) => statusTag(row.kind, KIND_MAP) },
      { render: (row) => esc(SCOPE_MAP[row.scope] || row.scope) },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { render: (row) => `<span class="muted" title="${esc(row.reason || '')}">${esc(row.reason || '-')}</span>` },
      { render: (row) => `<span class="num">${row.hit_count || 0}</span>` },
      { render: (row) => (row.expires_at ? esc(formatTime(row.expires_at)) : '<span class="muted">永久</span>') },
      { render: (row) => esc(row.created_by_name || '-') },
      {
        className: 'actions',
        render: (row) =>
          actionButtons([
            { act: 'edit', label: '编辑' },
            { act: 'toggle', label: row.status === 'active' ? '停用' : '启用' },
            { act: 'block-now', label: '封禁此段' },
            { act: 'delete', label: '删除', danger: true },
          ]),
      },
    ],
  });

  await table.load(1);

  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(query(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', () => openRuleModal());
  $('#btn-block-now')?.addEventListener('click', () => openModal('block-modal'));
  $('#btn-lookup')?.addEventListener('click', () => openModal('lookup-modal'));
  $('#rule-form').addEventListener('submit', submitRule);
  $('#block-form').addEventListener('submit', submitBlockNow);
  $('#lookup-submit')?.addEventListener('click', runLookup);

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'edit') await openRuleEdit(row);
    if (button.dataset.act === 'toggle') await toggleRule(row);
    if (button.dataset.act === 'block-now') await blockThisRange(row);
    if (button.dataset.act === 'delete') await removeRule(row);
  });
}

function query(form) {
  const values = serializeForm(form);
  return {
    keyword: values.keyword,
    kind: values.kind,
    scope: values.scope,
    status: values.status,
  };
}

function statusTag(value, map) {
  const [text, color] = map[value] || [value, 'gray'];
  return tag(text, color);
}

function openRuleModal(row = null) {
  editingId = row?.id || 0;
  $('#rule-modal-title').textContent = row ? '编辑 IP 规则' : '新建 IP 规则';
  const form = $('#rule-form');
  form.reset();
  $('#r-target').value = row?.target || '';
  $('#r-target').disabled = Boolean(row);
  $('#r-kind').value = row?.kind || 'block';
  $('#r-scope').value = row?.scope || 'all';
  $('#r-reason').value = row?.reason || '';
  $('#r-expires').value = row?.expires_at ? toLocalInput(row.expires_at) : '';
  openModal('rule-modal');
}

/**
 * 编辑前按 id 重新取一次详情。
 * 表格里的行是上一次加载的快照，期间别人可能已经改过这条规则；
 * 直接在旧数据上提交会把对方的修改静默覆盖掉。
 */
async function openRuleEdit(row) {
  openRuleModal(row);
  try {
    const fresh = await api.get(`/security/ip-rules/${row.id}`);
    openRuleModal(fresh);
  } catch {
    /* 取不到详情就沿用表格里的快照，请求层已提示 */
  }
}

/** 后端存 UTC，datetime-local 要的是本地墙上时间 */
function toLocalInput(isoValue) {
  const date = new Date(isoValue);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function submitRule(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const values = serializeForm(form);
  if (!values.target) {
    toast('请填写 IP 或网段', 'warning');
    return;
  }
  const payload = {
    target: values.target,
    kind: values.kind,
    scope: values.scope,
    reason: values.reason || null,
    expires_at: values.expires_at ? new Date(values.expires_at).toISOString() : null,
  };
  const button = $('#rule-submit');
  setLoading(button, true);
  try {
    if (editingId) await api.put(`/security/ip-rules/${editingId}`, payload);
    else await api.post('/security/ip-rules', payload);
    toast(editingId ? '规则已更新' : '规则已创建', 'success');
    closeModal('rule-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function toggleRule(row) {
  const next = row.status === 'active' ? 'disabled' : 'active';
  try {
    await api.post(`/security/ip-rules/${row.id}/status?status=${next}`);
    toast(next === 'active' ? '规则已启用' : '规则已停用', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function blockThisRange(row) {
  const ok = await confirmDialog({
    title: '封禁该网段',
    message: `将把 ${row.target} 按当前规则的作用范围直接封禁，操作不可撤销。`,
    okText: '封禁',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.post('/security/ip-rules/block-now', {
      ip: row.canonical_target || row.target,
      scope: row.scope,
      reason: `由规则 #${row.id} 一键封禁`,
      minutes: 0,
    });
    toast('已封禁', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function removeRule(row) {
  const ok = await confirmDialog({
    title: '删除规则',
    message: `将删除针对 ${row.target} 的${row.kind === 'block' ? '封禁' : '放行'}规则。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/security/ip-rules/${row.id}`);
    toast('规则已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function submitBlockNow(event) {
  event.preventDefault();
  const values = serializeForm(event.currentTarget);
  if (!values.ip) {
    toast('请填写 IP 地址', 'warning');
    return;
  }
  const button = $('#block-submit');
  setLoading(button, true);
  try {
    await api.post('/security/ip-rules/block-now', {
      ip: values.ip,
      scope: values.scope,
      reason: values.reason || null,
      minutes: Number(values.minutes || 0),
    });
    toast('已封禁该 IP', 'success');
    closeModal('block-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function runLookup() {
  const ip = $('#l-ip').value.trim();
  const box = $('#lookup-result');
  if (!ip) {
    box.textContent = '请先输入 IP。';
    return;
  }
  box.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  try {
    const result = await api.get(`/security/ip-rules/lookup/${encodeURIComponent(ip)}`);
    box.innerHTML = renderLookup(result);
  } catch (error) {
    box.innerHTML = `<div class="table-empty">${esc(error?.message || '查询失败')}</div>`;
  }
}

/**
 * 后端按 api / admin / portal 分别判定——规则可以只作用于其中一个作用域，
 * 所以「接口被封但页面能开」是常见情况，必须分开展示而不是给一个总结论。
 */
function renderLookup(result) {
  const scopes = result.scopes || {};
  const rows = Object.entries(scopes)
    .map(([name, item]) => {
      const rule = item.rule;
      return `<tr>
        <td>${esc(SCOPE_MAP[name] || name)}</td>
        <td>${item.blocked ? tag('已拦截', 'red') : tag('放行', 'green')}</td>
        <td class="mono">${esc(rule?.target || '-')}</td>
        <td>${esc(rule?.kind === 'allow' ? '放行' : rule ? '封禁' : '-')}</td>
        <td>${esc(rule?.reason || '-')}</td>
      </tr>`;
    })
    .join('');

  return `
    <div class="import-summary">
      ${tag(result.blocked ? '该 IP 会被拦截' : '该 IP 未被拦截', result.blocked ? 'red' : 'green')}
      ${tag(`规范形式 ${result.canonical || result.ip}`, 'blue')}
    </div>
    <div class="import-failures">
      <table>
        <thead><tr><th>作用域</th><th>结论</th><th>命中规则</th><th>类型</th><th>原因</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="muted">无数据</td></tr>'}</tbody>
      </table>
    </div>`;
}
