/* 首屏先套用上次缓存的主题，避免刷新时闪一下默认蓝。
   独立成文件（而不是内联脚本）是为了让 CSP 可以禁用 unsafe-inline。 */
(function () {
  'use strict';
  try {
    var raw = localStorage.getItem('fastcms_theme_snapshot');
    if (!raw) return;
    var snap = JSON.parse(raw) || {};
    var c = snap.config || {};
    var r = document.documentElement;
    r.dataset.dark = c.darkAlgorithm ? '1' : '0';
    r.dataset.layout = c.layout || 'mix';
    if (c.colorPrimary) r.style.setProperty('--primary', c.colorPrimary);
    if (c.colorSuccess) r.style.setProperty('--success', c.colorSuccess);
    if (c.colorWarning) r.style.setProperty('--warning', c.colorWarning);
    if (c.colorError) r.style.setProperty('--danger', c.colorError);
    if (c.fontSize) r.style.setProperty('--font-size', c.fontSize + 'px');
    if (c.controlHeight) r.style.setProperty('--control-h', c.controlHeight + 'px');
    if (c.borderRadius != null) {
      r.style.setProperty('--radius', c.borderRadius + 'px');
      r.style.setProperty('--radius-lg', (c.borderRadius + 4) + 'px');
    }
    if (c.siderWidth) r.style.setProperty('--sider-w', c.siderWidth + 'px');
    if (c.contentPadding != null) r.style.setProperty('--content-pad', c.contentPadding + 'px');
  } catch (e) {
    /* 缓存损坏时按默认主题渲染 */
  }
})();
