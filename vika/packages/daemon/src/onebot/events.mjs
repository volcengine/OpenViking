// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from '../connectors/protocol.mjs';

// OneBot v11 message events → canonical chat events. A message is
// (chat, message_id) within one account; the event id adds the send time
// because NapCat's message_id is a 32-bit hash that can repeat, and the Core
// deduplicates event ids for months.

const NAME_CHARS = 80;
const TEXT_CHARS = 16_000;
const MAX_SEGMENTS = 200;
const MAX_MENTIONS = 100;
const MAX_FILES = 10;
const CARD_JSON_CHARS = 64 * 1024;
const CARD_TEXT_CHARS = 200;
const CARD_URL_CHARS = 512;
const STICKER_CHARS = 32;
const CHAT = /^(group|private):([1-9]\d{0,15})$/;
const MESSAGE = /^ob:((?:group|private):[1-9]\d{0,15}):(-?\d{1,16})$/;
const MARKER = /^[a-z][a-z0-9_]{0,23}$/;
const SUB_TYPES = { group: 'normal', private: 'friend' };

const qqId = (value) => {
  const text = typeof value === 'number' ? String(value) : typeof value === 'string' ? value : '';
  return /^[1-9]\d{0,15}$/.test(text) && Number.isSafeInteger(Number(text)) ? text : null;
};

// NapCat's message_id is a signed hash: keep the sign, reject anything else.
const midOf = (value) => {
  const number = typeof value === 'number' ? value : typeof value === 'string' && /^-?\d{1,16}$/.test(value) ? Number(value) : NaN;
  return Number.isSafeInteger(number) ? String(number) : null;
};

export function groupChat(id) { return `group:${id}`; }
export function privateChat(id) { return `private:${id}`; }

export function parseChat(chatId) {
  const match = CHAT.exec(String(chatId ?? ''));
  return match && qqId(match[2]) ? { kind: match[1], id: match[2] } : null;
}

export function messageId(chat, mid) { return `ob:${chat}:${mid}`; }

export function parseMessageId(id) {
  const match = MESSAGE.exec(String(id ?? ''));
  if (!match || !parseChat(match[1]) || midOf(match[2]) !== match[2]) return null;
  return { chat: match[1], mid: match[2] };
}

export function cleanName(value) {
  const name = String(value ?? '').replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').trim().slice(0, NAME_CHARS);
  return name || null;
}

// The account's own messages come back as `message_sent` (NapCat, LLOneBot)
// or as an ordinary `message` whose author is the account (Lagrange).
export function isSelf(raw, selfId) {
  return raw?.post_type === 'message_sent' || (raw?.user_id != null && String(raw.user_id) === String(selfId));
}

// A private chat is named after the other person: the author of an incoming
// message, the recipient (`target_id`) of one the account sent.
export function chatOf(raw, selfId) {
  if (raw?.message_type === 'group') {
    const id = qqId(raw.group_id);
    return id ? groupChat(id) : null;
  }
  if (raw?.message_type === 'private') {
    const id = qqId(isSelf(raw, selfId) ? raw.target_id : raw.user_id);
    return id ? privateChat(id) : null;
  }
  return null;
}

// OneBot `time` is seconds; NapCat falls back to milliseconds when the
// client gave it no time.
export function eventTime(raw) {
  const time = typeof raw?.time === 'string' && /^\d{1,16}$/.test(raw.time) ? Number(raw.time) : raw?.time;
  if (!Number.isSafeInteger(time) || time <= 0) return null;
  return time > 1e11 ? time : time * 1000;
}

// A CQ-code string (messagePostFormat=string) as the segments it stands for,
// so its media URLs, which carry download keys, never become text.
function cqSegments(text) {
  const unescape = (value) => value.replace(/&#91;/g, '[').replace(/&#93;/g, ']').replace(/&#44;/g, ',').replace(/&amp;/g, '&');
  const segments = [];
  let last = 0;
  for (const match of text.matchAll(/\[CQ:([A-Za-z0-9_.-]+)((?:,[^\]]*)?)\]/g)) {
    if (match.index > last) segments.push({ type: 'text', data: { text: unescape(text.slice(last, match.index)) } });
    const data = {};
    for (const pair of match[2].split(',').slice(1)) {
      const at = pair.indexOf('=');
      if (at > 0) data[pair.slice(0, at)] = unescape(pair.slice(at + 1));
    }
    segments.push({ type: match[1], data });
    last = match.index + match[0].length;
  }
  if (last < text.length) segments.push({ type: 'text', data: { text: unescape(text.slice(last)) } });
  return segments;
}

function segmentsOf(raw) {
  const segments = typeof raw?.message === 'string' ? cqSegments(raw.message) : Array.isArray(raw?.message) ? raw.message : [];
  return segments.slice(0, MAX_SEGMENTS).map((segment) => ({
    type: typeof segment?.type === 'string' ? segment.type : '',
    data: segment?.data && typeof segment.data === 'object' && !Array.isArray(segment.data) ? segment.data : {},
  }));
}

// User ids the line @-mentions, so their names can be looked up before it is
// rendered. The author is not among them: the event carries the author's name.
export function namedUsers(raw) {
  const ids = new Set();
  for (const { type, data } of segmentsOf(raw)) {
    const id = type === 'at' ? qqId(data.qq) : null;
    if (id) ids.add(id);
  }
  return ids;
}

const httpUrl = (value) => typeof value === 'string' && value.length <= 8192 && /^https?:\/\//i.test(value) ? value : null;
const shortString = (value) => (typeof value === 'string' && value && value.length <= 1024
  && !/[\u0000-\u001f\u007f]/.test(value) ? value : null);
const fileSize = (value) => {
  const size = typeof value === 'string' && /^\d{1,16}$/.test(value) ? Number(value) : value;
  return Number.isSafeInteger(size) && size >= 0 ? size : null;
};

// The name to show for an attachment. Dialects disagree on the field, and
// some put a URL or an opaque media code in `file`; only a file-like name is used.
function fileName(data, fallback) {
  for (const value of [data.name, data.file_name, data.filename, data.file]) {
    const name = typeof value === 'string' && !value.includes('://') ? cleanName(value) : null;
    if (name && (value !== data.file || /\.[A-Za-z0-9]{1,8}$/.test(name))) return name;
  }
  return fallback;
}

// The download address is `url` only. `file` can be a name the sender chose,
// and a name that looks like an address must not become one.
function media(kind, data, fallback) {
  const local = shortString(data.file) && !data.file.includes('://') && !data.file.startsWith('base64:') ? data.file : null;
  return {
    kind, name: fileName(data, fallback), file: local, fileId: shortString(data.file_id),
    url: httpUrl(data.url), size: fileSize(data.file_size),
  };
}

// Stickers are images to QQ but words to a reader: a custom face (sub_type 1),
// a market face (emoji_id), or an image whose summary names it, such as
// "[动画表情]". "[图片]" is Lagrange's summary for any ordinary picture.
function isSticker(data) {
  const summary = typeof data.summary === 'string' ? data.summary.trim() : '';
  return Number(data.sub_type ?? data.subType) === 1 || data.emoji_id != null
    || (/^\[.+\]$/.test(summary) && summary !== '[图片]');
}

function stickerText(data) {
  const summary = cleanName(data.summary)?.slice(0, STICKER_CHARS);
  if (!summary || summary === '[图片]') return '[sticker]';
  return summary.startsWith('[') ? summary : `[${summary}]`;
}

// A share card or mini-app is a JSON blob of the sender's choosing: only its
// headline and link are shown, bounded, never the JSON itself.
function cardText(data) {
  let value = data.data;
  if (typeof value === 'string') {
    if (value.length > CARD_JSON_CHARS) return '[card]';
    try { value = JSON.parse(value); } catch { return '[card]'; }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '[card]';
  const meta = value.meta && typeof value.meta === 'object'
    ? Object.values(value.meta).filter((item) => item && typeof item === 'object').slice(0, 5) : [];
  const headline = [...new Set([value.prompt, ...meta.flatMap((item) => [item.title, item.desc])]
    .map((item) => (typeof item === 'string' ? cleanName(item.replace(/\s+/g, ' ')) : null)).filter(Boolean))]
    .slice(0, 2).join(' ').slice(0, CARD_TEXT_CHARS);
  const url = meta.flatMap((item) => [item.jumpUrl, item.qqdocurl, item.url]).find(shareUrl);
  return ['[card]', headline, url].filter(Boolean).join(' ');
}

function shareUrl(value) {
  if (typeof value !== 'string' || value.length > CARD_URL_CHARS || !/^https?:\/\//i.test(value)) return false;
  try {
    // Media links carry a download key; a card's share link has none.
    return !new URL(value).searchParams.has('rkey') && !/[\s\u0000-\u001f\u007f]/.test(value);
  } catch { return false; }
}

// One message's segments as text, mentions, attachments and the quoted
// message. `bareText` skips the leading quote and @self that addressing the
// account puts first, so a command prefix can be checked on what was typed.
// `spokenText` leaves out every @mention, so the agent's names are looked
// for in what was typed and not in the names of the people it mentions.
function render(raw, { chat, account, names }) {
  const selfId = String(account.selfId);
  const pieces = [];
  const bare = [];
  const spoken = [];
  const mentions = new Map();
  const files = [];
  let atSelf = false;
  let replyTo = null;
  let leading = true;
  for (const { type, data } of segmentsOf(raw)) {
    let piece = '';
    let addressing = false;
    if (type === 'text') {
      piece = typeof data.text === 'string' ? data.text : '';
      addressing = !piece.trim();
    } else if (type === 'at') {
      if (data.qq === 'all') piece = '@全体成员';
      else {
        const id = qqId(data.qq);
        if (!id) continue;
        const name = id === selfId ? cleanName(account.selfName) : cleanName(names.get(id)) ?? cleanName(data.name);
        piece = `@${name ?? id}`;
        if (!mentions.has(id)) mentions.set(id, name ?? null);
        if (id === selfId) atSelf = addressing = true;
      }
    } else if (type === 'reply') {
      const mid = midOf(data.id);
      if (mid && !replyTo) replyTo = messageId(chat, mid);
      addressing = true;
    } else if (type === 'image' || type === 'mface') {
      if (type === 'mface' || isSticker(data)) piece = stickerText(data);
      else if (files.length < MAX_FILES) files.push(media('image', data, 'image'));
    } else if (type === 'file' || type === 'video') {
      if (files.length < MAX_FILES) files.push(media('file', data, type === 'video' ? 'video.mp4' : 'file'));
    } else if (type === 'forward') {
      piece = '[forwarded chat]';
      const id = shortString(data.id);
      if (id && files.length < MAX_FILES) {
        files.push({ kind: 'forward', name: 'forwarded-chat.txt', file: id, fileId: null, url: null, size: null });
      }
    } else if (type === 'json') piece = cardText(data);
    else if (type === 'record') piece = '[voice message]';
    else if (type === 'face') piece = '[face]';
    else piece = MARKER.test(type) ? `[${type}]` : '[unsupported]';
    pieces.push(piece);
    if (type !== 'at') spoken.push(piece);
    if (leading && addressing) continue;
    leading = false;
    bare.push(piece);
  }
  return {
    text: pieces.join('').trim().slice(0, TEXT_CHARS),
    bareText: bare.join('').trim().slice(0, TEXT_CHARS),
    spokenText: spoken.join('').trim().slice(0, TEXT_CHARS),
    mentions: [...mentions].map(([id, name]) => ({ id, name })),
    files: files.map((file, index) => ({ id: String(index), ...file })),
    atSelf, replyTo,
  };
}

// The text of a message's segments alone, as a forwarded chat's nodes carry
// them; its attachments are not reachable from there.
export function segmentText(message, { chat, account }) {
  return render({ message }, { chat, account, names: new Map() }).text;
}

const attachmentsOf = (files) => files.map((file) => ({ id: file.id, type: file.kind === 'image' ? 'image' : 'file', name: file.name }));

function messageType(files) {
  if (!files.length) return 'text';
  return files.every((file) => file.kind === 'image') ? 'image' : 'file';
}

function senderName(raw, names, id) {
  return cleanName(raw.sender?.card) ?? cleanName(raw.sender?.nickname) ?? cleanName(names.get(id));
}

// The canonical event and what the client needs to stage, classify and route
// it; null when it is not a fresh message of this account. Admission (which
// chats exist) and classification are the caller's.
export function normalizeMessage(raw, { account, instance = 'onebot', names = new Map() }) {
  if (raw?.post_type !== 'message' && raw?.post_type !== 'message_sent') return null;
  if (!SUB_TYPES[raw.message_type] || raw.sub_type !== SUB_TYPES[raw.message_type]) return null;
  const selfId = String(account.selfId);
  if (raw.self_id != null && String(raw.self_id) !== selfId) return null;
  const chat = chatOf(raw, selfId);
  const mid = midOf(raw.message_id);
  const userId = qqId(raw.user_id);
  const at = eventTime(raw);
  if (!chat || !mid || !userId || at === null) throw new Error('Invalid OneBot message identity');
  if (at < account.activatedAt) return null;
  const self = isSelf(raw, selfId);
  const { text, bareText, spokenText, mentions, files, atSelf, replyTo } = render(raw, { chat, account, names });
  if (!text && !files.length) return null;
  // The Core takes at most 100 mentions. The text still shows every @, and
  // the agent's own is kept so that the line still mentions it.
  const listed = mentions.length > MAX_MENTIONS
    ? [...mentions.filter((item) => item.id === selfId), ...mentions.filter((item) => item.id !== selfId)].slice(0, MAX_MENTIONS)
    : mentions;
  const name = self ? cleanName(account.selfName) ?? senderName(raw, names, userId) : senderName(raw, names, userId);
  const actorId = self ? selfId : userId;
  const id = messageId(chat, mid);
  const envelope = eventMessage('onebot', {
    instance, id: `${selfId}:${chat}:${mid}:${Math.floor(at / 1000)}`, kind: 'message.created', occurred_at: at,
    subject: { type: 'conversation', id: chat },
    actor: { id: actorId, type: self ? 'bot' : 'user', ...(name ? { name } : {}) },
    conversation: { id: chat, type: raw.message_type === 'group' ? 'group' : 'p2p', tenant_id: selfId },
    message: { id, type: messageType(files), parent_id: replyTo, mentions: listed },
    content: { text, attachments: attachmentsOf(files) },
    reply_route: { action: 'message.reply', input: { message_id: id } },
  });
  const people = new Map();
  for (const [user, label] of [...(self ? [] : [[userId, name]]), ...mentions.map((item) => [item.id, item.name])]) {
    if (label && user !== selfId && !people.has(user)) people.set(user, label);
  }
  return { envelope, id, chat, mid, userId, self, text, bareText, spokenText, atSelf, replyTo, files, people: [...people] };
}

// History is context, never a new event: it keeps the account's own and old
// messages, without the activation cutoff.
export function historyMessage(raw, { account, chat, names = new Map() }) {
  const parsed = parseChat(chat);
  const selfId = String(account.selfId);
  const mid = midOf(raw?.message_id);
  const userId = qqId(raw?.user_id);
  const at = eventTime(raw);
  const inChat = parsed?.kind === 'group' ? qqId(raw?.group_id) === parsed.id
    : parsed?.kind === 'private' && raw?.message_type === 'private' && (userId === parsed.id || userId === selfId);
  if (!mid || !userId || at === null || !inChat) throw new Error('Invalid OneBot history message identity');
  const self = isSelf(raw, selfId);
  const { text, files } = render(raw, { chat, account, names });
  if (!text && !files.length) return null;
  const attachments = attachmentsOf(files);
  const id = messageId(chat, mid);
  const content = [text, ...attachments.map((file) => `[attachment type=${file.type} id=${file.id} name=${JSON.stringify(file.name)}]`)]
    .filter(Boolean).join('\n');
  return {
    // `conversation` lets the Core route a reply to a message it only saw here.
    message: { message_id: id, chat_id: chat, conversation: { id: chat, tenant_id: selfId, thread_id: null },
      create_time: String(at), msg_type: messageType(files),
      sender: { id: self ? selfId : userId, name: self ? cleanName(account.selfName) ?? senderName(raw, names, userId) : senderName(raw, names, userId),
        type: self ? 'bot' : 'user' },
      content, attachments },
    route: { chat, mid, files },
  };
}
