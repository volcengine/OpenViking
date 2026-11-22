// SPDX-License-Identifier: AGPL-3.0

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { DWClient, TOPIC_ROBOT } from 'dingtalk-stream';
import { decodeDingTalkChatId, normalizeDingTalkEvent } from './dingtalk-events.mjs';
import { DingTalkState } from './dingtalk-state.mjs';

const API = 'https://api.dingtalk.com/v1.0';
const RECEIVE_RETRY_DELAYS_MS = [1_000, 2_000, 5_000, 10_000];

export class DingTalkClient {
  #subscription = null;
  #credentials;
  #token;
  #tokenRequest;
  #dataDir;
  #timeoutMs;
  #dryRun;
  #logger;

  constructor(config, _registry, logger) {
    this.#dataDir = config.dataDir;
    this.#timeoutMs = Math.min(config.sendTimeoutMs ?? 30_000, 12_000);
    this.#dryRun = config.dryRun;
    this.#logger = logger;
  }

  async preflight() {
    let credentials;
    try {
      credentials = JSON.parse(await readFile(path.join(this.#dataDir, 'dingtalk.json'), 'utf8'));
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

  async #post(route, body, token) {
    let response;
    try {
      response = await fetch(API + route, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'x-acs-dingtalk-access-token': token } : {}) },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.#timeoutMs),
        redirect: 'error',
      });
    } catch {
      throw new Error(`DingTalk ${route} request failed or timed out`);
    }
    if (!response.ok) throw new Error(`DingTalk ${route} rejected the request (HTTP ${response.status})`);
    try {
      const data = await response.json();
      if (!data || typeof data !== 'object') throw new Error();
      return data;
    } catch {
      throw new Error(`DingTalk ${route} returned an invalid response`);
    }
  }

  async #accessToken() {
    if (this.#token?.expiresAt > Date.now()) return this.#token.value;
    if (this.#tokenRequest) return this.#tokenRequest;
    const { clientId, clientSecret } = this.#credentials;
    this.#tokenRequest = this.#post('/oauth2/accessToken', { appKey: clientId, appSecret: clientSecret })
      .then(({ accessToken, expireIn }) => {
        if (typeof accessToken !== 'string' || !accessToken || !Number.isFinite(expireIn) || expireIn <= 0) {
          throw new Error('DingTalk did not return a valid app access token');
        }
        this.#token = { value: accessToken, expiresAt: Date.now() + Math.max(0, expireIn - 60) * 1_000 };
        return accessToken;
      }).finally(() => { this.#tokenRequest = null; });
    return this.#tokenRequest;
  }

  async startSubscription(onLine) {
    if (typeof onLine !== 'function') throw new TypeError('DingTalk subscription requires an EVENT callback');
    if (this.#subscription) throw new Error('DingTalk subscription is already started');
    if (!this.#credentials) throw new Error('DingTalk must pass preflight before start');
    const { clientId, clientSecret, robotCode } = this.#credentials;
    const stream = new DWClient({ clientId, clientSecret, debug: false, keepAlive: true, subscriptions: [] });
    const state = new DingTalkState(this.#dataDir, JSON.stringify([clientId, robotCode]));
    const subscription = { stream, state, onLine, cursor: 0, draining: false, failures: 0, retryTimer: null };
    this.#subscription = subscription;
    stream.registerCallbackListener(TOPIC_ROBOT, (callback) => {
      const deliveryId = callback?.headers?.messageId;
      if (this.#subscription !== subscription || typeof deliveryId !== 'string' || !deliveryId) return;
      try {
        const event = normalizeDingTalkEvent(callback, { botId: robotCode });
        if (event) {
          state.prune();
          state.stage(event);
        }
        stream.socketCallBackResponse(deliveryId, null);
      } catch {
        this.#logger?.warn('dingtalk_callback_handling_failed');
      } finally {
        void this.#drain(subscription);
      }
    });

    try {
      await stream.connect();
      if (this.#subscription !== subscription || !stream.connected) throw new Error('DingTalk Stream did not open');
      void this.#drain(subscription);
    } catch {
      if (this.#subscription === subscription) this.stop();
      throw new Error('Could not start DingTalk Stream');
    }
  }

  async #drain(subscription) {
    if (this.#subscription !== subscription || subscription.draining || subscription.retryTimer) return;
    subscription.draining = true;
    try {
      while (this.#subscription === subscription) {
        const pending = subscription.state.next(subscription.cursor);
        if (!pending) break;
        await subscription.onLine(pending.envelope);
        if (this.#subscription !== subscription) break;
        subscription.state.forwarded(pending.seq);
        subscription.cursor = pending.seq;
        subscription.failures = 0;
      }
    } catch {
      if (this.#subscription === subscription) {
        const index = Math.min(subscription.failures, RECEIVE_RETRY_DELAYS_MS.length - 1);
        const delay = RECEIVE_RETRY_DELAYS_MS[index];
        subscription.failures = index + 1;
        subscription.retryTimer = setTimeout(() => {
          subscription.retryTimer = null;
          void this.#drain(subscription);
        }, delay);
        subscription.retryTimer.unref?.();
        this.#logger?.warn('dingtalk_receive_drain_failed', { retryInMs: delay });
      }
    } finally {
      subscription.draining = false;
    }
  }

  async sendMessage({ chat_id: chatId, content }) {
    const openConversationId = decodeDingTalkChatId(chatId);
    if (content?.type !== 'markdown' || typeof content.text !== 'string' || !content.text.trim()) {
      throw new Error('DingTalk group delivery requires Markdown text');
    }
    if (this.#dryRun) return { dry_run: true, chat_id: chatId };
    const title = Array.from(content.text.trim().split(/\r?\n/, 1)[0]).slice(0, 20).join('');
    const { processQueryKey } = await this.#post('/robot/groupMessages/send', {
      robotCode: this.#credentials.robotCode,
      openConversationId,
      msgKey: 'sampleMarkdown',
      msgParam: JSON.stringify({ title, text: content.text }),
    }, await this.#accessToken());
    if (typeof processQueryKey !== 'string' || !processQueryKey) {
      throw new Error('DingTalk did not return a group delivery receipt');
    }
    return { chat_id: chatId, process_query_key: processQueryKey };
  }

  replyMessage({ chat_id: chatId, content, reply_in_thread: inThread }) {
    if (inThread) throw new Error('DingTalk group delivery does not support thread replies');
    return this.sendMessage({ chat_id: chatId, content });
  }

  stop() {
    const subscription = this.#subscription;
    this.#subscription = null;
    if (!subscription) return;
    clearTimeout(subscription.retryTimer);
    try { subscription.stream.disconnect(); }
    finally { subscription.state.close(); }
  }
}
