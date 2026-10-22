/* ==========================================================================
   ui.js —— 数据表格、分页、下拉与树形工具
   表格的 <thead> 与筛选表单写在 HTML 模板里，这里只负责拉数据、填 <tbody>。
   ========================================================================== */

import {
  $, $$, confirmDialog, esc, emptyRow, setLoading, syncMultiSelects, toast,
} from './core.js';

export class DataTable {
  /**
   * @param {object} options
   * @param {HTMLTableElement} options.table 表格元素（<thead> 已在模板中写好）
   * @param {HTMLElement} [options.pager] 分页容器
   * @param {(params: object) => Promise<{items: any[], total: number}>} options.fetch 取数函数
   * @param {Array<{key?: string, render?: Function, className?: string, width?: number}>} options.columns
   * @param {boolean} [options.selectable] 开启行多选：渲染复选框列并维护跨页选中集
   * @param {(ids: Array<number|string>) => void} [options.onSelectionChange] 选中集变化回调
   */
  constructor(options) {
    this.table = options.table;
    this.tbody = this.table.querySelector('tbody');
    this.pager = options.pager || null;
    this.fetcher = options.fetch;
    this.columns = options.columns || [];
    this.rowKey = options.rowKey || 'id';
    this.pageSize = options.pageSize || 10;
    this.emptyText = options.empty || '暂无数据';
    this.onLoaded = options.onLoaded;
    this.selectable = Boolean(options.selectable);
    this.onSelectionChange = options.onSelectionChange;
    // 选中集按 id 存字符串，翻页 / 重新加载都不丢。
    // 只保留「当前页」的选中是最常见的误设计——用户翻到第 2 页再回来就全没了。
    this.selected = new Set();
    this.params = {};
    this.page = 1;
    this.total = 0;
    this.items = [];
    this.loading = false;
    this._bindSelection();
  }

  /** 复选框列用事件委托：勾选后只改选中集，不整表重绘，避免打断正在进行的请求 */
  _bindSelection() {
    if (!this.selectable) return;
    this.tbody.addEventListener('change', (event) => {
      const box = event.target.closest('input[data-row-check]');
      if (!box) return;
      const id = String(box.dataset.rowCheck);
      if (box.checked) this.selected.add(id);
      else this.selected.delete(id);
      this._syncHeaderCheckbox();
      this._emitSelection();
    });
    // 表头全选框：只作用于当前页已加载的行，跨页选中由 selected 集合保留
    const header = $('#check-all', this.table);
    header?.addEventListener('change', () => this.toggleAll(header.checked));
  }

  _syncHeaderCheckbox() {
    const header = $('#check-all', this.table);
    if (!header) return;
    const ids = this.items.map((item) => String(item[this.rowKey]));
    const checked = ids.filter((id) => this.selected.has(id)).length;
    header.checked = ids.length > 0 && checked === ids.length;
    header.indeterminate = checked > 0 && checked < ids.length;
  }

  _emitSelection() {
    if (this.onSelectionChange) this.onSelectionChange(this.selectedIds);
  }

  /** 全选 / 反选：只作用于当前页已加载的行 */
  toggleAll(checked) {
    this.items.forEach((item) => {
      const id = String(item[this.rowKey]);
      if (checked) this.selected.add(id);
      else this.selected.delete(id);
    });
    this.renderRows();
    this._syncHeaderCheckbox();
    this._emitSelection();
  }

  /** 供批量接口直接使用；纯字符串 id 也能安全返回 */
  get selectedIds() {
    return [...this.selected].map((value) =>
      value !== '' && Number.isFinite(Number(value)) ? Number(value) : value,
    );
  }

  clearSelection() {
    this.selected.clear();
    this.renderRows();
    this._syncHeaderCheckbox();
    this._emitSelection();
  }

  /** 列数 + 复选框列，供占位行与加载态算 colspan */
  get colspan() {
    return Math.max(1, this.columns.length + (this.selectable ? 1 : 0));
  }

  async load(page = this.page) {
    if (this.loading) return;
    this.loading = true;
    this.page = Math.max(1, page);
    this.tbody.innerHTML = `<tr><td colspan="${this.colspan}"><div class="loading-block"><span class="spinner"></span><span>加载中...</span></div></td></tr>`;
    try {
      const result = await this.fetcher({ page: this.page, page_size: this.pageSize, ...this.params });
      this.items = result?.items || [];
      this.total = Number(result?.total || 0);
      this.renderRows();
      this.renderPager();
      if (this.onLoaded) this.onLoaded(this.items, this.total);
    } catch (error) {
      this.items = [];
      this.tbody.innerHTML = emptyRow(this.colspan, error?.message || '加载失败');
    } finally {
      this.loading = false;
    }
  }

  renderRows() {
    if (!this.items.length) {
      this.tbody.innerHTML = emptyRow(this.colspan, this.emptyText);
      return;
    }
    this.tbody.innerHTML = this.items
      .map((item) => {
        const cells = this.columns
          .map((column) => {
            const value = column.render ? column.render(item) : esc(item[column.key] ?? '-');
            const style = column.width ? ` style="width:${column.width}px"` : '';
            return `<td class="${column.className || ''}"${style}>${value === undefined || value === null || value === '' ? '-' : value}</td>`;
          })
          .join('');
        const id = String(item[this.rowKey]);
        const box = this.selectable
          ? `<td class="checkbox-cell"><input type="checkbox" data-row-check="${esc(id)}" ${this.selected.has(id) ? 'checked' : ''} aria-label="选择该行"></td>`
          : '';
        return `<tr data-id="${esc(id)}">${box}${cells}</tr>`;
      })
      .join('');
    this._syncHeaderCheckbox();
  }

  renderPager() {
    if (!this.pager) return;
    const pages = Math.max(1, Math.ceil(this.total / this.pageSize));
    const current = Math.min(this.page, pages);
    const numbers = [];
    const start = Math.max(1, Math.min(current - 2, pages - 4));
    const end = Math.min(pages, start + 4);
    for (let index = start; index <= end; index += 1) numbers.push(index);

    this.pager.innerHTML = `
      <span class="total">共 ${this.total} 条</span>
      <select data-page-size>
        ${[10, 20, 50, 100].map((size) => `<option value="${size}" ${size === this.pageSize ? 'selected' : ''}>${size} 条/页</option>`).join('')}
      </select>
      <button data-page="${current - 1}" ${current <= 1 ? 'disabled' : ''}>上一页</button>
      ${numbers.map((page) => `<button data-page="${page}" class="${page === current ? 'active' : ''}">${page}</button>`).join('')}
      <button data-page="${current + 1}" ${current >= pages ? 'disabled' : ''}>下一页</button>`;

    this.pager.querySelector('[data-page-size]')?.addEventListener('change', (event) => {
      this.pageSize = Number(event.target.value);
      this.load(1);
    });
    $$('[data-page]', this.pager).forEach((button) => {
      button.addEventListener('click', () => {
        const page = Number(button.dataset.page);
        if (page >= 1 && page <= pages && page !== current) this.load(page);
      });
    });
  }

  /** 更新筛选条件并回到第一页 */
  search(params = {}) {
    this.params = { ...params };
    return this.load(1);
  }

  reload() {
    return this.load(this.page);
  }

  get rows() {
    return this.items;
  }

  find(id) {
    return this.items.find((item) => String(item[this.rowKey]) === String(id));
  }
}

/* -------------------------------- 树工具 -------------------------------- */

/** 把后端返回的树拍平成带层级标记的列表，用于表格展示 */
export function flattenTree(nodes, depth = 0, out = []) {
  (nodes || []).forEach((node) => {
    out.push({ ...node, __depth: depth });
    if (node.children?.length) flattenTree(node.children, depth + 1, out);
  });
  return out;
}

export function indentLabel(text, depth, extra = '') {
  const pad = depth * 18;
  const branch = depth > 0 ? '<span style="color:var(--text-4);margin-right:6px">└</span>' : '';
  return `<span style="padding-left:${pad}px;display:inline-flex;align-items:center;gap:4px">${branch}<span>${esc(text)}</span>${extra}</span>`;
}

export function buildOptions(list, valueKey = 'id', labelKey = 'name') {
  return (list || []).map((item) => ({ value: String(item[valueKey]), label: item[labelKey] }));
}

export function fillSelect(select, options, { placeholder, keep } = {}) {
  if (!select) return;
  const previous = select.value;
  const items = (options || []).map((option) =>
    typeof option === 'object'
      ? { value: String(option.value), label: option.label }
      : { value: String(option), label: String(option) },
  );
  const head = placeholder !== undefined ? `<option value="">${esc(placeholder)}</option>` : '';
  select.innerHTML = head + items.map((option) => `<option value="${esc(option.value)}">${esc(option.label)}</option>`).join('');
  if (keep && previous) {
    const match = items.find((option) => option.value === previous);
    if (match) select.value = previous;
  }
}

/** 树形下拉用缩进模拟层级（原生 select 不支持真正的树） */
export function treeOptions(nodes, depth = 0, out = []) {
  (nodes || []).forEach((node) => {
    out.push({ value: String(node.id), label: `${'　'.repeat(depth)}${node.name}` });
    if (node.children?.length) treeOptions(node.children, depth + 1, out);
  });
  return out;
}

/* -------------------------------- 小部件 -------------------------------- */

export function tag(text, color = 'gray') {
  return `<span class="tag ${color}">${esc(text)}</span>`;
}

export function boolTag(value, trueText = '是', falseText = '否') {
  return value ? tag(trueText, 'green') : tag(falseText, 'gray');
}

/** 行内操作按钮 */
export function actionButtons(actions) {
  return actions
    .filter(Boolean)
    .map(
      (action) =>
        `<button class="btn-link ${action.danger ? 'danger' : ''}" data-act="${esc(action.act)}">${esc(action.label)}</button>`,
    )
    .join(' ');
}

export function setText(selector, text, root = document) {
  const node = typeof selector === 'string' ? $(selector, root) : selector;
  if (node) node.textContent = text;
}

export function show(node, visible = true) {
  if (!node) return;
  node.classList.toggle('hidden', !visible);
}

export function emptyState(text) {
  return `<div class="table-empty"><div class="big">∅</div>${esc(text)}</div>`;
}


/** 填充 multi-select 组件（隐藏的 select + 可视面板） */
export function fillMultiSelect(wrapper, options) {
  if (!wrapper) return;
  const select = wrapper.querySelector('select[multiple]');
  const panel = wrapper.querySelector('.ms-panel');
  const items = (options || []).map((option) => ({ value: String(option.value), label: option.label }));
  if (select) {
    select.innerHTML = items.map((item) => `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join('');
  }
  if (panel) {
    panel.innerHTML = items.length
      ? items.map((item) => `<div class="ms-option" data-value="${esc(item.value)}">${esc(item.label)}</div>`).join('')
      : '<div class="ms-option muted">暂无可选项</div>';
  }
  syncMultiSelects(document);
}

export function selectedValues(select) {
  if (!select) return [];
  return Array.from(select.selectedOptions).map((option) => Number(option.value));
}

/* ------------------------------ 批量操作工具栏 ------------------------------ */

/**
 * 把「表格多选」与「批量操作条」绑在一起。
 *
 * 各个列表页的批量逻辑只差一个端点 + 一段确认文案，所以收到这里：
 * 页面只声明 actions，勾选/取消/确认/请求/结果提示全部复用。
 *
 * @param {object} options
 * @param {DataTable} options.table 已开启 selectable 的表格
 * @param {HTMLElement} [options.bar] 工具栏容器，隐藏与显示由本函数管理
 * @param {Array<{act: string, label: string, danger?: boolean, confirm?: string,
 *                run: (ids: Array<number>) => Promise<{ok?: number, failed?: number, message?: string}>}>} options.actions
 */
export function bindBatchBar({ table, bar, actions }) {
  if (!bar) return;
  const countNode = $('[data-batch-count]', bar);

  const refresh = (ids) => {
    bar.hidden = ids.length === 0;
    if (countNode) countNode.textContent = `已选 ${ids.length} 项`;
  };

  table.onSelectionChange = refresh;
  refresh(table.selectedIds);

  bar.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-batch-act]');
    if (!button) return;
    const act = button.dataset.batchAct;

    if (act === 'clear') {
      table.clearSelection();
      return;
    }

    const ids = table.selectedIds;
    if (!ids.length) {
      toast('请先勾选要操作的行', 'warning');
      return;
    }
    const action = actions.find((item) => item.act === act);
    if (!action) return;

    if (action.confirm) {
      const ok = await confirmDialog({
        title: action.label,
        message: action.confirm.replace('{n}', ids.length),
        okText: '确定执行',
        danger: Boolean(action.danger),
      });
      if (!ok) return;
    }

    setLoading(button, true);
    try {
      const result = await action.run(ids);
      const failed = Number(result?.failed || 0);
      const ok = Number(result?.ok ?? ids.length - failed);
      if (failed > 0) toast(`已完成 ${ok} 项，${failed} 项失败`, 'warning');
      else toast(result?.message || `已完成 ${ok} 项`, 'success');
      table.clearSelection();
      table.reload();
    } catch {
      /* 请求层已提示 */
    } finally {
      setLoading(button, false);
    }
  });
}
