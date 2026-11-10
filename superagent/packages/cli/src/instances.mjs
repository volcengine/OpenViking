// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { createRequire } from 'node:module';
import { list, t } from './i18n.mjs';

// Which instance a command means, when the user did not say or does not
// remember: the only one, or a choice that shows each instance's IMs.

const require = createRequire(import.meta.url);
const { loadDeploymentManifest, resolveDeploymentManifestPath } = require('../../../deployment-config.cjs');

// Read when used, so they are in the language of the run.
export const IM_LABELS = Object.freeze({
  get lark() { return t('飞书', 'Feishu/Lark'); },
  get weixin() { return t('微信', 'WeChat'); },
  get email() { return t('邮箱', 'Email'); },
  telegram: 'Telegram',
  qq: 'QQ',
  slack: 'Slack',
});

// The primary IM (`channel`) is where the bot reaches the owner unasked.
export const primaryMeaning = () => t('机器人主动找你时用，比如新邮件提醒', 'where the bot reaches you first, e.g. new-mail alerts');

// A manifest agent's connectors, as the loader reads them.
export function connectorsOf(agent) {
  return agent.connectors ?? [agent.channel ?? 'lark'];
}

export function imList(connectors, mailboxes = 0) {
  return list(connectors.map((connector) => (connector === 'email' && mailboxes > 1
    ? `${IM_LABELS.email}×${mailboxes}` : IM_LABELS[connector] ?? connector)));
}

// The loaded manifest, or null while Vika has no instance yet.
export function loadInstances(env = process.env) {
  if (!fs.existsSync(resolveDeploymentManifestPath(env))) return null;
  return loadDeploymentManifest(env);
}

// One line naming the manifest fields this release skipped, or null.
export function ignoredFieldsNotice(fields, filePath) {
  if (!fields?.length) return null;
  return t(`配置里有当前版本不认识的项，已忽略，可以从 ${filePath} 删掉：${list(fields)}`,
    `Ignored settings this version does not know; you can remove them from ${filePath}: ${list(fields)}`);
}

export function describeInstances(deployment) {
  const width = Math.max(...deployment.agents.map((agent) => agent.id.length));
  return deployment.agents.map((agent) => `  ${agent.id.padEnd(width)}  ${agent.displayName}  ${imList(agent.connectors, agent.emailAccounts?.length)}`).join('\n');
}

export async function chooseInstance({ deployment, requested, prompter, question }) {
  if (!deployment) throw new Error(t('还没有任何实例：先运行 vika setup', 'No instance yet: run vika setup first'));
  const { agents } = deployment;
  if (requested) {
    const agent = agents.find((candidate) => candidate.id === requested);
    if (!agent) throw new Error(t(`没有叫 ${requested} 的实例。现有实例：`, `No instance named ${requested}. The instances are:`) + `\n${describeInstances(deployment)}`);
    return agent;
  }
  if (agents.length === 1) return agents[0];
  if (!prompter?.tty) throw new Error(t('有多个实例，请在命令后面写上实例名：', 'There are several instances; name one after the command:') + `\n${describeInstances(deployment)}`);
  return prompter.select(question, agents.map((agent) => ({
    label: t(`${agent.displayName}（${agent.id}）`, `${agent.displayName} (${agent.id})`), hint: imList(agent.connectors, agent.emailAccounts?.length), value: agent,
  })), { initial: Math.max(0, agents.findIndex((agent) => agent.id === deployment.defaultInstance)) });
}
