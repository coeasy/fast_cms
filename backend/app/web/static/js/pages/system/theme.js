import {
  $, $$, api, applyTheme, can, closeModal, confirmDialog, DEFAULT_THEME, DEFAULT_THEME_CODE,
  esc, fillForm, openModal, resetForm, setLoading, themeState, toast, validateForm,
} from '../../core.js';
import { actionButtons } from '../../ui.js';

const OPTIONAL_COLORS = ['colorBgLayout', 'colorBgContainer', 'colorBgSider', 'colorBgHeader'];

let themes = [];
let editing = null;
let activeTheme = null;

export async function init() {
  bind();
  await load();
}

function bind() {
  $('#btn-refresh')?.addEventListener('click', () => load());
  $('#btn-create')?.addEventListener('click', () => openEditor(null));

  $('#theme-grid').addEventListener('click', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const card = button.closest('[data-id]');
    const theme = themes.find((item) => item.id === Number(card?.dataset.id));
    if (!theme) return;
    if (button.dataset.act === 'edit') openEditor(theme);
    else if (button.dataset.act === 'activate') activate(theme);
    else if (button.dataset.act === 'delete') remove(theme);
  });

  $('#theme-form').addEventListener('submit', submit);
  $('#theme-form').addEventListener('input', (event) => {
    if (event.target.matches('[data-preview]')) preview();
  });
  $('#theme-form').addEventListener('change', (event) => {
    if (event.target.matches('[data-preview]')) preview();
  });
  $$('[data-clear-color]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = $(`#theme-form [name="${button.dataset.clearColor}"]`);
      if (!input) return;
      input.value = '#ffffff';
      input.dataset.empty = '1';
      preview();
    });
  });

  document.getElementById('theme-modal')?.addEventListener('click', (event) => {
    if (event.target.closest('[data-modal-close]') || event.target.classList.contains('mask')) {
      restoreActiveTheme();
    }
  });
}

async function load() {
  const host = $('#theme-grid');
  host.innerHTML = '<div class="loading-block"><span class="spinner"></span></div>';
  try {
    themes = await api.get('/system/themes');
    activeTheme = themes.find((item) => item.is_active) || null;
    if (!activeTheme) {
      try {
        const pub = await api.get('/system/themes/public', null, { silent: true });
        activeTheme = pub?.active || null;
      } catch {
        activeTheme = null;
      }
    }
    render();
  } catch (error) {
    host.innerHTML = `<div class="card"><div class="table-empty">${esc(error?.message || '加载失败')}</div></div>`;
  }
}

function render() {
  const host = $('#theme-grid');
  if (!themes.length) {
    host.innerHTML = '<div class="card"><div class="table-empty">暂无主题模板</div></div>';
    return;
  }
  host.innerHTML = themes
    .map((theme) => {
      const config = { ...DEFAULT_THEME, ...(theme.config || {}) };
      const isActive = activeTheme?.id === theme.id || theme.is_active;
      return `
      <div class="card" data-id="${theme.id}" style="${isActive ? 'border-color:var(--primary)' : ''}">
        <div style="height:88px;background:${config.darkAlgorithm ? '#141414' : config.colorBgLayout || '#f5f7fa'};border-radius:var(--radius-lg) var(--radius-lg) 0 0;display:flex;align-items:center;gap:10px;padding:0 16px;border-bottom:1px solid var(--split)">
          <span style="width:34px;height:34px;border-radius:10px;background:${config.colorPrimary}"></span>
          <span style="width:10px;height:10px;border-radius:50%;background:${config.colorSuccess}"></span>
          <span style="width:10px;height:10px;border-radius:50%;background:${config.colorWarning}"></span>
          <span style="width:10px;height:10px;border-radius:50%;background:${config.colorError}"></span>
          <span style="margin-left:auto;font-size:12px;color:${config.darkAlgorithm ? '#aaa' : '#666'}">${esc(config.layout)}</span>
        </div>
        <div class="card-body" style="padding:16px">
          <div class="row between" style="gap:8px">
            <div style="min-width:0">
              <div class="strong">${esc(theme.name)}</div>
              <div class="tiny muted mono">${esc(theme.code)}</div>
            </div>
            <div class="tag-row">
              ${isActive ? '<span class="tag green">生效中</span>' : ''}
              ${theme.is_builtin ? '<span class="tag gold">内置</span>' : ''}
            </div>
          </div>
          <div class="small muted mt-8" style="min-height:40px">${esc(theme.description || '—')}</div>
          <div class="btn-row mt-8">
            ${isActive ? '' : `<button class="btn btn-sm btn-primary" data-act="activate">应用</button>`}
            ${can('system:theme:update') ? '<button class="btn btn-sm" data-act="edit">编辑</button>' : ''}
            ${can('system:theme:delete') && !theme.is_builtin ? '<button class="btn btn-sm btn-danger" data-act="delete">删除</button>' : ''}
          </div>
        </div>
      </div>`;
    })
    .join('');
}

function openEditor(theme) {
  editing = theme;
  const form = $('#theme-form');
  resetForm(form);
  $('#theme-modal-title').textContent = theme ? `编辑主题 ${theme.name}` : '新建主题';
  $('#th-code').disabled = Boolean(theme);

  const config = { ...DEFAULT_THEME, ...(theme?.config || {}) };
  fillForm(form, {
    name: theme?.name || '',
    code: theme?.code || '',
    description: theme?.description || '',
    colorPrimary: config.colorPrimary,
    colorSuccess: config.colorSuccess,
    colorWarning: config.colorWarning,
    colorError: config.colorError,
    layout: config.layout || 'side',
    navTheme: config.navTheme || 'light',
    borderRadius: config.borderRadius,
    fontSize: config.fontSize,
    controlHeight: config.controlHeight,
    siderWidth: config.siderWidth,
    contentPadding: config.contentPadding,
    componentSize: config.componentSize || 'middle',
    watermark: config.watermark || '',
    darkAlgorithm: config.darkAlgorithm,
    compactAlgorithm: config.compactAlgorithm,
    tableCompact: config.tableCompact,
    showBreadcrumb: config.showBreadcrumb,
    fixedHeader: config.fixedHeader,
    colorWeak: config.colorWeak,
  });

  OPTIONAL_COLORS.forEach((name) => {
    const input = form.elements[name];
    if (!input) return;
    if (config[name]) {
      input.value = config[name];
      input.dataset.empty = '0';
    } else {
      input.value = '#ffffff';
      input.dataset.empty = '1';
    }
  });

  openModal('theme-modal');
  preview();
}

function buildConfig() {
  const form = $('#theme-form');
  const value = (name) => form.elements[name]?.value ?? '';
  const checked = (name) => Boolean(form.elements[name]?.checked);
  const num = (name, fallback) => {
    const parsed = Number(value(name));
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const config = {
    colorPrimary: value('colorPrimary'),
    colorSuccess: value('colorSuccess'),
    colorWarning: value('colorWarning'),
    colorError: value('colorError'),
    borderRadius: num('borderRadius', DEFAULT_THEME.borderRadius),
    navTheme: value('navTheme') || 'light',
    layout: value('layout') || 'side',
    darkAlgorithm: checked('darkAlgorithm'),
    compactAlgorithm: checked('compactAlgorithm'),
    fixedHeader: checked('fixedHeader'),
    splitMenus: false,
    colorWeak: checked('colorWeak'),
    fontSize: num('fontSize', DEFAULT_THEME.fontSize),
    controlHeight: num('controlHeight', DEFAULT_THEME.controlHeight),
    componentSize: value('componentSize') || 'middle',
    contentPadding: num('contentPadding', DEFAULT_THEME.contentPadding),
    siderWidth: num('siderWidth', DEFAULT_THEME.siderWidth),
    showBreadcrumb: checked('showBreadcrumb'),
    tableCompact: checked('tableCompact'),
    watermark: value('watermark'),
  };
  OPTIONAL_COLORS.forEach((name) => {
    const input = form.elements[name];
    if (!input) return;
    config[name] = input.dataset.empty === '1' ? null : input.value;
  });
  return config;
}

function preview() {
  applyTheme(buildConfig(), editing?.code || 'preview');
}

function restoreActiveTheme() {
  applyTheme(activeTheme?.config || DEFAULT_THEME, activeTheme?.code || DEFAULT_THEME_CODE);
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!validateForm(form)) return;
  const config = buildConfig();
  const payload = {
    name: form.elements.name.value.trim(),
    description: form.elements.description.value.trim() || null,
    config,
  };
  const button = $('#theme-submit');
  setLoading(button, true);
  try {
    if (editing) {
      await api.put(`/system/themes/${editing.id}`, payload);
      toast('主题已更新', 'success');
    } else {
      payload.code = form.elements.code.value.trim();
      payload.sort = 0;
      await api.post('/system/themes', payload);
      toast('主题创建成功', 'success');
    }
    closeModal('theme-modal');
    await load();
    restoreActiveTheme();
  } catch {
    /* 请求层已提示 */
  } finally {
    setLoading(button, false);
  }
}

async function activate(theme) {
  try {
    await api.post(`/system/themes/${theme.id}/activate`);
    toast(`已启用主题「${theme.name}」`, 'success');
    await load();
    applyTheme(activeTheme?.config || DEFAULT_THEME, activeTheme?.code || DEFAULT_THEME_CODE);
  } catch {
    /* 请求层已提示 */
  }
}

async function remove(theme) {
  const ok = await confirmDialog({
    title: '删除主题',
    message: `确认删除主题「${theme.name}」？内置主题不可删除。`,
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/system/themes/${theme.id}`);
    toast('主题已删除', 'success');
    await load();
  } catch {
    /* 请求层已提示 */
  }
}
