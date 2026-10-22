import {
  $, $$, api, appUrl, can, closeModal, confirmDialog, esc, fillForm, formatTime, openModal,
  resetForm, serializeForm, setLoading, statusTag, STATUS_MAP, toast, utcIsoToWall,
  validateForm, wallToUtcIso,
} from '../../core.js';
import { DataTable, actionButtons, fillSelect } from '../../ui.js';

const JOB_TYPE_LABELS = {
  registry: { label: '注册表', color: 'blue' },
  python: { label: '脚本', color: 'purple' },
};

let table;
let definitions = [];
let templates = [];
let capabilities = { python_enabled: false, can_manage_python: false };
let editing = null;

export async function init() {
  await loadMeta();

  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/schedule/jobs', params),
    columns: [
      { render: (row) => `<span class="strong">${esc(row.name)}</span>${row.is_builtin ? ' <span class="tag gold">内置</span>' : ''}` },
      { render: (row) => `<span class="mono tiny">${esc(row.code)}</span>` },
      {
        render: (row) => {
          const meta = JOB_TYPE_LABELS[row.job_type] || { label: row.job_type, color: 'gray' };
          return `<span class="tag ${meta.color}">${esc(meta.label)}</span>`;
        },
      },
      { render: (row) => `<span class="tiny">${esc(triggerText(row))}</span>` },
      { render: (row) => esc(formatTime(row.next_run_at)) },
      {
        render: (row) =>
          row.last_run_at
            ? `<span class="row" style="gap:6px">${statusTag(row.last_status, STATUS_MAP)}<span class="tiny muted">${esc(formatTime(row.last_run_at))}</span></span>`
            : '-',
      },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

async function loadMeta() {
  try {
    capabilities = await api.get('/schedule/capabilities', null, { silent: true });
  } catch {
    capabilities = { python_enabled: false, can_manage_python: false };
  }
  try {
    definitions = await api.get('/schedule/jobs/definitions', null, { silent: true });
  } catch {
    definitions = [];
  }
  try {
    templates = await api.get('/schedule/templates', { page: 1, page_size: 100 }, { silent: true });
  } catch {
    templates = [];
  }
}

function triggerText(row) {
  if (row.trigger_type === 'cron') return `Cron ${row.cron_expr || '-'}`;
  if (row.trigger_type === 'interval') return `每 ${row.interval_seconds || '-'} 秒`;
  return `单次 ${formatTime(row.run_at)}`;
}

function rowActions(row) {
  return actionButtons([
    can('schedule:job:update') && { act: 'edit', label: '编辑' },
    can('schedule:job:state') && { act: row.status === 'enabled' ? 'pause' : 'resume', label: row.status === 'enabled' ? '暂停' : '恢复' },
    can('schedule:job:trigger') && { act: 'trigger', label: '执行' },
    { act: 'runs', label: '下次' },
    { act: 'logs', label: '日志' },
    can('schedule:job:delete') && !row.is_builtin && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', () => openCreate());

  $('table.data').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    const act = button.dataset.act;
    if (act === 'edit') openEdit(row);
    else if (act === 'pause') setState(row, 'pause');
    else if (act === 'resume') setState(row, 'resume');
    else if (act === 'trigger') trigger(row);
    else if (act === 'runs') showRuns(row);
    else if (act === 'logs') window.location.href = `${appUrl('schedule/log')}?job_id=${row.id}`;
    else if (act === 'delete') remove(row);
  });

  const form = $('#job-form');
  form.addEventListener('submit', submit);
  form.elements.job_type.addEventListener('change', () => syncSections());
  form.elements.trigger_type.addEventListener('change', () => syncSections());
  $('#btn-validate-script')?.addEventListener('click', validateScript);
  form.elements.handler_code.addEventListener('change', () => {
    const definition = definitions.find((item) => item.code === form.elements.handler_code.value);
    if (!definition) return;
    if (!form.elements.name.value.trim()) form.elements.name.value = definition.name;
    if (!editing) form.elements.code.value = definition.code;
    const help = $('#handler-help');
    if (help) help.textContent = definition.description || '来自后端注册表。';
  });
}

/* -------------------------------- 表单 -------------------------------- */

function syncSections() {
  const form = $('#job-form');
  const jobType = form.elements.job_type.value;
  const trigger = form.elements.trigger_type.value;
  const pythonAllowed = capabilities.python_enabled && capabilities.can_manage_python;

  if (!pythonAllowed) {
    Array.from(form.elements.job_type.options).forEach((option) => {
      if (option.value === 'python') option.disabled = true;
    });
    if (jobType === 'python') form.elements.job_type.value = 'registry';
  }

  $$('[data-registry-only]', form).forEach((node) => node.classList.toggle('hidden', jobType !== 'registry'));
  $$('[data-python-only]', form).forEach((node) => node.classList.toggle('hidden', jobType !== 'python'));
  $$('[data-registry-only] input, [data-registry-only] select', form).forEach((input) => {
    input.disabled = jobType !== 'registry';
  });
  $$('[data-python-only] textarea', form).forEach((input) => {
    input.disabled = jobType !== 'python';
  });

  const codeInput = form.elements.code;
  if (!editing) {
    if (jobType === 'registry') {
      codeInput.readOnly = true;
      codeInput.value = form.elements.handler_code.value || '';
    } else {
      codeInput.readOnly = false;
    }
  }

  $$('[data-trigger]', form).forEach((node) => {
    const active = node.dataset.trigger === trigger;
    node.classList.toggle('hidden', !active);
    $$('input', node).forEach((input) => {
      input.disabled = !active;
    });
  });
}

function openCreate() {
  editing = null;
  const form = $('#job-form');
  resetForm(form);
  $('#job-modal-title').textContent = '新建任务';
  form.elements.code.readOnly = false;
  fillSelect(form.elements.handler_code, definitions.map((item) => ({ value: item.code, label: `${item.name}（${item.code}）` })), { placeholder: '请选择任务定义' });
  form.elements.status.value = 'enabled';
  form.elements.timeout_seconds.value = '300';
  form.elements.args_json.value = '{}';
  form.elements.job_type.value = 'registry';
  form.elements.trigger_type.value = 'cron';
  syncSections();
  openModal('job-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#job-form');
  resetForm(form);
  $('#job-modal-title').textContent = `编辑任务 ${row.name}`;
  fillSelect(form.elements.handler_code, definitions.map((item) => ({ value: item.code, label: `${item.name}（${item.code}）` })), { placeholder: '请选择任务定义' });
  fillForm(form, {
    name: row.name,
    code: row.code,
    description: row.description,
    job_type: row.job_type,
    status: row.status,
    handler_code: row.job_type === 'registry' ? row.code : '',
    script_code: row.script_code || '',
    trigger_type: row.trigger_type,
    cron_expr: row.cron_expr || '',
    interval_seconds: row.interval_seconds,
    timezone: row.timezone || '',
    args_json: row.args_json || {},
    timeout_seconds: row.timeout_seconds,
  });
  form.elements.run_at.value = utcIsoToWall(row.run_at);
  form.elements.code.readOnly = true;
  syncSections();
  openModal('job-modal');
}

function buildPayload() {
  const form = $('#job-form');
  const values = serializeForm(form);
  const jobType = form.elements.job_type.value;
  const trigger = form.elements.trigger_type.value;

  const payload = {
    name: values.name,
    description: values.description,
    status: values.status,
    timezone: values.timezone || null,
    timeout_seconds: values.timeout_seconds,
    args_json: values.args_json || {},
    trigger_type: trigger,
    cron_expr: null,
    interval_seconds: null,
    run_at: null,
  };

  if (trigger === 'cron') payload.cron_expr = values.cron_expr;
  if (trigger === 'interval') payload.interval_seconds = values.interval_seconds;
  if (trigger === 'date') payload.run_at = wallToUtcIso(form.elements.run_at.value);

  if (jobType === 'python') payload.script_code = values.script_code || '';
  if (!editing) {
    payload.job_type = jobType;
    payload.code = jobType === 'registry' ? form.elements.handler_code.value : values.code;
  }
  return payload;
}

async function validateScript() {
  const form = $('#job-form');
  const code = (form.elements.script_code.value || '').trim();
  const hint = $('#script-validate-hint');
  if (!code) {
    toast('请先填写脚本内容', 'warning');
    return;
  }
  const button = $('#btn-validate-script');
  setLoading(button, true);
  try {
    await api.post('/schedule/python/validate', { script_code: code });
    if (hint) hint.textContent = '语法校验通过。';
    toast('脚本语法校验通过', 'success');
  } catch (error) {
    // 语法错误的具体位置对使用者最有价值，直接落到字段下方而不是只弹一条提示
    if (hint) hint.textContent = error?.message || '语法校验未通过。';
  } finally {
    setLoading(button, false);
  }
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;

  const jobType = form.elements.job_type.value;
  const trigger = form.elements.trigger_type.value;
  if (jobType === 'registry' && !form.elements.handler_code.value) {
    toast('请选择任务定义', 'warning');
    return;
  }
  if (trigger === 'cron' && !form.elements.cron_expr.value.trim()) {
    toast('请填写 Cron 表达式', 'warning');
    return;
  }
  if (trigger === 'interval' && !form.elements.interval_seconds.value) {
    toast('请填写间隔秒数', 'warning');
    return;
  }
  if (trigger === 'date' && !form.elements.run_at.value) {
    toast('请选择执行时间', 'warning');
    return;
  }

  const payload = buildPayload();
  const button = $('#job-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/schedule/jobs/${editing.id}`, payload);
      toast('任务已更新', 'success');
    } else {
      await api.post('/schedule/jobs', payload);
      toast('任务创建成功', 'success');
    }
    closeModal('job-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

/* -------------------------------- 操作 -------------------------------- */

async function setState(row, action) {
  try {
    await api.post(`/schedule/jobs/${row.id}/${action}`);
    toast(action === 'pause' ? '任务已暂停' : '任务已恢复', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function trigger(row) {
  const ok = await confirmDialog({
    title: '立即执行',
    message: `确认立即执行任务「${row.name}」？执行记录会写入执行日志。`,
    okText: '执行',
  });
  if (!ok) return;
  try {
    const result = await api.post(`/schedule/jobs/${row.id}/trigger`);
    toast(result?.accepted ? '已提交执行' : '任务未被接受', result?.accepted ? 'success' : 'warning');
    setTimeout(() => table.reload(), 800);
  } catch {
    /* 请求层已提示 */
  }
}

async function showRuns(row) {
  const body = $('#runs-body');
  body.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  openModal('runs-modal');
  try {
    const runs = await api.get(`/schedule/jobs/${row.id}/next-runs`, { count: 5 });
    body.innerHTML = runs?.length
      ? `<ul class="list-plain">${runs.map((time) => `<li style="cursor:default">${esc(formatTime(time))}</li>`).join('')}</ul>`
      : '<div class="table-empty">没有可预测的下次执行时间</div>';
  } catch (error) {
    body.innerHTML = `<div class="table-empty">${esc(error?.message || '加载失败')}</div>`;
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除任务',
    message: `确认删除任务「${row.name}」？执行日志会保留。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/schedule/jobs/${row.id}`);
    toast('任务已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
