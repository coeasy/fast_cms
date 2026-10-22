import {
  $, $$, api, can, closeModal, confirmDialog, esc, fillForm, openModal, resetForm,
  serializeForm, setLoading, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons, fillSelect } from '../../ui.js';

const TYPE_LABELS = {
  registry: { label: '注册表', color: 'blue' },
  python: { label: '脚本', color: 'purple' },
};

let table;
let definitions = [];
let editing = null;

export async function init() {
  try {
    definitions = await api.get('/schedule/jobs/definitions', null, { silent: true });
  } catch {
    definitions = [];
  }

  table = new DataTable({
    table: $('table.data'),
    fetch: async (params) => {
      const items = await api.get('/schedule/templates', params);
      return { items, total: items.length };
    },
    columns: [
      { render: (row) => `<span class="strong">${esc(row.name)}</span>${row.is_builtin ? ' <span class="tag gold">内置</span>' : ''}` },
      { render: (row) => `<span class="mono tiny">${esc(row.code)}</span>` },
      {
        render: (row) => {
          const meta = TYPE_LABELS[row.job_type] || { label: row.job_type, color: 'gray' };
          return `<span class="tag ${meta.color}">${esc(meta.label)}</span>`;
        },
      },
      {
        render: (row) =>
          row.trigger_type === 'cron'
            ? `<span class="tiny">Cron ${esc(row.cron_expr || '-')}</span>`
            : `<span class="tiny">每 ${row.interval_seconds || '-'} 秒</span>`,
      },
      { render: (row) => `${row.timeout_seconds} 秒` },
      { key: 'description', render: (row) => `<span class="muted">${esc(row.description || '-')}</span>` },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('schedule:template:update') && { act: 'edit', label: '编辑' },
    can('schedule:template:delete') && !row.is_builtin && { act: 'delete', label: '删除', danger: true },
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
    if (button.dataset.act === 'edit') openEdit(row);
    else if (button.dataset.act === 'delete') remove(row);
  });

  const form = $('#template-form');
  form.addEventListener('submit', submit);
  form.elements.job_type.addEventListener('change', syncSections);
  form.elements.trigger_type.addEventListener('change', syncSections);
}

function syncSections() {
  const form = $('#template-form');
  const jobType = form.elements.job_type.value;
  const trigger = form.elements.trigger_type.value;
  $$('[data-registry-only]', form).forEach((node) => node.classList.toggle('hidden', jobType !== 'registry'));
  $$('[data-python-only]', form).forEach((node) => node.classList.toggle('hidden', jobType !== 'python'));
  $$('[data-registry-only] select', form).forEach((input) => {
    input.disabled = jobType !== 'registry';
  });
  $$('[data-python-only] textarea', form).forEach((input) => {
    input.disabled = jobType !== 'python';
  });
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
  const form = $('#template-form');
  resetForm(form);
  $('#template-modal-title').textContent = '新建模板';
  form.elements.code.readOnly = false;
  fillSelect(form.elements.handler_code, definitions.map((item) => ({ value: item.code, label: `${item.name}（${item.code}）` })), { placeholder: '请选择处理器' });
  form.elements.job_type.value = 'registry';
  form.elements.trigger_type.value = 'cron';
  form.elements.timeout_seconds.value = '300';
  form.elements.args_json.value = '{}';
  syncSections();
  openModal('template-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#template-form');
  resetForm(form);
  $('#template-modal-title').textContent = `编辑模板 ${row.name}`;
  form.elements.code.readOnly = true;
  fillSelect(form.elements.handler_code, definitions.map((item) => ({ value: item.code, label: `${item.name}（${item.code}）` })), { placeholder: '请选择处理器' });
  fillForm(form, {
    name: row.name,
    code: row.code,
    job_type: row.job_type,
    handler_code: row.handler_code || '',
    script_code: row.script_code || '',
    description: row.description,
    trigger_type: row.trigger_type,
    cron_expr: row.cron_expr || '',
    interval_seconds: row.interval_seconds,
    timezone: row.timezone || '',
    timeout_seconds: row.timeout_seconds,
    args_json: row.args_json || {},
  });
  syncSections();
  openModal('template-modal');
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  const jobType = form.elements.job_type.value;
  const trigger = form.elements.trigger_type.value;

  const payload = {
    name: values.name,
    description: values.description,
    job_type: jobType,
    trigger_type: trigger,
    timezone: values.timezone || null,
    timeout_seconds: values.timeout_seconds,
    args_json: values.args_json || {},
    cron_expr: trigger === 'cron' ? values.cron_expr : null,
    interval_seconds: trigger === 'interval' ? values.interval_seconds : null,
    handler_code: jobType === 'registry' ? values.handler_code || null : null,
    script_code: jobType === 'python' ? values.script_code || '' : null,
  };

  const button = $('#template-submit');
  setLoading(button, true);
  try {
    if (editing) {
      delete payload.job_type;
      await api.put(`/schedule/templates/${editing.id}`, payload);
      toast('模板已更新', 'success');
    } else {
      payload.code = values.code;
      await api.post('/schedule/templates', payload);
      toast('模板创建成功', 'success');
    }
    closeModal('template-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除模板',
    message: `确认删除模板「${row.name}」？已由模板创建的任务不受影响。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/schedule/templates/${row.id}`);
    toast('模板已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
