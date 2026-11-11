// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { setTimeout as delay } from 'node:timers/promises';
import { OneBotError, OneBotSocket, ONEBOT_VERSION } from './api.mjs';
import { OneBotState } from './state.mjs';
import { PolicyFile, admitted, classify, groupMode } from './policy.mjs';
import { CONTEXT_AGE_MS, CONTEXT_LINES, ReleaseGate } from './gate.mjs';
import {
  chatOf, cleanName, groupChat, historyMessage, messageId, namedUsers, normalizeMessage, parseChat, parseMessageId, privateChat,
} from './events.mjs';
import { outboundSegments, plainText } from './format.mjs';
import { fetchMedia, fileSegment, readUpload } from './media.mjs';
import { sanitizeMemberName } from '../roster.mjs';

const CONNECT_MS = 10_000;
const NAME_LOOKUP_MS = 2_000;
const NAME_LOOKUPS = 5;
const NAME_LOOKUPS_AT_ONCE = 5;
const NAME_RETRY_MS = 10 * 60_000;
const FAILED_NAMES = 1000;
// chat.list asks the implementation for the account's groups at most once a
// minute, and keeps no more of them than any QQ account can be in.
const GROUP_LIST_MS = 60_000;
const GROUP_LIST = 5000;
// While the subscription runs, the policy file is looked at this often even
// when nothing else reads it, so a change reaches the waiting lines without traffic.
const POLICY_POLL_MS = 2000;
// The model names reactions as Lark does; QQ wants a face id. Every id below
// is in the QQ face table NapCat and LLOneBot ship (face_config.json): a
// system face by QSid, or an emoji by its code point (QCid). DONE and
// THINKING have no face there and are not offered.
const EMOJI = Object.freeze({ OK: '124', THUMBSUP: '76', LGTM: '76', THANKS: '128591', OnIt: '128064', SMILE: '14',
  HEART: '66', CLAP: '99', MUSCLE: '128170', FIRE: '128293', PARTY: '127881', SOB: '128557' });

// Same spelling either way: "Alice Smith", "alice_smith".
const nameKey = (name) => String(name ?? '').toLocaleLowerCase().replace(/[\s_]+/g, ' ').trim();

function within(ms, promise, message) {
  let timer;
  const expired = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); });
  return Promise.race([promise, expired]).finally(() => clearTimeout(timer));
}

// Settles as `promise` does, or rejects with `error()` once `signal` aborts.
function unless(signal, promise, error) {
  if (signal.aborted) return Promise.reject(error());
  let aborted;
  const cancelled = new Promise((_, reject) => { aborted = () => reject(error()); });
  signal.addEventListener('abort', aborted, { once: true });
  return Promise.race([promise, cancelled]).finally(() => signal.removeEventListener('abort', aborted));
}

// A QQ account over OneBot v11. The socket opens at preflight and stays open
// until stop: actions need it before the subscription starts and after it is
// paused. Lines are staged as they arrive, whether or not the subscription
// runs, and the release gate decides when a chat's staged lines reach the Core.
export class OneBotClient {
  constructor(config, _registry, logger, { WebSocketImpl = globalThis.WebSocket, fetchImpl = globalThis.fetch, paceMs = 1200,
    now = () => performance.now(), setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    this.config = config;
    this.logger = logger;
    this.WebSocketImpl = WebSocketImpl;
    this.fetchImpl = fetchImpl;
    this.paceMs = paceMs;
    // The gate's clock, monotonic. A line's staging time is wall-clock time
    // instead: it is stored, and its age is still known after a restart.
    this.now = now;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.state = new OneBotState(config.dataDir);
    this.policyFile = new PolicyFile(config.dataDir, logger);
    this.running = false;
    this.stopping = false;
    this.gate = null;
    this.sendTail = Promise.resolve();
    this.chatTails = new Map();
    this.writing = Promise.resolve();
    this.inbound = Promise.resolve();
    this.nextSendAt = new Map();
    this.actions = new AbortController();
    this.failedNames = new Map();
    this.lookingUp = 0;
    this.seenPolicy = null;
    this.groupList = null;
    this.poll = null;
  }

  // The policy as it is now. A policy that disconnects a chat (a group set to
  // "off", "*": "off" for the groups not named, "private" back to "owner")
  // deletes the lines waiting there the first time it is read, so they are
  // not delivered later, when the chat may be connected again. One that sets
  // a still connected group to "mention" turns its waiting ambient lines into
  // context, so they no longer wake the agent.
  policy() {
    const policy = this.policyFile.current();
    if (policy !== this.seenPolicy) {
      this.seenPolicy = policy;
      for (const { chat, classes } of this.state.pendingChats()) {
        if (!admitted(policy, this.account, chat)) this.state.discard(chat);
        else if (classes.has('ambient') && groupMode(policy, chat) === 'mention') {
          this.state.demote(chat);
          this.gate?.demoted(chat);
        }
      }
    }
    return policy;
  }

  // Reads the policy every POLICY_POLL_MS while the subscription runs.
  pollPolicy() {
    this.poll = this.setTimer(() => {
      try { this.policy(); } catch { this.logger.warn('onebot_policy_poll_failed'); }
      if (this.poll !== null) this.pollPolicy();
    }, POLICY_POLL_MS);
    this.poll?.unref?.();
  }

  async preflight() {
    this.account = this.state.account();
    if (!this.account) throw new Error('OneBot is not connected; run ov-superagent login onebot <instance> first');
    // An invalid policy file is an error here, naming the file; later edits
    // that do not parse keep the last good policy.
    this.policy();
    this.connection = new AbortController();
    this.socket = new OneBotSocket({ url: this.account.url, token: this.account.token, WebSocketImpl: this.WebSocketImpl,
      logger: this.logger, callTimeoutMs: this.config.sendTimeoutMs ?? 30_000 });
    let ready;
    const opened = new Promise((resolve) => { ready = resolve; });
    // Every connection, a reconnect too, is checked before anything goes through it.
    const run = this.socket.run((raw) => this.receive(raw), this.connection.signal, { onReady: (reopen) => {
      this.identity = this.verify(reopen);
      ready();
    } });
    // Plain disconnects reconnect inside the socket; what ends it (a rejected
    // token) fails a running worker.
    this.socketDone = run.catch((error) => this.fail(error));
    try {
      await within(CONNECT_MS, Promise.race([opened, run]),
        'Could not connect to the OneBot WebSocket server; check its address, that it is running and the access token');
      const unverified = await this.identity;
      if (unverified) throw unverified;
      this.state.prune(Date.now() - this.config.retentionMs);
      return { version: ONEBOT_VERSION, self: { id: this.account.selfId },
        owner: { id: this.account.ownerId, target: { chat_id: privateChat(this.account.ownerId) } } };
    } catch (error) {
      this.connection.abort();
      await this.socketDone;
      throw error;
    }
  }

  // Ends the socket for good: a running worker fails, a stopped one fails its next start.
  fail(error) {
    this.socketFailure ??= error;
    if (this.running && !this.stopping) {
      this.running = false;
      this.onUnexpectedExit({ code: 1, signal: null, stderr: error.message });
    }
    return error;
  }

  // Asks a new connection who is logged in: the server at the stored address
  // may come back as another QQ account, whose sends and history must not
  // pass for this one's. Asked once per connection: resolves null once it is
  // this account, else why not. A connection that does not answer within
  // CONNECT_MS is closed, and the socket opens a new one, asked again.
  verify(reopen) {
    const timeout = new AbortController();
    const timer = this.setTimer(() => timeout.abort(), CONNECT_MS);
    return this.socket.call('get_login_info', {}, timeout.signal).then((info) => {
      if (String(info?.user_id ?? '') === this.account.selfId) return null;
      this.connection.abort();
      return this.fail(new Error('OneBot account changed; authorize again'));
    }, (error) => {
      reopen();
      return error;
    }).finally(() => this.clearTimer(timer));
  }

  // Waits for the check of the current connection, or of the next one if it
  // is reopened meanwhile, until `signal` gives up or the client stops; an
  // action given up on here was not sent.
  async verified(action, signal) {
    const cancel = signal ? AbortSignal.any([signal, this.actions.signal]) : this.actions.signal;
    for (;;) {
      const seen = this.identity;
      const unverified = await unless(cancel, seen, () => new OneBotError(`OneBot ${action} was cancelled before it was sent`));
      if (this.socketFailure) throw this.socketFailure;
      if (seen !== this.identity) continue;
      if (!unverified) return;
      throw new OneBotError(`OneBot could not confirm the logged-in account; ${action} was not sent`);
    }
  }

  // Every action goes through here, so none reaches an unchecked connection.
  async call(action, params, signal) {
    await this.verified(action, signal);
    return this.socket.call(action, params, signal);
  }

  get subscriptionRunning() { return this.running; }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.running) throw new Error('OneBot receiver is already started');
    if (this.socketFailure) throw this.socketFailure;
    this.running = true;
    this.stopping = false;
    this.onLine = onLine;
    this.onUnexpectedExit = onUnexpectedExit;
    this.gate = new ReleaseGate({ settleMs: () => this.policy().settleMs, release: (chat) => this.release(chat),
      now: this.now, setTimer: this.setTimer, clearTimer: this.clearTimer });
    // Lines staged before a restart or while paused have waited long enough,
    // unless the policy has since disconnected their chat.
    this.policy();
    for (const { chat, classes } of this.state.pendingChats()) this.gate.resume(chat, classes);
    this.pollPolicy();
  }

  // Frames arrive in order, and their messages are staged in that order,
  // none before the account of the connection it came on is checked, and
  // none from a connection whose check failed. A message's names are looked
  // up as it arrives, beside those of the messages before it, so a slow
  // lookup holds the next messages back by its own budget at most.
  receive(raw) {
    if (raw.post_type !== 'message' && raw.post_type !== 'message_sent') return;
    const named = this.identity.then((unverified) => (unverified || this.socketFailure ? null : this.names(raw)));
    named.catch(() => {});
    this.inbound = this.inbound.then(async () => {
      const names = await named;
      if (names) await this.stage(raw, names);
    }).catch(() => this.logger.warn('onebot_message_rejected'));
  }

  async stage(raw, names) {
    if (typeof raw.message === 'string' && !this.stringFormat) {
      this.stringFormat = true;
      this.logger.warn('onebot_string_format', { hint: 'set the OneBot message format (messagePostFormat) to array' });
    }
    const account = this.account;
    const chat = chatOf(raw, account.selfId);
    // Chats that are not connected are dropped before anything is looked up or stored.
    if (!chat || !admitted(this.policy(), account, chat)) return;
    const line = normalizeMessage(raw, { account, instance: this.config.instance ?? 'onebot', names });
    if (!line) return;
    // The echo of a send can arrive before the send's own answer: let the
    // send on the socket settle, and its id be recorded, before deciding
    // whether it is the agent's. Sends still queued cannot have an echo yet.
    if (line.self) await this.writing;
    const quoted = line.replyTo ? parseMessageId(line.replyTo) : null;
    const cls = classify({
      kind: parseChat(chat).kind, chat, self: line.self, own: line.self && this.state.isSent(chat, line.mid),
      fromOwner: line.userId === account.ownerId, text: line.bareText, spoken: line.spokenText, atSelf: line.atSelf,
      replyToOwn: Boolean(quoted && this.state.isSent(quoted.chat, quoted.mid)),
    }, this.policy());
    if (cls === 'drop') return;
    this.state.recordRoute(line.id, { chat, mid: line.mid, files: line.files });
    for (const [user, name] of line.people) this.state.rememberPerson(chat, user, name);
    // A friend without a display name has written all the same, and target()
    // knows that from this table; the empty name is shown nowhere. In a group
    // a known entry would stop the person's card from being looked up.
    if (parseChat(chat).kind === 'private' && !line.self && !line.people.some(([user]) => user === line.userId)) {
      this.state.rememberPerson(chat, line.userId, '');
    }
    const at = Date.now();
    this.state.stage({ chat, cls, at, envelope: line.envelope });
    this.state.pruneContext(chat, { maxLines: CONTEXT_LINES, before: at - CONTEXT_AGE_MS });
    if (this.running) this.gate.staged(chat, cls);
  }

  // Every line staged in the chat, in arrival order. The gate calls this and
  // must not see it throw. A chat the policy no longer connects loses its lines.
  release(chat) {
    try {
      this.state.pruneContext(chat, { maxLines: CONTEXT_LINES, before: Date.now() - CONTEXT_AGE_MS });
      const connected = admitted(this.policy(), this.account, chat);
      for (const { seq, envelope } of this.state.pending(chat)) {
        if (connected) this.onLine(JSON.stringify(envelope));
        this.state.delivered(seq);
      }
    } catch {
      this.logger.warn('onebot_release_failed');
    }
  }

  // Display names for the line's @mentions: someone already shown in this
  // chat, else their group card, for a few people per line within a short
  // budget, since every line waits for this, and while no more than a few
  // lookups are under way, so a burst of new names cannot pile them up. An
  // id without a name stays an id.
  async names(raw) {
    const names = new Map();
    const chat = chatOf(raw, this.account.selfId);
    if (!chat || !admitted(this.policy(), this.account, chat)) return names;
    const ids = [...namedUsers(raw)].filter((id) => id !== this.account.selfId);
    if (!ids.length) return names;
    const { kind, id: group } = parseChat(chat);
    const known = new Map(this.state.people(chat).map((person) => [person.id, person.name]));
    const budget = new AbortController();
    const timer = this.setTimer(() => budget.abort(), NAME_LOOKUP_MS);
    let lookups = 0;
    for (const id of ids) {
      if (known.has(id)) { names.set(id, known.get(id)); continue; }
      const key = `${chat}:${id}`;
      if (kind !== 'group' || (this.failedNames.get(key) ?? 0) > Date.now() || budget.signal.aborted || lookups >= NAME_LOOKUPS
        || this.lookingUp >= NAME_LOOKUPS_AT_ONCE) continue;
      lookups += 1;
      this.lookingUp += 1;
      try {
        const member = await this.call('get_group_member_info', { group_id: Number(group), user_id: Number(id) }, budget.signal);
        const name = cleanName(member?.card) ?? cleanName(member?.nickname);
        if (!name) throw new Error('no name');
        names.set(id, name);
      } catch {
        // Entries go in time order: the expired ones, and the oldest when
        // the map is full, are at the front.
        const now = Date.now();
        this.failedNames.delete(key);
        for (const [entry, until] of this.failedNames) {
          if (until > now && this.failedNames.size < FAILED_NAMES) break;
          this.failedNames.delete(entry);
        }
        this.failedNames.set(key, now + NAME_RETRY_MS);
      } finally { this.lookingUp -= 1; }
    }
    this.clearTimer(timer);
    return names;
  }

  actionSignal() {
    return AbortSignal.any([this.actions.signal, AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
  }

  // Outbound reaches connected chats only: a group that is not "off", the
  // owner, and under "private": "friends" a friend who has written to this account.
  target(chat) {
    const parsed = parseChat(chat);
    if (!parsed) throw new Error('OneBot chat_id must be group:<id> or private:<id>');
    const open = admitted(this.policy(), this.account, chat) && (parsed.kind === 'group'
      || parsed.id === this.account.ownerId || this.state.people(chat).some((person) => person.id === parsed.id));
    if (!open) throw new Error(`OneBot is not connected to ${chat}`);
    return parsed;
  }

  message(id) {
    const parsed = parseMessageId(id);
    if (!parsed) throw new Error('Not a OneBot message id');
    this.target(parsed.chat);
    return parsed;
  }

  async sendMessage({ chat_id: chat, content }) {
    return this.post(chat, null, content);
  }

  // A reply quotes its target. QQ has no threads.
  async replyMessage({ message_id: id, content }) {
    const { chat, mid } = this.message(id);
    return this.post(chat, mid, content);
  }

  // Sends go out one at a time, and each chat's in its order. A chat's pace
  // is waited out before its send joins the queue, so it holds back only
  // that chat's next send.
  post(chat, quoted, content) {
    const signal = this.actionSignal();
    const action = (this.chatTails.get(chat) ?? Promise.resolve()).then(async () => {
      signal.throwIfAborted();
      const due = this.nextSendAt.get(chat) ?? 0;
      if (due > Date.now()) await delay(due - Date.now(), undefined, { signal });
      const turn = this.sendTail.then(async () => {
        signal.throwIfAborted();
        const { kind, id } = this.target(chat);
        const message = this.segments(chat, kind, quoted, content);
        // What a self line waits for (stage): this send, until its id is recorded.
        let settled;
        this.writing = new Promise((resolve) => { settled = resolve; });
        try {
          const startedAt = this.now();
          let sent;
          try {
            sent = await this.call(kind === 'group' ? 'send_group_msg' : 'send_private_msg',
              { [kind === 'group' ? 'group_id' : 'user_id']: Number(id), message }, signal);
          } finally { this.nextSendAt.set(chat, Date.now() + this.paceMs); }
          const raw = sent?.message_id;
          const result = typeof raw === 'number' || typeof raw === 'string' ? messageId(chat, String(raw)) : null;
          const mid = parseMessageId(result)?.mid;
          if (!mid) throw new Error('OneBot send returned no message id; delivery is uncertain');
          this.state.recordSent(chat, mid);
          // A send has no attachments to download, whatever an older
          // message with the same id had.
          this.state.recordRoute(result, { chat, mid, files: [] });
          this.gate?.sent(chat, startedAt);
          return { message_id: result, chat_id: chat };
        } finally { settled(); }
      });
      this.sendTail = turn.catch(() => {});
      return turn;
    });
    const tail = action.catch(() => {});
    this.chatTails.set(chat, tail);
    tail.then(() => { if (this.chatTails.get(chat) === tail) this.chatTails.delete(chat); });
    return action;
  }

  segments(chat, kind, quoted, content) {
    const reply = quoted ? [{ type: 'reply', data: { id: quoted } }] : [];
    if (content.type === 'markdown') {
      // NapCat drops an @ in a private chat, so there the name stays text.
      const mentions = kind === 'group' ? this.mentionsFor(chat, content.mentions ?? []) : [];
      return [...reply, ...outboundSegments(plainText(content.text ?? ''), mentions)];
    }
    if (content.type === 'file') {
      // QQ shows a file as a message of its own, without a quote.
      const segment = fileSegment(readUpload(content));
      return segment.type === 'image' ? [...reply, segment] : [segment];
    }
    throw new Error(`OneBot does not support ${content.type} messages`);
  }

  // A name must be someone this connector has shown the model in that chat,
  // exactly one of them, as stored or as the model saw it in `from=`
  // (sanitizeMemberName). Core passes names only: QQ numbers are not user ids
  // its schema accepts.
  mentionsFor(chat, mentions) {
    const known = mentions.length ? this.state.people(chat) : [];
    return mentions.map((mention) => {
      if (mention.id) return { id: mention.id, name: mention.name ?? known.find((person) => person.id === mention.id)?.name ?? null };
      const key = nameKey(mention.name);
      const matches = known.filter((person) => nameKey(person.name) === key || nameKey(sanitizeMemberName(person.name)) === key);
      if (matches.length !== 1) throw new Error(`mention "${mention.name}" is ${matches.length ? 'ambiguous in this chat' : 'not someone seen in this chat'}`);
      return matches[0];
    });
  }

  async reactMessage({ message_id: id, emoji_type: type }) {
    const { chat, mid } = this.message(id);
    if (parseChat(chat).kind !== 'group') throw new Error('QQ reactions work on group messages only');
    const emoji = Object.hasOwn(EMOJI, type) ? EMOJI[type] : null;
    if (!emoji) throw new Error(`OneBot has no reaction for ${type}; use one of ${Object.keys(EMOJI).join(', ')}`);
    await this.reaction(mid, emoji, true);
    return { message_id: id, reaction_id: emoji };
  }

  // Removes a temporary reaction (OnIt) once the answer is out.
  async deleteReaction({ message_id: id, reaction_id: emoji }) {
    const { mid } = this.message(id);
    if (!Object.values(EMOJI).includes(emoji)) throw new Error('Not a OneBot reaction');
    await this.reaction(mid, emoji, false);
    return { deleted: true };
  }

  // set_msg_emoji_like is NapCat's and LLOneBot's; Lagrange has none.
  async reaction(mid, emoji, set) {
    try {
      await this.call('set_msg_emoji_like', { message_id: Number(mid), emoji_id: emoji, set }, this.actionSignal());
    } catch (error) {
      if (error instanceof OneBotError && error.unsupported) throw new Error('Reactions are not available on this OneBot implementation');
      throw error;
    }
  }

  // QQ lets the sender recall a message for about two minutes.
  async retractMessage({ message_id: id }) {
    const { chat, mid } = this.message(id);
    if (!this.state.isSent(chat, mid)) throw new Error('OneBot retracts only messages this agent sent');
    await this.call('delete_msg', { message_id: Number(mid) }, this.actionSignal());
    return { message_id: id, retracted: true };
  }

  // Every attachment of a received message, a forwarded chat as a transcript.
  async getMedia({ message_id: id }) {
    const { chat } = this.message(id);
    const files = this.state.route(id)?.files ?? [];
    if (!files.length) throw new Error('No OneBot attachment exists for that message');
    const signal = this.actionSignal();
    const media = [];
    for (const file of files) {
      media.push(await fetchMedia({ socket: this, fetchImpl: this.fetchImpl, file, chat, account: this.account,
        directory: this.state.mediaDirectory, signal }));
    }
    return { media };
  }

  // The connected groups among those the account is in: every one that is not
  // "off", by the policy at the time of the listing.
  async listChats({ query = '', limit = 100, cursor } = {}) {
    if (cursor !== undefined) throw new Error('OneBot chat lists have no cursor');
    if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new Error('OneBot limit must be between 1 and 500');
    if (typeof query !== 'string' || query.length > 200) throw new Error('Invalid OneBot chat query');
    const groups = await this.groups();
    const policy = this.policy();
    const needle = query.toLocaleLowerCase();
    const chats = groups.filter(({ chat, name }) => admitted(policy, this.account, chat) && (name ?? '').toLocaleLowerCase().includes(needle))
      .map(({ chat, name }) => ({ chat_id: chat, name, chat_status: 'normal' }));
    return { chats: chats.slice(0, limit), has_more: chats.length > limit, next_cursor: null };
  }

  // The account's groups from get_group_list, kept for a minute: the model
  // may list chats several times in one turn, and a group the account has
  // joined meanwhile is listed a minute later at most.
  async groups() {
    if (this.groupList && this.now() < this.groupList.until) return this.groupList.groups;
    const list = await this.call('get_group_list', {}, this.actionSignal());
    if (!Array.isArray(list)) throw new Error('OneBot returned an invalid group list');
    const groups = list.slice(0, GROUP_LIST).map((group) => ({ chat: groupChat(group?.group_id), name: cleanName(group?.group_name) }))
      .filter(({ chat }) => parseChat(chat));
    this.groupList = { groups, until: this.now() + GROUP_LIST_MS };
    return groups;
  }

  // The newest messages of a connected chat, newest first. OneBot history
  // takes a message to start from, not a cursor, so only the newest page exists.
  async listChatMessages({ chat_id: chat, limit = 20, cursor, as = 'bot' }) {
    if (as !== 'bot') throw new Error('OneBot reads use this account only');
    if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error('OneBot limit must be between 1 and 50');
    if (cursor !== undefined) throw new Error('OneBot history has no cursor; it returns the newest messages only');
    const { kind, id } = this.target(chat);
    const page = await this.call(kind === 'group' ? 'get_group_msg_history' : 'get_friend_msg_history',
      { [kind === 'group' ? 'group_id' : 'user_id']: Number(id), count: limit }, this.actionSignal());
    if (!Array.isArray(page?.messages)) throw new Error('OneBot returned an invalid message list');
    // The whole page is validated before any route is cached.
    for (const raw of page.messages) historyMessage(raw, { account: this.account, chat });
    const names = new Map(this.state.people(chat).map((person) => [person.id, person.name]));
    // A private chat's history leaves out what its live lines leave out
    // (classify): lines to another program on the account, what the account
    // sent there that this connector did not, a stranger's `/` line. Neither
    // their text nor their attachments are kept.
    const policy = this.policy();
    const hidden = ({ self, bareText, message, route }) => kind === 'private' && (self ? !this.state.isSent(chat, route.mid)
      : classify({ kind, chat, self, fromOwner: message.sender.id === this.account.ownerId, text: bareText }, policy) === 'drop');
    // The page comes oldest first and QQ times are whole seconds: reversed
    // before the stable sort, messages of the same second are newest first too.
    const items = page.messages.map((raw) => historyMessage(raw, { account: this.account, chat, names }))
      .filter((item) => item && !hidden(item)).reverse().sort((a, b) => Number(b.message.create_time) - Number(a.message.create_time)).slice(0, limit);
    for (const { message, route } of items) {
      this.state.recordRoute(message.message_id, route);
      if (message.sender.type === 'user' && message.sender.name) this.state.rememberPerson(chat, message.sender.id, message.sender.name);
    }
    return { chat_id: chat, messages: items.map((item) => item.message), has_more: false, next_cursor: null };
  }

  // Stops delivering; the socket stays open, because the Core still sends
  // while it shuts down. Lines that arrive meanwhile are staged. A line
  // still being staged is not waited for: it may wait on a send's answer,
  // and with the gate gone it is staged without being delivered.
  async stopSubscription() {
    this.stopping = true;
    this.running = false;
    this.gate?.stop();
    this.gate = null;
    if (this.poll !== null) this.clearTimer(this.poll);
    this.poll = null;
  }

  // Outstanding actions are cancelled first, so a send stuck on its answer
  // does not hold up shutdown.
  async stop() {
    this.actions.abort();
    await this.stopSubscription();
    await Promise.all([this.sendTail, ...this.chatTails.values()]);
    this.connection?.abort();
    await this.socketDone;
    await this.inbound;
    this.state.close();
  }
}
