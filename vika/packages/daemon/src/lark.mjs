// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { redactSensitive } from './logger.mjs';
import { sanitizeNonArkChildEnv, terminateProcessTree } from './process-utils.mjs';
import { MEETING_EVENT_TYPES } from './meeting/index.mjs';
import { prepareLarkMediaDirectory } from './lark-media.mjs';
import { MAX_CARD_IMAGE_BYTES, cardImageFormat, checkCardImageTemplate, replaceCardImages, validImageKey } from './card-images.mjs';
import { readWorkspaceFile } from './workspace-file.mjs';
import { LarkImageCache } from './lark-image-cache.mjs';
import {
  DOC_COMMENT_EVENT, WHOLE_COMMENT_TYPES, commentContent, commentMessageId, commentPeople, docChatId, isDocChatId,
  normalizeDocComment, parseCommentMessageId, parseCommentNotice,
} from './lark-doc-comments.mjs';

export const SUBSCRIBED_EVENT_TYPES = Object.freeze([
  'im.message.receive_v1',
  'card.action.trigger',
  DOC_COMMENT_EVENT,
  ...MEETING_EVENT_TYPES,
]);

const IMAGE_TYPES = {
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
};
const MAX_CHAT_PAGES = 10;
// Reading one comment: each lark-cli call, the reply pages read when the
// comment's own page lacks the reply, and how long a looked-up name is kept.
const COMMENT_READ_MS = 10_000;
const MAX_REPLY_PAGES = 5;
const NAME_TTL_MS = 24 * 60 * 60 * 1_000;
const MAX_NAMES = 500;
// The IM working indicator has no comment twin; Typing says the same there.
const COMMENT_EMOJI = { OnIt: 'Typing' };

function findField(value, field, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 8) return null;
  if (typeof value[field] === 'string' && value[field]) return value[field];
  for (const child of Object.values(value)) {
    const found = findField(child, field, depth + 1);
    if (found) return found;
  }
  return null;
}

function deliveredMessage(data) {
  const result = {};
  for (const field of ['message_id', 'chat_id', 'thread_id', 'root_id']) {
    const value = findField(data, field);
    if (value) result[field] = value;
  }
  return result;
}

function oneLineLabel(value, max = 200) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}

function cleanMessage(message) {
  const content = typeof message?.content === 'string' ? message.content : JSON.stringify(message?.content ?? '');
  return {
    message_id: String(message?.message_id ?? ''),
    chat_id: String(message?.chat_id ?? ''),
    msg_type: String(message?.msg_type ?? 'unknown').slice(0, 80),
    create_time: String(message?.create_time ?? ''),
    sender: {
      id: String(message?.sender?.id ?? message?.sender?.sender_id ?? '').slice(0, 200),
      name: oneLineLabel(message?.sender?.name),
    },
    content: oneLineLabel(content, 4_000),
  };
}

function messageList(data) {
  const raw = Array.isArray(data?.messages) ? data.messages : Array.isArray(data?.items) ? data.items : [];
  return raw.map(cleanMessage).filter((message) => message.message_id);
}

// Lark rich text supports <at> tags. A resolved name written inline as
// "@Name" becomes that person's mention in place; every other mention is
// prefixed. @all is never sent.
export function renderMentions(text, mentions = []) {
  let rendered = text.trim().replace(/<at\b[^>]*\buser_id=["']all["'][^>]*>.*?<\/at>/gis, '所有人');
  const inline = new Set();
  for (const { id, name } of [...mentions].filter((item) => item.name).sort((a, b) => b.name.length - a.name.length)) {
    if (!rendered.includes(`@${name}`)) continue;
    rendered = rendered.split(`@${name}`).join(`<at user_id="${id}"></at>`);
    inline.add(id);
  }
  const prefix = mentions.filter(({ id }) => !inline.has(id)).map(({ id }) => `<at user_id="${id}"></at>`).join(' ');
  return prefix ? `${prefix} ${rendered}` : rendered;
}

function findChatName(value, depth = 0) {
  if (depth > 8 || value === null || value === undefined) return null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findChatName(item, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (typeof value !== 'object') return null;
  if (typeof value.name === 'string' && value.name.trim()) return value.name.trim();
  for (const child of Object.values(value)) {
    const found = findChatName(child, depth + 1);
    if (found) return found;
  }
  return null;
}

// A comment's document as the drive shortcuts take it: a bare token and type.
function commentTarget({ fileToken, fileType }) {
  return ['--token', fileToken, '--type', fileType];
}

function commentItems(data) {
  return Array.isArray(data?.items) ? data.items : Array.isArray(data?.comments) ? data.comments : [];
}

export class LarkClient {
  constructor(config, registry, logger) {
    this.config = config;
    this.registry = registry;
    this.logger = logger;
    this.subscriber = null;
    this.stopping = false;
    this.profile = config.profile || null;
    this.imageCache = null;
    this.imageUploads = new Map();
    // The bot as check found it: whose comments are its own, and its name
    // where a comment mentions it.
    this.selfId = null;
    this.selfName = null;
    this.names = new Map(); // open_id -> { name, atMs }
  }

  async preflight() {
    if (!this.profile) {
      const listed = await this.registry.run(this.config.larkCliBin, ['profile', 'list'], {
        timeoutMs: 10_000,
      });
      if (listed.code !== 0) throw new Error('lark-cli profile list failed');
      let profiles;
      try { profiles = JSON.parse(listed.stdout); } catch {}
      const active = Array.isArray(profiles) ? profiles.filter((profile) => profile?.active) : [];
      if (active.length !== 1 || typeof active[0].name !== 'string' || !active[0].name) {
        throw new Error('lark-cli must have exactly one active profile, or set VIKA_LARK_PROFILE');
      }
      this.profile = active[0].name;
    }
    const prefix = ['--profile', this.profile];
    const checks = [
      { args: ['event', '+subscribe', '--help'], required: ['--event-types', '--quiet'] },
      { args: ['im', '--help'], required: ['+chat-list', '+messages-reply', '+messages-send'] },
    ];
    const outputs = [];
    for (const check of checks) {
      const result = await this.registry.run(this.config.larkCliBin, [...prefix, ...check.args], {
        timeoutMs: 10_000,
      });
      const text = `${result.stdout}\n${result.stderr}`;
      const missing = check.required.filter((flag) => !text.includes(flag));
      if (result.code !== 0 || missing.length > 0) {
        throw new Error(`lark-cli preflight failed for ${check.args.join(' ')}${missing.length ? `; missing ${missing.join(', ')}` : ''}`);
      }
      outputs.push(text.trim().split(/\r?\n/).at(-1) || 'unknown');
    }
    const doctor = await this.registry.run(this.config.larkCliBin, [...prefix, 'doctor'], {
      timeoutMs: 30_000,
    });
    if (doctor.code !== 0) throw new Error('lark-cli doctor failed for the selected profile');
    const version = await this.registry.run(this.config.larkCliBin, ['--version'], {
      timeoutMs: 10_000,
    });
    if (version.code !== 0) throw new Error('lark-cli --version failed');
    return {
      profile: this.profile,
      version: `${version.stdout}\n${version.stderr}`.trim().split(/\r?\n/).at(-1),
      owner: await this.owner(prefix),
      self: await this.self(prefix),
    };
  }

  // The bot itself as this app sees it: an inbound mention whose id is this
  // open_id calls the bot, whatever name it shows. Renaming the bot keeps the
  // open_id. Without it mentions stay unclassified; the Connector still runs.
  async self(prefix) {
    const info = await this.registry.run(this.config.larkCliBin, [
      ...prefix, 'api', 'GET', '/open-apis/bot/v3/info', '--as', 'bot', '--json',
    ], { timeoutMs: 10_000 });
    let bot = null;
    try { bot = JSON.parse(info.stdout)?.data ?? null; } catch {}
    const openId = bot?.open_id ?? null;
    if (info.code !== 0 || typeof openId !== 'string' || !/^ou_[A-Za-z0-9_-]+$/.test(openId)) {
      this.logger.warn('lark_self_unknown', { reason: info.code !== 0 ? 'bot_info_failed' : 'no_open_id' });
      return null;
    }
    this.selfId = openId;
    this.selfName = typeof bot.app_name === 'string' && bot.app_name.trim() ? oneLineLabel(bot.app_name, 100) : null;
    return { id: openId };
  }

  // The owner is the user this profile is logged in as (the identity
  // search_messages reads with). Without a user login there is no owner, and
  // the Core refuses to notify one; the Connector itself still runs.
  async owner(prefix) {
    const status = await this.registry.run(this.config.larkCliBin, [...prefix, 'auth', 'status', '--json'], {
      timeoutMs: 10_000,
    });
    let openId = null;
    try { openId = JSON.parse(status.stdout)?.identities?.user?.openId ?? null; } catch {}
    if (status.code !== 0 || typeof openId !== 'string' || !/^ou_[A-Za-z0-9_-]+$/.test(openId)) {
      this.logger.warn('lark_owner_unknown', { reason: status.code !== 0 ? 'auth_status_failed' : 'no_user_login' });
      return null;
    }
    return { id: openId, target: { user_id: openId } };
  }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.subscriber) throw new Error('Lark subscriber is already running');
    const args = [
      '--profile', this.profile,
      'event', '+subscribe',
      '--event-types', SUBSCRIBED_EVENT_TYPES.join(','),
      '--as', 'bot',
      '--quiet',
      '--format', 'ndjson',
    ];

    this.stopping = false;
    const child = this.registry.register(spawn(this.config.larkCliBin, args, {
      cwd: this.config.projectDir,
      env: sanitizeNonArkChildEnv(process.env),
      shell: false,
      detached: this.config.connectorWorker ? false : process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
    }));
    this.subscriber = child;
    let buffer = '';
    let stderrTail = '';
    child.stdout?.setEncoding('utf8');
    child.stderr?.setEncoding('utf8');
    child.stdout?.on('data', (chunk) => {
      buffer += chunk;
      while (true) {
        const index = buffer.indexOf('\n');
        if (index < 0) break;
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        if (line.trim()) onLine(line);
      }
      if (Buffer.byteLength(buffer, 'utf8') > this.config.maxEventBytes) {
        this.logger.warn('lark_event_line_oversize', {
          bytes: Buffer.byteLength(buffer, 'utf8'),
        });
        buffer = '';
      }
    });
    child.stderr?.on('data', (chunk) => {
      stderrTail = (stderrTail + chunk).slice(-4000);
    });
    child.once('error', (error) => {
      this.logger.error('lark_subscriber_error', { error });
    });
    child.once('exit', (code, signal) => {
      this.subscriber = null;
      if (buffer.trim()) onLine(buffer);
      if (!this.stopping) {
        // Leader is gone; stop() will not see this handle. Reap the group so
        // grandchildren that ignored the leader's death do not leak.
        terminateProcessTree(child, 'SIGKILL');
        onUnexpectedExit({ code, signal, stderr: stderrTail });
      }
    });
    return child;
  }

  get subscriptionRunning() {
    return Boolean(this.subscriber && this.subscriber.exitCode === null);
  }

  // IM operations behind the Core action routes. The Core has already checked
  // authority, freshness, idempotency and content; these only speak lark-cli.
  im(args, label, options) {
    return this.cli(['im', ...args], label, options);
  }

  async cli(args, label, { cwd = this.config.projectDir, timeoutMs = this.config.sendTimeoutMs } = {}) {
    const result = await this.registry.run(this.config.larkCliBin, ['--profile', this.profile, ...args], {
      cwd, timeoutMs,
    });
    if (result.timedOut) throw new Error(`lark-cli ${label} timed out after ${timeoutMs}ms`);
    let parsed = null;
    try { parsed = JSON.parse(result.stdout); } catch {}
    if (result.code !== 0 || parsed?.ok === false) {
      const detail = parsed?.error?.message || result.stderr.trim() || result.stdout.trim() || `exit ${result.code}`;
      throw new Error(`lark-cli ${label} failed: ${redactSensitive(detail).slice(-2_000)}`);
    }
    if (!parsed || typeof parsed !== 'object') throw new Error(`lark-cli ${label} returned invalid JSON`);
    return parsed.data ?? parsed;
  }

  async deliver(target, content, idempotencyKey) {
    const suffix = ['--idempotency-key', idempotencyKey, '--format', 'json'];
    let data;
    if (content.type === 'markdown') {
      data = await this.im([...target, '--markdown', renderMentions(content.text, content.mentions), ...suffix], 'send message');
    } else if (content.type === 'file') {
      // lark-cli accepts only cwd-relative upload paths.
      const flag = IMAGE_TYPES[path.extname(content.path).toLowerCase()] ? '--image' : '--file';
      data = await this.im([...target, flag, `./${path.basename(content.path)}`, ...suffix], 'send attachment',
        { cwd: path.dirname(content.path) });
    } else if (content.type === 'file_key') {
      data = await this.im([...target, '--file', content.key, ...suffix], 'send attachment');
    } else if (content.type === 'card') {
      const card = content.card_images ? await this.cardWithImages(content) : content.card;
      data = await this.im([...target, '--msg-type', 'interactive', '--content', JSON.stringify(card), ...suffix], 'send card');
    } else {
      throw new Error(`unsupported Lark content type: ${content.type}`);
    }
    return deliveredMessage(data);
  }

  async cardWithImages({ card, card_images: assets }) {
    if (!this.profile) throw new Error('card image upload requires an explicit daemon Lark profile');
    if (!Array.isArray(assets) || !assets.length) throw new Error('card_images requires frozen image resources');
    checkCardImageTemplate(card, assets.map((asset) => asset.alias));
    // Check every frozen resource before the first external side effect.
    const root = path.join(this.config.tempDir, 'outbox');
    for (const asset of assets) {
      const { bytes } = readWorkspaceFile(path.relative(root, asset.path), root, MAX_CARD_IMAGE_BYTES);
      cardImageFormat(bytes);
      if (createHash('sha256').update(bytes).digest('hex') !== asset.digest) throw new Error('card image snapshot changed');
    }
    this.imageCache ??= new LarkImageCache(this.config.dataDir);
    for (const asset of assets) {
      const cached = this.imageCache.get(this.config.instance || 'lark', this.profile, asset.digest);
      if (cached && !validImageKey(cached)) throw new Error('cached Lark image_key is invalid');
    }
    const keys = new Map();
    for (const asset of assets) keys.set(asset.alias, await this.uploadCardImage(asset));
    return replaceCardImages(card, keys);
  }

  async uploadCardImage(asset) {
    const instance = this.config.instance || 'lark';
    const profile = this.profile;
    const cached = this.imageCache.get(instance, profile, asset.digest);
    if (cached) {
      if (!validImageKey(cached)) throw new Error('cached Lark image_key is invalid');
      return cached;
    }
    const pendingKey = JSON.stringify([instance, profile, asset.digest]);
    if (this.imageUploads.has(pendingKey)) return this.imageUploads.get(pendingKey);
    const upload = (async () => {
      const data = await this.im(['images', 'create', '--as', 'bot', '--data', '{"image_type":"message"}',
        '--file', `image=./${path.basename(asset.path)}`, '--format', 'json'], 'upload card image', { cwd: path.dirname(asset.path) });
      const key = data.image_key;
      // A failed durable write aborts the whole card. Restart/retry can reuse
      // each preceding success; a crash before this commit may orphan a key.
      this.imageCache.put(instance, profile, asset.digest, key);
      return key;
    })();
    this.imageUploads.set(pendingKey, upload);
    try { return await upload; } finally { this.imageUploads.delete(pendingKey); }
  }

  // A chat, or one user's direct chat with the bot (the owner, for a notice).
  sendMessage({ chat_id: chatId, user_id: userId, content, idempotency_key: idempotencyKey }) {
    if (isDocChatId(chatId)) throw new Error('a document takes no new message; reply to one of its comments');
    const target = userId ? ['--user-id', userId] : ['--chat-id', chatId];
    return this.deliver(['+messages-send', '--as', 'bot', ...target], content, idempotencyKey);
  }

  async replyMessage({ message_id: messageId, content, reply_in_thread: inThread = false, idempotency_key: idempotencyKey }) {
    const comment = parseCommentMessageId(messageId);
    if (comment) return this.replyToComment(comment, content);
    const delivered = await this.deliver([
      '+messages-reply', '--as', 'bot', '--message-id', messageId, ...(inThread ? ['--reply-in-thread'] : []),
    ], content, idempotencyKey);
    // The reply shortcut may return only a message id. Card callbacks are routed
    // by the delivered thread, so read it back instead of guessing from the root.
    // The card is already sent: a failed lookup only leaves the thread unknown,
    // which the Core reports as a callback registration warning.
    if (content.type === 'card' && inThread && !delivered.thread_id && delivered.message_id) {
      try {
        const details = await this.im([
          '+messages-mget', '--as', 'bot', '--message-ids', delivered.message_id, '--no-reactions', '--format', 'json',
        ], 'resolve delivered thread');
        return { ...deliveredMessage(details), ...delivered };
      } catch {
        return delivered;
      }
    }
    return delivered;
  }

  async reactMessage({ message_id: messageId, emoji_type: emojiType }) {
    const comment = parseCommentMessageId(messageId);
    if (comment) {
      const emoji = COMMENT_EMOJI[emojiType] ?? emojiType;
      await this.reactToComment(comment, emoji, 'add');
      // A comment reaction has no id of its own: the emoji names it.
      return { reaction_id: emoji };
    }
    const data = await this.im([
      'reactions', 'create', '--as', 'bot',
      '--params', JSON.stringify({ message_id: messageId }),
      '--data', JSON.stringify({ reaction_type: { emoji_type: emojiType } }),
      '--format', 'json',
    ], 'react to message');
    return { reaction_id: findField(data, 'reaction_id') };
  }

  async deleteReaction({ message_id: messageId, reaction_id: reactionId }) {
    const comment = parseCommentMessageId(messageId);
    if (comment) {
      await this.reactToComment(comment, reactionId, 'delete');
      return { deleted: true };
    }
    await this.im([
      'reactions', 'delete', '--as', 'bot', '--message-id', messageId, '--reaction-id', reactionId, '--format', 'json',
    ], 'delete reaction');
    return { deleted: true };
  }

  async retractMessage({ message_id: messageId }) {
    // lark-cli marks message deletion high-risk; the agent tool contract
    // already requires an explicit user request, so confirm here.
    const comment = parseCommentMessageId(messageId);
    if (comment) {
      await this.cli(['drive', '+delete-reply', '--as', 'bot', ...commentTarget(comment),
        '--comment-id', comment.commentId, '--reply-id', comment.replyId, '--yes', '--format', 'json'], 'delete comment reply');
      return { retracted: true };
    }
    await this.im([
      'messages', 'delete', '--as', 'bot', '--params', JSON.stringify({ message_id: messageId }), '--format', 'json', '--yes',
    ], 'retract message');
    return { retracted: true };
  }

  async listChats({ query = '', limit = 100 } = {}) {
    const chats = new Map();
    let pageToken = '';
    let hasMore = false;
    for (let page = 0; page < MAX_CHAT_PAGES; page += 1) {
      const data = await this.im([
        '+chat-list', '--as', 'bot', '--types', 'group', '--sort', 'active_time', '--page-size', '100', '--format', 'json',
        ...(pageToken ? ['--page-token', pageToken] : []),
      ], 'list chats');
      for (const chat of Array.isArray(data.chats) ? data.chats : []) {
        const chatId = String(chat?.chat_id ?? '');
        if (!chatId || chats.has(chatId)) continue;
        chats.set(chatId, {
          chat_id: chatId,
          name: oneLineLabel(chat?.name),
          chat_status: oneLineLabel(chat?.chat_status ?? 'unknown'),
        });
      }
      hasMore = Boolean(data.has_more);
      const next = typeof data.page_token === 'string' ? data.page_token : '';
      if (!hasMore || !next || next === pageToken) break;
      pageToken = next;
    }
    const needle = String(query).trim().toLocaleLowerCase();
    const matched = [...chats.values()].filter((chat) => !needle || chat.name.toLocaleLowerCase().includes(needle));
    return { chats: matched.slice(0, limit), has_more: hasMore || matched.length > limit };
  }

  async listChatMessages({ chat_id: chatId, limit = 20, as = 'bot' }) {
    const data = await this.im([
      '+chat-messages-list', '--as', as, '--chat-id', chatId, '--sort', 'desc', '--page-size', String(limit), '--format', 'json',
    ], 'list chat messages');
    return { messages: messageList(data), has_more: Boolean(data.has_more) };
  }

  async searchMessages({ query, chat_id: chatId, start, end, limit = 20 }) {
    const data = await this.im([
      '+messages-search', '--as', 'user', '--query', query, '--page-size', String(limit), '--format', 'json',
      ...(chatId ? ['--chat-id', chatId] : []), ...(start ? ['--start', start] : []), ...(end ? ['--end', end] : []),
    ], 'search messages');
    return { messages: messageList(data).slice(0, limit), has_more: Boolean(data.has_more) };
  }

  async listThreadMessages({ thread_id: threadId, limit = 50, as = 'bot' }) {
    const data = await this.im([
      '+threads-messages-list', '--as', as, '--thread', threadId, '--order', 'asc', '--page-size', String(limit), '--format', 'json',
    ], 'list thread messages');
    return { messages: messageList(data), has_more: Boolean(data.has_more) };
  }

  // Downloads one message resource into the private media directory. The
  // file name is derived from (message, key) so a repeated view reuses it.
  async getMedia({ message_id: messageId, key }) {
    if (!key) throw new Error('Lark media requires the image_key or file_key from the message placeholder');
    const kind = key.startsWith('img_') ? 'image' : 'file';
    const directory = prepareLarkMediaDirectory(this.config.tempDir);
    const output = `lark-${createHash('sha256').update(`${messageId}:${key}`).digest('hex').slice(0, 24)}`;
    const data = await this.im([
      '+messages-resources-download', '--as', 'bot', '--message-id', messageId, '--file-key', key,
      '--type', kind, '--output', output, '--format', 'json',
    ], 'download message resource', { cwd: directory });
    const saved = path.resolve(directory, typeof data?.saved_path === 'string' ? data.saved_path : output);
    const name = path.basename(saved);
    return { media: [{
      path: saved, name, kind,
      mime_type: IMAGE_TYPES[path.extname(name).toLowerCase()] ?? (kind === 'image' ? 'image/jpeg' : 'application/octet-stream'),
      size: fs.statSync(saved).size,
    }] };
  }

  // A document comment as a chat message: read the comment, its thread, the
  // document's title and the people's names as the bot, then normalize. A
  // comment of the bot's own is dropped. A failed read still wakes the bot,
  // told it could not read the comment; it never throws for that.
  async docCommentEvent(raw) {
    const notice = parseCommentNotice(raw);
    if (this.selfId && notice.fromId === this.selfId) return null;
    const [comment, meta] = await Promise.allSettled([this.readComment(notice), this.readDocumentMeta(notice)]);
    const context = comment.status === 'fulfilled' ? comment.value : { error: true };
    if (comment.status === 'rejected') {
      this.logger.warn('lark_doc_comment_unreadable', { error: redactSensitive(String(comment.reason?.message ?? comment.reason)).slice(-500) });
    }
    if (meta.status === 'fulfilled') Object.assign(context, meta.value);
    context.names = await this.lookupNames(commentPeople(notice, context, this.selfId));
    return normalizeDocComment(notice, context, { selfId: this.selfId, selfName: this.selfName });
  }

  // The comment card (quote, whole/solved flags) and its replies up to the
  // one the notice is about.
  async readComment(notice) {
    const card = await this.queryComment(notice);
    let replies = Array.isArray(card.reply_list?.replies) ? card.reply_list.replies : [];
    const found = () => !notice.replyId || replies.some((reply) => String(reply?.reply_id) === notice.replyId);
    if (!found() && (card.has_more || card.reply_list?.has_more)) {
      replies = [];
      let pageToken = '';
      for (let page = 0; page < MAX_REPLY_PAGES && !found(); page += 1) {
        const data = await this.cli(['drive', '+list-replies', '--as', 'bot', ...commentTarget(notice),
          '--comment-id', notice.commentId, '--page-size', '100', ...(pageToken ? ['--page-token', pageToken] : []),
          '--format', 'json'], 'list comment replies', { timeoutMs: COMMENT_READ_MS });
        replies.push(...(Array.isArray(data.items) ? data.items : []));
        const next = typeof data.page_token === 'string' ? data.page_token : '';
        if (!data.has_more || !next || next === pageToken) break;
        pageToken = next;
      }
    }
    return { comment: card, replies };
  }

  async queryComment({ fileToken, fileType, commentId }) {
    const data = await this.cli(['drive', '+batch-query-comments', '--as', 'bot', ...commentTarget({ fileToken, fileType }),
      '--comment-ids', commentId, '--format', 'json'], 'read comment', { timeoutMs: COMMENT_READ_MS });
    const card = commentItems(data).find((item) => String(item?.comment_id) === commentId);
    if (!card) throw new Error('lark-cli read comment found no such comment');
    return card;
  }

  async readDocumentMeta({ fileToken, fileType }) {
    const data = await this.cli(['drive', 'metas', 'batch_query', '--as', 'bot', '--data', JSON.stringify({
      request_docs: [{ doc_token: fileToken, doc_type: fileType }], with_url: true,
    }), '--format', 'json'], 'read document title', { timeoutMs: COMMENT_READ_MS });
    const meta = (Array.isArray(data.metas) ? data.metas : []).find((item) => item?.doc_token === fileToken) ?? {};
    return {
      title: typeof meta.title === 'string' ? meta.title : null,
      url: typeof meta.url === 'string' && /^https:\/\//.test(meta.url) ? meta.url : null,
    };
  }

  // Display names by open_id, kept a day. A failed lookup leaves the name
  // unknown (the line shows a shortened id); it is tried again next time.
  async lookupNames(ids) {
    const now = Date.now();
    const names = new Map();
    await Promise.all(ids.slice(0, 10).map(async (id) => {
      const cached = this.names.get(id);
      if (cached && now - cached.atMs < NAME_TTL_MS) {
        names.set(id, cached.name);
        return;
      }
      try {
        const data = await this.cli(['contact', '+get-user', '--as', 'bot', '--user-id', id, '--format', 'json'],
          'user lookup', { timeoutMs: COMMENT_READ_MS });
        const name = oneLineLabel(data?.user?.name ?? data?.name, 100);
        if (!name) return;
        names.set(id, name);
        this.names.delete(id);
        this.names.set(id, { name, atMs: now });
        while (this.names.size > MAX_NAMES) this.names.delete(this.names.keys().next().value);
      } catch {}
    }));
    return names;
  }

  // A reply to a comment, in its thread. A whole-document comment takes no
  // reply, nor does a resolved one: the answer then opens a new
  // whole-document comment that mentions whom it answers. The comment API
  // has no idempotency key, so the Core never retries an uncertain one.
  async replyToComment(target, content) {
    if (content.type !== 'markdown') throw new Error('a document comment takes text only');
    const card = await this.queryComment(target);
    const delivered = (commentId, replyId) => ({
      ...(commentId && replyId ? { message_id: commentMessageId({ ...target, commentId, replyId }) } : {}),
      chat_id: docChatId(target.fileType, target.fileToken),
      ...(commentId ? { thread_id: commentId } : {}),
    });
    if (!card.is_whole && !card.is_solved) {
      const data = await this.cli(['drive', '+add-reply', '--as', 'bot', ...commentTarget(target),
        '--comment-id', target.commentId, '--content', JSON.stringify(commentContent(content.text)), '--format', 'json'],
      'reply to comment');
      return delivered(target.commentId, findField(data, 'reply_id'));
    }
    if (!WHOLE_COMMENT_TYPES.has(target.fileType)) {
      throw new Error('this comment is resolved, and this kind of document takes no new whole-document comment to answer in');
    }
    const replies = Array.isArray(card.reply_list?.replies) ? card.reply_list.replies : [];
    const author = replies.find((reply) => String(reply?.reply_id) === target.replyId)?.user_id;
    const mention = typeof author === 'string' && /^ou_/.test(author) && author !== this.selfId ? [author] : [];
    const data = await this.cli(['drive', '+add-comment', '--as', 'bot', '--doc', target.fileToken, '--type', target.fileType,
      '--full-comment', '--content', JSON.stringify(commentContent(content.text, mention)), '--format', 'json'],
    'add whole-document comment');
    return delivered(findField(data, 'comment_id'), findField(data, 'reply_id'));
  }

  reactToComment(target, emoji, action) {
    return this.cli(['drive', '+react-reply', '--as', 'bot', ...commentTarget(target), '--reply-id', target.replyId,
      '--emoji', emoji, '--action', action, '--format', 'json'], `${action === 'add' ? 'react to' : 'remove reaction from'} comment`);
  }

  // Reflect a settled choice without depending on the short-lived callback token.
  async updateCard(messageId, card) {
    if (this.config.dryRun) return { sent: false, dryRun: true };
    return this.runJson([
      '--profile', this.profile, 'im', 'messages', 'patch', '--as', 'bot',
      '--message-id', messageId, '--data', JSON.stringify({ content: JSON.stringify(card) }),
      '--format', 'json',
    ], 'update answered card', this.config.sendTimeoutMs);
  }

  // Chat member roster fetch for name<->open_id resolution. Returns the parsed
  // lark-cli JSON payload; roster.mjs owns bucket parsing.
  listChatMembers(chatId) {
    return this.im(['+chat-members-list', '--as', 'bot', '--chat-id', chatId, '--page-all', '--format', 'json'],
      'chat member lookup', { timeoutMs: 15_000 });
  }

  async getChatName(chatId) {
    return findChatName(await this.im(['chats', 'get', '--as', 'bot', '--chat-id', chatId, '--format', 'json'],
      'chat lookup', { timeoutMs: 15_000 }));
  }

  async joinMeeting({ meetingNumber, callId = null, password = null }) {
    const args = [
      '--profile', this.profile,
      'vc', '+meeting-join', '--as', 'bot',
      '--meeting-number', meetingNumber,
    ];
    if (callId) args.push('--call-id', callId);
    if (password) args.push('--password', password);
    args.push('--format', 'json');
    return this.runJson(args, 'meeting join', this.config.sendTimeoutMs ?? 30_000);
  }

  async leaveMeeting(meetingId) {
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(String(meetingId ?? ''))) {
      throw new Error('meeting leave requires a valid meeting id');
    }
    return await this.runJson([
      '--profile', this.profile,
      'vc', '+meeting-leave', '--as', 'bot',
      '--meeting-id', String(meetingId),
      '--format', 'json',
    ], 'meeting leave', this.config.sendTimeoutMs ?? 30_000);
  }

  async listMeetingEvents({ meetingId, pageToken = null, startMs = null }) {
    const args = [
      '--profile', this.profile,
      'vc', '+meeting-events', '--as', 'bot',
      '--meeting-id', meetingId,
    ];
    if (pageToken) args.push('--page-token', pageToken);
    else if (Number.isFinite(startMs) && startMs > 0) {
      // Small overlap makes timestamp-only crash recovery lossless; event_key
      // dedupe removes the overlap after the pull.
      args.push('--start', new Date(Math.max(0, startMs - 2_000)).toISOString());
    }
    args.push('--page-all', '--format', 'json');
    return this.runJson(args, 'meeting event recovery', 30_000);
  }

  async runJson(args, label, timeoutMs) {
    const result = await this.registry.run(this.config.larkCliBin, args, {
      cwd: this.config.projectDir,
      timeoutMs,
    });
    if (result.timedOut) throw new Error(`lark-cli ${label} timed out after ${timeoutMs}ms`);
    let parsed = null;
    try { parsed = JSON.parse(result.stdout); } catch {}
    if (result.code !== 0 || parsed?.ok === false) {
      const detail = parsed?.error?.message || result.stderr.trim() || `exit ${result.code}`;
      const code = parsed?.error?.code ? ` (${parsed.error.code})` : '';
      throw new Error(`lark-cli ${label} failed${code}: ${detail.slice(-2000)}`);
    }
    if (!parsed) throw new Error(`lark-cli ${label} returned invalid JSON`);
    return parsed;
  }

  async stopSubscription() {
    this.stopping = true;
    const child = this.subscriber;
    if (!child?.pid) return;
    terminateProcessTree(child, 'SIGTERM');
    if (child.exitCode === null) {
      await Promise.race([
        new Promise((resolve) => child.once('exit', resolve)),
        new Promise((resolve) => setTimeout(resolve, 3_000)),
      ]);
    }
    terminateProcessTree(child, 'SIGKILL');
  }

  async stop() {
    await this.stopSubscription();
    this.imageCache?.close();
    this.imageCache = null;
  }
}
