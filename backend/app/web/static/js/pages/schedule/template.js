import { $, esc } from '../../core/base.js';
import { fillForm } from '../../core/form.js';
import { can } from '../../core/permission.js';
import { api } from '../../core/request.js';
import { actionButtons, createCrudPage, fillSelect } from '../../ui.js';

const TYPE_LABELS = {
  registry: { label: '注册表', color: 'blue' },
  python: { label: '脚本', color: 'purple' },
};

let definitions = [];
let page;

export async function init() {
  try {
    definitions = await api.get('/schedule/jobs/definitions', null, { silent: true });
  } catch {
    definitions = [];
  }

  page = createCrudPage({
    endpoint: '/schedule/templates',
    filters: '#filters',
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
    form: {
      id: '#template-form',
      modal: 'template-modal',
      titleId: '#template-modal-title',
      submitId: '#template-submit',
      titleCreate: '新建模板',
      titleEdit: (row) => `编辑模板 ${row.name}`,
      onCreate: (form) => {
        form.elements.code.readOnly = false;
        fillHandlerOptions(form);
        form.elements.job_type.value = 'registry';
        form.elements.trigger_type.value = 'cron';
        form.elements.timeout_seconds.value = '300';
        form.elements.args_json.value = '{}';
        syncSections();
      },
      onEdit: (form, row) => {
        form.elements.code.readOnly = true;
        fillHandlerOptions(form);
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
      },
      payload: (values, row) => {
        const jobType = values.job_type;
        const trigger = values.trigger_type;
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
        if (row) delete payload.job_type;
        else payload.code = values.code;
        return payload;
      },
    },
    messages: { created: 'template.created', updated: 'template.updated', deleted: 'template.deleted' },
    confirm: {
      title: '删除模板',
      message: (row) => `确认删除模板「${row.name}」？已由模板创建的任务不受影响。`,
    },
  });

  await page.init();

  // 类型/触发器切换要联动显示对应区块，这段是模板页独有的，留在页面里
  const form = $('#template-form');
  form.elements.job_type.addEventListener('change', syncSections);
  form.elements.trigger_type.addEventListener('change', syncSections);
}

function rowActions(row) {
  return actionButtons([
    can('schedule:template:update') && { act: 'edit', label: '编辑' },
    can('schedule:template:delete') && !row.is_builtin && { act: 'delete', label: '删除', danger: true },
  ]);
}

function fillHandlerOptions(form) {
  fillSelect(
    form.elements.handler_code,
    definitions.map((item) => ({ value: item.code, label: `${item.name}（${item.code}）` })),
    { placeholder: '请选择处理器' },
  );
}

/** 按「任务类型 / 触发方式」切换可见区块，并同步 disabled（隐藏字段不能提交） */
function syncSections() {
  const form = $('#template-form');
  const jobType = form.elements.job_type.value;
  const trigger = form.elements.trigger_type.value;
  form.querySelectorAll('[data-registry-only]').forEach((node) => node.classList.toggle('hidden', jobType !== 'registry'));
  form.querySelectorAll('[data-python-only]').forEach((node) => node.classList.toggle('hidden', jobType !== 'python'));
  form.querySelectorAll('[data-registry-only] select').forEach((input) => {
    input.disabled = jobType !== 'registry';
  });
  form.querySelectorAll('[data-python-only] textarea').forEach((input) => {
    input.disabled = jobType !== 'python';
  });
  form.querySelectorAll('[data-trigger]').forEach((node) => {
    const active = node.dataset.trigger === trigger;
    node.classList.toggle('hidden', !active);
    node.querySelectorAll('input').forEach((input) => {
      input.disabled = !active;
    });
  });
}
