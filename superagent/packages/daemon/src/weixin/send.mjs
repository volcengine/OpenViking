// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import { sendItems, sendText } from './api.mjs';
import { readOutboundMedia, uploadOutboundMedia } from './media.mjs';

const WEIXIN_TEXT_LIMIT = 4_000;

// The Core outbox owns the idempotency key; deriving the provider client id
// from it keeps a retried attempt on the same Weixin message id.
function clientIdFor(idempotencyKey, peerId) {
  const digest = createHash('sha256').update(idempotencyKey).update('\0').update(peerId).digest('hex');
  return `vika-${digest.slice(0, 32)}`;
}

// Weixin shows a bot's text the way Markdown does: a single line break turns
// into a space, and only a blank line starts a new line. Each line break is
// made a blank line so the text reads as written; fenced code keeps its own.
export function weixinText(text) {
  const lines = text.split('\n');
  let fenced = false;
  let out = '';
  lines.forEach((line, index) => {
    if (index > 0) {
      const previous = lines[index - 1];
      out += !fenced && previous.trim() && line.trim() ? '\n\n' : '\n';
    }
    out += line;
    if (/^\s*```/.test(line)) fenced = !fenced;
  });
  return out;
}

export async function sendWeixinMessage({ state, to, content, idempotencyKey }) {
  const account = state.account();
  if (!account?.token) throw new Error('Weixin is not logged in; run vika login weixin <instance> first');
  // The bot talks only with its owner, the account that scanned the login QR code.
  if (!account.userId || to !== account.userId) throw new Error('Weixin can only message the account that scanned the login QR code');
  // Weixin accepts a message without the context token of the owner's last
  // message to this login, then never delivers it: until the owner writes
  // to the bot after a scan, it cannot reach them.
  const contextToken = state.contextToken(to);
  if (!contextToken) {
    throw new Error('The Weixin owner has not written to this bot since it logged in; it cannot reach them until they send it a message');
  }
  const clientId = clientIdFor(idempotencyKey, to);
  const common = { apiBaseUrl: account.baseUrl, token: account.token, to, contextToken, clientId };
  let response;
  if (content.type === 'markdown') {
    const text = weixinText(content.text);
    if (text.length > WEIXIN_TEXT_LIMIT) {
      throw new Error(`Weixin text exceeds the ${WEIXIN_TEXT_LIMIT}-character limit`);
    }
    response = await sendText({ ...common, text });
  } else if (content.type === 'file') {
    const media = readOutboundMedia(content.path);
    const item = await uploadOutboundMedia({ account, to, media });
    response = await sendItems({ ...common, items: [item] });
  } else {
    throw new Error(`Weixin does not support ${content.type} messages`);
  }
  return { message_id: response.message_id ?? null, client_id: clientId };
}
