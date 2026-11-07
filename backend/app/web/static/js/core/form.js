// @ts-check
/* ==========================================================================
   core/form.js —— 表单读取/填充/校验与多选组件
   基于原生校验（required / minlength / pattern），错误就地展示。
   ========================================================================== */

import { $, $$, esc, ApiError } from './base.js';

/**
 * 表单控件的类型。TS 对「input | select | textarea」的公共字段推断得相当保守，
 * 而 checkJs 又不能像 TS 源码那样写 ``as``，所以这里统一把 [name] 控件视作
 * ``HTMLInputElement``——它的 API 是三者里最宽的（value/disabled/checked/required）。
 * select 专属的 ``.options`` / ``.selectedOptions`` / ``.multiple`` 在用到的地方单独断言。
 * @typedef {HTMLInputElement} FormField
 */

/**
 * 取表单里所有带 name 的控件。
 * @param {ParentNode} [root]
 * @returns {FormField[]}
 */
const fields = (root = document) => /** @type {FormField[]} */ ($$('[name]', root));

/**
 * 把任意元素断言成多选 select。
 * @param {Element} node
 * @returns {HTMLSelectElement}
 */
const asSelect = (node) => /** @type {HTMLSelectElement} */ (node);

/**
 * 读取表单值：按 data-type 做类型转换，跳过 disabled 与未命名控件。
 * checkbox 视为布尔，multiple select 视为数组。
 */
export function serializeForm(form) {
  const data = {};
  fields(form).forEach((field) => {
    const name = field.name;
    if (!name || field.disabled) return;
    const type = field.dataset.type || '';
    if (field.type === 'checkbox') {
      data[name] = /** @type {HTMLInputElement} */ (field).checked;
      return;
    }
    if (field.tagName === 'SELECT' && asSelect(field).multiple) {
      data[name] = Array.from(asSelect(field).selectedOptions).map((opt) => coerce(opt.value, type));
      return;
    }
    let value = field.value;
    if (type === 'number') {
      data[name] = value === '' ? null : Number(value);
      return;
    }
    if (type === 'bool') {
      data[name] = value === 'true';
      return;
    }
    if (type === 'json') {
      if (value.trim() === '') {
        data[name] = {};
        return;
      }
      try {
        data[name] = JSON.parse(value);
      } catch {
        throw new ApiError({ code: 'INVALID_JSON', message: `${field.dataset.label || name} 不是合法的 JSON` });
      }
      return;
    }
    if (type === 'array') {
      data[name] = value === '' ? [] : value.split(',').map((item) => item.trim()).filter(Boolean);
      return;
    }
    if (type === 'nullable') {
      data[name] = value === '' ? null : value;
      return;
    }
    data[name] = value;
  });
  return data;
}

function coerce(value, type) {
  if (type === 'number') return Number(value);
  return value;
}

export function fillForm(form, values = {}) {
  fields(form).forEach((field) => {
    const name = field.name;
    if (!name) return;
    const value = values[name];
    if (field.type === 'checkbox') {
      /** @type {HTMLInputElement} */ (field).checked = Boolean(value);
      return;
    }
    if (field.tagName === 'SELECT' && asSelect(field).multiple) {
      const list = Array.isArray(value) ? value.map(String) : [];
      Array.from(asSelect(field).options).forEach((opt) => {
        opt.selected = list.includes(opt.value);
      });
      return;
    }
    if (field.dataset.type === 'json') {
      field.value = value === undefined || value === null ? '' : JSON.stringify(value, null, 2);
      return;
    }
    field.value = value === undefined || value === null ? '' : String(value);
  });
  syncMultiSelects(form);
}

export function resetForm(form) {
  form.reset();
  $$('.field.invalid', form).forEach((node) => node.classList.remove('invalid'));
  fields(form).forEach((field) => {
    if (field.type === 'checkbox') {
      const box = /** @type {HTMLInputElement} */ (field);
      box.checked = box.defaultChecked;
    }
  });
  syncMultiSelects(form);
}

/**
 * 把错误信息挂到控件所在的 .field 包装层上。
 * @param {Element} field 控件本身
 * @param {string} message 空字符串表示清除错误
 */
export function setFieldError(field, message) {
  const wrapper = field.closest('.field');
  if (!wrapper) return;
  const invalid = Boolean(message);
  wrapper.classList.toggle('invalid', invalid);
  const err = wrapper.querySelector('.err');
  if (err) {
    err.textContent = message || '';
    // 无障碍：错误文案与控件关联，读屏器聚焦到该字段时会读出错误
    if (invalid) {
      const name = /** @type {FormField} */ (field).name || 'field';
      if (!err.id) err.id = `${name}-error`;
      field.setAttribute('aria-invalid', 'true');
      field.setAttribute('aria-describedby', err.id);
    } else {
      field.removeAttribute('aria-invalid');
      field.removeAttribute('aria-describedby');
    }
  }
}

/** 基于 required / minlength / pattern 的原生校验，错误就地展示 */
export function validateForm(form) {
  /** @type {FormField | null} */
  let firstInvalid = null;
  fields(form).forEach((field) => {
    if (field.disabled || field.type === 'hidden') return;
    let message = '';
    const value = String(field.value ?? '').trim();
    if (field.required && field.type !== 'checkbox' && !value) {
      message = `${field.dataset.label || '该项'}不能为空`;
    } else if (field.type === 'checkbox' && field.required && !/** @type {HTMLInputElement} */ (field).checked) {
      message = `请勾选${field.dataset.label || '该项'}`;
    } else if (value && field.minLength > 0 && value.length < field.minLength) {
      message = `${field.dataset.label || '该项'}至少 ${field.minLength} 位`;
    } else if (value && field.pattern && !new RegExp(`^(?:${field.pattern})$`).test(value)) {
      message = field.dataset.patternMessage || `${field.dataset.label || '该项'}格式不正确`;
    } else if (value && field.type === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      message = '邮箱格式不正确';
    }
    setFieldError(field, message);
    if (message && !firstInvalid) firstInvalid = field;
  });
  if (firstInvalid) {
    firstInvalid.focus();
    return false;
  }
  return true;
}

export function setLoading(button, loading) {
  if (!button) return;
  button.classList.toggle('loading', Boolean(loading));
  button.disabled = Boolean(loading);
}

/* ------------------------- 多选（原生 select multiple） ------------------------- */

/**
 * 按已选项重绘所有 multi-select 外观。``root`` 限定重绘范围（默认整个文档）。
 * @param {ParentNode} [root=document] 查找范围，默认整个文档
 */
export function syncMultiSelects(root = document) {
  $$('.multi-select', root).forEach((/** @type {HTMLElement} */ wrapper) => {
    const select = wrapper.querySelector('select[multiple]');
    const box = /** @type {HTMLElement} */ (wrapper.querySelector('.ms-box'));
    if (!select || !box) return;
    const selected = Array.from(asSelect(select).selectedOptions);
    box.innerHTML = selected.length
      ? selected.map((opt) => `<span class="tag blue">${esc(opt.text)}</span>`).join('')
      : `<span class="placeholder">${esc(/** @type {HTMLElement} */ (wrapper).dataset.placeholder || '请选择')}</span>`;
    const chosen = new Set(selected.map((opt) => opt.value));
    wrapper.querySelectorAll('.ms-option').forEach((node) => {
      node.classList.toggle('active', chosen.has(/** @type {HTMLElement} */ (node).dataset.value));
    });
  });
}

export function bindMultiSelects(root = document) {
  root.addEventListener('click', (event) => {
    const target = /** @type {Element} */ (event.target);
    const wrapper = target.closest('.multi-select');
    if (!wrapper) {
      $$('.multi-select.open').forEach((node) => node.classList.remove('open'));
      return;
    }
    const option = /** @type {HTMLElement|null} */ (target.closest('.ms-option'));
    const select = wrapper.querySelector('select[multiple]');
    if (option && select) {
      const value = option.dataset.value;
      const target = Array.from(asSelect(select).options).find((opt) => opt.value === value);
      if (target) target.selected = !target.selected;
      option.classList.toggle('active', Boolean(target?.selected));
      syncMultiSelects(/** @type {ParentNode} */ (wrapper.parentElement) || document);
      select.dispatchEvent(new Event('change', { bubbles: true }));
      event.stopPropagation();
      return;
    }
    if (target.closest('.ms-box')) {
      wrapper.classList.toggle('open');
      event.stopPropagation();
    }
  });
}
