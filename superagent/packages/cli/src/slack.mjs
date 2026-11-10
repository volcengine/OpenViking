// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { SlackApi, SlackError, validToken } from '../../daemon/src/slack/api.mjs';
import { loginSlack } from '../../daemon/src/slack/auth.mjs';
import { SlackState } from '../../daemon/src/slack/state.mjs';
import { t } from './i18n.mjs';
import { GoBack, PromptAborted } from './prompt.mjs';
import { showScan } from './qr.mjs';

// Slack has no scan-to-create: the owner creates an app in their own
// workspace from the manifest below (prefilled through a link), or brings an
// existing app set up to match it, installs it, and pastes its two tokens.
// The app stays internal to that workspace.

export const BOT_SCOPES = Object.freeze(['chat:write', 'reactions:write', 'files:read', 'files:write', 'users:read',
  'channels:history', 'groups:history', 'im:history', 'mpim:history',
  'channels:read', 'groups:read', 'im:read', 'mpim:read']);
export const BOT_EVENTS = Object.freeze(['message.channels', 'message.groups', 'message.im', 'message.mpim']);

export function appManifest(name) {
  const display = String(name || 'Vika').trim().slice(0, 35) || 'Vika';
  // The bot user's name allows only a-z, 0-9, -, _ and .
  const handle = display.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'vika';
  return {
    display_information: { name: display, description: 'Vika personal agent' },
    features: {
      app_home: { home_tab_enabled: false, messages_tab_enabled: true, messages_tab_read_only_enabled: false },
      bot_user: { display_name: handle, always_online: true },
    },
    oauth_config: { scopes: { bot: [...BOT_SCOPES] } },
    settings: { event_subscriptions: { bot_events: [...BOT_EVENTS] }, socket_mode_enabled: true, token_rotation_enabled: false },
  };
}

export function createAppUrl(name) {
  return `https://api.slack.com/apps?new_app=1&manifest_json=${encodeURIComponent(JSON.stringify(appManifest(name)))}`;
}

function explain(error) {
  if (error instanceof SlackError) {
    if (error.auth) return t('token 无效、已撤销或缺少权限：确认 App 已安装到工作区，并完整复制 token。', 'A token is invalid, revoked or lacks a scope: check the app is installed to the workspace and copy the whole token.');
    if (error.code === 'ratelimited') return t('Slack 请求过于频繁，请稍后再试。', 'Too many requests to Slack; try again in a while.');
  }
  if (error.name === 'TimeoutError') return t('绑定超时：请在 5 分钟内私聊机器人发送口令。', 'Binding timed out: send the code to the bot in a direct message within 5 minutes.');
  if (/belongs to another app/.test(error.message)) return t('两个 token 不属于同一个 Slack App：请从同一个 App 里复制。', 'The two tokens belong to different Slack apps; copy both from the same app.');
  if (/Another program is receiving/.test(error.message)) return t('有别的程序在接收这个 App 的消息：先停掉原来用它的程序，或换一个 App；每个 Vika 实例要用独立的 Slack App。', 'Another program is receiving this app\'s events: stop the program that used it, or use another app; each Vika instance needs its own Slack app.');
  if (/Socket Mode was turned off/.test(error.message)) return t('这个 App 没有打开 Socket Mode：在 App 设置的 Socket Mode 页面打开。', 'Socket Mode is off for this app; turn it on under Socket Mode in the app settings.');
  if (/already in use/.test(error.message)) return t('这个实例正在接收 Slack 消息，请先停止实例再重新授权。', 'This instance is receiving Slack messages; stop it before authorizing again.');
  return t('Slack 连接失败，请检查网络和 token 后重试。', 'Could not connect to Slack; check the network and the tokens, then try again.');
}

async function askToken(prompter, label, kind) {
  for (;;) {
    const token = (await prompter.secret(label)).trim();
    if (validToken(token, kind)) return token;
    prompter.warn(kind === 'app'
      ? t('App-Level Token 以 xapp- 开头，请完整复制，不要包含空格。', 'An app-level token starts with xapp-; copy all of it, without spaces.')
      : /^xoxe[.-]/.test(token)
        ? t('这个 App 开了 Token Rotation，token 会过期，不能用：请新建一个 App。', 'This app has token rotation on, so its tokens expire; create a new app instead.')
        : t('Bot User OAuth Token 以 xoxb- 开头，请完整复制，不要包含空格。', 'A bot user OAuth token starts with xoxb-; copy all of it, without spaces.'));
  }
}

export async function connectSlack({ plan, prompter, fetchImpl = globalThis.fetch, WebSocketImpl = globalThis.WebSocket,
  canGoBack = false, receiver = {} }) {
  prompter.section('Slack');
  // Also as a file, for pasting into "From a manifest" when the link is too long to open.
  const manifestFile = path.join(plan.dataDir, 'slack', 'app-manifest.json');
  fs.mkdirSync(path.dirname(manifestFile), { recursive: true, mode: 0o700 });
  fs.writeFileSync(manifestFile, `${JSON.stringify(appManifest(plan.displayName), null, 2)}\n`, { mode: 0o600 });
  prompter.info(t('1. 准备一个 Slack App（每个 Vika 实例一个）。新建：打开下面的链接，选择工作区，用预填的配置创建：',
    '1. Get a Slack app (one per Vika instance). To create one, open the link below, pick the workspace and create it from the prefilled manifest:'));
  prompter.write(`   ${createAppUrl(plan.displayName)}\n`);
  prompter.info(t(`   链接打不开时，在 api.slack.com/apps 选 Create New App → From a manifest，粘贴 ${manifestFile} 的内容。`,
    `   If the link does not open, choose Create New App → From a manifest at api.slack.com/apps and paste ${manifestFile}.`));
  prompter.info(t(`   用已有的 App 也可以：对照 ${manifestFile} 打开 Socket Mode、在 App Home 允许用户发消息，补齐事件订阅和 Bot Token Scopes。开了 Token Rotation 的 App 不能用，也不能再有别的程序接收它的消息。`,
    `   An existing app works too: following ${manifestFile}, turn on Socket Mode, let users send messages in App Home, and add any missing event subscriptions and bot token scopes. An app with token rotation on cannot be used, and no other program may receive its events.`));
  prompter.info(t('2. 点 Install to Workspace 安装（已有 App 改过权限要点 Reinstall），再到 OAuth & Permissions 复制 Bot User OAuth Token（xoxb-）。',
    '2. Install to Workspace (reinstall an existing app whose scopes changed), then copy the Bot User OAuth Token (xoxb-) under OAuth & Permissions.'));
  prompter.info(t('3. 到 Basic Information → App-Level Tokens 生成一个带 connections:write 的 token（xapp-），已有的也可以直接用。',
    '3. Under Basic Information → App-Level Tokens, generate a token with connections:write (xapp-), or use one the app already has.'));
  const navigable = prompter.navigable;
  try {
    for (;;) {
      prompter.navigable = canGoBack;
      const botToken = await askToken(prompter, 'Bot User OAuth Token (xoxb-)', 'bot');
      prompter.navigable = false;
      const appToken = await askToken(prompter, 'App-Level Token (xapp-)', 'app');
      try {
        const bound = await verify({ plan, prompter, botToken, appToken, fetchImpl, WebSocketImpl, canGoBack, receiver });
        await new SlackApi(botToken, { fetchImpl }).send('chat.postMessage', { channel: bound.ownerChannel,
          text: t('绑定好了：你是这个机器人的主人。', 'Bound: you are this bot\'s owner.') }, AbortSignal.timeout(10_000)).catch(() => {});
        prompter.success(t(`Slack 已接好（${bound.team || bound.teamId}）`, `Slack is connected (${bound.team || bound.teamId})`));
        return;
      } catch (error) {
        if (error instanceof GoBack || error instanceof PromptAborted) throw error;
        prompter.warn(explain(error));
        prompter.navigable = canGoBack;
        const next = await prompter.select(t('接下来怎么办', 'What now'), [
          { label: t('重新输入 token 并绑定', 'Enter the tokens again'), value: 'retry' },
          { label: canGoBack ? t('返回上一步', 'Go back') : t('退出接入', 'Stop'), value: 'leave' },
        ]);
        if (next === 'leave') throw canGoBack ? new GoBack() : new PromptAborted();
      }
    }
  } finally { prompter.navigable = navigable; }
}

async function verify({ plan, prompter, botToken, appToken, fetchImpl, WebSocketImpl, canGoBack, receiver }) {
  const state = new SlackState(plan.dataDir);
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(300_000)]);
  let scan;
  let keys = prompter.watchKey(null, { onBack: canGoBack ? () => controller.abort(new GoBack()) : null });
  try {
    return await loginSlack({ state, botToken, appToken, fetchImpl, WebSocketImpl, signal, receiver,
      onChallenge: ({ code, url, botName }) => {
        keys.stop(); keys = null;
        scan = showScan(prompter, { url, where: 'Slack',
          title: t(`绑定主人：在 Slack 里私聊 ${botName || plan.displayName}，发送 ${code}`, `Bind the owner: send ${code} to ${botName || plan.displayName} in a Slack direct message`),
          label: t('等待你发送口令（5 分钟内）', 'Waiting for the code (within 5 minutes)'),
          onBack: canGoBack ? () => controller.abort(new GoBack()) : null });
      },
    });
  } catch (error) { throw signal.aborted ? signal.reason : error; }
  finally { keys?.stop(); scan?.stop(); state.close(); }
}
