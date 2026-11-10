// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { SlackApi, SlackError, SLACK_VERSION, TEXT_LIMIT, FILE_BYTES } from './api.mjs';
import { SlackState } from './state.mjs';
import { SocketModeReceiver } from './socket.mjs';
import { cleanName, historyMessage, messageId, namedUsers, normalizeMessage, parseMessageId, relevant, validChannel } from './events.mjs';
import { toMrkdwn } from './format.mjs';
import { SlackCli } from './cli.mjs';

const NAME_LOOKUP_MS = 2_000;
// The model names reactions as Lark does; Slack wants its own emoji names.
const EMOJI = { OK: 'ok_hand', THUMBSUP: '+1', DONE: 'white_check_mark', THANKS: 'pray', OnIt: 'eyes',
  LGTM: 'white_check_mark', SMILE: 'smile', HEART: 'heart', CLAP: 'clap', MUSCLE: 'muscle', FIRE: 'fire',
  PARTY: 'tada', JIAYI: 'heavy_plus_sign', SOB: 'sob', THINKING: 'thinking_face' };

// Same spelling either way: "Alice Smith", "alice_smith".
const nameKey = (name) => String(name ?? '').toLocaleLowerCase().replace(/[\s_]+/g, ' ').trim();
const userName = (user) => cleanName(user?.profile?.display_name) ?? cleanName(user?.profile?.real_name)
  ?? cleanName(user?.real_name) ?? cleanName(user?.name);
const validCursor = (value) => typeof value === 'string' && /^[!-~]{1,2048}$/.test(value);

function pageInput(limit, max, cursor, as = 'bot') {
  if (as !== 'bot') throw new Error('Slack queries support bot identity only');
  if (!Number.isInteger(limit) || limit < 1 || limit > max) throw new Error(`Slack limit must be between 1 and ${max}`);
  if (cursor !== undefined && !validCursor(cursor)) throw new Error('Invalid Slack cursor');
}

function pagination(page, cursor) {
  const next = page.response_metadata?.next_cursor ?? '';
  if (next !== '' && (!validCursor(next) || next === cursor)) throw new Error('Slack returned an invalid cursor');
  if (page.has_more === true && !next) throw new Error('Slack returned more results without a continuation cursor');
  return { has_more: Boolean(next), next_cursor: next || null };
}

export class SlackClient {
  constructor(config, registry, logger, { fetchImpl = globalThis.fetch, WebSocketImpl = globalThis.WebSocket, receiver = {}, paceMs = 1100, queryApi = null } = {}) {
    this.config = config;
    this.logger = logger;
    this.fetchImpl = fetchImpl;
    this.WebSocketImpl = WebSocketImpl;
    this.receiverOptions = receiver;
    this.paceMs = paceMs;
    this.state = new SlackState(config.dataDir);
    this.running = false;
    this.stopping = false;
    this.sendTail = Promise.resolve();
    this.nextSendAt = new Map();
    this.actions = new AbortController();
    this.failedNames = new Map();
    this.registry = registry;
    this.queryApi = queryApi;
  }

  async preflight() {
    this.account = this.state.account();
    if (!this.account) throw new Error('Slack is not connected; run vika login slack <instance> first');
    this.state.acquire();
    try {
      this.api = new SlackApi(this.account.botToken, { fetchImpl: this.fetchImpl });
      this.appApi = new SlackApi(this.account.appToken, { kind: 'app', fetchImpl: this.fetchImpl });
      this.queryApi ??= new SlackCli(this.account.botToken, { bin: this.config.slackCliBin, registry: this.registry });
      const identity = await this.api.identity(AbortSignal.timeout(15_000));
      if (identity.teamId !== this.account.teamId || identity.appId !== this.account.appId
        || identity.botUserId !== this.account.botUserId) throw new Error('Slack bot identity changed; authorize again');
      this.state.prune(Date.now() - this.config.retentionMs);
      return { version: SLACK_VERSION, owner: { id: this.account.ownerId, target: { chat_id: this.account.ownerChannel } } };
    } catch (error) {
      this.state.release();
      if (error instanceof SlackError && error.auth) throw new Error('Slack authorization failed; run vika login slack <instance>');
      throw error;
    }
  }

  get subscriptionRunning() { return this.running; }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.running) throw new Error('Slack receiver is already started');
    this.running = true;
    this.stopping = false;
    this.receiveAbort = new AbortController();
    this.onLine = onLine;
    const signal = this.receiveAbort.signal;
    const receiver = new SocketModeReceiver({ api: this.appApi, appId: this.account.appId, WebSocketImpl: this.WebSocketImpl,
      logger: this.logger, maxFrameBytes: this.config.maxEventBytes ?? 1_000_000, ...this.receiverOptions });
    // Staged envelopes from before a restart or a login go first.
    this.drain();
    this.monitorPromise = receiver.run((payload) => {
      if (!relevant(payload, this.account)) return;
      this.state.stage(this.account.teamId, payload);
      this.drain();
    }, signal).catch((error) => {
      if (!this.stopping) onUnexpectedExit({ code: 1, signal: null, stderr: error.message });
    }).finally(() => { this.running = false; });
    return this.monitorPromise;
  }

  // Staged envelopes to the Core, one at a time and in order. A message that
  // cannot be read is logged and dropped rather than retried forever.
  drain() {
    if (this.draining) return this.draining;
    this.draining = (async () => {
      for (let rows; (rows = this.state.pending(this.account.teamId)).length;) {
        for (const { seq, payload } of rows) {
          if (this.receiveAbort?.signal.aborted) return;
          try {
            const names = await this.names(payload.event);
            const event = normalizeMessage(payload, { account: this.account, instance: this.config.instance ?? 'slack',
              agentName: this.config.agentName, names });
            if (event) this.emit(event);
          } catch {
            this.logger.warn('slack_message_rejected');
          }
          this.state.delivered(seq);
        }
      }
    })().catch(() => this.logger.warn('slack_drain_failed')).finally(() => { this.draining = null; });
    return this.draining;
  }

  emit({ envelope, id, route, people }) {
    this.state.recordRoute(id, route);
    for (const [user, name] of people) this.state.rememberPerson(route.channel, user, name);
    this.onLine(JSON.stringify(envelope));
  }

  // Display names for the event's author and mentions, from the cache or
  // users.info within a short budget; an id without a name stays an id.
  async names(event) {
    const names = new Map();
    const signal = AbortSignal.timeout(NAME_LOOKUP_MS);
    for (const id of namedUsers(event ?? {})) {
      if (id === this.account.botUserId) continue;
      const cached = this.state.cachedName(id);
      if (cached) { names.set(id, cached); continue; }
      if ((this.failedNames.get(id) ?? 0) > Date.now() || signal.aborted) continue;
      try {
        const { user } = await this.queryApi.call('users.info', { user: id }, signal);
        const name = userName(user);
        if (name) { this.state.saveName(id, name); names.set(id, name); }
      } catch {
        this.failedNames.set(id, Date.now() + 10 * 60_000);
      }
    }
    return names;
  }

  // One Slack page per tool call. The cursor, not the page length, says
  // whether the roster is complete. Profiles supply labels, never authority.
  async listChatMembers(channel, { limit = 100, cursor } = {}) {
    if (!validChannel(channel)) throw new Error('Slack chat_id must be a channel id');
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new Error('Slack member limit must be between 1 and 200');
    if (cursor !== undefined && !validCursor(cursor)) throw new Error('Invalid Slack member cursor');
    const signal = this.actionSignal();
    let page;
    try {
      page = await this.queryApi.call('conversations.members', { channel, limit, cursor }, signal);
    } catch (error) {
      if (error instanceof SlackError && error.code === 'missing_scope') {
        error.message += '. Add the Bot Token Scope for this conversation type: channels:read (public channels), groups:read (private channels), mpim:read (group DMs), or im:read (DMs), then reinstall the Slack app to the workspace to grant it. If the bot token changes, update it with vika login slack <instance>.';
      }
      throw error;
    }
    if (!Array.isArray(page.members) || page.members.length > limit
      || page.members.some((id) => typeof id !== 'string' || !/^[UW][A-Z0-9]{2,30}$/.test(id))) {
      throw new Error('Slack returned an invalid member list');
    }
    const nextCursor = page.response_metadata?.next_cursor ?? '';
    if (nextCursor !== '' && (!validCursor(nextCursor) || nextCursor === cursor)) {
      throw new Error('Slack returned an invalid member cursor');
    }
    const ids = [...new Set(page.members)];
    const members = new Array(ids.length);
    let next = 0;
    // Bound profile fan-out; a slow or inaccessible profile must not hide a
    // member whose ID Slack returned, or masquerade as a known human.
    await Promise.all(Array.from({ length: Math.min(4, ids.length) }, async () => {
      while (next < ids.length) {
        const index = next++;
        members[index] = await this.memberProfile(ids[index], signal);
      }
    }));
    signal.throwIfAborted();
    for (const member of members) {
      if (member.name) {
        this.state.saveName(member.user_id, member.name);
        this.state.rememberPerson(channel, member.user_id, member.name);
      }
    }
    return { chat_id: channel, members, has_more: Boolean(nextCursor), next_cursor: nextCursor || null };
  }

  async memberProfile(id, signal) {
    const member = { user_id: id, name: null, is_self: id === this.account.botUserId,
      is_bot: id === this.account.botUserId ? true : null, deleted: null };
    try {
      signal.throwIfAborted();
      const { user } = await this.queryApi.call('users.info', { user: id },
        AbortSignal.any([signal, AbortSignal.timeout(NAME_LOOKUP_MS)]));
      if (user?.id !== id) throw new Error('Slack returned an invalid member profile');
      member.name = userName(user);
      member.is_bot = typeof user.is_bot === 'boolean' ? user.is_bot : member.is_bot;
      member.deleted = typeof user.deleted === 'boolean' ? user.deleted : null;
    } catch (error) {
      member.profile_error = error instanceof SlackError ? error.code : 'profile_unavailable';
    }
    return member;
  }

  async read(method, params, signal) {
    try { return await this.queryApi.call(method, params, signal); } catch (error) {
      if (error instanceof SlackError && error.code === 'missing_scope') {
        error.message += '. Grant the bot the conversation read/history scope for this type (channels, groups, im or mpim), then reinstall the Slack app. User profiles require users:read.';
      }
      throw error;
    }
  }

  async listChats({ query = '', limit = 100, cursor } = {}) {
    pageInput(limit, 500, cursor);
    if (typeof query !== 'string' || query.length > 200) throw new Error('Invalid Slack chat query');
    const page = await this.read('users.conversations', {
      types: 'public_channel,private_channel,mpim', exclude_archived: true, limit, cursor,
    }, this.actionSignal());
    if (!Array.isArray(page.channels) || page.channels.length > limit
      || page.channels.some(chat => !validChannel(chat?.id))) throw new Error('Slack returned an invalid conversation list');
    const chats = page.channels.filter(chat => !chat.is_im && chat.is_member !== false && !chat.is_archived)
      .map(chat => ({ chat_id: chat.id, name: cleanName(chat.name), chat_status: 'normal',
        tenant_key: this.account.teamId, external: chat.is_ext_shared === true }))
      .filter(chat => (chat.name ?? '').toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    return { chats, ...pagination(page, cursor) };
  }

  async listChatMessages({ chat_id: channel, limit = 20, cursor, as = 'bot' }) {
    pageInput(limit, 50, cursor, as);
    if (!validChannel(channel)) throw new Error('Slack chat_id must be a channel id');
    const signal = this.actionSignal();
    const page = await this.read('conversations.history', { channel, limit, cursor }, signal);
    return this.historyPage(page, { channel, limit, cursor, signal });
  }

  async listThreadMessages({ thread_id: id, limit = 50, cursor, as = 'bot' }) {
    pageInput(limit, 50, cursor, as);
    const parsed = parseMessageId(id, this.account.teamId);
    if (!parsed) throw new Error('Slack thread_id must be a full sl:<team>:<channel>:<ts> id in this workspace');
    const route = this.state.route(id);
    const root = route?.threadTs ?? parsed.ts;
    const signal = this.actionSignal();
    const page = await this.read('conversations.replies', { channel: parsed.channel, ts: root, limit, cursor }, signal);
    return this.historyPage(page, { channel: parsed.channel, root, limit, cursor, signal });
  }

  async historyPage(page, { channel, root, limit, cursor, signal }) {
    if (!Array.isArray(page.messages) || page.messages.length > limit) throw new Error('Slack returned an invalid message list');
    const more = pagination(page, cursor);
    // Validate the entire page before caching any route. A thread may only
    // contain its root and replies to that root.
    for (const event of page.messages) {
      historyMessage(event, { account: this.account, channel });
      if (root && (event.thread_ts ?? event.ts) !== root) throw new Error('Slack returned a message outside the requested thread');
    }
    const names = new Map();
    const ids = [...new Set(page.messages.flatMap(event => [...namedUsers(event)]))];
    const missing = [];
    for (const id of ids) {
      const cached = this.state.cachedName(id);
      if (cached) names.set(id, cached);
      else if (id !== this.account.botUserId) missing.push(id);
    }
    // Labels are optional. Keep the whole page usable when profiles are slow.
    const labels = AbortSignal.any([signal, AbortSignal.timeout(NAME_LOOKUP_MS)]);
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(4, missing.length) }, async () => {
      while (next < missing.length && !labels.aborted) {
        const member = await this.memberProfile(missing[next++], labels);
        if (member.name) names.set(member.user_id, member.name);
      }
    }));
    signal.throwIfAborted();
    const messages = [];
    for (const event of page.messages) {
      const item = historyMessage(event, { account: this.account, channel, names, agentName: this.config.agentName });
      if (!item) continue;
      this.state.recordRoute(item.message.message_id, item.route, { refresh: true });
      if (item.route.threadTs && !this.state.route(item.message.thread_id)) {
        this.state.recordRoute(item.message.thread_id, { channel, ts: item.route.threadTs });
      }
      messages.push(item.message);
    }
    for (const [id, name] of names) {
      this.state.saveName(id, name);
      this.state.rememberPerson(channel, id, name);
    }
    return { chat_id: channel, messages, ...more };
  }

  target(id) {
    const route = this.state.route(id);
    if (!route) throw new Error('Slack message is not known to this connector');
    return route;
  }

  sendMessage({ chat_id: channel, content }) {
    if (!validChannel(channel)) return Promise.reject(new Error('Slack chat_id must be a channel id'));
    return this.post(channel, null, content);
  }

  // A reply lands in the target's thread when it is in one; reply_in_thread
  // starts one on a top-level message. Slack has no quoted reply.
  replyMessage({ message_id: id, content, reply_in_thread: inThread = false }) {
    const route = this.target(id);
    return this.post(route.channel, route.threadTs ?? (inThread ? route.ts : null), content);
  }

  post(channel, threadTs, content) {
    const signal = AbortSignal.any([this.actions.signal, AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
    const action = this.sendTail.then(async () => {
      signal.throwIfAborted();
      // About one message per second per channel.
      const due = this.nextSendAt.get(channel) ?? 0;
      if (due > Date.now()) await delay(due - Date.now(), undefined, { signal });
      try {
        if (content.type === 'markdown') return await this.postText(channel, threadTs, content, signal);
        if (content.type === 'file') return await this.postFile(channel, threadTs, content, signal);
        throw new Error(`Slack does not support ${content.type} messages`);
      } finally { this.nextSendAt.set(channel, Date.now() + this.paceMs); }
    });
    this.sendTail = action.catch(() => {});
    return action;
  }

  async postText(channel, threadTs, content, signal) {
    if (typeof content.text !== 'string' || !content.text.trim() || content.text.length > TEXT_LIMIT) {
      throw new Error(`Slack text must contain 1–${TEXT_LIMIT} characters; send longer content as a file`);
    }
    const mentions = this.mentionsFor(channel, content.mentions ?? []);
    const sent = await this.api.send('chat.postMessage', { channel, text: toMrkdwn(content.text, mentions),
      unfurl_links: false, unfurl_media: false, ...(threadTs ? { thread_ts: threadTs } : {}) }, signal);
    if (sent.channel !== channel || !/^\d{1,12}\.\d{1,8}$/.test(sent.ts ?? '')) {
      throw new Error('Slack send returned an unexpected message; delivery is uncertain');
    }
    return this.delivered(channel, sent.ts, threadTs);
  }

  delivered(channel, ts, threadTs) {
    const id = messageId(this.account.teamId, channel, ts);
    this.state.recordRoute(id, { channel, ts, threadTs });
    return { message_id: id, chat_id: channel, ...(threadTs ? { thread_id: threadTs } : {}) };
  }

  // Mentions by id pass through; a name must be someone this connector has
  // shown the model in that channel, exactly one of them.
  mentionsFor(channel, mentions) {
    const known = mentions.some((mention) => !mention.id) ? this.state.people(channel) : [];
    return mentions.map((mention) => {
      if (mention.id) {
        if (!/^[UW][A-Z0-9]{2,30}$/.test(mention.id)) throw new Error('Slack mentions take user ids such as U0123ABCD');
        return { id: mention.id, name: mention.name ?? known.find((person) => person.id === mention.id)?.name ?? null };
      }
      const matches = known.filter((person) => nameKey(person.name) === nameKey(mention.name));
      if (matches.length !== 1) {
        throw new Error(`mention "${mention.name}" is ${matches.length ? 'ambiguous' : 'not someone seen in this chat'}; use mention_user_ids`);
      }
      return matches[0];
    });
  }

  async postFile(channel, threadTs, content, signal) {
    const fd = fs.openSync(content.path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    let bytes;
    try {
      const stat = fs.fstatSync(fd);
      if (!stat.isFile() || stat.size < 1 || stat.size > FILE_BYTES) throw new Error('Slack upload must be a nonempty regular file of at most 20 MiB');
      bytes = Buffer.alloc(stat.size);
      if (fs.readSync(fd, bytes, 0, bytes.length, 0) !== bytes.length) throw new Error('Slack upload file changed');
    } finally { fs.closeSync(fd); }
    const name = path.basename((content.name || content.path).replaceAll('\\', '/'));
    const file = await this.api.upload({ bytes, name, channel, threadTs }, signal);
    // Sharing is asynchronous: the message appears a moment later. Without it
    // the delivery stands, only without a message id to retract.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await delay(700, undefined, { signal });
      const info = await this.api.call('files.info', { file }, signal).catch(() => null);
      const shares = info?.file?.shares ?? {};
      const ts = [...(shares.public?.[channel] ?? []), ...(shares.private?.[channel] ?? [])].at(-1)?.ts;
      if (/^\d{1,12}\.\d{1,8}$/.test(ts ?? '')) return { ...this.delivered(channel, ts, threadTs), file_id: file };
    }
    return { message_id: null, chat_id: channel, file_id: file };
  }

  async reactMessage({ message_id: id, emoji_type: type }) {
    const { channel, ts } = this.target(id);
    const name = EMOJI[type] ?? (/^[a-z0-9_]{1,64}$/.test(type) ? type : null);
    if (!name) throw new Error(`Slack has no reaction for ${type}; use a Slack emoji name such as eyes or white_check_mark`);
    try {
      await this.api.send('reactions.add', { channel, timestamp: ts, name }, this.actionSignal());
    } catch (error) {
      if (!(error instanceof SlackError && error.code === 'already_reacted')) throw error;
    }
    return { message_id: id, reaction_id: name };
  }

  async deleteReaction({ message_id: id, reaction_id: name }) {
    const { channel, ts } = this.target(id);
    try {
      await this.api.send('reactions.remove', { channel, timestamp: ts, name }, this.actionSignal());
    } catch (error) {
      if (!(error instanceof SlackError && error.code === 'no_reaction')) throw error;
    }
    return { deleted: true };
  }

  async retractMessage({ message_id: id }) {
    const target = parseMessageId(id, this.account.teamId);
    if (!target) throw new Error('Not a Slack message id of this workspace');
    await this.api.send('chat.delete', { channel: target.channel, ts: target.ts }, this.actionSignal());
    return { message_id: id, retracted: true };
  }

  // Files the bot received in a message, or the one `key` names.
  async getMedia({ message_id: id, key = null }) {
    const route = this.target(id);
    const wanted = route.files.filter((file) => !key || file.id === key);
    if (!wanted.length) throw new Error('No Slack file exists for that message');
    const signal = this.actionSignal();
    const media = [];
    for (const file of wanted) {
      if (file.size !== null && file.size > FILE_BYTES) throw new Error('Slack downloads support files up to 20 MiB');
      const { file: info } = await this.api.call('files.info', { file: file.id }, signal);
      const bytes = await this.api.download(info?.url_private_download ?? info?.url_private, signal);
      const filename = path.join(this.state.mediaDirectory, createHash('sha256').update(bytes).digest('hex'));
      const temp = `${filename}.${randomUUID()}`;
      try { fs.writeFileSync(temp, bytes, { flag: 'wx', mode: 0o600 }); fs.renameSync(temp, filename); }
      finally { fs.rmSync(temp, { force: true }); }
      media.push({ path: filename, name: path.basename(file.name.replaceAll('\\', '/')), kind: file.kind,
        mime_type: file.mimeType, size: bytes.length });
    }
    return { media };
  }

  actionSignal() {
    return AbortSignal.any([this.actions.signal, AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
  }

  async stopSubscription() {
    this.stopping = true;
    this.receiveAbort?.abort();
    await this.monitorPromise;
    await this.draining;
    this.running = false;
  }

  async stop() {
    await this.stopSubscription();
    this.actions.abort();
    await this.sendTail;
    this.state.close();
  }
}
