// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomBytes } from 'node:crypto';
import { QQApi, identifier } from './api.mjs';
import { createGateway, gatewayFailure } from './gateway.mjs';
import { normalizeMessage, privateSender } from './events.mjs';

export async function loginQQ({ state, appId, appSecret, fetchImpl = globalThis.fetch,
  gatewayFactory = createGateway, signal = AbortSignal.timeout(180_000), onChallenge = () => {}, instance = 'qq' }) {
  state.acquire();
  const controller = new AbortController();
  const receiveSignal = AbortSignal.any([signal, controller.signal]);
  let run;
  let onAbort;
  try {
    const api = new QQApi({ appId, appSecret }, { fetchImpl });
    // Validate credentials before displaying the challenge; never replace the
    // saved account until the gateway authenticates a fresh private response.
    await api.gateway(receiveSignal);
    const existing = state.account();
    const sameBot = existing?.appId === appId;
    const challenge = `vika-bind ${randomBytes(18).toString('hex')}`;
    const startedAt = Math.floor(Date.now() / 1000) * 1000;
    const matched = Promise.withResolvers();
    let shown = false;
    let ownerId;
    onAbort = () => matched.reject(receiveSignal.reason);
    receiveSignal.addEventListener('abort', onAbort, { once: true });
    const gateway = gatewayFactory({ api, signal: receiveSignal,
      onReady: () => {
        if (shown || receiveSignal.aborted) return;
        shown = true;
        Promise.resolve().then(() => onChallenge({ appId, challenge })).catch(matched.reject);
      },
      onMessage: (message) => {
        if (receiveSignal.aborted || ownerId) return;
        try {
          if (sameBot) {
            const event = normalizeMessage(message, existing, state, instance);
            if (event) state.stage(event);
          }
          const sender = privateSender(message);
          const at = Date.parse(message.timestamp);
          if (!shown || !sender || !Number.isFinite(at) || at < startedAt || at > Date.now() + 30_000
            || message.content?.trim() !== challenge) return;
          identifier(message.messageId, 'verification message id');
          ownerId = sender;
          matched.resolve({ ownerId, at });
        } catch { matched.reject(new Error('QQ binding received invalid data')); }
      },
    });
    // Attach both rejection handlers before starting, including immediate fake
    // transports used by tests. SDK errors may contain signed URLs: redact them.
    run = Promise.resolve().then(() => gateway.start()).then(() => {
      if (!receiveSignal.aborted && !ownerId) matched.reject(new Error('QQ binding connection ended'));
    }, (error) => matched.reject(gatewayFailure(error)));
    if (receiveSignal.aborted) onAbort();
    const verified = await matched.promise;
    signal.throwIfAborted();
    state.saveAccount({ appId, appSecret, ownerId: verified.ownerId,
      activatedAt: sameBot && existing.ownerId === verified.ownerId ? existing.activatedAt : verified.at });
    return { appId, ownerId: verified.ownerId };
  } finally {
    if (onAbort) receiveSignal.removeEventListener('abort', onAbort);
    controller.abort();
    await run;
    state.release();
  }
}
