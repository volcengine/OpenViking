// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { TelegramState } from '../../daemon/src/telegram/state.mjs';
import { TelegramError } from '../../daemon/src/telegram/api.mjs';
import { loginTelegram } from '../../daemon/src/telegram/auth.mjs';
import { t } from './i18n.mjs';
import { GoBack, PromptAborted } from './prompt.mjs';
import { showScan } from './qr.mjs';

function explain(error) {
  if (error instanceof TelegramError) {
    if ([401, 404].includes(error.code)) return t('Bot token 无效或已撤销，请从官方 @BotFather 重新复制。', 'The bot token is invalid or revoked; copy it again from the official @BotFather.');
    if (error.code === 409) return t('这个 Bot 正被其他客户端接收，请停止那个客户端，或创建独立 Bot。', 'Another client is receiving this bot\'s updates; stop it, or create a bot of its own.');
    if (error.code === 429) return t('Telegram 请求过于频繁，请稍后再试。', 'Too many requests to Telegram; try again in a while.');
  }
  if (error.name === 'TimeoutError') return t('绑定超时，请重新打开本次绑定链接并点击 Start。', 'Binding timed out; open the link again and tap Start.');
  if (/active webhook/.test(error.message)) return t('这个 Bot 已配置 Webhook。请使用独立 Bot，或先在原服务中移除 Webhook。', 'This bot has a webhook. Use a bot of its own, or remove the webhook in the other service first.');
  if (/already in use/.test(error.message)) return t('这个实例正在接收 Telegram 消息，请先停止实例再重新授权。', 'This instance is receiving Telegram messages; stop it before authorizing again.');
  if (error.message === 'Invalid Telegram bot token') return t('Bot token 格式不正确，请完整复制，不要包含空格。', 'That bot token is malformed; copy all of it, without spaces.');
  return t('Telegram 连接失败，请检查网络和 Bot token 后重试。', 'Could not connect to Telegram; check the network and the bot token, then try again.');
}

export async function connectTelegram({ plan, prompter, fetchImpl = globalThis.fetch, canGoBack = false }) {
  prompter.section('Telegram');
  prompter.info(t('在官方 @BotFather 中发送 /newbot 创建独立 Bot，复制 Bot token。每个 Vika 实例使用一个 Bot。',
    'Send /newbot to the official @BotFather to create a bot for Vika, and copy its token. Each Vika instance uses its own bot.'));
  prompter.info(t('接入后只接受你的私聊；普通 Bot 对话不是端到端加密对话。',
    'Once connected it only takes your private messages; bot chats are not end-to-end encrypted.'));
  const navigable = prompter.navigable;
  try {
    for (;;) {
      prompter.navigable = canGoBack;
      const token = await prompter.secret('Telegram Bot token');
      prompter.navigable = false;
      try {
        await verify({ plan, prompter, token, fetchImpl, canGoBack });
        prompter.success(t('Telegram 已接好', 'Telegram is connected'));
        return;
      } catch (error) {
        if (error instanceof GoBack || error instanceof PromptAborted) throw error;
        prompter.warn(explain(error));
        prompter.navigable = canGoBack;
        const next = await prompter.select(t('接下来怎么办', 'What now'), [
          { label: t('重新输入 Bot token 并绑定', 'Enter the bot token again'), value: 'retry' },
          { label: canGoBack ? t('返回上一步', 'Go back') : t('退出接入', 'Stop'), value: 'leave' },
        ]);
        if (next === 'leave') throw canGoBack ? new GoBack() : new PromptAborted();
      }
    }
  } finally { prompter.navigable = navigable; }
}

async function verify({ plan, prompter, token, fetchImpl, canGoBack }) {
  const state = new TelegramState(plan.dataDir);
  const controller = new AbortController();
  const timeout = AbortSignal.timeout(180_000);
  const signal = AbortSignal.any([controller.signal, timeout]);
  let scan;
  let keys = prompter.watchKey(null, { onBack: canGoBack ? () => controller.abort(new GoBack()) : null });
  try {
    await loginTelegram({ state, token, fetchImpl, signal,
      onChallenge: ({ username, url }) => {
        keys.stop(); keys = null;
        scan = showScan(prompter, { url, where: 'Telegram',
          title: t(`绑定 @${username}：扫码或打开链接，在 Bot 私聊里点 Start`, `Bind @${username}: scan the code or open the link, then tap Start`),
          label: t('等待你点 Start（3 分钟内）', 'Waiting for Start (within 3 minutes)'),
          onBack: canGoBack ? () => controller.abort(new GoBack()) : null });
      },
    });
  } catch (error) { throw signal.aborted ? signal.reason : error; }
  finally { keys?.stop(); scan?.stop(); state.close(); }
}
