// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomBytes } from 'node:crypto';
import { OneBotSocket } from './api.mjs';
import { cleanName, eventTime, isSelf } from './events.mjs';

const ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789';
const MAX_GROUPS = 200;
const quiet = { debug() {}, info() {}, warn() {}, error() {} };

function code() {
  return `vika-${[...randomBytes(8)].map((byte) => ALPHABET[byte % ALPHABET.length]).join('')}`;
}

const qqId = (value) => {
  const text = typeof value === 'number' ? String(value) : typeof value === 'string' ? value : '';
  return /^[1-9]\d{0,15}$/.test(text) && Number.isSafeInteger(Number(text)) ? text : null;
};

// The code contains no CQ-code characters, so a string-format message
// compares as it is.
function textOf(message) {
  if (typeof message === 'string') return message;
  if (!Array.isArray(message)) return '';
  return message.map((segment) => (segment?.type === 'text' && typeof segment.data?.text === 'string' ? segment.data.text : '')).join('');
}

// Connects, asks who the account is, then binds the owner: the person who
// sends the one-time code to the account in a private friend message. The
// instance's own receiver may stay connected meanwhile; OneBot gives every
// connection every event. A failed login leaves the stored account as it was.
export async function loginOneBot({ state, url, token, WebSocketImpl = globalThis.WebSocket,
  signal = AbortSignal.timeout(300_000), onChallenge = () => {}, logger = quiet }) {
  const socket = new OneBotSocket({ url, token, WebSocketImpl, logger });
  const secret = code();
  const startedAt = Date.now() - 5_000;
  const done = new AbortController();
  let self = null;
  let ready, bind;
  const opened = new Promise((resolve) => { ready = resolve; });
  const bound = new Promise((resolve) => { bind = resolve; });
  // One connection: a login that loses it is started again by the person.
  const running = socket.run((raw) => {
    const at = eventTime(raw);
    const peer = qqId(raw.user_id);
    if (self && raw.post_type === 'message' && raw.message_type === 'private' && raw.sub_type === 'friend'
      && peer && !isSelf(raw, self.selfId) && textOf(raw.message).trim() === secret && at !== null && at >= startedAt) {
      bind({ ownerId: peer, at });
    }
  }, AbortSignal.any([signal, done.signal]), { onReady: () => ready(), reconnect: false });
  const ended = running.then(() => { throw signal.reason ?? new Error('OneBot login was cancelled'); });
  ended.catch(() => {});
  try {
    await Promise.race([opened, ended]);
    const info = await Promise.race([socket.call('get_login_info', {}, signal), ended]);
    const selfId = qqId(info?.user_id);
    if (!selfId) throw new Error('OneBot returned an invalid login account');
    self = { selfId, selfName: cleanName(info.nickname) ?? '' };
    onChallenge({ code: secret, ...self });
    const owner = await Promise.race([bound, ended]);
    // Only to show the group ids the policy file can name.
    const groups = await socket.call('get_group_list', {}, signal).then((list) => (Array.isArray(list) ? list : [])
      .slice(0, MAX_GROUPS).map((group) => ({ id: qqId(group?.group_id), name: cleanName(group?.group_name) ?? '' }))
      .filter((group) => group.id), () => []);
    const existing = state.account();
    state.saveAccount({ url, token, ...self, ownerId: owner.ownerId,
      activatedAt: existing?.selfId === selfId ? existing.activatedAt : owner.at });
    return { ...self, ownerId: owner.ownerId, groups };
  } finally {
    done.abort();
    await running.catch(() => {});
  }
}
