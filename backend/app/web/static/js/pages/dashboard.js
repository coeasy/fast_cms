import {
  $,
  esc
} from '../core/base.js';
import {
  api
} from '../core/request.js';
import {
  formatRecentTime,
  formatTime
} from '../core/time.js';
import {
  formatMs,
  formatNumber
} from '../core/format.js';
import {
  statusTag,
  STATUS_MAP,
  emptyRow,
  renderState
} from '../core/other.js';

let timer = 0;
// 是否成功渲染过一次。首次加载失败必须切到错误态：否则页面只剩「-」和空表，
// 用户会以为系统里本来就没数据，而不是「这次没取到」。
let loaded = false;

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
    loaded = true;
  } catch (error) {
    if (manual) console.warn(error);
    if (!loaded) {
      renderState($('#trend-chart'), { state: 'error', message: '登录趋势加载失败，请刷新重试' });
      renderState($('#recent-logins'), {
        state: 'error',
        message: '最近登录加载失败，请刷新重试',
        colspan: 4,
      });
      renderState($('#recent-jobs'), {
        state: 'error',
        message: '任务记录加载失败，请刷新重试',
        colspan: 4,
      });
    }
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
    if (!node) return;
    // 统计值动辄五位数，不加千分位在卡片里很难一眼读出量级
    node.textContent =
      value === undefined || value === null
        ? '0'
        : typeof value === 'number'
          ? formatNumber(value)
          : String(value);
  });
}

function renderTrend(trend) {
  const host = $('#trend-chart');
  if (!host) return;
  if (!trend.length) {
    renderState(host, { state: 'empty', emptyText: '暂无登录数据' });
    return;
  }
  const max = Math.max(1, ...trend.map((item) => item.success + item.failure));
  const bars = trend
    .map((item) => {
      const success = Math.round((item.success / max) * 168);
      const failure = Math.round((item.failure / max) * 168);
      return `<div class="group">
        <span class="bar" style="height:${success <= 0 ? 2 : Math.max(4, success)}px;background:var(--success)" title="成功 ${esc(item.success)}"></span>
        <span class="bar" style="height:${failure <= 0 ? 2 : Math.max(4, failure)}px;background:var(--danger)" title="失败 ${esc(item.failure)}"></span>
      </div>`;
    })
    .join('');
  const labels = trend
    .map((item) => `<div class="lbl">${esc(String(item.date).slice(5))}<b>${esc(item.success + item.failure)}</b></div>`)
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
