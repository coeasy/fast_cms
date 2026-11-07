// @ts-check
/* ==========================================================================
   core/theme.js —— 主题引擎
   把主题配置映射成 CSS 变量；主题模板里的每一项都对应一个变量，
   未配置的项保持样式表默认值。缓存写入 localStorage 供首屏预置。
   ========================================================================== */

import { storage, esc } from './base.js';

const THEME_CACHE = 'fastcms_theme_snapshot';
const THEME_PREF = 'fastcms_theme_code';

export const DEFAULT_THEME = {
  colorPrimary: '#1677ff',
  colorSuccess: '#52c41a',
  colorWarning: '#faad14',
  colorError: '#ff4d4f',
  borderRadius: 6,
  navTheme: 'light',
  layout: 'mix',
  darkAlgorithm: false,
  compactAlgorithm: false,
  fixedHeader: true,
  splitMenus: false,
  colorWeak: false,
  fontSize: 14,
  controlHeight: 32,
  componentSize: 'middle',
  contentPadding: 24,
  siderWidth: 208,
  showBreadcrumb: true,
  tableCompact: false,
  watermark: '',
};

export const DEFAULT_THEME_CODE = 'classic-blue';

function isHex(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
}

function lighten(hex, ratio) {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift) => {
    const raw = (value >> shift) & 255;
    return Math.round(raw + (255 - raw) * ratio);
  };
  return `#${[channel(16), channel(8), channel(0)].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
}

function darken(hex, ratio) {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift) => Math.round(((value >> shift) & 255) * (1 - ratio));
  return `#${[channel(16), channel(8), channel(0)].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
}

export function brandGradient(hex) {
  const base = isHex(hex) ? hex : DEFAULT_THEME.colorPrimary;
  return `linear-gradient(135deg, ${base} 0%, ${lighten(base, 0.3)} 100%)`;
}

export function heroGradient(hex, dark = false) {
  const base = isHex(hex) ? hex : DEFAULT_THEME.colorPrimary;
  const from = darken(base, dark ? 0.78 : 0.55);
  const mid = darken(base, dark ? 0.4 : 0.22);
  return `linear-gradient(135deg, ${from} 0%, ${mid} 55%, ${lighten(base, 0.12)} 100%)`;
}

export function heroGlow(hex, alpha = 0.28) {
  const base = isHex(hex) ? hex : DEFAULT_THEME.colorPrimary;
  const value = Number.parseInt(base.slice(1), 16);
  const channel = (shift) => (value >> shift) & 255;
  return `rgba(${channel(16)}, ${channel(8)}, ${channel(0)}, ${alpha})`;
}

export const themeState = { code: DEFAULT_THEME_CODE, config: { ...DEFAULT_THEME } };

export function getPreferredThemeCode() {
  return storage()?.getItem(THEME_PREF) || null;
}

export function setPreferredThemeCode(code) {
  const store = storage();
  if (!store) return;
  if (code) store.setItem(THEME_PREF, code);
  else store.removeItem(THEME_PREF);
}

export function applyTheme(config, code = DEFAULT_THEME_CODE, { persist = false } = {}) {
  const merged = { ...DEFAULT_THEME, ...(config || {}) };
  themeState.code = code || DEFAULT_THEME_CODE;
  themeState.config = merged;

  const root = document.documentElement;
  const dark = Boolean(merged.darkAlgorithm);
  root.dataset.dark = dark ? '1' : '0';

  const set = (name, value) => {
    if (value === undefined || value === null || value === '') root.style.removeProperty(name);
    else root.style.setProperty(name, String(value));
  };

  set('--primary', merged.colorPrimary);
  set('--success', merged.colorSuccess);
  set('--warning', merged.colorWarning);
  set('--danger', merged.colorError);
  set('--primary-weak', `${isHex(merged.colorPrimary) ? merged.colorPrimary : DEFAULT_THEME.colorPrimary}1f`);
  set('--radius', `${merged.borderRadius}px`);
  set('--radius-lg', `${merged.borderRadius + 4}px`);
  set('--radius-sm', `${Math.max(2, merged.borderRadius - 2)}px`);
  set('--font-size', `${merged.fontSize}px`);
  set('--font-size-sm', `${Math.max(11, merged.fontSize - 1)}px`);
  set('--control-h', `${merged.controlHeight}px`);
  set('--sider-w', `${merged.siderWidth}px`);
  set('--content-pad', `${merged.contentPadding}px`);
  set('--brand-gradient', brandGradient(merged.colorPrimary));
  set('--table-head-bg', merged.tableHeaderBg || (dark ? '#1a1e24' : '#fafbfd'));
  set('--table-cell-y', merged.tableCompact ? '7px' : '12px');

  if (merged.colorBgLayout) set('--bg-layout', merged.colorBgLayout);
  else root.style.removeProperty('--bg-layout');
  if (merged.colorBgContainer) set('--bg-container', merged.colorBgContainer);
  else root.style.removeProperty('--bg-container');
  if (merged.colorBgSider) set('--bg-sider', merged.colorBgSider);
  else root.style.removeProperty('--bg-sider');
  if (merged.colorBgHeader) set('--bg-header', merged.colorBgHeader);
  else root.style.removeProperty('--bg-header');
  if (merged.colorLink) set('--link', merged.colorLink);
  else root.style.removeProperty('--link');

  root.dataset.layout = merged.layout || 'mix';
  root.dataset.navTheme = merged.navTheme || 'light';
  root.dataset.colorWeak = merged.colorWeak ? '1' : '0';
  root.dataset.fixedHeader = merged.fixedHeader ? '1' : '0';
  root.dataset.breadcrumb = merged.showBreadcrumb ? '1' : '0';

  applyWatermark(merged.watermark);
  document.dispatchEvent(new CustomEvent('theme:change', { detail: themeState }));

  const store = storage();
  if (store) {
    try {
      store.setItem(THEME_CACHE, JSON.stringify(themeState));
      if (persist) store.setItem(THEME_PREF, themeState.code);
    } catch {
      /* 主题写缓存失败不影响渲染 */
    }
  }
}

function applyWatermark(text) {
  let node = document.getElementById('fc-watermark');
  if (!text) {
    node?.remove();
    return;
  }
  if (!node) {
    node = document.createElement('div');
    node.id = 'fc-watermark';
    node.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:1;overflow:hidden;';
    document.body.appendChild(node);
  }
  const rows = 12;
  const cols = 8;
  let html = '';
  for (let r = 0; r < rows; r += 1) {
    html += '<div style="display:flex">';
    for (let c = 0; c < cols; c += 1) {
      html += `<span style="display:inline-block;width:220px;padding:40px 0 0 30px;transform:rotate(-22deg);color:var(--text-4);opacity:.28;font-size:13px;white-space:nowrap">${esc(text)}</span>`;
    }
    html += '</div>';
  }
  node.innerHTML = html;
}
