// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from '../connectors/protocol.mjs';

// Slack message events → canonical chat events. A message is (channel, ts)
// within a workspace; the event id is that identity rather than Slack's
// event_id, so a redelivery, an overlapping connection or a later catch-up
// read all deduplicate in the Core.

const TS = /^\d{1,12}\.\d{1,8}$/;
const CHANNEL = /^[CDG][A-Z0-9]{2,30}$/;
const USER = /^[UW][A-Z0-9]{2,30}$/;
const BOT = /^B[A-Z0-9]{2,30}$/;
const FILE = /^F[A-Z0-9]{2,30}$/;
// New input only: edits, deletions, joins, topic changes and the like are
// not something to answer, and as accepted events they would make the
// agent's pending decision in that chat stale.
const SUBTYPES = new Set([undefined, 'thread_broadcast', 'file_share', 'me_message', 'bot_message']);
const CHANNEL_TYPES = { im: 'p2p', mpim: 'group', channel: 'group', group: 'group' };
const NAME_CHARS = 80;

export function messageId(teamId, channel, ts) { return `sl:${teamId}:${channel}:${ts}`; }

export function parseMessageId(id, teamId) {
  const match = /^sl:(T[A-Z0-9]{2,30}):([CDG][A-Z0-9]{2,30}):(\d{1,12}\.\d{1,8})$/.exec(String(id ?? ''));
  if (!match || match[1] !== teamId) return null;
  return { channel: match[2], ts: match[3] };
}

export function validChannel(id) { return CHANNEL.test(id ?? ''); }
export function validTimestamp(value) { return typeof value === 'string' && TS.test(value); }

export function cleanName(value) {
  const name = String(value ?? '').replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').trim().slice(0, NAME_CHARS);
  return name || null;
}

function millis(ts) {
  const [seconds, fraction] = ts.split('.');
  return Number(seconds) * 1000 + Number(fraction.padEnd(3, '0').slice(0, 3));
}

function isBot(event) { return Boolean(event.bot_id) || event.subtype === 'bot_message'; }

// Whether a Socket Mode payload is a message this agent should hear. The
// bot's own posts come back as events too and are dropped here: as accepted
// events they would wake the model with its own words and block the second
// message of a burst as stale.
export function relevant(payload, account) {
  const event = payload?.event;
  if (payload?.type !== 'event_callback' || event?.type !== 'message' || payload.team_id !== account.teamId) return false;
  if (payload.api_app_id && payload.api_app_id !== account.appId) return false;
  if (event.hidden || !SUBTYPES.has(event.subtype) || !CHANNEL_TYPES[event.channel_type]) return false;
  return event.user !== account.botUserId && event.bot_id !== account.botId;
}

// User ids whose names the event shows: its author and everyone it mentions.
export function namedUsers(event) {
  const ids = new Set(USER.test(event.user ?? '') && !isBot(event) ? [event.user] : []);
  for (const [, id] of String(event.text ?? '').matchAll(/<@([UW][A-Z0-9]{2,30})(?:\|[^>]*)?>/g)) ids.add(id);
  return ids;
}

// Slack's markup back to plain text. Only tokens Slack itself produced are
// rendered: text a person typed arrives HTML-escaped, so a literal "<@U1>"
// stays literal.
export function renderText(text, nameOf) {
  return String(text ?? '')
    .replace(/<@([UW][A-Z0-9]{2,30})(?:\|[^>]*)?>/g, (_, id) => `@${nameOf(id) ?? id}`)
    .replace(/<#([CG][A-Z0-9]{2,30})(?:\|([^>]*))?>/g, (_, id, label) => `#${label || id}`)
    .replace(/<!(here|channel|everyone)(?:\|[^>]*)?>/g, '@$1')
    .replace(/<!subteam\^[A-Z0-9]+(?:\|([^>]*))?>/g, (_, label) => label || '@group')
    .replace(/<![^>|]*\|([^>]*)>/g, '$1')
    .replace(/<![^>]*>/g, '')
    .replace(/<((?:https?|mailto):[^>|]+)\|([^>]+)>/g, (_, url, label) => (url.endsWith(label) ? url : `${label} (${url})`))
    .replace(/<((?:https?|mailto):[^>|]+)>/g, '$1')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}

// App posts often say everything in attachments or blocks with empty text.
function fallbackText(event) {
  const parts = [];
  for (const attachment of Array.isArray(event.attachments) ? event.attachments.slice(0, 10) : []) {
    for (const key of ['pretext', 'title', 'text', 'fallback']) {
      if (typeof attachment?.[key] === 'string' && attachment[key].trim()) { parts.push(attachment[key].trim()); break; }
    }
  }
  return parts.join('\n');
}

function files(event) {
  const list = Array.isArray(event.files) ? event.files.slice(0, 10) : [];
  return list.filter((file) => FILE.test(file?.id ?? '') && !['tombstone', 'hidden_by_limit', 'external'].includes(file.mode))
    .map((file) => ({
      id: file.id,
      kind: String(file.mimetype ?? '').startsWith('image/') ? 'image' : 'file',
      name: cleanName(file.name) ?? file.id,
      mimeType: typeof file.mimetype === 'string' ? file.mimetype.slice(0, 255) : 'application/octet-stream',
      size: Number.isSafeInteger(file.size) && file.size >= 0 ? file.size : null,
    }));
}

// History is context, never a new event: retain old/self messages without
// applying the receive activation cutoff or waking the model again.
export function historyMessage(event, { account, channel, names = new Map(), agentName }) {
  if (!validTimestamp(event?.ts) || (event.channel && event.channel !== channel)
    || (event.thread_ts !== undefined && !validTimestamp(event.thread_ts))) throw new Error('Invalid Slack history message identity');
  if (event.hidden || ['tombstone', 'message_deleted'].includes(event.subtype)) return null;
  const bot = isBot(event) || event.user === account.botUserId;
  const author = USER.test(event.user ?? '') ? event.user : BOT.test(event.bot_id ?? '') ? event.bot_id : null;
  const nameOf = (id) => id === account.botUserId ? cleanName(agentName) : names.get(id) ?? null;
  const name = bot ? cleanName(event.bot_profile?.name ?? event.username) ?? nameOf(author) : nameOf(author);
  const attached = files(event);
  const text = renderText(event.text, nameOf).trim() || renderText(fallbackText(event), nameOf).trim();
  const id = messageId(account.teamId, channel, event.ts);
  const threadTs = event.thread_ts && event.thread_ts !== event.ts ? event.thread_ts : null;
  const root = messageId(account.teamId, channel, threadTs ?? event.ts);
  const content = [text, ...attached.map(file => `[attachment type=${file.kind} id=${file.id} name=${JSON.stringify(file.name)}]`)].filter(Boolean).join('\n');
  return {
    message: { message_id: id, chat_id: channel, tenant_key: account.teamId, thread_id: root,
      conversation: { id: channel, tenant_id: account.teamId, thread_id: threadTs },
      thread_ts: threadTs, create_time: String(millis(event.ts)), msg_type: attached.length ? 'file' : 'text',
      sender: { id: author, name, type: bot ? 'bot' : author ? 'user' : 'unknown' }, content,
      attachments: attached.map(file => ({ id: file.id, type: file.kind, name: file.name })) },
    route: { channel, ts: event.ts, threadTs, files: attached },
  };
}

// The canonical event, its reply route and the people it named; null when
// it is not for the agent. `names` maps user and bot ids to display names.
export function normalizeMessage(payload, { account, instance = 'slack', agentName = null, names = new Map() }) {
  if (!relevant(payload, account)) return null;
  const event = payload.event;
  if (!CHANNEL.test(event.channel ?? '') || !TS.test(event.ts ?? '')) throw new Error('Invalid Slack message identity');
  const at = millis(event.ts);
  if (at < account.activatedAt) return null;
  const bot = isBot(event);
  const authorId = USER.test(event.user ?? '') ? event.user : bot && BOT.test(event.bot_id ?? '') ? event.bot_id : null;
  if (!authorId) return null;
  const nameOf = (id) => (id === account.botUserId ? cleanName(agentName) : names.get(id) ?? null);
  const authorName = bot ? cleanName(event.bot_profile?.name ?? event.username) ?? nameOf(authorId) : nameOf(authorId);
  const attached = files(event);
  const text = renderText(event.text, nameOf).trim() || renderText(fallbackText(event), nameOf).trim();
  if (!text && attached.length === 0) return null;
  const id = messageId(account.teamId, event.channel, event.ts);
  const threadTs = TS.test(event.thread_ts ?? '') && event.thread_ts !== event.ts ? event.thread_ts : null;
  const rootId = threadTs ? messageId(account.teamId, event.channel, threadTs) : null;
  const mentioned = [...namedUsers(event)].filter((user) => user !== authorId);
  const people = [[authorId, authorName], ...mentioned.map((user) => [user, nameOf(user)])]
    .filter(([user, name]) => name && USER.test(user) && user !== account.botUserId);
  const envelope = eventMessage('slack', {
    instance, id: `${account.teamId}:${event.channel}:${event.ts}`, kind: 'message.created', occurred_at: at,
    subject: { type: 'conversation', id: event.channel },
    actor: { id: authorId, type: bot ? 'bot' : 'user', ...(authorName ? { name: authorName } : {}) },
    conversation: { id: event.channel, type: CHANNEL_TYPES[event.channel_type], tenant_id: account.teamId,
      thread_id: threadTs, ...(payload.is_ext_shared_channel === true ? { external: true } : {}) },
    message: {
      id, type: attached.length ? (attached.every((file) => file.kind === 'image') ? 'image' : 'file') : 'text',
      parent_id: rootId, root_id: rootId, thread_root_id: rootId,
      mentions: mentioned.map((user) => ({ id: user, name: nameOf(user) })),
    },
    content: { text, attachments: attached.map((file) => ({ id: file.id, type: file.kind, name: file.name })) },
    reply_route: { action: 'message.reply', input: { message_id: id } },
  });
  return { envelope, id, route: { channel: event.channel, ts: event.ts, threadTs, files: attached }, people };
}
