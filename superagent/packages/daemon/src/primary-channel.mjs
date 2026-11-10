// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { updateManifest } from './deployment-manifest.mjs';

const IM_NAMES = { lark: '飞书', weixin: '微信', telegram: 'Telegram', slack: 'Slack', qq: 'QQ' };
const label = (id) => IM_NAMES[id] ? `${IM_NAMES[id]}（${id}）` : '未设置';
const PRIMARY_NOTES = {
  weixin: '\n微信主动通知依赖会话上下文；长时间未互动后，可能需要再给机器人发一条消息才能恢复接收。',
  qq: '\nQQ 主动通知需要机器人已开通主动消息权限且有剩余配额；配额用尽时通知会发送失败，不会自动重试。',
};
const USAGE = '/primary — 将当前 IM 设为主 IM\n/primary status — 查看当前主 IM';

// A Core setting, independent of the model/session. The owner is verified on
// the incoming Connector, so they can switch away from an unavailable primary.
export function executePrimaryCommand(command, { event, config, connectors }) {
  if (event?.chatType !== 'p2p' || event.senderType !== 'user' || !connectors.isOwner(event)) {
    return { ok: false, text: '仅限已绑定的 owner 在与机器人的私聊中使用 /primary。' };
  }
  const target = event.connectorInstance;
  const arg = command.arg.trim().toLowerCase();
  if (arg === 'status') {
    return { ok: true, text: `当前主 IM：${label(config.channel)}\n当前聊天所在 IM：${label(target)}\n主 IM 用于主动通知和新的邮件审批。\n${USAGE}` };
  }
  if (arg) return { ok: false, text: `用法：\n${USAGE}\n请在要设为主 IM 的平台私聊机器人。` };

  const manifest = new Map(connectors.manifests()).get(target);
  if (!Object.hasOwn(IM_NAMES, target) || !config.connectors.includes(target)
    || !manifest?.actions?.['message.send']?.content?.includes('markdown')) {
    return { ok: false, text: '当前渠道不支持作为主 IM；需要已接入且支持主动发送文本的 IM。' };
  }
  if (!connectors.available(target)) {
    return { ok: false, text: '当前 IM 连接暂不可用，主 IM 未变更，请恢复连接后重试。' };
  }
  const owner = connectors.owner(target);
  if (!owner?.target || ![owner.target.chat_id, owner.target.user_id].some((id) => typeof id === 'string' && id)) {
    return { ok: false, text: '当前 IM 缺少 owner 的私聊地址，主 IM 未变更，请先完成账号绑定。' };
  }
  if (!config.manifestPath || !config.instanceId) {
    return { ok: false, text: '无法定位当前实例的配置文件，主 IM 未变更。' };
  }

  const previous = config.channel;
  updateManifest(config.manifestPath, (value, deployment) => {
    const instance = deployment.agents.find((agent) => agent.id === config.instanceId);
    if (!instance || instance.dataDir !== config.dataDir || !instance.connectors.includes(target)) {
      throw new Error('实例配置已变更，主 IM 未切换；请检查 vika setup 中的接入配置并重启实例。');
    }
    if (instance.channel !== previous && instance.channel !== target) {
      throw new Error('配置中的主 IM 已被其他操作修改，未覆盖该设置；请重启实例同步配置。');
    }
    value.agents.find((agent) => agent.id === config.instanceId).channel = target;
  });
  // ActionService shares this object and resolves the primary on each send.
  // Persist first: any failure above leaves the running route unchanged.
  config.channel = target;
  const text = previous === target
    ? `当前主 IM 已是${label(target)}。`
    : `主 IM 已从${label(previous)}切换为${label(target)}，已保存并立即生效，无需重启。\n后续主动通知和新的邮件审批将发到这里；已有待审批消息仍在原 IM 处理。`;
  return { ok: true, text: text + (PRIMARY_NOTES[target] ?? '') };
}
