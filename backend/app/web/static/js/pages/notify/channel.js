import { $, esc } from '../../core/base.js';
import { confirmDialog, toast } from '../../core/dialog.js';
import { fillForm, setFieldError } from '../../core/form.js';
import { msg } from '../../core/messages.js';
import { STATUS_MAP, statusTag } from '../../core/other.js';
import { can } from '../../core/permission.js';
import { api } from '../../core/request.js';
import { actionButtons, createCrudPage, fillMultiSelect } from '../../ui.js';

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

let page;

export async function init() {
  fillMultiSelect($('#ch-events'), EVENT_OPTIONS);

  page = createCrudPage({
    endpoint: '/notify/channels',
    pagerSelector: '.pager',
    filters: '#filters',
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
    form: {
      id: '#channel-form',
      modal: 'channel-modal',
      titleId: '#channel-modal-title',
      submitId: '#channel-submit',
      titleCreate: '新建渠道',
      titleEdit: (row) => `编辑渠道 ${row.name}`,
      onCreate: (form) => {
        // 新建时地址必填；编辑时留空表示「保留原地址」，因此只切换 required
        form.querySelectorAll('[data-create-only]').forEach((node) => node.classList.remove('hidden'));
        $('#ch-url').required = true;
        $('#ch-url').placeholder = 'https://...';
        selectEvents(['job.failed']);
        form.status.value = 'enabled';
      },
      onEdit: (form, row) => {
        form.querySelectorAll('[data-create-only]').forEach((node) => node.classList.add('hidden'));
        $('#ch-url').required = false;
        $('#ch-url').placeholder = row.webhook_url_masked || '留空表示保留原有地址';
        fillForm(form, {
          name: row.name,
          channel_type: row.channel_type,
          remark: row.remark,
          status: row.status,
        });
        selectEvents(row.events || []);
      },
      payload: (values, row) => {
        if (!values.events?.length) {
          setFieldError($('#ch-events select'), '至少选择一个事件');
          toast(msg('channel.requireEvents'), 'warning');
          return false;
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
        else if (!row) {
          toast(msg('channel.requireWebhook'), 'warning');
          return false;
        }
        return payload;
      },
    },
    messages: { created: 'channel.created', updated: 'channel.updated', deleted: 'channel.deleted' },
    confirm: {
      title: '删除渠道',
      message: (row) => `确认删除渠道「${row.name}」？历史发送记录会保留。`,
    },
    actions: [{ act: 'test', run: (row) => testChannel(row) }],
  });

  await page.init();
}

function rowActions(row) {
  return actionButtons([
    can('notify:channel:update') && { act: 'edit', label: '编辑' },
    can('notify:channel:test') && { act: 'test', label: '测试' },
    can('notify:channel:delete') && { act: 'delete', label: '删除', danger: true },
  ]);
}

/** multi-select 是「隐藏 select + 可视面板」，改选中态后必须派发 change 让面板同步 */
function selectEvents(values) {
  const select = $('#ch-events select');
  const chosen = new Set(values);
  Array.from(select.options).forEach((option) => {
    option.selected = chosen.has(option.value);
  });
  select.dispatchEvent(new Event('change'));
}

async function testChannel(row) {
  const ok = await confirmDialog({
    title: '测试渠道',
    message: `将向「${row.name}」发送一条测试消息，确认继续？`,
    okText: '发送',
  });
  if (!ok) return;
  try {
    const record = await api.post(`/notify/channels/${row.id}/test`);
    if (record?.status === 'success') toast(msg('channel.testSent'), 'success');
    else toast(`测试发送结果：${record?.status || '未知'}${record?.last_error ? `（${record.last_error}）` : ''}`, 'warning');
    page.table.reload();
  } catch {
    /* 请求层已提示 */
  }
}
