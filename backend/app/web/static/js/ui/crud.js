// @ts-check
/* ==========================================================================
   ui/crud.js —— 标准「列表 + 弹窗表单」页面的骨架
   ========================================================================== */

import { $ } from '../core/base.js';
import { closeModal, confirmDialog, openModal, toast } from '../core/dialog.js';
import { fillForm, resetForm, serializeForm, setLoading, validateForm } from '../core/form.js';
import { msg } from '../core/messages.js';
import { api } from '../core/request.js';
import { DataTable } from './table.js';
import { actionButtons } from './widgets.js';

/**
 * 弹窗表单配置。没有表单的页面（只读列表 + 行内动作）可以整个不传。
 *
 * @typedef {object} CrudForm
 * @property {string} [id] 表单选择器
 * @property {string} [modal] 弹窗 id（openModal/closeModal 用，不带 `#`）
 * @property {string} [titleId] 弹窗标题元素选择器
 * @property {string} [submitId] 提交按钮选择器（用于 loading 态）
 * @property {string} [titleCreate] 新建时的标题
 * @property {(row: any) => string} [titleEdit] 编辑时的标题
 * @property {(form: HTMLFormElement, page: object, preset?: any) => void} [onCreate] 新建时重置后的钩子
 * @property {(form: HTMLFormElement, row: any, page: object) => void} [onEdit] 编辑填充钩子
 * @property {(values: object, row: any) => object|false} [payload]
 *        提交前改造；返回 false 表示校验不通过、中止提交
 */

/**
 * @typedef {object} CrudConfirm
 * @property {string} [title] 确认框标题
 * @property {(row: any) => string} [message] 确认文案（可按行动态生成）
 * @property {string} [okText] 确认按钮文案
 * @property {(result: any, row: any) => void} [afterDelete] 删除请求成功后的自定义提示
 */

/**
 * @typedef {object} CrudOptions
 * @property {string} endpoint 列表与详情共用的 API 前缀，如 `'/system/depts'`
 * @property {string} [tableSelector] 表格选择器，默认 `'table.data'`
 * @property {string} [pagerSelector] 分页容器选择器，默认不启用分页
 * @property {Array<object>} columns DataTable 列定义
 * @property {(params: object) => Promise<{items: any[], total: number}>} [fetch]
 *        自定义取数（默认 `api.get(endpoint, params)`，整表返回时用它包成 items/total）
 * @property {(rows: any[], total: number) => void} [onLoaded] 每次加载完的回调
 * @property {string} [filters] 筛选表单选择器；给了就自动绑 submit/reset
 * @property {boolean} [selectable] 透传给 DataTable：开启行多选
 * @property {string} [rowKey] 透传给 DataTable：行唯一键字段名，默认 `'id'`
 * @property {number} [pageSize] 透传给 DataTable：默认每页条数
 * @property {string} [empty] 透传给 DataTable：空态文案
 * @property {CrudForm} [form] 弹窗表单配置
 * @property {{created?: string, updated?: string, deleted?: string}} [messages] 三个文案 key
 * @property {CrudConfirm} [confirm] 删除确认配置
 * @property {{create?: string, update?: string, delete?: string}} [permissions] 权限码，用于生成默认行内按钮
 * @property {(row: any) => string} [rowActions] 完全自定义行内按钮 HTML
 * @property {Array<{act: string, run: (row: any, page: object) => any}>} [actions]
 *        额外的行内动作处理
 */

/**
 * 生成一个标准 CRUD 页面控制器。
 *
 * 19 个列表页里有十几页是同一套流程：建表 → 拉数据 → 绑刷新/新建 → 行内操作委托 →
 * 弹窗表单 → 提交（loading + toast + 关闭 + 刷新）→ 删除确认。这段样板每页 60~90 行，
 * 抄十遍之后每个页面都长得一样，但改一处交互（比如提交成功后不再关弹窗）要改十遍。
 *
 * 这里只收**完全一致**的部分；凡是页面之间不一样的（默认值、字段联动、额外校验、
 * 行内特殊动作），一律通过钩子交回页面——宁可多一个钩子，也不要把差异塞进配置里。
 *
 * 所有 DOM id 都写成带 `#` 的字符串：`scripts/check_contract.py::check_dom_ids`
 * 会拿它们和渲染后的模板比对，写错一个 id 门禁就失败。
 *
 * @param {CrudOptions} options
 * @returns {object} 页面控制器：`{ table, reload, openCreate, openEdit, remove, find }`
 */
export function createCrudPage(options) {
  const {
    endpoint,
    tableSelector = 'table.data',
    columns,
    fetch: customFetch,
    onLoaded,
    filters,
    form = /** @type {CrudForm} */ ({}),
    messages = {},
    confirm = {},
    permissions = {},
    rowActions,
    actions = [],
  } = options;

  const page = { table: null, editing: null, rows: [] };

  const tableEl = /** @type {HTMLTableElement} */ ($(tableSelector));
  page.table = new DataTable({
    table: tableEl,
    pager: options.pagerSelector ? $(options.pagerSelector) : undefined,
    columns,
    selectable: options.selectable,
    rowKey: options.rowKey,
    pageSize: options.pageSize,
    empty: options.empty,
    onLoaded: (rows, total) => {
      page.rows = rows;
      if (onLoaded) onLoaded(rows, total);
    },
    fetch:
      customFetch ||
      (async (params) => {
        const items = await api.get(endpoint, params);
        const list = Array.isArray(items) ? items : items?.items || [];
        return { items: list, total: Array.isArray(items) ? list.length : Number(items?.total || 0) };
      }),
  });

  /* ------------------------------ 行内按钮 ------------------------------ */

  const defaultActions = (row) => {
    const buttons = [];
    if (permissions.update) buttons.push({ act: 'edit', label: '编辑' });
    if (permissions.delete) buttons.push({ act: 'delete', label: '删除', danger: true });
    return buttons;
  };

  /** 页面没自定义 rowActions 时，按 permissions 生成「编辑 / 删除」两个按钮 */
  const renderActions = (row) => {
    if (rowActions) return rowActions(row);
    return actionButtons(defaultActions(row));
  };

  page.rowActions = renderActions;

  /* ------------------------------ 弹窗表单 ------------------------------ */

  // 没有表单就是「只读列表 + 行内动作」页（如封禁名单、在线会话），
  // 这时 form.id 缺省，所有表单相关逻辑自动跳过。
  const formEl = () => (form.id ? /** @type {HTMLFormElement|null} */ ($(form.id)) : null);

  function setTitle(text) {
    const node = form.titleId ? $(form.titleId) : null;
    if (node) node.textContent = text;
  }

  page.openCreate = (preset) => {
    page.editing = null;
    const element = formEl();
    if (!element) return;
    resetForm(element);
    setTitle(form.titleCreate || '新建');
    if (form.onCreate) form.onCreate(element, page, preset);
    openModal(form.modal);
  };

  page.openEdit = (row) => {
    page.editing = row;
    const element = formEl();
    if (!element) return;
    resetForm(element);
    setTitle(form.titleEdit ? form.titleEdit(row) : '编辑');
    if (form.onEdit) form.onEdit(element, row, page);
    else fillForm(element, row);
    openModal(form.modal);
  };

  async function submit(event) {
    event.preventDefault();
    const element = event.currentTarget;
    if (!validateForm(element)) return;
    let values = serializeForm(element);
    if (form.payload) {
      const next = form.payload(values, page.editing);
      if (next === false) return;
      values = next;
    }
    const button = form.submitId ? $(form.submitId) : null;
    setLoading(button, true);
    try {
      if (page.editing) {
        await api.put(`${endpoint}/${page.editing.id}`, values);
        if (messages.updated) toast(msg(messages.updated), 'success');
      } else {
        await api.post(endpoint, values);
        if (messages.created) toast(msg(messages.created), 'success');
      }
      closeModal(form.modal);
      page.table.reload();
    } catch {
      /* 请求层已提示 */
    } finally {
      setLoading(button, false);
    }
  }

  page.remove = async (row) => {
    const ok = await confirmDialog({
      title: confirm.title || '删除',
      message: confirm.message ? confirm.message(row) : '确认删除该记录？',
      okText: confirm.okText || '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      const result = await api.del(`${endpoint}/${row.id}`);
      // 删除的实际结果不一定是「删掉了」——内置/被引用的记录通常改成停用，
      // 这时提示文案必须跟着变，否则用户以为删成功了。
      if (confirm.afterDelete) confirm.afterDelete(result, row);
      else if (messages.deleted) toast(msg(messages.deleted), 'success');
      page.table.reload();
    } catch {
      /* 请求层已提示 */
    }
  };

  /* ------------------------------ 事件绑定 ------------------------------ */

  function bind() {
    if (filters) {
      const filterForm = $(filters);
      filterForm?.addEventListener('submit', (event) => {
        event.preventDefault();
        page.table.search(serializeForm(event.currentTarget));
      });
      filterForm?.addEventListener('reset', () => setTimeout(() => page.table.search({}), 0));
    }
    $('#btn-refresh')?.addEventListener('click', () => page.table.reload());
    $('#btn-create')?.addEventListener('click', () => page.openCreate());
    tableEl?.addEventListener('click', (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      const button = /** @type {HTMLElement|null} */ (target.closest('[data-act]'));
      if (!button) return;
      const row = page.table.find(button.closest('tr')?.dataset.id);
      if (!row) return;
      const act = button.dataset.act;
      const custom = actions.find((item) => item.act === act);
      if (custom) {
        custom.run(row, page);
        return;
      }
      if (act === 'edit') page.openEdit(row);
      else if (act === 'delete') page.remove(row);
    });
    formEl()?.addEventListener('submit', submit);
  }

  page.init = async () => {
    await page.table.load(1);
    bind();
    return page;
  };

  return page;
}
