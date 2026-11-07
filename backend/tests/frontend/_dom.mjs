/**
 * 最小 DOM 桩：让 core/base.js 的 readConfig() 在 import 期能跑（配置块缺失返回 {}）。
 * 纯逻辑单测只关心函数行为，不需要真实浏览器；需要真实 DOM 的交互不在此覆盖
 * （由 check_contract.py 的页面↔JS↔#id 三向校验兜底）。
 */
globalThis.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => ({
    appendChild() {},
    remove() {},
    addEventListener() {},
    focus() {},
    className: '',
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  }),
};
