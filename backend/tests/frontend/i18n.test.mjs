import { test } from 'node:test';
import assert from 'node:assert/strict';
await import('./_dom.mjs');
const { t, hasMessage, registerMessages, setLocale, getLocale, resetI18n } = await import(
  '../../app/web/static/js/core/i18n.js'
);

test('t: 未注册语言包时返回中文兜底', () => {
  resetI18n();
  assert.equal(t('common.cancel', '取消'), '取消');
});
test('t: 既无语言包也无兜底时返回 key 本身', () => {
  resetI18n();
  assert.equal(t('common.unknown'), 'common.unknown');
});
test('t: 兜底为空串时按空串处理，不回退到 key', () => {
  resetI18n();
  assert.equal(t('common.empty', ''), '');
});
test('t: 注册后优先用语言包文案', () => {
  resetI18n();
  registerMessages({ 'common.cancel': 'Cancel' });
  assert.equal(t('common.cancel', '取消'), 'Cancel');
});
test('t: 默认不覆盖已注册的 key', () => {
  resetI18n();
  registerMessages({ 'common.cancel': 'Cancel' });
  registerMessages({ 'common.cancel': 'Abort' });
  assert.equal(t('common.cancel', '取消'), 'Cancel');
});
test('t: override 时才覆盖已注册的 key', () => {
  resetI18n();
  registerMessages({ 'common.cancel': 'Cancel' });
  registerMessages({ 'common.cancel': 'Abort' }, 'zh-CN', { override: true });
  assert.equal(t('common.cancel', '取消'), 'Abort');
});
test('t: 占位符按名字替换', () => {
  resetI18n();
  registerMessages({ 'import.tooMany': '单次最多导入 {max} 行，请分批' });
  assert.equal(t('import.tooMany', '兜底', { max: 500 }), '单次最多导入 500 行，请分批');
});
test('t: 缺少实参时保留占位符原文', () => {
  resetI18n();
  registerMessages({ 'import.tooMany': '单次最多导入 {max} 行' });
  assert.equal(t('import.tooMany', '兜底', {}), '单次最多导入 {max} 行');
});
test('t: 多余实参不影响结果', () => {
  resetI18n();
  registerMessages({ 'import.tooMany': '上限 {max}' });
  assert.equal(t('import.tooMany', '兜底', { max: 1, extra: 2 }), '上限 1');
});
test('t: 实参为 0 与 false 时也要替换（不能被真值判断吞掉）', () => {
  resetI18n();
  registerMessages({ 'probe.zero': '{n} 条 / {flag}' });
  assert.equal(t('probe.zero', '兜底', { n: 0, flag: false }), '0 条 / false');
});
test('setLocale / getLocale: 切换语言包', () => {
  resetI18n();
  registerMessages({ 'common.cancel': '取消' }, 'zh-CN');
  registerMessages({ 'common.cancel': '取消（繁）' }, 'zh-TW');
  assert.equal(getLocale(), 'zh-CN');
  setLocale('zh-TW');
  assert.equal(getLocale(), 'zh-TW');
  assert.equal(t('common.cancel', '取消'), '取消（繁）');
});
test('setLocale: 切到没有语言包的语言时回退兜底', () => {
  resetI18n();
  registerMessages({ 'common.cancel': 'Cancel' }, 'en-US');
  setLocale('en-US');
  setLocale('ja-JP');
  assert.equal(t('common.cancel', '取消'), '取消');
});
test('setLocale: 非法入参被忽略', () => {
  resetI18n();
  setLocale('');
  setLocale(null);
  assert.equal(getLocale(), 'zh-CN');
});
test('hasMessage: 只认当前语言', () => {
  resetI18n();
  registerMessages({ 'common.cancel': 'Cancel' }, 'en-US');
  assert.equal(hasMessage('common.cancel'), false);
  assert.equal(hasMessage('common.cancel', 'en-US'), true);
});
test('registerMessages: 忽略非字符串值', () => {
  resetI18n();
  registerMessages({ 'a.b': 1, 'c.d': null, 'e.f': 'ok' });
  assert.equal(hasMessage('a.b'), false);
  assert.equal(hasMessage('c.d'), false);
  assert.equal(hasMessage('e.f'), true);
});
test('registerMessages: 非对象入参不抛错', () => {
  resetI18n();
  registerMessages(null);
  registerMessages('nope');
  assert.equal(t('common.cancel', '取消'), '取消');
});
test('resetI18n: 清空语言包并恢复默认语言', () => {
  registerMessages({ 'common.cancel': 'Cancel' }, 'en-US');
  setLocale('en-US');
  resetI18n();
  assert.equal(getLocale(), 'zh-CN');
  assert.equal(hasMessage('common.cancel', 'en-US'), false);
});
