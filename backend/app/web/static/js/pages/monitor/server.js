import { $, api, esc, formatBytes, formatTime, formatUptime, toast } from '../../core.js';

export async function init() {
  $('#btn-refresh')?.addEventListener('click', () => load());
  await load();
}

async function load() {
  try {
    const data = await api.get('/monitor/server');
    render(data);
  } catch (error) {
    toast(error?.message || '服务器信息加载失败', 'error');
  }
}

function percentBar(id, value) {
  const bar = $(`#${id}-bar`);
  const percent = Math.max(0, Math.min(100, Number(value) || 0));
  if (bar) {
    bar.style.width = `${percent}%`;
    bar.parentElement.classList.toggle('warn', percent >= 70 && percent < 90);
    bar.parentElement.classList.toggle('danger', percent >= 90);
  }
  const node = $(`#${id}-percent`);
  if (node) node.textContent = `${percent.toFixed(1)}%`;
}

function render(data) {
  if (!data) return;
  const { os = {}, cpu = {}, memory = {}, disk = {}, process = {}, runtime = {} } = data;

  percentBar('cpu', cpu.percent);
  const load = (cpu.load_avg || []).map((value) => (value === null ? '-' : Number(value).toFixed(2))).join(' / ');
  $('#cpu-detail').textContent = `${cpu.cores ?? '-'} 核（物理 ${cpu.physical_cores ?? '-'}）· 负载 ${load || '-'}`;

  percentBar('mem', memory.percent);
  $('#mem-detail').textContent = `${formatBytes(memory.used)} / ${formatBytes(memory.total)} · 可用 ${formatBytes(memory.available)}`;

  percentBar('disk', disk.percent);
  $('#disk-detail').textContent = `${formatBytes(disk.used)} / ${formatBytes(disk.total)} · 剩余 ${formatBytes(disk.free)}`;

  $('#proc-rss').textContent = formatBytes(process.memory_rss);
  $('#proc-detail').textContent = `PID ${process.pid ?? '-'} · ${process.threads ?? '-'} 线程 · 启动于 ${formatTime(process.created_at)}`;

  const osItems = [
    ['主机名', os.hostname],
    ['系统', `${os.system || '-'} ${os.release || ''}`.trim()],
    ['版本', os.version],
    ['架构', os.machine],
    ['Python', os.python],
    ['运行时长', formatUptime(os.uptime_seconds)],
    ['启动时间', formatTime(os.boot_time)],
  ];
  const runtimeItems = [
    ['应用', runtime.app_name],
    ['版本', runtime.app_version],
    ['Git', runtime.git_sha],
    ['数据库', runtime.database],
    ['调度模式', runtime.scheduler_mode],
    ['时区', runtime.timezone],
  ];

  $('#os-desc').innerHTML = osItems
    .map(([key, value]) => `<div class="desc-item"><div class="k">${esc(key)}</div><div class="v">${esc(value || '-')}</div></div>`)
    .join('');
  $('#runtime-desc').innerHTML = runtimeItems
    .map(([key, value]) => `<div class="desc-item"><div class="k">${esc(key)}</div><div class="v">${esc(value || '-')}</div></div>`)
    .join('');
}
