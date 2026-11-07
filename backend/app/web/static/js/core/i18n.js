// @ts-check
/**
 * 极简国际化留缝（B-10 / 6-3）。
 *
 * 产品当前只做简体中文，所以**不引入**任何 i18n 框架、不做复数规则、不做日期格式
 * 分叉——那些东西一旦提前上，维护成本远大于收益。这里只解决一个问题：
 *
 *   文案将来要能集中，但现在散在几百个调用点里。
 *
 * 用法是「key + 中文兜底」：
 *
 *   import { t } from './i18n.js';
 *   toast(t('common.networkError', '网络连接失败，请检查后端服务'), 'error');
 *
 * 没有注册任何语言包时 `t()` 原样返回兜底文案，也就是今天的行为完全不变；
 * 将来要做繁体或英文，只需 `registerMessages({ 'common.networkError': '...' }, 'zh-TW')`
 * 再 `setLocale('zh-TW')`，调用点一行都不用改。
 *
 * 三条约定：
 * - key 用 `模块.语义` 形式（`common.networkError`），不要用中文当 key；
 * - 兜底文案必填，否则漏注册时会直接把 key 显示给用户；
 * - 占位符用 `{name}`，第三个参数传实参对象。
 */

/** locale -> (key -> 文案) */
const packs = new Map();
let currentLocale = 'zh-CN';

/**
 * 注册某个语言的文案包。
 * @param {Record<string, string>} messages key -> 文案
 * @param {string} [locale] 目标语言，默认当前语言
 * @param {{ override?: boolean }} [options] override=false 时不覆盖已注册的 key
 */
export function registerMessages(messages, locale = currentLocale, { override = false } = {}) {
  if (!messages || typeof messages !== 'object') return;
  let pack = packs.get(locale);
  if (!pack) {
    pack = new Map();
    packs.set(locale, pack);
  }
  for (const [key, text] of Object.entries(messages)) {
    if (typeof text !== 'string') continue;
    if (!override && pack.has(key)) continue;
    pack.set(key, text);
  }
}

export function setLocale(locale) {
  if (typeof locale === 'string' && locale) currentLocale = locale;
}

export function getLocale() {
  return currentLocale;
}

export function hasMessage(key, locale = currentLocale) {
  const pack = packs.get(locale);
  return Boolean(pack && pack.has(key));
}

/**
 * 取文案：当前语言命中就用注册的，否则用兜底，兜底也没有就把 key 原样返回
 * （宁可显示 key 也不要显示 undefined）。
 * @param {string} key
 * @param {string} [fallback]
 * @param {Record<string, unknown>} [params]
 * @returns {string}
 */
export function t(key, fallback, params) {
  const pack = packs.get(currentLocale);
  const template = (pack && pack.get(key)) ?? fallback ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : whole,
  );
}

/** 仅测试用：清空所有语言包并恢复默认语言。 */
export function resetI18n() {
  packs.clear();
  currentLocale = 'zh-CN';
}
