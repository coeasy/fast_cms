// @ts-check
/* ==========================================================================
   core/dialog.js —— Toast 与弹层
   弹层一律写在 HTML 里（<div class="mask" data-modal="user-form" hidden>），
   这里只负责显隐与焦点管理，避免用 JS 拼 DOM。

   无障碍（5-4）：
   - 打开时给弹层声明 role="dialog" / aria-modal="true" / aria-labelledby；
   - Tab / Shift+Tab 在弹层内循环（焦点陷阱），不跑到背景页面；
   - 关闭时把焦点归还给「打开它的那个元素」，键盘用户不丢焦点位置；
   - 多个弹层可叠加，用栈管理，只对最上层做陷阱与归还。
   ========================================================================== */

import { $, $$, esc } from './base.js';
import { icon } from '../icons.js';
import { t } from './i18n.js';

/* 可聚焦元素选择器：与原生 Tab 序列一致，排除 disabled 与隐藏 input */
const FOCUSABLE = 'a[href], area[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/* 当前打开的弹层栈：{ mask, opener }。opener 是打开它时的 activeElement，
   关闭时用来归还焦点。 */
const modalStack = [];

function focusables(root) {
  return $$(FOCUSABLE, root);
}

/** 给弹层补无障碍语义（幂等：已声明过就不再重复写）。 */
function markDialog(mask) {
  const dialog = mask.querySelector('.modal') || mask;
  if (dialog.getAttribute('role') !== 'dialog') {
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    const title = mask.querySelector('.modal-head span[id], [id$="-title"]');
    if (title && title.id) dialog.setAttribute('aria-labelledby', title.id);
  }
}

/** Tab 焦点陷阱：只作用于栈顶弹层。 */
function onKeydown(event) {
  const top = modalStack[modalStack.length - 1];
  if (!top || event.key !== 'Tab') return;
  const list = focusables(top.mask);
  if (!list.length) return;
  const first = list[0];
  const last = list[list.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function pushModal(mask) {
  if (!modalStack.length) document.addEventListener('keydown', onKeydown);
  modalStack.push({ mask, opener: document.activeElement });
  markDialog(mask);
}

function popModal(mask) {
  const index = modalStack.findIndex((entry) => entry.mask === mask);
  if (index < 0) return;
  const [entry] = modalStack.splice(index, 1);
  if (!modalStack.length) document.removeEventListener('keydown', onKeydown);
  // 焦点归还给打开它的元素（若仍在文档中）
  const opener = entry.opener;
  if (opener && typeof opener.focus === 'function' && document.contains?.(opener)) {
    opener.focus();
  }
}

function toastHost() {
  let host = $('.toasts');
  if (!host) {
    host = document.createElement('div');
    host.className = 'toasts';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
  }
  return host;
}

const TOAST_ICON = { success: 'check', error: 'close', warning: 'alert', info: 'info' };

export function toast(message, type = 'info', duration = 3200) {
  const host = toastHost();
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.setAttribute('role', type === 'error' ? 'alert' : 'status');
  node.innerHTML = `<span class="ico" aria-hidden="true">${icon(TOAST_ICON[type] || 'info', 15)}</span><span>${esc(message)}</span>`;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .2s';
    setTimeout(() => node.remove(), 220);
  }, duration);
  return node;
}

export function openModal(id) {
  const node = typeof id === 'string' ? document.getElementById(id) : id;
  if (!node) return null;
  node.hidden = false;
  document.body.style.overflow = 'hidden';
  pushModal(node);
  const focusable = node.querySelector(FOCUSABLE);
  if (focusable) setTimeout(() => focusable.focus(), 40);
  return node;
}

export function closeModal(id) {
  const node = typeof id === 'string' ? document.getElementById(id) : id;
  if (!node) return;
  node.hidden = true;
  popModal(node);
  if (!$('.mask:not([hidden])')) document.body.style.overflow = '';
}

export function closeTopModal() {
  const masks = $$('.mask').filter((node) => !node.hidden);
  if (masks.length) closeModal(masks[masks.length - 1]);
}

/**
 * 确认对话框。
 * @param {{ title?: string, message?: string, okText?: string, cancelText?: string, danger?: boolean }} [options]
 * @returns {Promise<boolean>} true=点了确认，false=取消或点遮罩
 */
export function confirmDialog({ title, message = '', okText, cancelText, danger = false } = {}) {
  // 默认文案走 t()：今天直接返回中文，将来注册语言包即可切换，调用点无需改动
  const heading = title ?? t('common.confirmTitle', '确认操作');
  const ok = okText ?? t('common.confirm', '确定');
  const cancel = cancelText ?? t('common.cancel', '取消');
  return new Promise((resolve) => {
    const mask = document.createElement('div');
    mask.className = 'mask';
    mask.style.zIndex = '200';
    mask.innerHTML = `
      <div class="modal" style="width:420px" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <div class="modal-head" id="confirm-dialog-title">${esc(heading)}</div>
        <div class="modal-body"><div style="color:var(--text-2)">${esc(message)}</div></div>
        <div class="modal-foot">
          <button class="btn" data-act="cancel">${esc(cancel)}</button>
          <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${esc(ok)}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    pushModal(mask);
    const done = (value) => {
      mask.remove();
      popModal(mask);
      if (!$('.mask:not([hidden])')) document.body.style.overflow = '';
      resolve(value);
    };
    mask.addEventListener('click', (event) => {
      const target = /** @type {Element} */ (event.target);
      const act = target.closest('[data-act]')?.getAttribute('data-act');
      if (act === 'ok') done(true);
      else if (act === 'cancel' || target === mask) done(false);
    });
    setTimeout(() => /** @type {HTMLElement} */ (mask.querySelector('[data-act=ok]'))?.focus(), 40);
  });
}

/** 通用弹层开合：所有带 data-modal-open / data-modal-close 的元素自动生效 */
export function bindModalTriggers(root = document) {
  root.addEventListener('click', (event) => {
    const target = /** @type {Element} */ (event.target);
    const opener = target.closest('[data-modal-open]');
    if (opener) {
      event.preventDefault();
      openModal(opener.getAttribute('data-modal-open'));
      return;
    }
    const closer = target.closest('[data-modal-close]');
    if (closer) {
      event.preventDefault();
      const modalId = /** @type {HTMLElement} */ (closer).dataset.modalClose;
      closeModal(modalId || /** @type {HTMLElement} */ (closer.closest('.mask'))?.id);
      return;
    }
    if (target.classList?.contains('mask')) closeModal(target);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeTopModal();
  });
}
