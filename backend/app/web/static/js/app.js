/* ==========================================================================
   app.js —— 页面入口：按 body[data-page] 决定加载哪套外壳与页面脚本
   模板只提供 HTML，行为全部在这里按需加载：
     登录页      -> shell/auth.js
     前台首页    -> shell/portal.js
     错误页      -> 无脚本
     后台页面    -> shell/admin.js + pages/<page>.js

   任何一环加载失败都必须**渲染出可读的错误**，不能留一片空白：
   空白页对用户是「系统坏了」，对运维是「没有线索」，两者都没法自助解决。
   ========================================================================== */

import { esc } from './core/base.js';

const pageKey = document.body.dataset.page || '';
const PUBLIC_PAGES = new Set(['login', 'error']);

/**
 * 渲染页面级错误态。
 *
 * 覆盖三种失败：页面脚本 404 / 语法错误（import 直接 reject）、脚本 init 抛异常、
 * 外壳启动失败。文案刻意带上脚本路径与错误信息——用户能据此反馈，运维能据此定位。
 *
 * @param {string} title 一句话说明发生了什么
 * @param {unknown} error 原始错误
 * @param {string} [hint] 下一步建议
 */
function renderPageError(title, error, hint) {
  const host = document.getElementById('main') || document.body;
  const detail = error instanceof Error ? error.message : String(error ?? '');
  const target = pageKey.startsWith('portal/') ? `portal/${pageKey.slice('portal/'.length)}.js` : `pages/${pageKey}.js`;
  host.innerHTML = `
    <div class="page-error" role="alert">
      <div class="page-error-icon" aria-hidden="true">!</div>
      <h1>${esc(title)}</h1>
      <p>页面脚本 <code>${esc(target)}</code> 没能加载或初始化。常见原因：前端资源缺失、浏览器缓存了旧版本、或该页面依赖的接口不可用。</p>
      ${hint ? `<p>${esc(hint)}</p>` : ''}
      ${detail ? `<p class="page-error-detail">错误详情：<code>${esc(detail)}</code></p>` : ''}
      <div class="page-error-actions">
        <button type="button" class="btn" data-page-reload>重新加载</button>
        <a class="btn-link" href="/">回到工作台</a>
      </div>
    </div>`;
  host.querySelector('[data-page-reload]')?.addEventListener('click', () => window.location.reload());
}

async function main() {
  const [{ applyPermissions }, { bindModalTriggers }, { bindMultiSelects }] = await Promise.all([
    import('./core/permission.js'),
    import('./core/dialog.js'),
    import('./core/form.js'),
  ]);

  if (pageKey === 'login') {
    const { initLoginPage } = await import('./shell/auth.js');
    bindModalTriggers();
    bindMultiSelects();
    initLoginPage();
    return;
  }

  if (PUBLIC_PAGES.has(pageKey)) {
    bindModalTriggers();
    return;
  }

  if (pageKey.startsWith('portal/')) {
    const { initPortal } = await import('./shell/portal.js');
    await initPortal();
    // 前台子页面（公告列表/详情等）：pageKey=portal/content-list -> portal/content-list.js
    if (pageKey !== 'portal/home') {
      const page = pageKey.slice('portal/'.length);
      try {
        const mod = await import(`./portal/${page}.js`);
        await mod.init?.();
      } catch (error) {
        console.error(`[fastcms] 前台页面 ${pageKey} 初始化失败`, error);
        renderPageError('页面加载失败', error, '如果反复出现，请联系管理员确认前端资源版本。');
      }
    }
    return;
  }

  const { bootAdmin } = await import('./shell/admin.js');
  bindModalTriggers();
  bindMultiSelects();

  let ready = false;
  try {
    ready = await bootAdmin();
  } catch (error) {
    console.error('[fastcms] 启动失败', error);
    document.getElementById('boot-mask')?.remove();
    // 外壳启动失败时连侧边栏都没有，错误必须直接落在 main 里
    renderPageError('后台启动失败', error, '请确认后端服务与数据库可用后重新加载。');
    return;
  }
  if (!ready) return;
  applyPermissions();

  try {
    const module = await import(`./pages/${pageKey}.js`);
    await module.init?.();
  } catch (error) {
    console.error(`[fastcms] 页面 ${pageKey} 初始化失败`, error);
    renderPageError('页面加载失败', error, '如果反复出现，请联系管理员确认前端资源版本。');
  }
}

// 入口自身失败也必须可见：模块 404、语法错误、外壳 import 失败都会让 main()
// 直接 reject，此时 boot-mask 还盖在页面上，用户看到的是永远转圈的白屏。
main().catch((error) => {
  console.error('[fastcms] 页面启动失败', error);
  document.getElementById('boot-mask')?.remove();
  renderPageError('页面启动失败', error, '请确认后端服务可用后重新加载。');
});
