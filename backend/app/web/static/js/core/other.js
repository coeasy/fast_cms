// @ts-check
/* ==========================================================================
   core/other.js —— 杂项：复制、空态行、状态标签
   ========================================================================== */

import { esc } from './base.js';
import { toast } from './dialog.js';
import { msg } from './messages.js';

export function copyText(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text).then(() => toast(msg('common.copied'), 'success'));
  }
  const input = document.createElement('textarea');
  input.value = text;
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  input.remove();
  toast(msg('common.copied'), 'success');
  return Promise.resolve();
}

export function emptyRow(colspan, text = '暂无数据') {
  return `<tr><td colspan="${colspan}"><div class="table-empty" role="status"><div class="big" aria-hidden="true">∅</div>${esc(text)}</div></td></tr>`;
}

/**
 * 统一「加载中 / 空 / 错误」三态渲染，消除各页面重复的内联样板。
 *
 * 页面此前各自拼 `<div class="loading-block">` / `<div class="table-empty">`，
 * 且 table 场景还要手动包 `<tr><td colspan>`、容器场景又要包 `.card`/`<li>`。
 * 这里把三态 + 三种包裹方式收口成一次调用：
 *
 * - 表格（<tbody>）：传 `colspan`，自动包 `<tr><td colspan>`
 * - 块容器：传 `wrap: 'card'` 或 `wrap: 'li'`
 * - 纯文本（门户列表）：传 `plain: true`，渲染 `<p class="muted">`
 *
 * @param {HTMLElement} target 目标容器（<tbody> 或任意块级元素）
 * @param {object} [options]
 * @param {'loading'|'empty'|'error'} [options.state='empty'] 当前状态
 * @param {string} [options.message] error 状态显示的错误信息
 * @param {string} [options.emptyText='暂无数据'] empty 状态显示的空态文案
 * @param {number} [options.colspan] 若提供且 target 是 <tbody>，包一层 <tr><td colspan>
 * @param {''|'card'|'li'} [options.wrap=''] 额外包裹类名或标签
 * @param {boolean} [options.plain=false] 用 <p class="muted"> 纯文本形态（门户列表）
 */
export function renderState(target, { state = 'empty', message, emptyText = '暂无数据', colspan = 0, wrap = '', plain = false } = /** @type {{state?: 'loading'|'empty'|'error', message?: string, emptyText?: string, colspan?: number, wrap?: ''|'card'|'li', plain?: boolean}} */ ({})) {
  if (!target) return;
  let block;
  if (state === 'loading') {
    block = '<div class="loading-block" role="status" aria-live="polite"><span class="spinner" aria-hidden="true"></span></div>';
  } else {
    const text = state === 'empty' ? emptyText : message || '加载失败';
    block = '<div class="table-empty" role="status"><div class="big" aria-hidden="true">∅</div>' + esc(text) + '</div>';
  }
  if (plain) {
    const text = state === 'loading' ? '加载中…' : state === 'empty' ? emptyText : message || '加载失败，请刷新重试。';
    target.innerHTML = '<p class="muted">' + text + '</p>';
    return;
  }
  if (wrap === 'card') block = '<div class="card">' + block + '</div>';
  else if (wrap === 'li') block = '<li>' + block + '</li>';
  if (colspan && target.tagName === 'TBODY') {
    target.innerHTML = '<tr><td colspan="' + colspan + '">' + block + '</td></tr>';
  } else {
    target.innerHTML = block;
  }
}

export function statusTag(status, map = {}) {
  const meta = map[status] || { label: status || '-', color: 'gray' };
  return `<span class="tag ${meta.color}">${esc(meta.label)}</span>`;
}

export const STATUS_MAP = {
  active: { label: '启用', color: 'green' },
  disabled: { label: '禁用', color: 'gray' },
  enabled: { label: '启用', color: 'green' },
  paused: { label: '暂停', color: 'orange' },
  draft: { label: '草稿', color: 'gray' },
  published: { label: '已发布', color: 'green' },
  offline: { label: '已下线', color: 'orange' },
  success: { label: '成功', color: 'green' },
  failure: { label: '失败', color: 'red' },
  failed: { label: '失败', color: 'red' },
  running: { label: '运行中', color: 'blue' },
  skipped: { label: '跳过', color: 'gray' },
  interrupted: { label: '中断', color: 'orange' },
  pending: { label: '待发送', color: 'blue' },
  sending: { label: '发送中', color: 'cyan' },
};
