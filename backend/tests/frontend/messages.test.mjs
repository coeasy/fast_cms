/**
 * 文案表（10-4）：所有 `msg('key')` 的 key 必须真的登记在 MESSAGES_ZH。
 *
 * 这条测试是文案表能被长期维护的前提——没有它，改一个 key 名或删一条文案，
 * 只会在用户面前显示成 `user.deleted` 这样的裸 key。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const JS_ROOT = new URL('../../app/web/static/js/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');

const { MESSAGES_ZH, msg } = await import('../../app/web/static/js/core/messages.js');

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (name.endsWith('.js')) out.push(path);
  }
  return out;
}

test('代码里用到的每个 msg key 都已登记', () => {
  const missing = [];
  const seen = new Set();
  for (const file of walk(JS_ROOT)) {
    const src = readFileSync(file, 'utf8');
    for (const match of src.matchAll(/msg\(\s*'([^']+)'/g)) {
      const key = match[1];
      seen.add(key);
      if (!(key in MESSAGES_ZH)) missing.push(`${file.slice(JS_ROOT.length)}: ${key}`);
    }
  }
  assert.deepEqual(missing, [], `这些 key 没有登记到 MESSAGES_ZH：\n${missing.join('\n')}`);
  assert.ok(seen.size >= 40, `只扫到 ${seen.size} 个 key，扫描逻辑可能失效了`);
});

test('msg: 命中的 key 返回中文', () => {
  assert.equal(msg('user.deleted'), '用户已删除');
});

test('msg: 具名占位符按实参替换', () => {
  assert.equal(msg('batch.selected', { n: 3 }), '已选 3 项');
  assert.equal(msg('batch.partial', { ok: 2, failed: 1 }), '已完成 2 项，1 项失败');
});

test('msg: 未登记的 key 原样返回并告警一次', () => {
  const warnings = [];
  const original = console.warn;
  console.warn = (text) => warnings.push(text);
  try {
    assert.equal(msg('nope.missing'), 'nope.missing');
    assert.equal(msg('nope.missing'), 'nope.missing');
  } finally {
    console.warn = original;
  }
  assert.equal(warnings.length, 1, '同一个漏注册只应告警一次');
  assert.match(warnings[0], /nope\.missing/);
});

test('文案表里没有空值', () => {
  const empty = Object.entries(MESSAGES_ZH)
    .filter(([, text]) => typeof text !== 'string' || text.trim() === '')
    .map(([key]) => key);
  assert.deepEqual(empty, []);
});
