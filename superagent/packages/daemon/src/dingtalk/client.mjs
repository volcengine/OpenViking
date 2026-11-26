// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { DWClient, TOPIC_ROBOT } from 'dingtalk-stream';
import { safeError, opaqueRef } from '../logger.mjs';
import { CONTEXT_AGE_MS } from '../onebot/gate.mjs';
import { chatId, isDwsGroupMessage, normalizeDwsMessage, messageId, normalizeMessage, parseChatId, parseMessageTarget } from './events.mjs';
import { requestDingTalk, sendGroup } from './api.mjs';
import { checkDws, DwsSubscription, sendDws } from './dws.mjs';
import { DingTalkGate } from './gate.mjs';
import { classifyDws, loadPolicy, parsePolicy } from './policy.mjs';
import { DingTalkState } from './state.mjs';

// The SDK reconnects a dropped Stream by itself, every second and silently.
// One that stays down this long (a reset secret, a disabled app, no network)
// is reported. The Core restarts the Connector when all receivers are down.
const DOWN_LIMIT_MS = 60_000;
const WATCH_MS = 5_000;
const DWS_RETRY_MS = 60_000;

// The SDK reports through console, and stdout carries the Connector protocol.
function quietConsole(logger) {
  console.log = console.info = () => logger.debug('dingtalk_sdk_status');
  console.warn = console.error = (...args) => logger.warn('dingtalk_sdk_warning', {
    error: safeError(args.find((arg) => arg instanceof Error) ?? args.join(' ')),
  });
}

// What a DWS message carried instead of text, for the skipped-content log.
function contentKind(content) {
  if (content == null) return 'missing';
  return Array.isArray(content) ? 'list' : typeof content;
}

export class DingTalkClient {
  #credentials;
  #token;
  #tokenRequest;

  constructor(config, registry, logger) {
    this.config = config;
    this.logger = logger;
    this.registry = registry;
    this.policy = parsePolicy({});
    this.timeoutMs = Math.min(config.sendTimeoutMs ?? 30_000, 12_000);
    this.stream = null;
    this.state = null;
    this.watch = null;
    this.dwsSends = new Map();
    this.dwsInbound = new Map();
    this.dwsContentSeen = new Set();
    this.allGroupsWarned = false;
  }

  async preflight() {
    this.policy = await loadPolicy(this.config.dataDir);
    const mixed = this.policy.stream && this.policy.dws;
    if (this.policy.stream || this.policy.dws?.sendAs === 'robot') {
      let credentials;
      try {
        credentials = JSON.parse(await readFile(path.join(this.config.dataDir, 'dingtalk.json'), 'utf8'));
      } catch {
        throw new Error('Configure DingTalk app credentials in the instance data directory (dingtalk.json)');
      }
      const { clientId, clientSecret, robotCode = clientId } = credentials ?? {};
      if (![clientId, clientSecret, robotCode].every((value) => typeof value === 'string' && value.trim() !== '')) {
        throw new Error('DingTalk credentials require clientId, clientSecret and a valid robotCode');
      }
      this.#credentials = { clientId, clientSecret, robotCode };
      if (!mixed) await this.#accessToken();
    }
    // In mixed mode, configuration remains required and remote authentication
    // runs in each transport so one remote outage cannot block the other.
    if (this.policy.dws && !mixed) await checkDws(this.registry, this.policy.dws);
    return { self: { id: this.#credentials?.robotCode ?? this.policy.dws.selfOpenId } };
  }

  async #accessToken() {
    if (this.#token?.expiresAt > Date.now()) return this.#token.value;
    const { clientId, clientSecret } = this.#credentials;
    this.#tokenRequest ??= requestDingTalk('/oauth2/accessToken', { appKey: clientId, appSecret: clientSecret }, this.timeoutMs)
      .then(({ accessToken, expireIn }) => {
        if (typeof accessToken !== 'string' || !accessToken || !Number.isFinite(expireIn) || expireIn <= 0) {
          throw new Error('DingTalk did not return a valid app access token');
        }
        this.#token = { value: accessToken, expiresAt: Date.now() + Math.max(0, expireIn - 60) * 1_000 };
        return accessToken;
      }).finally(() => { this.#tokenRequest = null; });
    return this.#tokenRequest;
  }

  startSubscription(onLine, onUnexpectedExit) {
    this.stopping = null;
    this.state = new DingTalkState(this.config.dataDir, this.policy.dws?.profile);
    this.onLine = onLine;
    this.onUnexpectedExit = onUnexpectedExit;
    this.streamDown = !this.policy.stream;
    const pendingGroups = new Set(this.state.pendingKinds().map(({ group_id: group }) => group));
    for (const group of pendingGroups) this.state.reconcileGroup(group, this.policy.route(group));
    if (this.policy.dws) {
      if (this.policy.dws.allGroups && !this.allGroupsWarned) {
        this.allGroupsWarned = true;
        this.logger.warn('dingtalk_dws_all_groups_enabled', {
          detail: 'Unlisted visible groups use ambient processing. Admitted messages may be stored in SQLite and synced to OpenViking memory.',
        });
      }
      const state = this.state;
      this.gate = new DingTalkGate(state, (group) => this.drainDws(group), (error) => {
        this.logger.warn('dingtalk_dws_release_failed', { error: safeError(error) });
        queueMicrotask(() => {
          if (this.state === state) this.fail({ code: 1, stderr: 'DingTalk DWS release failed' });
        });
      });
      this.gate.resume();
      this.startDws();
    }
    this.drain();
    if (this.policy.stream) {
      quietConsole(this.logger);
      this.startStream();
    }
  }

  startDws() {
    const dws = new DwsSubscription(this.registry, this.policy.dws, this.logger,
      (raw, group) => this.receiveDws(raw, group), (exit, teardownFailed) => {
        if (this.dws !== dws) return;
        this.dws = null;
        if (!teardownFailed && (this.stream?.connected || !this.streamDown)) {
          this.dwsRetry = setTimeout(() => this.startDws(), DWS_RETRY_MS);
        } else {
          this.fail(exit);
        }
      });
    this.dws = dws;
    dws.start(this.policy.dwsGroups);
  }

  startStream() {
    const { clientId, clientSecret } = this.#credentials;
    const stream = new DWClient({ clientId, clientSecret, debug: false, keepAlive: true, subscriptions: [] });
    stream.registerCallbackListener(TOPIC_ROBOT, (callback) => {
      if (this.stream === stream) this.receive(callback);
    });
    this.stream = stream;
    void stream.connect();
    let downSince = Date.now();
    this.watch = setInterval(() => {
      if (stream.connected) { downSince = null; this.streamDown = false; return; }
      downSince ??= Date.now();
      if (this.streamDown || Date.now() - downSince < DOWN_LIMIT_MS) return;
      this.streamDown = true;
      const reason = `DingTalk Stream was disconnected for ${DOWN_LIMIT_MS / 1_000} seconds`;
      if (this.dws) {
        // The SDK already owns Stream reconnection. Keep that socket lifecycle
        // in place while DWS continues receiving.
        this.logger.warn('dingtalk_stream_disconnected', { reason });
      } else {
        this.fail({ code: 1, signal: null, stderr: reason });
      }
    }, WATCH_MS);
    this.watch.unref?.();
  }

  fail(exit) {
    const notify = this.onUnexpectedExit;
    void this.stop().then(
      () => notify(exit),
      (error) => notify({ ...exit, stderr: `${exit.stderr}; ${safeError(error)}` }),
    );
  }

  // A message is committed before DingTalk is acknowledged; one left
  // unacknowledged is delivered again. A callback that cannot be read is
  // acknowledged and dropped: a redelivery would read the same.
  receive(callback) {
    let event = null;
    try {
      event = normalizeMessage(callback, { botId: this.#credentials.robotCode });
    } catch {
      this.logger.warn('dingtalk_message_rejected');
    }
    try {
      if (event && this.policy.route(parseChatId(event.conversation.id)).channel !== 'stream') event = null;
      if (event) this.state.stage(event);
    } catch (error) {
      this.logger.warn('dingtalk_stage_failed', { error: safeError(error) });
      return;
    }
    this.stream?.socketCallBackResponse(callback.headers.messageId, null);
    if (event) this.drain();
  }

  // Staged messages to the Core, in order, each removed once written.
  drain() {
    for (let rows; (rows = this.state.pending()).length;) {
      for (const { seq, envelope } of rows) {
        const group = parseChatId(JSON.parse(envelope).conversation.id);
        if (this.policy.route(group).channel === 'stream') this.onLine(envelope);
        this.state.delivered(seq);
      }
    }
  }

  sendMessage({ chat_id: chat, content, idempotency_key: key }) {
    return this.#sendToGroup(chat, parseChatId(chat), content.text, key);
  }

  replyMessage({ message_id: id, content, idempotency_key: key }) {
    const { group, message } = parseMessageTarget(id);
    return this.#sendToGroup(chatId(group), group, content.text, key, message);
  }

  receiveDws(raw, group) {
    if (!group) return;
    const route = this.policy.route(group);
    if (route.channel !== 'dws') return;
    const state = this.state;
    const previous = this.dwsInbound.get(group);
    const stage = () => this.stageDws(raw, group, route, state);
    const next = (previous ? previous.then(stage) : stage()).catch((error) => {
      this.logger.warn('dingtalk_dws_stage_failed', { error: safeError(error) });
    });
    this.dwsInbound.set(group, next);
    return next.then(() => {
      if (this.dwsInbound.get(group) === next) this.dwsInbound.delete(group);
    });
  }

  async stageDws(raw, group, route, state) {
    if (this.state !== state) return;
    const event = normalizeDwsMessage(raw, { group, tenantId: this.policy.dws.tenantId });
    if (!event) {
      // Text is the only content this Connector reads; say so once per group
      // and content type instead of dropping an image or a card without trace.
      if (isDwsGroupMessage(raw, group) && typeof raw.content !== 'string') {
        this.noteSkippedContent(group, contentKind(raw.content));
      }
      return;
    }
    if (event.actor.id === this.policy.dws.selfOpenId) {
      if (this.policy.dws.sendAs === 'robot') return;
      // A personal send can echo before the CLI returns its message ID.
      await Promise.allSettled([...this.dwsSends].filter(([, chat]) => chat === group).map(([send]) => send));
      if (this.state !== state || state.isDwsSent(event.message.id, Date.now() - CONTEXT_AGE_MS)) return;
      this.gate.stage(event, group, 'context');
      return;
    }
    this.gate.stage(event, group, classifyDws(event, route.strategy, this.policy.dws.selfOpenId));
  }

  noteSkippedContent(group, kind) {
    const key = `${group}:${kind}`;
    if (this.dwsContentSeen.has(key)) return;
    this.dwsContentSeen.add(key);
    this.logger.info('dingtalk_dws_content_skipped', { group: opaqueRef(group), content_type: kind });
  }

  drainDws(group) {
    for (let rows; (rows = this.state.pendingGroup(group)).length;) {
      for (const { seq, envelope } of rows) {
        if (this.policy.route(group).channel === 'dws') this.onLine(envelope);
        this.state.delivered(seq);
      }
    }
  }

  async #sendToGroup(chat, group, text, key, message) {
    const route = this.policy.route(group);
    if (route.channel === 'off') throw new Error('DingTalk group is disabled');
    if (this.config.dryRun) return { dry_run: true, chat_id: chat };
    // The action's start identifies which release the reply answers.
    const startedAt = performance.now();
    const gate = this.gate;
    let result;
    if (route.channel === 'dws' && this.policy.dws.sendAs === 'user') {
      const state = this.state;
      const sending = sendDws(this.registry, this.policy.dws, {
        group, message, text, key, cwd: this.config.projectDir, timeoutMs: this.config.sendTimeoutMs ?? 30_000,
      }).then((id) => {
        const encoded = messageId(group, id);
        if (state && this.state === state) {
          try {
            state.recordDwsSent(encoded, Date.now() - CONTEXT_AGE_MS);
          } catch (error) {
            this.logger.warn('dingtalk_dws_record_send_failed', { error: safeError(error) });
          }
        }
        return encoded;
      });
      this.dwsSends.set(sending, group);
      try {
        result = { chat_id: chat, message_id: await sending };
      } finally {
        this.dwsSends.delete(sending);
      }
    } else {
      const processQueryKey = await sendGroup({ group, robotCode: this.#credentials.robotCode,
        text, timeoutMs: this.timeoutMs, token: await this.#accessToken() });
      result = { chat_id: chat, process_query_key: processQueryKey };
    }
    if (route.channel === 'dws' && this.gate === gate) {
      try {
        gate?.sent(group, startedAt);
      } catch (error) {
        this.logger.warn('dingtalk_dws_gate_sent_failed', { error: safeError(error) });
      }
    }
    return result;
  }

  stop() {
    return this.stopping ??= this.close();
  }

  async close() {
    clearTimeout(this.dwsRetry);
    clearInterval(this.watch);
    const { stream, gate, state, dws } = this;
    this.stream = null;
    this.state = null;
    this.gate = null;
    this.dws = null;
    this.dwsInbound.clear();
    const [subscription, ...cleanup] = await Promise.allSettled([
      () => dws?.stop(), () => stream?.disconnect(), () => gate?.stop(), () => state?.close(),
    ].map(async (stop) => stop()));
    for (const result of cleanup) {
      if (result.status === 'rejected') this.logger.warn('dingtalk_stop_failed', { error: safeError(result.reason) });
    }
    if (subscription.status === 'rejected') throw subscription.reason;
  }
}
