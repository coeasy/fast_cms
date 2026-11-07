// @ts-check
/**
 * 界面文案表（10-4）。
 *
 * 为什么不直接把中文写在 `toast(...)` 里：同一句话会在十几个页面重复，
 * 改一次措辞要 grep 全站；做繁体/英文时更是无从下手。这里把**固定文案**
 * 收成一张表，调用点只写 key：
 *
 *   import { msg } from '../core/messages.js';
 *   toast(msg('user.deleted'), 'success');
 *
 * 动态文案（拼接了名称、数量等）继续走 `msg(key, { name })` 的具名占位符。
 * 表里没有的 key 会原样返回 key 并打一条 warn——宁可显示 key 也不要显示
 * `undefined`，而且测试会把这个漏注册挡在门禁层。
 */

import { hasMessage, registerMessages, t } from './i18n.js';

export const MESSAGES_ZH = {
  'common.exportStarted': '导出已开始',
  'common.downloadStarted': '下载已开始',
  'common.copied': '已复制',
  'common.copyFailed': '复制失败，请手动选择文本复制',
  'common.retentionRange': '保留天数需在 7-3650 之间',
  'common.retentionPositive': '保留天数必须大于 0',
  'common.selectFirst': '请先勾选要操作的行',
  'common.themeSwitched': '主题已切换',
  'common.allRead': '已全部标为已读',
  'common.confirmRun': '确定执行',
  'batch.selected': '已选 {n} 项',
  'batch.empty': '请先勾选要操作的行',
  'batch.done': '已完成 {ok} 项',
  'batch.partial': '已完成 {ok} 项，{failed} 项失败',
  'user.created': '用户创建成功',
  'user.updated': '用户已更新',
  'user.deleted': '用户已删除',
  'user.statusUpdated': '状态已更新',
  'user.banned': '用户已封禁',
  'user.unbanned': '用户已解封',
  'user.passwordReset': '密码已重置，该用户所有会话已失效',
  'user.rolesUpdated': '角色已更新',
  'user.importSelectFile': '请先选择 CSV 文件',
  'role.created': '角色创建成功',
  'role.updated': '角色已更新',
  'role.deleted': '角色已删除',
  'role.permissionsSaved': '权限已保存',
  'role.dataScopeUpdated': '数据范围已更新',
  'dept.created': '部门创建成功',
  'dept.updated': '部门已更新',
  'dept.deleted': '部门已删除',
  'menu.created': '菜单创建成功',
  'menu.updated': '菜单已更新',
  'menu.deleted': '菜单已删除',
  'menu.referenced': '该菜单已被引用，已改为停用',
  'dict.typeCreated': '字典类型创建成功',
  'dict.typeUpdated': '字典类型已更新',
  'dict.typeDeleted': '字典类型已删除',
  'dict.itemCreated': '字典项创建成功',
  'dict.itemUpdated': '字典项已更新',
  'dict.itemDeleted': '字典项已删除',
  'config.created': '参数创建成功',
  'config.updated': '参数已更新',
  'config.deleted': '参数已删除',
  'config.jsonInvalid': 'JSON 类型的值必须是合法 JSON',
  'notice.created': '公告已创建',
  'notice.updated': '公告已更新',
  'notice.deleted': '公告已删除',
  'job.created': '任务创建成功',
  'job.updated': '任务已更新',
  'job.deleted': '任务已删除',
  'job.scriptOk': '脚本语法校验通过',
  'job.requireScript': '请先填写脚本内容',
  'job.requireCron': '请填写 Cron 表达式',
  'job.requireInterval': '请填写间隔秒数',
  'job.requireTemplate': '请选择任务定义',
  'job.requireTime': '请选择执行时间',
  'template.created': '模板创建成功',
  'template.updated': '模板已更新',
  'template.deleted': '模板已删除',
  'channel.created': '渠道创建成功',
  'channel.updated': '渠道已更新',
  'channel.deleted': '渠道已删除',
  'channel.testSent': '测试消息已发送',
  'channel.requireEvents': '至少选择一个订阅事件',
  'channel.requireWebhook': '请填写 Webhook 地址',
  'apiKey.deleted': 'API Key 已删除',
  'apiKey.requireName': '请填写名称',
  'ipRule.deleted': '规则已删除',
  'ipRule.requireIp': '请填写 IP 地址',
  'ipRule.requireTarget': '请填写 IP 或网段',
  'ipRule.banned': '已封禁',
  'ban.bannedIp': '已封禁该 IP',
  'ban.requireIp': '请先输入 IP 地址',
  'ban.noLoginIp': '该用户没有记录到登录 IP',
  'online.kicked': '该会话已下线',
  'profile.saved': '资料已保存',
  'profile.avatarUpdated': '头像已更新',
  'profile.avatarTooLarge': '头像大小不能超过 2 MB',
  'profile.deviceKicked': '该设备已下线',
  'password.changed': '密码修改成功，其他会话已失效',
  'theme.created': '主题创建成功',
  'theme.updated': '主题已更新',
  'theme.deleted': '主题已删除',
  'file.deleted': '文件已删除',
  'codegen.requireModule': '请填写模块名',
  'codegen.requireTable': '请选择数据表',
};

registerMessages(MESSAGES_ZH, 'zh-CN');

/** 已 warn 过的 key，避免同一个漏注册在控制台刷屏。 */
const warned = new Set();

/**
 * 取文案：命中文案表就返回中文，没命中返回 key 本身并告警一次。
 * @param {string} key 形如 `user.deleted` 的文案 key
 * @param {Record<string, unknown>} [params] 具名占位符实参
 * @returns {string}
 */
export function msg(key, params) {
  if (!hasMessage(key)) {
    if (!warned.has(key)) {
      warned.add(key);
      console.warn(`[fastcms] 文案 key 未登记：${key}`);
    }
    return key;
  }
  return t(key, undefined, params);
}
