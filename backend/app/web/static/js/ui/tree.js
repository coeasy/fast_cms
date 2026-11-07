// @ts-check
/* ==========================================================================
   ui/tree.js —— 树形数据的展示与下拉
   后端返回的是嵌套结构（部门、菜单、字典），表格与下拉都要「拍平 + 加层级标记」。
   ========================================================================== */

import { esc } from '../core/base.js';

/** 把后端返回的树拍平成带层级标记的列表，用于表格展示 */
export function flattenTree(nodes, depth = 0, out = []) {
  (nodes || []).forEach((node) => {
    out.push({ ...node, __depth: depth });
    if (node.children?.length) flattenTree(node.children, depth + 1, out);
  });
  return out;
}

export function indentLabel(text, depth, extra = '') {
  const pad = depth * 18;
  const branch = depth > 0 ? '<span style="color:var(--text-4);margin-right:6px">└</span>' : '';
  return `<span style="padding-left:${pad}px;display:inline-flex;align-items:center;gap:4px">${branch}<span>${esc(text)}</span>${extra}</span>`;
}

/** 树形下拉用缩进模拟层级（原生 select 不支持真正的树） */
export function treeOptions(nodes, depth = 0, out = []) {
  (nodes || []).forEach((node) => {
    out.push({ value: String(node.id), label: `${'　'.repeat(depth)}${node.name}` });
    if (node.children?.length) treeOptions(node.children, depth + 1, out);
  });
  return out;
}
