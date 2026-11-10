// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { loginQQ } from '../../daemon/src/qq/auth.mjs';
import { QQError } from '../../daemon/src/qq/api.mjs';
import { QQState } from '../../daemon/src/qq/state.mjs';
import { GoBack, PromptAborted } from './prompt.mjs';
import { t } from './i18n.mjs';

function explain(error) {
  if (/already in use/.test(error.message)) return t('这个实例正在接收 QQ 消息，请先停止实例再重新授权。',
    'This instance is receiving QQ messages; stop it before authorizing again.');
  if (/Invalid QQ AppID or AppSecret/.test(error.message)) return t('AppID 或 AppSecret 格式不正确，请完整复制。',
    'The AppID or AppSecret format is invalid; copy the full value.');
  if (error instanceof QQError) return t(`QQ 验证失败（HTTP ${error.status}，错误码 ${error.code ?? '未知'}），请检查密钥和机器人权限。`,
    `QQ verification failed (HTTP ${error.status}, code ${error.code ?? 'unknown'}); check the credentials and bot permissions.`);
  if (/gateway/.test(error.message)) return t('QQ 长连接失败，请检查网络、机器人上线状态以及单聊消息权限。',
    'The QQ connection failed; check the network, bot status and private-message permissions.');
  if (error.name === 'TimeoutError') return t('绑定超时，请重新输入密钥，并在 3 分钟内向机器人私聊发送新的验证码。',
    'Binding timed out; enter the credentials again and send the new code to the bot in a private chat within 3 minutes.');
  return t('QQ 连接失败，请检查网络和机器人配置后重试。', 'QQ could not connect; check the network and bot settings, then retry.');
}

export async function connectQQ({ plan, prompter, fetchImpl = globalThis.fetch, gatewayFactory, canGoBack = false }) {
  prompter.section('QQ');
  prompter.info(t('1. 还没有 QQ 开放平台开发者账号的，先在 https://q.qq.com/#/ 按页面引导注册；没有开发者账号无法创建机器人。',
    '1. Without a QQ Open Platform developer account, first register one at https://q.qq.com/#/ by following its steps; a bot cannot be created without it.'));
  prompter.info(t('2. 再到 https://q.qq.com/qqbot/openclaw/ 创建机器人，复制 AppID 和 AppSecret，并用 QQ 添加这个机器人。',
    '2. Then create a bot at https://q.qq.com/qqbot/openclaw/, copy its AppID and AppSecret, and add the bot in QQ.'));
  prompter.info(t('每个 Vika 实例使用一个独立机器人；启用单聊消息权限，接入后只接受完成验证码绑定的本人私聊。',
    'Use a separate bot for each Vika instance and enable private messages; only the owner who verifies the code can chat with it.'));
  if (canGoBack) prompter.info(t('输入密钥或等待绑定时，按 ← / Esc 可以返回。', 'While entering credentials or waiting, press ← / Esc to go back.'));
  const navigable = prompter.navigable;
  try {
    for (;;) {
      prompter.navigable = canGoBack;
      const appId = (await prompter.text('QQ AppID')).trim();
      const appSecret = await prompter.secret('QQ AppSecret');
      prompter.navigable = false;
      try {
        await verify({ plan, prompter, appId, appSecret, fetchImpl, gatewayFactory, canGoBack });
        prompter.success(t('QQ 已接好；实例启动后，在这个机器人的私聊中发任务即可。', 'QQ is connected; once the instance starts, message this bot with a task.'));
        return;
      } catch (error) {
        if (error instanceof GoBack || error instanceof PromptAborted) throw error;
        prompter.warn(explain(error));
        prompter.navigable = canGoBack;
        const next = await prompter.select(t('接下来怎么办', 'What next'), [
          { label: t('重新输入 AppID / AppSecret 并绑定', 'Enter AppID / AppSecret and bind again'), value: 'retry' },
          { label: canGoBack ? t('返回上一步', 'Go back') : t('退出接入', 'Leave setup'), value: 'leave' },
        ]);
        if (next === 'leave') throw canGoBack ? new GoBack() : new PromptAborted();
      }
    }
  } finally { prompter.navigable = navigable; }
}

async function verify({ plan, prompter, appId, appSecret, fetchImpl, gatewayFactory, canGoBack }) {
  const state = new QQState(plan.dataDir);
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(180_000)]);
  const keys = prompter.watchKey(null, { onBack: canGoBack ? () => controller.abort(new GoBack()) : null });
  try {
    await loginQQ({ state, appId, appSecret, fetchImpl, gatewayFactory, signal,
      onChallenge: ({ challenge }) => {
        prompter.info(t('请在 3 分钟内，用你自己的 QQ 向机器人私聊发送下面一整行（不要发到群里）：',
          'Within 3 minutes, send the entire line below to the bot in a private chat from your own QQ account (not a group):'));
        prompter.write(`\n${challenge}\n\n`);
        prompter.info(t('等待 QQ 私聊验证…', 'Waiting for QQ private-chat verification…'));
      },
    });
  } catch (error) { throw signal.aborted ? signal.reason : error; }
  finally { keys.stop(); state.close(); }
}
