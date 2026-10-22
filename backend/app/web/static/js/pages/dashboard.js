import { $, api, esc, formatMs, formatRecentTime, formatTime, statusTag, STATUS_MAP } from '../core.js';
import { emptyRow } from '../core.js';

let timer = 0;

export async function init() {
  $('#refresh')?.addEventListener('click', () => load(true));
  await load();
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') load(false);
  }, 60_000);
  window.addEventListener('beforeunload', () => window.clearInterval(timer));
}

async function load(manual = false) {
  const button = $('#refresh');
  if (manual) button?.classList.add('loading');
  try {
    const data = await api.get('/dashboard/summary', null, { silent: !manual });
    renderStats(data?.stats || {});
    renderTrend(data?.login_trend || []);
    renderLogins(data?.recent_logins || []);
    renderJobs(data?.recent_job_logs || []);
  } catch (error) {
    if (manual) console.warn(error);
  } finally {
    button?.classList.remove('loading');
  }
}

function renderStats(stats) {
  const map = {
    total_users: stats.total_users,
    active_users: stats.active_users,
    online_users: stats.online_users,
    dept_role: `${stats.total_depts ?? 0} / ${stats.total_roles ?? 0}`,
    published_notices: stats.published_notices,
    enabled_jobs: stats.enabled_jobs,
    today_login_success: stats.today_login_success,
    today_login_failure: stats.today_login_failure,
  };
  Object.entries(map).forEach(([key, value]) => {
    const node = document.querySelector(`[data-stat="${key}"]`);
    if (node) node.textContent = value === undefined || value === null ? '0' : String(value);
  });
}

function renderTrend(trend) {
  const host = $('#trend-chart');
  if (!host) return;
  if (!trend.length) {
    host.innerHTML = '<div class="table-empty"><div class="big">∅</div>暂无登录数据</div>';
    return;
  }
  const max = Math.max(1, ...trend.map((item) => item.success + item.failure));
  const bars = trend
    .map((item) => {
      const success = Math.round((item.success / max) * 168);
      const failure = Math.round((item.failure / max) * 168);
      return `<div class="group">
        <span class="bar" style="height:${success <= 0 ? 2 : Math.max(4, success)}px;background:var(--success)" title="成功 ${item.success}"></span>
        <span class="bar" style="height:${failure <= 0 ? 2 : Math.max(4, failure)}px;background:var(--danger)" title="失败 ${item.failure}"></span>
      </div>`;
    })
    .join('');
  const labels = trend
    .map((item) => `<div class="lbl">${esc(String(item.date).slice(5))}<b>${item.success + item.failure}</b></div>`)
    .join('');
  host.innerHTML = `
    <div style="position:relative">
      <div class="chart-grid"><i></i><i></i><i></i><i></i></div>
      <div class="chart-bars">${bars}</div>
    </div>
    <div class="chart-labels">${labels}</div>`;
}

function renderLogins(items) {
  const tbody = $('#recent-logins');
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = emptyRow(4, '暂无记录');
    return;
  }
  tbody.innerHTML = items
    .map(
      (item) => `<tr>
        <td class="nowrap">${esc(item.username)}</td>
        <td>${statusTag(item.status, STATUS_MAP)}</td>
        <td class="mono tiny">${esc(item.ip || '-')}</td>
        <td class="nowrap">${esc(formatRecentTime(item.created_at))}</td>
      </tr>`,
    )
    .join('');
}

function renderJobs(items) {
  const tbody = $('#recent-jobs');
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = emptyRow(4, '暂无任务执行记录');
    return;
  }
  tbody.innerHTML = items
    .map(
      (item) => `<tr>
        <td>${esc(item.job_name || item.job_code || '-')}</td>
        <td>${statusTag(item.status, STATUS_MAP)}</td>
        <td class="num">${esc(formatMs(item.duration_ms))}</td>
        <td class="nowrap">${esc(formatTime(item.started_at))}</td>
      </tr>`,
    )
    .join('');
}
