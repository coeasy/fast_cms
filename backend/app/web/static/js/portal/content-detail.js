/* ==========================================================================
   portal/content-detail.js —— 前台：公告正文
   匿名拉取单条已发布公告；不存在或未发布时给出可回退的提示。
   ========================================================================== */

import { $, esc } from '../core/base.js';
import { api } from '../core/request.js';
import { formatRecentTime } from '../core/time.js';

export function init() {
  const detail = $('#notice-detail');
  if (!detail) return;
  const noticeId = Number(window.location.pathname.split('/').pop());
  if (!Number.isInteger(noticeId) || noticeId <= 0) {
    detail.innerHTML = '<p class="muted">无效的公告地址。</p>';
    return;
  }

  (async () => {
    try {
      // api.get 已解信封：返回的就是 NoticeOut 本身，不能再读 .data，
      // 否则任何公告都会掉进下面「公告不存在或未发布」的兜底分支。
      const item = await api.get(`/content/notices/${noticeId}/published`, null, { silent: true });
      if (!item || !item.id) throw new Error('empty');
      document.title = `${item.title} · 公告`;
      detail.innerHTML = `
        <header class="notice-detail-head">
          <h1>${item.is_top ? '<span class="tag orange">置顶</span> ' : ''}${esc(item.title)}</h1>
          <div class="muted notice-detail-meta">
            发布于 ${formatRecentTime(item.published_at)}
          </div>
        </header>
        <div class="notice-detail-body">${esc(item.content)}</div>
      `;
    } catch {
      detail.innerHTML = `
        <p class="muted">公告不存在或未发布。</p>
        <a class="btn btn-primary" href="content">返回公告列表</a>
      `;
    }
  })();
}
