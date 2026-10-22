import { $, api, esc, statusTag, toast } from '../../core.js';
import { tag } from '../../ui.js';

const SCOPE_MAP = { all: '全部', api: '接口', admin: '后台', portal: '前台' };
// 归属地为空时的两种原因要说清楚：库没加载是配置问题，IP 无法解析是输入问题
const MAX_HISTORY = 8;
// 查询历史落到 localStorage：刷新页面后运营还要复查同一批 IP，内存态一刷新就丢
const HISTORY_KEY = 'ip-locate.history';
let history = loadHistory();

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((ip) => typeof ip === 'string' && ip).slice(0, MAX_HISTORY) : [];
  } catch {
    return [];
  }
}

function saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  } catch {
    // 隐私模式等场景写不进去就算了，历史只是便利功能，不能影响查询本身
  }
}

export async function init() {
  $('#ip-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const ip = $('#f-ip').value.trim();
    if (!ip) {
      toast('请先输入 IP 地址', 'warning');
      return;
    }
    runQuery(ip);
  });
  $('#btn-self')?.addEventListener('click', runSelfQuery);
  $('#btn-refresh')?.addEventListener('click', resetPage);
  renderHistory();
}

function resetPage() {
  $('#f-ip').value = '';
  $('#locate-result').innerHTML =
    '<div class="table-empty">输入 IP 后点击「查询」，或点右上角「查询我的 IP」看当前出口地址。</div>';
}

async function runQuery(ip) {
  const box = $('#locate-result');
  box.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  try {
    const result = await api.get(`/security/ip-locate/${encodeURIComponent(ip)}`);
    renderResult(result);
    remember(ip);
  } catch (error) {
    box.innerHTML = `<div class="table-empty">${esc(error?.message || '查询失败')}</div>`;
  }
}

async function runSelfQuery() {
  const box = $('#locate-result');
  box.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  try {
    const result = await api.get('/security/ip-locate/current');
    $('#f-ip').value = result.ip;
    renderResult(result);
    remember(result.ip);
  } catch (error) {
    box.innerHTML = `<div class="table-empty">${esc(error?.message || '查询失败')}</div>`;
  }
}

function renderResult(result) {
  const location = result.location;
  const rules = result.rules || {};
  const box = $('#locate-result');

  const place = location
    ? `<div class="locate-head">
         <span class="locate-ip mono">${esc(result.ip)}</span>
         <span class="locate-label">${esc(location.label)}</span>
         ${location.domestic ? '' : tag('境外', 'orange')}
       </div>`
    : `<div class="locate-head">
         <span class="locate-ip mono">${esc(result.ip)}</span>
         <span class="locate-label muted">归属地不可用</span>
       </div>
       <div class="help">离线库未加载或该地址无法解析，规则判定不受影响；原因见下方「数据库状态」。</div>`;

  const detail = location
    ? `<div class="desc-grid">
        ${desc('国家 / 地区', location.country || '-')}
        ${desc('省份 / 州', location.province || '-')}
        ${desc('城市', location.city || '-')}
        ${desc('运营商', location.isp || '-')}
        ${desc('国家码', location.country_code || '-')}
        ${desc('原始数据', location.raw || '-', true)}
      </div>`
    : '';

  box.innerHTML = `
    ${place}
    ${detail}
    <div class="import-summary">
      ${tag(rules.blocked ? '该 IP 会被拦截' : '该 IP 未被拦截', rules.blocked ? 'red' : 'green')}
      ${tag(`规范形式 ${rules.canonical || result.ip}`, 'blue')}
    </div>
    <div class="import-failures">
      <table>
        <thead><tr><th>作用域</th><th>结论</th><th>命中规则</th><th>类型</th><th>原因</th></tr></thead>
        <tbody>${renderScopeRows(rules.scopes || {})}</tbody>
      </table>
    </div>`;
  renderLibrary(result.library);
}

/**
 * 规则可以只作用于 api / admin / portal 之一，所以按作用域分别判定，
 * 只查 all 会漏掉「接口被封但页面能开」这种最常见的情况。
 */
function renderScopeRows(scopes) {
  const rows = Object.entries(scopes).map(([name, item]) => {
    const rule = item.rule;
    return `<tr>
      <td>${esc(SCOPE_MAP[name] || name)}</td>
      <td>${statusTag(item.blocked ? 'blocked' : 'pass', { blocked: ['已拦截', 'red'], pass: ['放行', 'green'] })}</td>
      <td class="mono">${esc(rule?.target || '-')}</td>
      <td>${esc(rule ? (rule.kind === 'allow' ? '放行' : '封禁') : '-')}</td>
      <td>${esc(rule?.reason || '-')}</td>
    </tr>`;
  });
  return rows.join('') || '<tr><td colspan="5" class="muted">无数据</td></tr>';
}

function renderLibrary(library) {
  const box = $('#library-state');
  if (!library) return;
  const versions = (library.versions || [])
    .map((item) => `${item.version} ${item.loaded ? tag('已加载', 'green') : tag('未加载', 'red')} ${(item.size_bytes / 1048576).toFixed(1)} MB`)
    .join(' ');
  box.innerHTML = `
    ${desc('功能开关', library.enabled ? '已启用' : '已关闭')}
    ${desc('缓存策略', library.policy)}
    ${desc('数据目录', library.data_dir, true)}
    ${desc('数据文件', versions || '-', true)}`;
}

function remember(ip) {
  const index = history.indexOf(ip);
  if (index >= 0) history.splice(index, 1);
  history.unshift(ip);
  if (history.length > MAX_HISTORY) history.pop();
  saveHistory();
  renderHistory();
}

function clearHistory() {
  history = [];
  saveHistory();
  renderHistory();
}

function renderHistory() {
  const box = $('#history-list');
  if (!box) return;
  if (!history.length) {
    box.innerHTML = '<span class="muted">暂无查询记录</span>';
    return;
  }
  box.innerHTML = `${history
    .map((ip) => `<button class="btn btn-link mono" data-ip="${esc(ip)}">${esc(ip)}</button>`)
    .join('')}<button class="btn btn-link muted" data-action="clear-history">清空</button>`;
  box.querySelectorAll('[data-ip]').forEach((button) => {
    button.addEventListener('click', () => runQuery(button.dataset.ip));
  });
  box.querySelector('[data-action="clear-history"]')?.addEventListener('click', clearHistory);
}

function desc(key, value, full = false) {
  return `<div class="desc-item"${full ? ' style="grid-column:1/-1"' : ''}><div class="k">${esc(key)}</div><div class="v">${esc(value)}</div></div>`;
}
