// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { normalizeEvent } from '../event.mjs';
import { eventMessage } from './protocol.mjs';
import { createHash } from 'node:crypto';
import { isMeetingEvent, normalizeMeetingEvent } from '../meeting/events.mjs';

export function normalizeLarkEvent(input, instance = 'lark') {
  if (isMeetingEvent(input)) {
    const { raw, ...meeting } = normalizeMeetingEvent(input);
    const kind = {
      'vc.bot.meeting_invited_v1': 'meeting.invited',
      'vc.bot.meeting_activity_v1': 'meeting.activity',
      'vc.bot.meeting_ended_v1': 'meeting.ended',
    }[meeting.eventType];
    return eventMessage('lark', {
      instance, id: meeting.eventId, kind, occurred_at: meeting.createTimeMs,
      subject: { type: 'meeting', id: meeting.meetingId || meeting.meetingNumber || meeting.callId || meeting.eventId },
      content: {}, data: { meeting },
    });
  }
  if ((input?.header?.event_type ?? input?.type ?? input?.event_type) === 'card.action.trigger') {
    const raw = input.event ?? input;
    const id = input.header?.event_id ?? input.event_id;
    const cardId = raw.context?.open_message_id ?? raw.open_message_id ?? raw.message_id;
    const operator = raw.operator?.open_id ?? raw.operator_id;
    if (!/^om_[A-Za-z0-9_-]{8,}$/.test(cardId ?? '')
      || !/^ou_[A-Za-z0-9_-]{8,}$/.test(operator ?? '')) throw new Error('invalid card callback identity');
    const action = raw.action ?? {};
    const interaction = {
      message_id: cardId,
      conversation_id: raw.context?.open_chat_id ?? raw.chat_id ?? null,
      tag: action.tag ?? raw.action_tag,
      value: parseJson(action.value ?? raw.action_value),
      name: action.name ?? raw.action_name,
      form: parseJson(action.form_value ?? raw.form_value),
      input: action.input_value ?? raw.input_value,
      option: action.option ?? raw.option,
      options: action.options ?? raw.options,
      checked: action.checked ?? raw.checked,
    };
    if (JSON.stringify(interaction).length > 12000) throw new Error('card action exceeds the input limit');
    return eventMessage('lark', {
      instance, id, kind: 'message.action',
      occurred_at: Number(input.header?.create_time ?? raw.timestamp ?? Date.now()),
      subject: { type: 'message', id: cardId }, actor: { type: 'user', id: operator },
      message: { id: `ca_${createHash('sha256').update(JSON.stringify([instance, id])).digest('hex').slice(0, 40)}`, type: 'card_action' },
      content: { text: '[card action]' }, data: { interaction },
    });
  }
  const messageEvent = normalizeLarkMessageEvent(input, instance);
  if (messageEvent) return messageEvent;
  return normalizeLarkMessage(input, instance).envelope;
}

// A recall or a reaction on a message the bot can see. Lark names the message
// but not always its chat or thread (reactions carry no chat at all), so the
// Core places it from its own history (lark-message-events.mjs). msg= is a
// stable internal id; the message it is about is the parent.
const MESSAGE_EVENT_KINDS = {
  'im.message.recalled_v1': 'message.recalled',
  'im.message.reaction.created_v1': 'message.reaction.created',
  'im.message.reaction.deleted_v1': 'message.reaction.deleted',
};

function normalizeLarkMessageEvent(input, instance) {
  const kind = MESSAGE_EVENT_KINDS[input?.header?.event_type ?? input?.type ?? input?.event_type];
  if (!kind) return null;
  const raw = input.event ?? input;
  const id = valueOrNull(input.header?.event_id ?? input.event_id);
  const target = valueOrNull(raw.message_id);
  if (!id || !/^om_[A-Za-z0-9_-]{8,}$/.test(target ?? '')) throw new Error('invalid Lark message event');
  const recall = kind === 'message.recalled';
  const operatorType = recall ? null : valueOrNull(raw.operator_type) ?? 'user';
  const operator = recall ? null : operatorType === 'app' ? valueOrNull(raw.app_id)
    : valueOrNull(raw.user_id?.open_id ?? raw.operator_id?.open_id);
  // The envelope names the subscribing app; the event names the reacting app.
  const receiverAppId = valueOrNull(input.header?.app_id);
  const emoji = recall ? null : valueOrNull(raw.reaction_type?.emoji_type);
  if (!recall && (!emoji || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(emoji))) throw new Error('invalid Lark reaction');
  const at = Number.parseInt(String((recall ? raw.recall_time : raw.action_time) ?? input.header?.create_time ?? Date.now()), 10);
  return eventMessage('lark', {
    instance, id, kind, occurred_at: Number.isFinite(at) ? at : Date.now(),
    subject: { type: 'message', id: target },
    actor: operator ? { type: operatorType === 'app' ? 'app' : 'user', id: operator }
      : operatorType === 'app' ? { type: 'app', id: 'bot' } : null,
    message: { id: `${recall ? 'rc' : 're'}_${createHash('sha256').update(JSON.stringify([instance, id])).digest('hex').slice(0, 40)}`,
      type: recall ? 'recall' : 'reaction', parent_id: target },
    content: { text: recall ? '[recalled a message]'
      : kind === 'message.reaction.created' ? `[reaction ${emoji}]` : `[removed reaction ${emoji}]` },
    data: { target_message_id: target, chat_id: valueOrNull(raw.chat_id), recall_type: valueOrNull(raw.recall_type),
      emoji_type: emoji, supported: operatorType !== 'app'
        || Boolean(operator && receiverAppId && operator !== receiverAppId) },
  });
}

// Lark's provider schema is decoded only at the Connector boundary. Media is
// delivered as a resource reference, never as bytes in the prompt.
const AGENT_VISIBLE_MESSAGE_TYPES = new Set([
  'text', 'post', 'image', 'file', 'audio', 'media', 'video', 'sticker',
  'merge_forward', 'video_chat', 'interactive', 'card_action',
]);
const IMAGE_KEY = /^img_[A-Za-z0-9_-]{8,}$/;

// A card can carry its text in up to 16 languages, and the client shows the
// one its own language asks for. The message JSON keeps them all, so one
// locale is read, once: English first, then Chinese, then whatever the card
// lists first. Reading every copy would put the same sentence in the prompt
// two or three times, in languages the reader never chose.
const LOCALES = new Set([
  'zh_cn', 'zh_hk', 'zh_tw', 'en_us', 'ja_jp', 'id_id', 'vi_vn', 'th_th',
  'pt_br', 'es_es', 'ko_kr', 'de_de', 'fr_fr', 'it_it', 'ru_ru', 'ms_my',
]);
const LOCALE_PRIORITY = ['en', 'zh'];
// A field translated per language beside the field it translates
// (局部多语言): that field keeps the default text and stays the one read.
const LOCAL_I18N = { i18n_content: 'content', i18n_default_value: 'default_value' };
// A block configured in every language in place of a whole field (全局多语言):
// the locale map decides the field, and the field it replaces is not read.
// i18n_header is the JSON 1.0 header, i18n_elements and i18n the body and its
// title, i18n_text_tag_list the title's tags.
const GLOBAL_I18N = {
  i18n: 'content', i18n_header: 'header', i18n_elements: 'elements', i18n_text_tag_list: 'text_tag_list',
};

function localeRank(locale) {
  const rank = LOCALE_PRIORITY.indexOf(String(locale).toLowerCase().split('_')[0]);
  return rank === -1 ? LOCALE_PRIORITY.length : rank;
}

// Reads the first language of a locale-keyed map ({en_us: ..., zh_cn: ...})
// that actually yields text, into `output`. A block of its own may hold
// nothing readable (a divider, an empty array), and then the language after
// it is read instead, rather than nothing standing in for it.
function readLocale(map, output, mediaKeys, depth) {
  if (!map || typeof map !== 'object' || Array.isArray(map)) return false;
  const filled = Object.entries(map).filter(([locale]) => LOCALES.has(locale.toLowerCase()));
  // The sort is stable, so locales of one rank keep the card's own order.
  filled.sort(([left], [right]) => localeRank(left) - localeRank(right));
  for (const [, text] of filled) {
    const scratch = [];
    collectText(text, scratch, mediaKeys, depth + 1);
    if (scratch.length === 0) continue;
    output.push(...scratch);
    return true;
  }
  return false;
}

// The image of an i18n_img_key map in the language the card text is read in.
function cardImageKey(map) {
  if (!map || typeof map !== 'object' || Array.isArray(map)) return undefined;
  return Object.entries(map)
    .filter(([locale, key]) => LOCALES.has(locale.toLowerCase()) && typeof key === 'string' && IMAGE_KEY.test(key))
    .sort(([left], [right]) => localeRank(left) - localeRank(right))[0]?.[1];
}

function parseJson(value) {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

// The media references a composite keeps (the model can still resolve them)
// and where its embedded card JSON starts. The first `{` whose remainder
// parses is the payload: the prefix is the client's fallback text, and a `{`
// inside that prefix never parses because the card JSON follows it.
const MEDIA_REFERENCE = /\[(?:image|file|audio|media|video|sticker)[^\]]*\]/g;

// Reads one language out of interactive content, or null when no embedded
// object parses (the caller keeps the string as it was). The one-language
// rule is collectText's: the default field when the card carries one beside
// its i18n map, then the locale priority.
function humanizeCompositeCard(text) {
  if (typeof text !== 'string' || !text.includes('{')) return null;
  let card = null;
  let index = -1;
  while ((index = text.indexOf('{', index + 1)) !== -1) {
    const candidate = parseJson(text.slice(index).trim());
    if (candidate !== null && typeof candidate === 'object' && !Array.isArray(candidate)) {
      card = candidate;
      break;
    }
  }
  if (card === null) return null;
  const refs = text.match(MEDIA_REFERENCE) ?? [];
  const parts = [];
  // An image the event already references is not listed a second time.
  const seen = new Set(refs.flatMap((ref) => {
    const key = /image_key=(\S+?)\]/.exec(ref)?.[1];
    return key ? [`image:${key}`] : [];
  }));
  collectText(card, parts, seen);
  if (parts.length === 0) return refs.length > 0 ? refs.join('\n') : '[card]';
  return [...refs, ...parts].join('\n').trim();
}

function collectText(value, output, mediaKeys = new Set(), depth = 0) {
  if (depth > 8 || value === null || value === undefined) return;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed) output.push(trimmed);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectText(item, output, mediaKeys, depth + 1);
    return;
  }
  if (typeof value !== 'object') return;

  // Rich-post image/media nodes carry resources inside otherwise generic
  // content. Preserve only the resource pointer that the matching MCP tool
  // can retrieve; keep the generic sensitive-key filter below for everything
  // else.
  // Any card image counts, not only an img element: a cover or background
  // ({img_key}), the items of an img_combination, a list or panel picture.
  // A custom_icon is decoration and stays out.
  if (value.tag !== 'custom_icon' && (value.tag === 'img' || value.tag === 'image'
    || 'img_key' in value || 'image_key' in value || 'i18n_img_key' in value)) {
    // Posts name the image image_key; card img elements name it img_key, or
    // only i18n_img_key per language, read by the same one-language rule.
    const imageKey = [value.image_key, value.img_key, cardImageKey(value.i18n_img_key)]
      .find((candidate) => typeof candidate === 'string' && IMAGE_KEY.test(candidate));
    if (imageKey) {
      const key = `image:${imageKey}`;
      if (!mediaKeys.has(key)) output.push(`[image image_key=${imageKey}]`);
      mediaKeys.add(key);
      const alt = [];
      collectText(value.alt, alt, mediaKeys, depth + 1);
      output.push(...alt);
      return;
    }
  }
  if ((value.tag === 'media' || value.tag === 'file')
    && typeof value.file_key === 'string' && value.file_key) {
    const key = `file:${value.file_key}`;
    if (!mediaKeys.has(key)) output.push(`[${value.tag} file_key=${value.file_key}]`);
    mediaKeys.add(key);
    return;
  }

  const visited = new Set();
  // A multi-language post carries its whole body under a locale key
  // ({zh_cn: {...}, en_us: {...}}), so it is read through the same rule.
  if (Object.keys(value).every((key) => LOCALES.has(key.toLowerCase()))) {
    readLocale(value, output, mediaKeys, depth);
    return;
  }
  // Text read through a locale map, kept until the title and the other plain
  // fields are in, so a card reads in its own order.
  const translated = [];
  for (const [key, base] of Object.entries(LOCAL_I18N)) {
    // The default field shields its translations only while it says something:
    // an empty default (content: "") leaves the card's text in the map, and
    // reading nothing there would drop the whole card to [card].
    const shields = Object.hasOwn(value, base)
      && (typeof value[base] !== 'string' || value[base].trim() !== '');
    if (!Object.hasOwn(value, key) || shields) continue;
    if (readLocale(value[key], translated, mediaKeys, depth)) visited.add(key);
  }
  for (const [key, base] of Object.entries(GLOBAL_I18N)) {
    if (!Object.hasOwn(value, key)) continue;
    // A map that reads nothing leaves the field it would replace readable.
    if (readLocale(value[key], translated, mediaKeys, depth)) visited.add(key).add(base);
  }
  const priorityKeys = ['text', 'title', 'content', 'name', 'file_name'];
  // A card's title sits under header and reads before the body.
  if (Object.hasOwn(value, 'header') && !visited.has('header')) {
    collectText(value.header, output, mediaKeys, depth + 1);
    visited.add('header');
  }
  for (const key of priorityKeys) {
    // A field a language map already replaced is not read a second time.
    if (visited.has(key) || !Object.hasOwn(value, key)) continue;
    collectText(value[key], output, mediaKeys, depth + 1);
    visited.add(key);
  }
  for (const field of translated) collectText(field, output, mediaKeys, depth + 1);
  for (const [key, child] of Object.entries(value)) {
    // Layout metadata a card carries beside its text: reading it would put
    // `vertical`, `12px 12px` and `2.0` in front of every card's sentence.
    if (visited.has(key) || /^i18n/i.test(key)
      || ['tag', 'style', 'language', 'schema', 'direction', 'padding', 'fallback',
        'template', 'type', 'size', 'width'].includes(key)
      || /(?:_key|_id|url|token|secret)$/i.test(key)) {
      // A picture named by a field of its own (cover_img_key, bg_image_key).
      if (value.tag !== 'custom_icon' && /(?:img|image)_key$/i.test(key) && typeof child === 'string' && IMAGE_KEY.test(child)
        && !mediaKeys.has(`image:${child}`)) {
        output.push(`[image image_key=${child}]`);
        mediaKeys.add(`image:${child}`);
      }
      continue;
    }
    collectText(child, output, mediaKeys, depth + 1);
  }
}

export function humanizeContent(messageType, rawContent) {
  if (messageType === 'interactive' && (rawContent == null
    || (typeof rawContent === 'string' && !rawContent.trim()))) return '[card]';
  // A card arrives as clean JSON, as a composite (media references and a
  // "please upgrade the client" note around the card's JSON), or as the note
  // alone. Left verbatim, every language copy in a composite travels into the
  // journal and the OV session sync, so interactive content is read through
  // the same one-language rule whatever shape it arrives in.
  if (messageType === 'interactive') {
    const humanized = humanizeCompositeCard(rawContent);
    if (humanized !== null) return humanized;
  }
  if (typeof rawContent === 'string' && !rawContent.trim().startsWith('{') && !rawContent.trim().startsWith('[')) {
    return rawContent.trim();
  }
  const parsed = parseJson(rawContent);
  if (typeof parsed === 'string') return parsed.trim();
  // image_key/file_key are resource pointers scoped to this message and requiring bot auth
  // to resolve, so surfacing them here (unlike other *_key/*_url fields) is not a content
  // leak — see packages/daemon/agent/lark-message-formats.md for how to fetch each type.
  if (messageType === 'image' && typeof parsed?.image_key === 'string' && IMAGE_KEY.test(parsed.image_key)) {
    return `[image image_key=${parsed.image_key}]`;
  }
  if (['file', 'audio', 'media', 'video', 'sticker'].includes(messageType)
    && typeof parsed?.file_key === 'string' && parsed.file_key) {
    const name = typeof parsed.file_name === 'string' && parsed.file_name ? ` file_name=${parsed.file_name}` : '';
    return `[${messageType} file_key=${parsed.file_key}${name}]`;
  }
  const parts = [];
  collectText(parsed, parts);
  const unique = [...new Set(parts)];
  if (unique.length > 0) return unique.join('\n').trim();
  // Lark emits call invitations and call lifecycle notices as video_chat IM
  // messages. They often carry no human-readable body, but still need to wake
  // the Agent so it can inspect the active meeting and decide whether to join.
  if (messageType === 'video_chat') return '[Video call]';
  if (messageType === 'merge_forward') return '[Forwarded messages]';
  // A card's receive event can arrive without any extractable body even though
  // the message API does return its text, so fall back to a placeholder that
  // wakes the Agent instead of letting empty_content drop the card. Reading the
  // body is a list_chat_messages call away — see lark-message-formats.md.
  if (messageType === 'interactive') return '[card]';
  if (['image', 'file', 'audio', 'media', 'video', 'sticker'].includes(messageType)) {
    return `[${messageType}]`;
  }
  return '';
}

function normalizeMentions(value) {
  if (!Array.isArray(value)) return [];
  return value.map((mention) => {
    if (!mention || typeof mention !== 'object') return null;
    return {
      key: mention.key ?? null,
      name: mention.name ?? null,
      id: mention.id?.open_id ?? mention.id?.user_id ?? mention.open_id ?? mention.user_id ?? null,
    };
  }).filter(Boolean);
}

function valueOrNull(value) {
  return value === undefined || value === null || value === '' ? null : String(value);
}

export function normalizeLarkMessage(input, instance = 'lark') {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('event must be a JSON object');
  }

  const isRaw = input.header && input.event;
  const header = isRaw ? input.header : {};
  const event = isRaw ? input.event : input;
  const message = isRaw ? event.message ?? {} : input;
  const sender = isRaw ? event.sender ?? {} : {};

  const type = valueOrNull(header.event_type ?? input.type ?? input.event_type);
  const messageId = valueOrNull(message.message_id ?? input.message_id ?? input.id);
  const eventId = valueOrNull(header.event_id ?? input.event_id);
  const chatId = valueOrNull(message.chat_id ?? input.chat_id);
  const chatType = valueOrNull(message.chat_type ?? input.chat_type) ?? 'unknown';
  const messageType = valueOrNull(message.message_type ?? input.message_type) ?? 'unknown';
  const senderType = valueOrNull(sender.sender_type ?? input.sender_type);
  // Another bot's message (with the include_bot permission) may carry no
  // user id; it still needs an actor, so the Core knows a bot wrote it.
  const senderId = valueOrNull(
    sender.sender_id?.open_id ?? sender.sender_id?.user_id ?? sender.sender_id?.app_id ?? input.sender_id,
  ) ?? (['app', 'bot'].includes(senderType) ? 'bot' : null);
  const rootId = valueOrNull(message.root_id ?? input.root_id);
  const parentId = valueOrNull(message.parent_id ?? input.parent_id);
  const threadId = valueOrNull(message.thread_id ?? input.thread_id);
  const threadRootId = threadId ? (rootId || (parentId && parentId !== messageId ? parentId : messageId)) : null;
  const tenantKey = valueOrNull(header.tenant_key ?? sender.tenant_key ?? input.tenant_key) ?? '-';
  const createTimeRaw = message.create_time ?? header.create_time ?? input.create_time ?? input.timestamp;
  const createTimeMs = Number.parseInt(String(createTimeRaw ?? Date.now()), 10);
  const rawContent = message.content ?? input.content ?? '';
  const content = humanizeContent(messageType, rawContent);
  const mentions = normalizeMentions(message.mentions ?? input.mentions);

  if (!type) throw new Error('event type is missing');
  if (!messageId) throw new Error('message_id is missing');
  if (!chatId) throw new Error('chat_id is missing');

  if (type !== 'im.message.receive_v1') throw new Error(`unsupported Lark message event: ${type}`);
  return normalizeEvent(eventMessage('lark', {
    instance,
    id: eventId || `message:${messageId}`,
    kind: 'message.created',
    occurred_at: Number.isFinite(createTimeMs) ? createTimeMs : Date.now(),
    subject: { type: 'conversation', id: chatId },
    actor: senderId ? { id: senderId, type: senderType || 'user' } : null,
    content: { text: content },
    conversation: { id: chatId, type: chatType, tenant_id: tenantKey, thread_id: threadId },
    message: { id: messageId, type: messageType, root_id: rootId, parent_id: parentId,
      thread_root_id: threadRootId, mentions },
    reply_route: { action: 'message.reply', input: { message_id: messageId } },
    data: { supported: AGENT_VISIBLE_MESSAGE_TYPES.has(messageType) },
  }));
}
