import { test } from 'node:test';
import assert from 'node:assert/strict';
await import('./_dom.mjs');
const { formatBytes, formatUptime, formatNumber, formatMs } = await import('../../app/web/static/js/core/format.js');

test('formatBytes: 0 与负数返回 0 B', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(-5), '0 B');
});
test('formatBytes: 字节级不保留小数', () => {
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1), '1 B');
});
test('formatBytes: KB/MB/GB 保留两位', () => {
  assert.equal(formatBytes(1024), '1.00 KB');
  assert.equal(formatBytes(1536), '1.50 KB');
  assert.equal(formatBytes(1048576), '1.00 MB');
  assert.equal(formatBytes(1073741824), '1.00 GB');
});
test('formatBytes: 非法输入回退 0 B', () => {
  assert.equal(formatBytes('bad'), '0 B');
  assert.equal(formatBytes(NaN), '0 B');
});
test('formatUptime: 空/非正返回 -', () => {
  assert.equal(formatUptime(0), '-');
  assert.equal(formatUptime(-5), '-');
  assert.equal(formatUptime(null), '-');
});
test('formatUptime: 天时分换算', () => {
  assert.equal(formatUptime(90061), '1 天 1 小时 1 分');
  assert.equal(formatUptime(3661), '0 天 1 小时 1 分');
  assert.equal(formatUptime(59), '0 天 0 小时 0 分');
  assert.equal(formatUptime(86400), '1 天 0 小时 0 分');
});
test('formatNumber: 千分位', () => {
  assert.equal(formatNumber(1234567), '1,234,567');
  assert.equal(formatNumber(0), '0');
  assert.equal(formatNumber(1000), '1,000');
});
test('formatMs: 毫秒与秒', () => {
  assert.equal(formatMs(0), '0 ms');
  assert.equal(formatMs(500), '500 ms');
  assert.equal(formatMs(999), '999 ms');
  assert.equal(formatMs(1000), '1.00 s');
  assert.equal(formatMs(1500), '1.50 s');
});
test('formatMs: 非法输入返回 -', () => {
  assert.equal(formatMs(NaN), '-');
  assert.equal(formatMs('x'), '-');
});
