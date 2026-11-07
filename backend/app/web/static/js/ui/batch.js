// @ts-check
/* ==========================================================================
   ui/batch.js —— 批量操作工具栏
   把「表格多选」与「批量操作条」绑在一起。
   ========================================================================== */

import { $ } from '../core/base.js';
import { confirmDialog, toast } from '../core/dialog.js';
import { setLoading } from '../core/form.js';
import { msg } from '../core/messages.js';

/**
 * 把「表格多选」与「批量操作条」绑在一起。
 *
 * 各个列表页的批量逻辑只差一个端点 + 一段确认文案，所以收到这里：
 * 页面只声明 actions，勾选/取消/确认/请求/结果提示全部复用。
 *
 * @param {object} options
 * @param {import('./table.js').DataTable} options.table 已开启 selectable 的表格
 * @param {HTMLElement} [options.bar] 工具栏容器，隐藏与显示由本函数管理
 * @param {Array<{act: string, label: string, danger?: boolean, confirm?: string,
 *                run: (ids: Array<number>) => Promise<{ok?: number, failed?: number, message?: string}>}>} options.actions
 */
export function bindBatchBar({ table, bar, actions }) {
  if (!bar) return;
  const countNode = $('[data-batch-count]', bar);

  const refresh = (ids) => {
    bar.hidden = ids.length === 0;
    if (countNode) countNode.textContent = msg('batch.selected', { n: ids.length });
  };

  table.onSelectionChange = refresh;
  refresh(table.selectedIds);

  bar.addEventListener('click', async (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const button = /** @type {HTMLElement|null} */ (target.closest('[data-batch-act]'));
    if (!button) return;
    const act = button.dataset.batchAct;

    if (act === 'clear') {
      table.clearSelection();
      return;
    }

    const ids = table.selectedIds;
    if (!ids.length) {
      toast(msg('batch.empty'), 'warning');
      return;
    }
    const action = actions.find((item) => item.act === act);
    if (!action) return;

    if (action.confirm) {
      const ok = await confirmDialog({
        title: action.label,
        message: action.confirm.replace('{n}', String(ids.length)),
        okText: msg('common.confirmRun'),
        danger: Boolean(action.danger),
      });
      if (!ok) return;
    }

    setLoading(button, true);
    try {
      const result = await action.run(ids);
      const failed = Number(result?.failed || 0);
      const ok = Number(result?.ok ?? ids.length - failed);
      if (failed > 0) toast(msg('batch.partial', { ok, failed }), 'warning');
      else toast(result?.message || msg('batch.done', { ok }), 'success');
      table.clearSelection();
      table.reload();
    } catch {
      /* 请求层已提示 */
    } finally {
      setLoading(button, false);
    }
  });
}
