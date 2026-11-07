/* ==========================================================================
   core.js —— 前端基础设施统一入口（按职责拆分的聚合出口）
   原生 ES Module，无构建、无第三方依赖。

   各职责已拆到 core/ 目录：
     base       —— 页面运行参数、DOM 工具、公共原语（ApiError）
     session    —— 会话状态与访问令牌
     dialog     —— Toast 与弹层
     request    —— 统一请求层（envelope、401 单飞刷新、下载）
     permission —— 前端权限判断
     form       —— 表单读取/填充/校验与多选组件
     time       —— 系统时区与时间格式化
     format     —— 纯格式化函数
     theme      —— 主题引擎
     other      —— 复制、空态行、状态标签

   保留本文件作为聚合出口，方便旧的 `from '../core.js'` 导入继续工作；
   页面脚本可按需从 core/<module>.js 直接导入，按需加载对应职责。
   ========================================================================== */

export * from './core/base.js';
export * from './core/session.js';
export * from './core/dialog.js';
export * from './core/request.js';
export * from './core/permission.js';
export * from './core/form.js';
export * from './core/time.js';
export * from './core/format.js';
export * from './core/theme.js';
export * from './core/other.js';
