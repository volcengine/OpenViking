// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { DWClient, TOPIC_ROBOT } from 'dingtalk-stream';
import { safeError } from '../logger.mjs';
import { chatId, normalizeMessage, parseChatId, parseMessageId } from './events.mjs';
import { DingTalkState } from './state.mjs';

const API = 'https://api.dingtalk.com/v1.0';
// The SDK reconnects a dropped Stream by itself, every second and silently.
// One that stays down this long (a reset secret, a disabled app, no network)
// is reported, and the Core restarts the Connector with its backoff.
const DOWN_LIMIT_MS = 60_000;
const WATCH_MS = 5_000;

// The SDK reports through console, and stdout carries the Connector protocol.
function quietConsole(logger) {
  console.log = console.info = () => logger.debug('dingtalk_sdk_status');
  console.warn = console.error = (...args) => logger.warn('dingtalk_sdk_warning', {
    error: safeError(args.find((arg) => arg instanceof Error) ?? args.join(' ')),
  });
}

export class DingTalkClient {
  #credentials;
  #token;
  #tokenRequest;

  constructor(config, _registry, logger) {
    this.config = config;
    this.logger = logger;
    this.timeoutMs = Math.min(config.sendTimeoutMs ?? 30_000, 12_000);
    this.stream = null;
    this.state = null;
    this.watch = null;
  }

  async preflight() {
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
    await this.#accessToken();
    return { self: { id: robotCode } };
  }

  async #request(route, body, token) {
    let response;
    try {
      response = await fetch(API + route, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'x-acs-dingtalk-access-token': token } : {}) },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs),
        redirect: 'error',
      });
    } catch {
      throw new Error(`DingTalk ${route} request failed or timed out`);
    }
    if (!response.ok) throw new Error(`DingTalk ${route} rejected the request (HTTP ${response.status})`);
    const data = await response.json().catch(() => null);
    if (!data || typeof data !== 'object') throw new Error(`DingTalk ${route} returned an invalid response`);
    return data;
  }

  async #accessToken() {
    if (this.#token?.expiresAt > Date.now()) return this.#token.value;
    if (this.#tokenRequest) return this.#tokenRequest;
    const { clientId, clientSecret } = this.#credentials;
    this.#tokenRequest = this.#request('/oauth2/accessToken', { appKey: clientId, appSecret: clientSecret })
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
    if (this.stream) throw new Error('DingTalk receiver is already started');
    quietConsole(this.logger);
    const { clientId, clientSecret } = this.#credentials;
    this.state = new DingTalkState(this.config.dataDir);
    this.onLine = onLine;
    const stream = new DWClient({ clientId, clientSecret, debug: false, keepAlive: true, subscriptions: [] });
    stream.registerCallbackListener(TOPIC_ROBOT, (callback) => this.receive(callback));
    this.stream = stream;
    // Staged messages from before a restart go first.
    this.drain();
    void stream.connect();
    let downSince = Date.now();
    this.watch = setInterval(() => {
      if (stream.connected) { downSince = null; return; }
      downSince ??= Date.now();
      if (Date.now() - downSince < DOWN_LIMIT_MS) return;
      this.stop();
      onUnexpectedExit({ code: 1, signal: null, stderr: `DingTalk Stream was disconnected for ${DOWN_LIMIT_MS / 1_000} seconds` });
    }, WATCH_MS);
    this.watch.unref?.();
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
        this.onLine(envelope);
        this.state.delivered(seq);
      }
    }
  }

  sendMessage({ chat_id: chat, content }) {
    return this.#sendToGroup(chat, parseChatId(chat), content.text);
  }

  // DingTalk has no quoted reply: the answer is a new message in the group.
  replyMessage({ message_id: id, content }) {
    const conversationId = parseMessageId(id);
    return this.#sendToGroup(chatId(conversationId), conversationId, content.text);
  }

  async #sendToGroup(chat, openConversationId, text) {
    if (this.config.dryRun) return { dry_run: true, chat_id: chat };
    const title = Array.from(text.trim().split(/\r?\n/, 1)[0]).slice(0, 20).join('');
    const { processQueryKey } = await this.#request('/robot/groupMessages/send', {
      robotCode: this.#credentials.robotCode,
      openConversationId,
      msgKey: 'sampleMarkdown',
      msgParam: JSON.stringify({ title, text }),
    }, await this.#accessToken());
    if (!processQueryKey) throw new Error('DingTalk did not return a group delivery receipt');
    return { chat_id: chat, process_query_key: processQueryKey };
  }

  stop() {
    clearInterval(this.watch);
    this.stream?.disconnect();
    this.state?.close();
    this.stream = null;
    this.state = null;
  }
}
