/**
 * DataTable.load 的并发语义：最新请求胜出，旧响应不得覆盖新结果。
 *
 * 这里用最小表格桩（只要 querySelector('tbody') 与 innerHTML）驱动真实
 * DataTable，不引入 DOM 库：表格渲染结果用 items / tbody.innerHTML 断言。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
await import('./_dom.mjs');
const { DataTable } = await import('../../app/web/static/js/ui.js');

function makeTable() {
  const tbody = { innerHTML: '', addEventListener() {} };
  return { querySelector: () => tbody, tbody };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

test('load: 慢请求后到，不覆盖先到的新结果', async () => {
  const calls = [];
  const table = makeTable();
  const dt = new DataTable({
    table,
    columns: [{ key: 'name' }],
    fetch: (params) => {
      const d = deferred();
      calls.push({ params, d });
      return d.promise;
    },
  });

  const first = dt.load(1);
  const second = dt.load(2);
  assert.equal(calls.length, 2, '并发请求都要真的发出去');

  // 第二次（最新）先返回
  calls[1].d.resolve({ items: [{ id: 2, name: 'new' }], total: 20 });
  await second;
  assert.deepEqual(dt.rows.map((r) => r.name), ['new']);

  // 第一次（过期）后返回：必须被丢弃
  calls[0].d.resolve({ items: [{ id: 1, name: 'stale' }], total: 20 });
  await first;
  assert.deepEqual(dt.rows.map((r) => r.name), ['new'], '旧响应不能覆盖新结果');
});

test('load: 加载中改筛选条件不会被丢弃', async () => {
  const calls = [];
  const table = makeTable();
  const dt = new DataTable({
    table,
    columns: [{ key: 'name' }],
    fetch: (params) => {
      const d = deferred();
      calls.push({ params, d });
      return d.promise;
    },
  });

  const initial = dt.load(1);
  assert.equal(calls.length, 1);

  // 用户在第一页还没回来时点了搜索
  const searched = dt.search({ keyword: 'abc' });
  assert.equal(calls.length, 2, '加载中的搜索也必须发起请求');
  assert.equal(calls[1].params.keyword, 'abc');
  assert.equal(calls[1].params.page, 1, '搜索回到第一页');

  calls[1].d.resolve({ items: [{ id: 3, name: 'hit' }], total: 1 });
  await searched;
  calls[0].d.resolve({ items: [{ id: 1, name: 'all' }], total: 9 });
  await initial;

  assert.deepEqual(dt.rows.map((r) => r.name), ['hit']);
});

test('load: 旧请求失败不影响新结果', async () => {
  const calls = [];
  const table = makeTable();
  const dt = new DataTable({
    table,
    columns: [{ key: 'name' }],
    fetch: (params) => {
      const d = deferred();
      calls.push({ params, d });
      return d.promise;
    },
  });

  const first = dt.load(1);
  const second = dt.load(2);

  calls[1].d.resolve({ items: [{ id: 2, name: 'ok' }], total: 2 });
  await second;

  calls[0].d.reject(new Error('网络错误'));
  await first;

  assert.deepEqual(dt.rows.map((r) => r.name), ['ok']);
  assert.equal(dt.loading, false, '过期请求的失败不能把 loading 卡住');
});

test('load: 失败后 loading 复位，可再次加载', async () => {
  const table = makeTable();
  let attempt = 0;
  const dt = new DataTable({
    table,
    columns: [{ key: 'name' }],
    fetch: async () => {
      attempt += 1;
      if (attempt === 1) throw new Error('boom');
      return { items: [{ id: 1, name: 'ok' }], total: 1 };
    },
  });

  await dt.load(1);
  assert.equal(dt.loading, false);
  assert.match(table.tbody.innerHTML, /boom/);

  await dt.load(1);
  assert.deepEqual(dt.rows.map((r) => r.name), ['ok']);
});
