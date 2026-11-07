// @ts-check
/* ==========================================================================
   core/permission.js —— 前端权限判断
   权限集合由 /auth/me 下发（state.permissions），前端不保存静态映射。
   ========================================================================== */

import { $$ } from './base.js';
import { state } from './session.js';

export function can(code) {
  if (!code) return true;
  if (state.isSuperuser) return true;
  return state.permissions.includes(code);
}

/** 模板里用 data-perm="code" 标记需要权限的元素，启动后统一隐藏无权项 */
export function applyPermissions(root = document) {
  $$('[data-perm]', root).forEach((node) => {
    if (!can(node.dataset.perm)) node.hidden = true;
  });
}
