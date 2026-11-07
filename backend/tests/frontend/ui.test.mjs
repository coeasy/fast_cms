import { test } from 'node:test';
import assert from 'node:assert/strict';
await import('./_dom.mjs');
const { pageRange, flattenTree, buildOptions, treeOptions, selectedValues, boolTag, tag } = await import('../../app/web/static/js/ui.js');

/* ------------------------------ 分页计算 ------------------------------ */
test('pageRange: 空数据归一到 1 页', () => {
  assert.deepEqual(pageRange(0, 10, 1), { pages: 1, current: 1, start: 1, end: 1, numbers: [1] });
});
test('pageRange: 首页窗口 [1..5]', () => {
  assert.deepEqual(pageRange(100, 10, 1).numbers, [1, 2, 3, 4, 5]);
});
test('pageRange: 中间页窗口居中', () => {
  assert.deepEqual(pageRange(100, 10, 5).numbers, [3, 4, 5, 6, 7]);
});
test('pageRange: 末页窗口收敛到末尾', () => {
  assert.deepEqual(pageRange(100, 10, 10).numbers, [6, 7, 8, 9, 10]);
});
test('pageRange: 不满一页只显示 1 页', () => {
  const r = pageRange(5, 10, 1);
  assert.equal(r.pages, 1);
  assert.deepEqual(r.numbers, [1]);
});
test('pageRange: 越界 page 被钳到有效范围', () => {
  const r = pageRange(100, 10, 99);
  assert.equal(r.current, 10);
  assert.deepEqual(r.numbers, [6, 7, 8, 9, 10]);
});
test('pageRange: pageSize 非法时用 1', () => {
  assert.equal(pageRange(100, 0, 1).pages, 100);
});
test('pageRange: 恰好整除不产生多余页', () => {
  assert.equal(pageRange(50, 10, 5).pages, 5);
  assert.equal(pageRange(51, 10, 6).pages, 6);
});

/* ------------------------------ 树 / 下拉 ------------------------------ */
test('flattenTree: 拍平并标记层级', () => {
  const flat = flattenTree([{ id: 1, name: 'a', children: [{ id: 2, name: 'b' }] }]);
  assert.equal(flat.length, 2);
  assert.equal(flat[0].__depth, 0);
  assert.equal(flat[1].__depth, 1);
  assert.equal(flat[1].id, 2);
});
test('flattenTree: 空与无 children 安全', () => {
  assert.deepEqual(flattenTree([]), []);
  assert.deepEqual(flattenTree(null), []);
  assert.deepEqual(flattenTree([{ id: 1 }]), [{ id: 1, __depth: 0 }]);
});
test('buildOptions: 默认 id/name 键', () => {
  assert.deepEqual(buildOptions([{ id: 1, name: 'x' }]), [{ value: '1', label: 'x' }]);
});
test('buildOptions: 自定义键名', () => {
  assert.deepEqual(buildOptions([{ code: 'a', title: 'T' }], 'code', 'title'), [{ value: 'a', label: 'T' }]);
});
test('treeOptions: 用全角空格模拟缩进', () => {
  const opts = treeOptions([{ id: 1, name: 'root', children: [{ id: 2, name: 'child' }] }]);
  assert.equal(opts[0].label, 'root');
  assert.equal(opts[1].label, '　child');
});
test('selectedValues: 转数字数组', () => {
  const select = { selectedOptions: [{ value: '3' }, { value: '7' }] };
  assert.deepEqual(selectedValues(select), [3, 7]);
});

/* ------------------------------ 标签 ------------------------------ */
test('boolTag: 真/假分别绿色/灰色', () => {
  assert.equal(boolTag(true), '<span class="tag green">是</span>');
  assert.equal(boolTag(false), '<span class="tag gray">否</span>');
});
test('boolTag: 自定义文案', () => {
  assert.equal(boolTag(true, '启用', '停用'), '<span class="tag green">启用</span>');
});
test('tag: 转义 HTML 特殊字符', () => {
  assert.equal(tag('<b>&'), '<span class="tag gray">&lt;b&gt;&amp;</span>');
});
