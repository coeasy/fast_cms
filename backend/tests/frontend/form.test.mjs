import { test } from 'node:test';
import assert from 'node:assert/strict';
await import('./_dom.mjs');
const { serializeForm } = await import('../../app/web/static/js/core/form.js');

/** 构造一个最小表单桩：querySelectorAll('[name]') 返回字段数组。 */
function mkForm(fields) {
  return { querySelectorAll: (sel) => (sel === '[name]' ? fields : []) };
}
/** 构造字段对象，data-type 等由调用方传入。 */
function field(name, { type = 'text', value = '', disabled = false, checked = false, dataset = {}, multiple = false, selectedOptions = [], tagName } = {}) {
  return { name, type, value, disabled, checked, dataset, multiple, selectedOptions, tagName: tagName || (multiple ? 'SELECT' : 'INPUT') };
}

test('serializeForm: 普通文本原样返回', () => {
  const data = serializeForm(mkForm([field('username', { value: 'alice' })]));
  assert.deepEqual(data, { username: 'alice' });
});
test('serializeForm: 空文本字段保留空串', () => {
  assert.deepEqual(serializeForm(mkForm([field('note', { value: '' })])), { note: '' });
});
test('serializeForm: number 类型转数字，空转 null', () => {
  assert.deepEqual(serializeForm(mkForm([field('age', { type: 'number', value: '30', dataset: { type: 'number' } })])), { age: 30 });
  assert.deepEqual(serializeForm(mkForm([field('age', { type: 'number', value: '', dataset: { type: 'number' } })])), { age: null });
});
test('serializeForm: bool 类型转布尔', () => {
  assert.deepEqual(serializeForm(mkForm([field('on', { dataset: { type: 'bool' }, value: 'true' })])), { on: true });
  assert.deepEqual(serializeForm(mkForm([field('on', { dataset: { type: 'bool' }, value: 'false' })])), { on: false });
});
test('serializeForm: json 类型解析，空转 {}', () => {
  assert.deepEqual(serializeForm(mkForm([field('cfg', { dataset: { type: 'json' }, value: '{"a":1}' })])), { cfg: { a: 1 } });
  assert.deepEqual(serializeForm(mkForm([field('cfg', { dataset: { type: 'json' }, value: '' })])), { cfg: {} });
});
test('serializeForm: json 非法抛 INVALID_JSON', () => {
  assert.throws(
    () => serializeForm(mkForm([field('cfg', { dataset: { type: 'json' }, value: '{bad' })])),
    (err) => err && err.code === 'INVALID_JSON',
  );
});
test('serializeForm: array 类型按逗号拆分', () => {
  assert.deepEqual(serializeForm(mkForm([field('tags', { dataset: { type: 'array' }, value: 'a, b, c' })])), { tags: ['a', 'b', 'c'] });
  assert.deepEqual(serializeForm(mkForm([field('tags', { dataset: { type: 'array' }, value: '' })])), { tags: [] });
});
test('serializeForm: nullable 空转 null', () => {
  assert.deepEqual(serializeForm(mkForm([field('desc', { dataset: { type: 'nullable' }, value: '' })])), { desc: null });
  assert.deepEqual(serializeForm(mkForm([field('desc', { dataset: { type: 'nullable' }, value: 'x' })])), { desc: 'x' });
});
test('serializeForm: checkbox 视为布尔', () => {
  assert.deepEqual(serializeForm(mkForm([field('ok', { type: 'checkbox', checked: true })])), { ok: true });
  assert.deepEqual(serializeForm(mkForm([field('ok', { type: 'checkbox', checked: false })])), { ok: false });
});
test('serializeForm: multiple select 转数组并做类型转换', () => {
  const opts = [{ value: '1' }, { value: '2' }];
  assert.deepEqual(serializeForm(mkForm([field('ids', { multiple: true, tagName: 'SELECT', selectedOptions: opts, dataset: { type: 'number' } })])), { ids: [1, 2] });
});
test('serializeForm: disabled 与未命名字段跳过', () => {
  const data = serializeForm(mkForm([field('skip', { disabled: true, value: 'x' }), field('', { value: 'y' }), field('keep', { value: 'z' })]));
  assert.deepEqual(data, { keep: 'z' });
});
