// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomBytes } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { TelegramApi, integer } from './api.mjs';
import { normalizeUpdate, privateSender } from './events.mjs';

export async function loginTelegram({ state, token, fetchImpl = globalThis.fetch,
  signal = AbortSignal.timeout(180_000), onChallenge = () => {}, instance = 'telegram' }) {
  state.acquire();
  try {
    const api = new TelegramApi(token, { fetchImpl });
    const { botId, username } = await api.inspect(signal);
    const existing = state.account();
    const sameBot = existing?.botId === botId;
    let offset = sameBot ? existing.offset : 0;
    const nonce = randomBytes(24).toString('base64url');
    const startedAt = Math.floor(Date.now() / 1000) * 1000;
    await onChallenge({ username, url: `https://t.me/${username}?start=${nonce}` });
    for (;;) {
      signal.throwIfAborted();
      const batch = await api.updates(offset, AbortSignal.any([signal, AbortSignal.timeout(35_000)]));
      signal.throwIfAborted();
      let handshake;
      for (const update of batch) {
        if (sameBot) {
          const event = normalizeUpdate(update, existing, instance);
          if (event) state.stage(botId, update.update_id, event);
        }
        const message = update.message;
        if (!handshake && privateSender(message) && Number.isSafeInteger(message.date)
          && message.date * 1000 >= startedAt
          && (message.text === `/start ${nonce}` || message.text === `/start@${username} ${nonce}`)) handshake = update;
      }
      if (handshake) {
        const message = handshake.message;
        integer(message.message_id, 'verification message id', 1);
        const ownerId = String(message.from.id), chatId = String(message.chat.id);
        const sameOwner = sameBot && existing.ownerId === ownerId && existing.chatId === chatId;
        signal.throwIfAborted();
        // The next poll replays any messages after the challenge. Existing
        // owner messages consumed during login are delivered from pending.
        state.saveAccount({ botId, ownerId, chatId, token, offset: handshake.update_id + 1,
          activatedAt: sameOwner ? existing.activatedAt : message.date * 1000 });
        return { botId, ownerId, chatId, username };
      }
      if (batch.length) offset = batch.at(-1).update_id + 1;
      else await delay(250, undefined, { signal });
    }
  } finally { state.release(); }
}
