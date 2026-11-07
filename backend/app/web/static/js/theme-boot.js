/* 首屏先套用上次缓存的主题，避免刷新时闪一下默认蓝。
   独立成文件（而不是内联脚本）是为了让 CSP 可以禁用 unsafe-inline，
   也因此无法 import core/theme.js——普通脚本必须同步执行，模块脚本是异步的。

   ⚠️ 这里的变量清单必须与 core/theme.js 的 applyTheme 保持一致：
   少一个变量，首屏就会用默认值渲染一次，随后被 applyTheme 覆盖，表现为
   一次可见的「跳色」（尤其是 --primary-weak 与 --brand-gradient）。
   tests/frontend/theme-boot.test.mjs 会比对两边的变量名，漏加会失败。 */
(function () {
  'use strict';

  function lighten(hex, ratio) {
    var value = parseInt(hex.slice(1), 16);
    function channel(shift) {
      var raw = (value >> shift) & 255;
      return Math.round(raw + (255 - raw) * ratio);
    }
    return (
      '#' +
      [channel(16), channel(8), channel(0)]
        .map(function (n) {
          return n.toString(16).padStart(2, '0');
        })
        .join('')
    );
  }

  try {
    var raw = localStorage.getItem('fastcms_theme_snapshot');
    if (!raw) return;
    var snap = JSON.parse(raw) || {};
    var c = snap.config || {};
    var r = document.documentElement;
    var dark = !!c.darkAlgorithm;

    r.dataset.dark = dark ? '1' : '0';
    r.dataset.layout = c.layout || 'mix';
    if (c.navTheme) r.dataset.navTheme = c.navTheme;
    r.dataset.colorWeak = c.colorWeak ? '1' : '0';
    r.dataset.fixedHeader = c.fixedHeader ? '1' : '0';
    r.dataset.breadcrumb = c.showBreadcrumb ? '1' : '0';

    if (c.colorPrimary) {
      r.style.setProperty('--primary', c.colorPrimary);
      r.style.setProperty('--primary-weak', c.colorPrimary + '1f');
      r.style.setProperty(
        '--brand-gradient',
        'linear-gradient(135deg, ' + c.colorPrimary + ' 0%, ' + lighten(c.colorPrimary, 0.3) + ' 100%)',
      );
    }
    if (c.colorSuccess) r.style.setProperty('--success', c.colorSuccess);
    if (c.colorWarning) r.style.setProperty('--warning', c.colorWarning);
    if (c.colorError) r.style.setProperty('--danger', c.colorError);
    if (c.fontSize) {
      r.style.setProperty('--font-size', c.fontSize + 'px');
      r.style.setProperty('--font-size-sm', Math.max(11, c.fontSize - 1) + 'px');
    }
    if (c.controlHeight) r.style.setProperty('--control-h', c.controlHeight + 'px');
    if (c.borderRadius != null) {
      r.style.setProperty('--radius', c.borderRadius + 'px');
      r.style.setProperty('--radius-lg', c.borderRadius + 4 + 'px');
      r.style.setProperty('--radius-sm', Math.max(2, c.borderRadius - 2) + 'px');
    }
    if (c.siderWidth) r.style.setProperty('--sider-w', c.siderWidth + 'px');
    if (c.contentPadding != null) r.style.setProperty('--content-pad', c.contentPadding + 'px');
    r.style.setProperty('--table-head-bg', c.tableHeaderBg || (dark ? '#1a1e24' : '#fafbfd'));
    r.style.setProperty('--table-cell-y', c.tableCompact ? '7px' : '12px');
    if (c.colorBgLayout) r.style.setProperty('--bg-layout', c.colorBgLayout);
    if (c.colorBgContainer) r.style.setProperty('--bg-container', c.colorBgContainer);
    if (c.colorBgSider) r.style.setProperty('--bg-sider', c.colorBgSider);
    if (c.colorBgHeader) r.style.setProperty('--bg-header', c.colorBgHeader);
    if (c.colorLink) r.style.setProperty('--link', c.colorLink);
  } catch (e) {
    /* 缓存损坏时按默认主题渲染 */
  }
})();
