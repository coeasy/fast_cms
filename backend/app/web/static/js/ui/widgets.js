// @ts-check
/* ==========================================================================
   ui/widgets.js —— 表格之外的小部件：标签、下拉填充、多选、空态
   全部是「拿到数据 → 拼 HTML 字符串」的纯渲染函数，没有状态、不发请求。
   ========================================================================== */

import { esc } from '../core/base.js';
import { syncMultiSelects } from '../core/form.js';

/**
 * 把任意数据列表规范成 ``{value, label}`` 选项。
 *
 * 支持两种输入：对象数组按 ``valueKey``/``labelKey`` 取值，原始值数组则
 * 值与文案相同。``fillSelect`` 直接复用这里，避免「下拉框自己拼一遍映射」
 * 与「树形选项用另一套映射」两份实现慢慢跑偏。
 */
export function buildOptions(list, valueKey = 'id', labelKey = 'name') {
  return (list || []).map((item) =>
    typeof item === 'object' && item !== null
      ? { value: String(item[valueKey]), label: item[labelKey] }
      : { value: String(item), label: String(item) },
  );
}

/**
 * 用数据列表重填一个 <select> 的 option。
 *
 * @param {HTMLSelectElement} select 目标下拉框
 * @param {Array<{value: any, label: any}|string|number>} options
 *        选项列表：对象数组取 value/label，原始值数组则值与文案相同
 * @param {{placeholder?: string, keep?: boolean}} [opts]
 *        placeholder：在头部插入一个空值选项；keep：重填后尽量保留原选中值
 */
export function fillSelect(select, options, { placeholder, keep } = /** @type {{placeholder?: string, keep?: boolean}} */ ({})) {
  if (!select) return;
  const previous = select.value;
  const items = buildOptions(options || [], 'value', 'label');
  const head = placeholder !== undefined ? `<option value="">${esc(placeholder)}</option>` : '';
  select.innerHTML = head + items.map((option) => `<option value="${esc(option.value)}">${esc(option.label)}</option>`).join('');
  if (keep && previous) {
    const match = items.find((option) => option.value === previous);
    if (match) select.value = previous;
  }
}

export function tag(text, color = 'gray') {
  return `<span class="tag ${color}">${esc(text)}</span>`;
}

export function boolTag(value, trueText = '是', falseText = '否') {
  return value ? tag(trueText, 'green') : tag(falseText, 'gray');
}

/** 行内操作按钮 */
export function actionButtons(actions) {
  return actions
    .filter(Boolean)
    .map(
      (action) =>
        `<button class="btn-link ${action.danger ? 'danger' : ''}" data-act="${esc(action.act)}">${esc(action.label)}</button>`,
    )
    .join(' ');
}

export function emptyState(text) {
  return `<div class="table-empty" role="status"><div class="big" aria-hidden="true">∅</div>${esc(text)}</div>`;
}

/** 填充 multi-select 组件（隐藏的 select + 可视面板） */
export function fillMultiSelect(wrapper, options) {
  if (!wrapper) return;
  const select = wrapper.querySelector('select[multiple]');
  const panel = wrapper.querySelector('.ms-panel');
  const items = (options || []).map((option) => ({ value: String(option.value), label: option.label }));
  if (select) {
    select.innerHTML = items.map((item) => `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join('');
  }
  if (panel) {
    panel.innerHTML = items.length
      ? items.map((item) => `<div class="ms-option" data-value="${esc(item.value)}">${esc(item.label)}</div>`).join('')
      : '<div class="ms-option muted">暂无可选项</div>';
  }
  syncMultiSelects(document);
}

export function selectedValues(select) {
  if (!select) return [];
  return Array.from(select.selectedOptions).map((option) => Number(option.value));
}
