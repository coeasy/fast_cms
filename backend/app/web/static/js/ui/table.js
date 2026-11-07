// @ts-check
/* ui/table.js —— 数据表格与分页。
   表格的 <thead> 与筛选表单写在 HTML 模板里，这里只负责拉数据、填 <tbody>。 */

import { $, $$, esc } from '../core/base.js';
import { emptyRow } from '../core/other.js';

export class DataTable {
  /**
   * @param {object} options
   * @param {HTMLTableElement} options.table 表格元素（<thead> 已在模板中写好）
   * @param {HTMLElement} [options.pager] 分页容器
   * @param {(params: object) => Promise<{items: any[], total: number}>} options.fetch 取数函数
   * @param {Array<{key?: string, render?: Function, className?: string, width?: number}>} options.columns
   * @param {boolean} [options.selectable] 开启行多选：渲染复选框列并维护跨页选中集
   * @param {string} [options.rowKey] 行唯一键字段名，默认 `'id'`
   * @param {number} [options.pageSize] 默认每页条数，默认 10
   * @param {string} [options.empty] 空态文案，默认 `'暂无数据'`
   * @param {(rows: any[], total: number) => void} [options.onLoaded] 每次加载完成的回调
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
      const box = /** @type {HTMLInputElement|null} */ (
        /** @type {Element} */ (event.target).closest('input[data-row-check]')
      );
      if (!box) return;
      const id = String(box.dataset.rowCheck);
      if (box.checked) this.selected.add(id);
      else this.selected.delete(id);
      this._syncHeaderCheckbox();
      this._emitSelection();
    });
    // 表头全选框：只作用于当前页已加载的行，跨页选中由 selected 集合保留
    const header = /** @type {HTMLInputElement|null} */ ($('#check-all', this.table));
    header?.addEventListener('change', () => this.toggleAll(header.checked));
  }

  _syncHeaderCheckbox() {
    const header = /** @type {HTMLInputElement|null} */ ($('#check-all', this.table));
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

  /**
   * 取数并渲染。
   *
   * 用「请求序号 + 最新胜出」而不是「加载中就丢弃」：后者有两个真问题——
   * 用户在加载过程中改筛选条件时，``search()`` 已经把 params 换掉，但这次
   * 请求被直接 return 掉，界面就停在旧数据上，直到下次翻页才「突然」生效；
   * 连点两次翻页同理，第二次点击石沉大海。
   *
   * 现在允许并发，只有序号最新的一次请求能把结果写进表格，先发后到的旧响应
   * 直接丢弃，不会覆盖新数据。
   */
  async load(page = this.page) {
    const seq = (this._seq || 0) + 1;
    this._seq = seq;
    this.loading = true;
    this.page = Math.max(1, page);
    this.tbody.innerHTML = `<tr><td colspan="${this.colspan}"><div class="loading-block" role="status" aria-live="polite"><span class="spinner" aria-hidden="true"></span><span>加载中...</span></div></td></tr>`;
    try {
      const result = await this.fetcher({ page: this.page, page_size: this.pageSize, ...this.params });
      if (seq !== this._seq) return;
      this.items = result?.items || [];
      this.total = Number(result?.total || 0);
      this.renderRows();
      this.renderPager();
      if (this.onLoaded) this.onLoaded(this.items, this.total);
    } catch (error) {
      if (seq !== this._seq) return;
      this.items = [];
      this.tbody.innerHTML = emptyRow(this.colspan, error?.message || '加载失败');
    } finally {
      if (seq === this._seq) this.loading = false;
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
    const { pages, current, numbers } = pageRange(this.total, this.pageSize, this.page);
    this.pager.setAttribute('role', 'navigation');
    this.pager.setAttribute('aria-label', '分页');

    this.pager.innerHTML = `
      <span class="total" aria-live="polite">共 ${this.total} 条</span>
      <select data-page-size aria-label="每页条数">
        ${[10, 20, 50, 100].map((size) => `<option value="${size}" ${size === this.pageSize ? 'selected' : ''}>${size} 条/页</option>`).join('')}
      </select>
      <button data-page="${current - 1}" aria-label="上一页" ${current <= 1 ? 'disabled' : ''}>上一页</button>
      ${numbers.map((page) => `<button data-page="${page}" class="${page === current ? 'active' : ''}" ${page === current ? 'aria-current="page"' : ''}>${page}</button>`).join('')}
      <button data-page="${current + 1}" aria-label="下一页" ${current >= pages ? 'disabled' : ''}>下一页</button>`;

    this.pager.querySelector('[data-page-size]')?.addEventListener('change', (event) => {
      this.pageSize = Number(/** @type {HTMLSelectElement} */ (event.target).value);
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

/**
 * 纯函数：计算分页的页码窗口（renderPager 与 `node --test` 单测共用）。
 *
 * 返回 ``{ pages, current, start, end, numbers }``：``numbers`` 是 5 页窗口，
 * 居中于 current，首末页自动收敛。把计算抽成纯函数，分页行为才能被单测钉住。
 */
export function pageRange(total, pageSize, page) {
  const size = Math.max(1, Number(pageSize) || 1);
  const pages = Math.max(1, Math.ceil(Number(total) / size));
  const current = Math.min(Math.max(1, Number(page) || 1), pages);
  const numbers = [];
  const start = Math.max(1, Math.min(current - 2, pages - 4));
  const end = Math.min(pages, start + 4);
  for (let index = start; index <= end; index += 1) numbers.push(index);
  return { pages, current, start, end, numbers };
}
