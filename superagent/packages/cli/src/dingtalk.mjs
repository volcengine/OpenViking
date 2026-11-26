// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { GoBack, PromptAborted } from './prompt.mjs';
import { t } from './i18n.mjs';

export async function connectDingTalk({ plan, prompter, canGoBack = false }) {
  const { DingTalkClient } = await import('../../daemon/src/dingtalk/client.mjs');
  prompter.section(t('钉钉群机器人', 'DingTalk group bot'));
  prompter.info(t('请先发布开启 Stream 模式的企业内部机器人。钉钉用于收发群消息，主渠道负责联系 owner。',
    'Publish an internal Stream-mode bot first. DingTalk handles group messages; the primary IM reaches the owner.'));
  const navigable = prompter.navigable;
  prompter.navigable = canGoBack;
  let credentials;
  try {
    const required = (value) => value.trim() ? null : t('请输入有效的 AppKey', 'Enter a valid AppKey');
    const clientId = (await prompter.text('DingTalk AppKey', { validate: required })).trim();
    const clientSecret = (await prompter.secret('DingTalk AppSecret')).trim();
    const robotCode = (await prompter.text('DingTalk RobotCode', { initial: clientId })).trim() || clientId;
    credentials = { clientId, clientSecret, robotCode };
  } catch (error) {
    if (canGoBack && error instanceof PromptAborted) throw new GoBack();
    throw error;
  } finally {
    prompter.navigable = navigable;
  }

  fs.mkdirSync(plan.dataDir, { recursive: true, mode: 0o700 });
  const stagingDir = fs.mkdtempSync(path.join(plan.dataDir, '.dingtalk-'));
  const staged = path.join(stagingDir, 'dingtalk.json');
  try {
    fs.writeFileSync(staged, JSON.stringify(credentials) + '\n', { mode: 0o600, flag: 'wx' });
    await new DingTalkClient({ dataDir: stagingDir }).preflight();
    fs.renameSync(staged, path.join(plan.dataDir, 'dingtalk.json'));
  } finally {
    fs.rmSync(staged, { force: true });
    fs.rmdirSync(stagingDir);
  }
}
