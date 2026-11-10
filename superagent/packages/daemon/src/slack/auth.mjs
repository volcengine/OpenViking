// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomBytes } from 'node:crypto';
import { SlackApi } from './api.mjs';
import { relevant } from './events.mjs';
import { SocketModeReceiver } from './socket.mjs';

const ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789';
const quiet = { debug() {}, info() {}, warn() {}, error() {} };

function code() {
  return `vika-${[...randomBytes(8)].map((byte) => ALPHABET[byte % ALPHABET.length]).join('')}`;
}

function millis(ts) {
  const [seconds, fraction = ''] = String(ts ?? '').split('.');
  return Number(seconds) * 1000 + Number(fraction.padEnd(3, '0').slice(0, 3));
}

// Checks both tokens belong to one app, then binds the owner: the person who
// sends the one-time code to the bot in a direct message. While re-binding
// the same app, messages for the agent that arrive meanwhile are staged and
// delivered once the instance runs again.
export async function loginSlack({ state, botToken, appToken, fetchImpl = globalThis.fetch,
  WebSocketImpl = globalThis.WebSocket, signal = AbortSignal.timeout(300_000), onChallenge = () => {},
  logger = quiet, receiver = {} }) {
  state.acquire();
  try {
    const api = new SlackApi(botToken, { fetchImpl });
    const appApi = new SlackApi(appToken, { kind: 'app', fetchImpl });
    const identity = await api.identity(signal);
    const existing = state.account();
    const sameApp = existing?.teamId === identity.teamId && existing?.appId === identity.appId;
    const secret = code();
    const startedAt = Date.now() - 5_000;
    const done = new AbortController();
    let bound = null;
    const receiving = new SocketModeReceiver({ api: appApi, appId: identity.appId, WebSocketImpl, logger, ...receiver })
      .run((payload) => {
        const event = payload?.event;
        if (!bound && payload?.type === 'event_callback' && payload.team_id === identity.teamId && event?.type === 'message'
          && event.channel_type === 'im' && !event.subtype && !event.bot_id && /^[UW][A-Z0-9]{2,30}$/.test(event.user ?? '')
          && /^D[A-Z0-9]{2,30}$/.test(event.channel ?? '') && String(event.text ?? '').trim() === secret
          && millis(event.ts) >= startedAt) {
          bound = { ownerId: event.user, ownerChannel: event.channel, at: millis(event.ts) };
          done.abort();
        } else if (sameApp && relevant(payload, existing)) {
          state.stage(existing.teamId, payload);
        }
      }, AbortSignal.any([signal, done.signal]), {
        onReady: () => onChallenge({ code: secret, team: identity.team, botName: identity.botName,
          url: `https://slack.com/app_redirect?app=${identity.appId}&team=${identity.teamId}` }),
      });
    try { await receiving; } catch (error) { if (!bound) throw signal.aborted ? signal.reason : error; }
    if (!bound) throw signal.reason ?? new Error('Slack binding was cancelled');
    // Everyone in the workspace talks to the bot, not only the owner: a new
    // owner on the same app keeps the messages staged meanwhile.
    state.saveAccount({ teamId: identity.teamId, appId: identity.appId, botId: identity.botId, botUserId: identity.botUserId,
      ownerId: bound.ownerId, ownerChannel: bound.ownerChannel, botToken, appToken,
      activatedAt: sameApp ? existing.activatedAt : bound.at });
    return { ...identity, ownerId: bound.ownerId, ownerChannel: bound.ownerChannel };
  } finally { state.release(); }
}
