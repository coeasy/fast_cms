/**
 * theme-boot.js 与 core/theme.js 的变量清单必须一致。
 *
 * 前者是首屏同步脚本（普通脚本，无法 import 模块），后者是运行时应用主题。
 * 两边各写一份是不可避免的，但漏一个变量就会「首屏默认色 → 启动后跳色」。
 * 这里直接比对源码里的 CSS 变量名，防止单边修改。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = path.resolve(here, '../../app/web/static/js');

async function read(name) {
  return readFile(path.join(jsDir, name), 'utf8');
}

function vars(text) {
  const found = new Set();
  for (const m of text.matchAll(/['"](--[a-z0-9-]+)['"]/g)) found.add(m[1]);
  return found;
}

test('theme-boot 覆盖 applyTheme 设置的全部 CSS 变量', async () => {
  const theme = await read('core/theme.js');
  const boot = await read('theme-boot.js');

  // applyTheme 里通过 set('--x', ...) 统一写变量
  const applied = vars(theme);
  const booted = vars(boot);

  const missing = [...applied].filter((name) => !booted.has(name)).sort();
  assert.deepEqual(missing, [], `theme-boot.js 缺少变量：${missing.join(', ')}`);
});

test('theme-boot 同步首屏依赖的 dataset 标记', async () => {
  const theme = await read('core/theme.js');
  const boot = await read('theme-boot.js');

  const marks = [...theme.matchAll(/root\.dataset\.([A-Za-z]+)\s*=/g)].map((m) => m[1]);
  assert.ok(marks.length >= 4, 'theme.js 应设置多个 dataset 标记');
  const missing = [...new Set(marks)].filter((name) => !boot.includes(`dataset.${name}`));
  assert.deepEqual(missing, [], `theme-boot.js 缺少 dataset 标记：${missing.join(', ')}`);
});
