/* ==========================================================================
   shell/portal.js —— 前台外壳
   前台首页只依赖 core.js 的主题引擎与请求封装，不引入后台外壳的任何代码。
   ========================================================================== */

import {
  $, api, applyTheme, brandGradient, esc, heroGradient, heroGlow,
  DEFAULT_THEME, DEFAULT_THEME_CODE,
} from '../core.js';

export async function initPortal() {
  let config = DEFAULT_THEME;
  let code = DEFAULT_THEME_CODE;
  let publicConfig = {};
  try {
    const themes = await api.get('/system/themes/public', null, { silent: true });
    config = themes?.active?.config || DEFAULT_THEME;
    code = themes?.active?.code || DEFAULT_THEME_CODE;
  } catch {
    /* 用默认主题 */
  }
  try {
    publicConfig = (await api.get('/system/public-config', null, { silent: true })) || {};
  } catch {
    publicConfig = {};
  }

  applyTheme(config, code);

  const title = String(publicConfig['system.title'] || 'Fast CMS');
  const description = String(publicConfig['system.description'] || '');
  document.title = `${title} · 首页`;

  const titleNode = $('#portal-title');
  if (titleNode) titleNode.textContent = title;
  const logo = $('#portal-logo');
  if (logo) {
    logo.textContent = title.slice(0, 1);
    logo.style.background = brandGradient(config.colorPrimary);
  }
  if (description) {
    const heroTitle = $('#portal-hero-title');
    if (heroTitle) heroTitle.textContent = description;
  }

  const nav = document.querySelector('.portal-nav');
  if (nav) {
    nav.style.background = heroGradient(config.colorPrimary, Boolean(config.darkAlgorithm));
  }
  const glow = document.querySelector('.portal-hero');
  if (glow) glow.style.background = `radial-gradient(60% 50% at 50% 0%, ${heroGlow(config.colorPrimary, 0.14)} 0%, transparent 100%)`;

  const copyright = String(publicConfig['system.copyright'] || '');
  const icp = String(publicConfig['system.icp'] || '');
  const foot = document.querySelector('.portal-footer');
  if (foot && (copyright || icp)) {
    foot.insertAdjacentHTML(
      'beforeend',
      `<div class="mt-8">${esc(copyright)}${icp ? ` · <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer noopener">${esc(icp)}</a>` : ''}</div>`,
    );
  }
}
