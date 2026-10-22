import {
  $, $$, api, can, closeModal, confirmDialog, esc, fillForm, openModal, resetForm,
  serializeForm, setFieldError, setLoading, statusTag, STATUS_MAP, toast, validateForm,
} from '../../core.js';
import { DataTable, actionButtons, fillMultiSelect } from '../../ui.js';

const CHANNEL_LABELS = {
  dingtalk: { label: '钉钉', color: 'blue' },
  feishu: { label: '飞书', color: 'cyan' },
  wecom: { label: '企业微信', color: 'green' },
  webhook: { label: 'Webhook', color: 'gray' },
};

const EVENT_OPTIONS = [
  { value: 'job.failed', label: '任务失败' },
  { value: 'job.interrupted', label: '任务中断' },
  { value: 'notice.published', label: '公告发布' },
];

let table;
let editing = null;

export async function init() {
  fillMultiSelect($('#ch-events'), EVENT_OPTIONS);

  table = new DataTable({
    table: $('table.data'),
    pager: $('.pager'),
    fetch: (params) => api.get('/notify/channels', params),
    columns: [
      { render: (row) => `<span class="strong">${esc(row.name)}</span>` },
      {
        render: (row) => {
          const meta = CHANNEL_LABELS[row.channel_type] || { label: row.channel_type, color: 'gray' };
          return `<span class="tag ${meta.color}">${esc(meta.label)}</span>`;
        },
      },
      { render: (row) => `<span class="mono tiny break">${esc(row.webhook_url_masked || '-')}</span>` },
      {
        render: (row) =>
          `<span class="tag-row">${(row.events || []).map((event) => `<span class="tag gray">${esc(event)}</span>`).join('')}</span>`,
      },
      { render: (row) => (row.has_secret ? '<span class="tag green">已配置</span>' : '-') },
      { render: (row) => statusTag(row.status, STATUS_MAP) },
      { className: 'actions', render: (row) => rowActions(row) },
    ],
  });

  await table.load(1);
  bind();
}

function rowActions(row) {
  return actionButtons([
    can('notify:channel:update') && { act: 'edit', label: '编辑' },
    can('notify:channel:test') && { act: 'test', label: '测试' },
    can('notify:channel:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

function bind() {
  $('#filters').addEventListener('submit', (event) => {
    event.preventDefault();
    table.search(serializeForm(event.currentTarget));
  });
  $('#filters').addEventListener('reset', () => setTimeout(() => table.search({}), 0));
  $('#btn-refresh')?.addEventListener('click', () => table.reload());
  $('#btn-create')?.addEventListener('click', openCreate);

  $('table.data').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const row = table.find(Number(button.closest('tr')?.dataset.id));
    if (!row) return;
    if (button.dataset.act === 'edit') openEdit(row);
    else if (button.dataset.act === 'test') test(row);
    else if (button.dataset.act === 'delete') remove(row);
  });

  $('#channel-form').addEventListener('submit', submit);
}

function openCreate() {
  editing = null;
  const form = $('#channel-form');
  resetForm(form);
  $('#channel-modal-title').textContent = '新建渠道';
  $$('[data-create-only]', form).forEach((node) => node.classList.remove('hidden'));
  $('#ch-url').required = true;
  $('#ch-url').placeholder = 'https://...';
  const select = $('#ch-events select');
  Array.from(select.options).forEach((option) => {
    option.selected = option.value === 'job.failed';
  });
  select.dispatchEvent(new Event('change'));
  form.status.value = 'enabled';
  openModal('channel-modal');
}

function openEdit(row) {
  editing = row;
  const form = $('#channel-form');
  resetForm(form);
  $('#channel-modal-title').textContent = `编辑渠道 ${row.name}`;
  $$('[data-create-only]', form).forEach((node) => node.classList.add('hidden'));
  $('#ch-url').required = false;
  $('#ch-url').placeholder = row.webhook_url_masked || '留空表示保留原有地址';
  fillForm(form, {
    name: row.name,
    channel_type: row.channel_type,
    remark: row.remark,
    status: row.status,
  });
  const select = $('#ch-events select');
  const chosen = new Set(row.events || []);
  Array.from(select.options).forEach((option) => {
    option.selected = chosen.has(option.value);
  });
  select.dispatchEvent(new Event('change'));
  openModal('channel-modal');
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const values = serializeForm(form);
  if (!values.events?.length) {
    setFieldError($('#ch-events select'), '至少选择一个事件');
    toast('至少选择一个订阅事件', 'warning');
    return;
  }
  const payload = {
    name: values.name,
    channel_type: values.channel_type,
    events: values.events,
    status: values.status,
    remark: values.remark,
    secret: values.secret || null,
  };
  if (values.webhook_url) payload.webhook_url = values.webhook_url;
  else if (!editing) {
    toast('请填写 Webhook 地址', 'warning');
    return;
  }

  const button = $('#channel-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/notify/channels/${editing.id}`, payload);
      toast('渠道已更新', 'success');
    } else {
      await api.post('/notify/channels', payload);
      toast('渠道创建成功', 'success');
    }
    closeModal('channel-modal');
    table.reload();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function test(row) {
  const ok = await confirmDialog({
    title: '测试渠道',
    message: `将向「${row.name}」发送一条测试消息，确认继续？`,
    okText: '发送',
  });
  if (!ok) return;
  try {
    const record = await api.post(`/notify/channels/${row.id}/test`);
    if (record?.status === 'success') toast('测试消息已发送', 'success');
    else toast(`测试发送结果：${record?.status || '未知'}${record?.last_error ? `（${record.last_error}）` : ''}`, 'warning');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}

async function remove(row) {
  const ok = await confirmDialog({
    title: '删除渠道',
    message: `确认删除渠道「${row.name}」？历史发送记录会保留。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/notify/channels/${row.id}`);
    toast('渠道已删除', 'success');
    table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
