/* ==========================================================================
   app.js —— 页面入口：按 body[data-page] 决定加载哪套外壳与页面脚本
   模板只提供 HTML，行为全部在这里按需加载：
     登录页      -> shell/auth.js
     前台首页    -> shell/portal.js
     错误页      -> 无脚本
     后台页面    -> shell/admin.js + pages/<page>.js
   ========================================================================== */

const pageKey = document.body.dataset.page || '';
const PUBLIC_PAGES = new Set(['login', 'error']);

async function main() {
  const { applyPermissions, bindModalTriggers, bindMultiSelects } = await import('./core.js');

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

  if (pageKey === 'portal/home') {
    const { initPortal } = await import('./shell/portal.js');
    await initPortal();
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
    return;
  }
  if (!ready) return;
  applyPermissions();

  try {
    const module = await import(`./pages/${pageKey}.js`);
    await module.init?.();
  } catch (error) {
    console.error(`[fastcms] 页面 ${pageKey} 初始化失败`, error);
  }
}

main();
