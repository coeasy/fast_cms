/* ==========================================================================
   portal/content-list.js —— 前台：公开公告列表
   匿名拉取已发布公告（/content/notices/published），前端不感知登录态。
   ========================================================================== */

import { $, esc } from '../core/base.js';
import { api } from '../core/request.js';
import { formatRecentTime } from '../core/time.js';
import { renderState } from '../core/other.js';

const PAGE_SIZE = 10;
let page = 1;
let total = 0;

function noticeHref(id) {
  return `${window.location.pathname.replace(/\/content$/, '')}/content/${id}`;
}

function render(items) {
  const list = $('#notice-list');
  if (!list) return;
  if (!items.length) {
    list.innerHTML = '<p class="muted">暂无公告。</p>';
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
      <a class="notice-item" href="${noticeHref(item.id)}">
        <div class="notice-item-head">
          <h2>${item.is_top ? '<span class="tag orange">置顶</span> ' : ''}${esc(item.title)}</h2>
          <time class="muted">${formatRecentTime(item.published_at)}</time>
        </div>
        <p class="muted notice-item-excerpt">${esc(String(item.content || '').slice(0, 120))}</p>
      </a>`,
    )
    .join('');
}

function renderPager() {
  const pager = $('#notice-pager');
  if (!pager) return;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pages <= 1) {
    pager.hidden = true;
    return;
  }
  pager.hidden = false;
  const prev = $('#notice-prev');
  const next = $('#notice-next');
  if (prev) prev.disabled = page <= 1;
  if (next) next.disabled = page >= pages;
  const info = $('#notice-page-info');
  if (info) info.textContent = `第 ${page} / ${pages} 页`;
}

async function load() {
  const list = $('#notice-list');
  renderState(list, { state: 'loading', plain: true });
  try {
    // api.get 已经在 parseEnvelope 里解过一次信封（返回 data 载荷），这里拿到的
    // 就是 Page{items,total,...}；再解一层会永远是 undefined，列表恒为空。
    const data = (await api.get(
      '/content/notices/published',
      { page, page_size: PAGE_SIZE },
      { silent: true },
    )) || {};
    total = Number(data.total || 0);
    render(data.items || []);
    renderPager();
  } catch {
    renderState(list, { state: 'error', plain: true });
  }
}

export function init() {
  $('#notice-prev')?.addEventListener('click', () => {
    if (page > 1) {
      page -= 1;
      load();
    }
  });
  $('#notice-next')?.addEventListener('click', () => {
    page += 1;
    load();
  });
  load();
}
