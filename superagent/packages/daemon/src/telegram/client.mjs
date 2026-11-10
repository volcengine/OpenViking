// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { setTimeout as delay } from 'node:timers/promises';
import { TelegramApi, TelegramError, TELEGRAM_VERSION, TEXT_LIMIT, integer } from './api.mjs';
import { TelegramState } from './state.mjs';
import { normalizeUpdate, localMessageId } from './events.mjs';
import { downloadFile, filePayload } from './media.mjs';

export class TelegramClient {
  constructor(config, _registry, logger, { fetchImpl = globalThis.fetch } = {}) {
    this.config = config;
    this.logger = logger;
    this.fetchImpl = fetchImpl;
    this.state = new TelegramState(config.dataDir);
    this.running = false;
    this.stopping = false;
    this.sendTail = Promise.resolve();
    this.actions = new AbortController();
    this.nextSendAt = 0;
  }

  async preflight() {
    this.account = this.state.account();
    if (!this.account) throw new Error('Telegram is not connected; run vika login telegram <instance> first');
    this.state.acquire();
    try {
      this.api = new TelegramApi(this.account.token, { fetchImpl: this.fetchImpl });
      const identity = await this.api.inspect();
      if (identity.botId !== this.account.botId) throw new Error('Telegram bot identity changed; authorize again');
      this.state.prune(Date.now() - this.config.retentionMs);
      return { version: TELEGRAM_VERSION, owner: { id: this.account.ownerId, target: { chat_id: this.account.chatId } } };
    } catch (error) { this.state.release(); throw error; }
  }

  get subscriptionRunning() { return this.running; }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.running) throw new Error('Telegram receiver is already started');
    this.running = true;
    this.stopping = false;
    this.receiveAbort = new AbortController();
    this.monitorPromise = this.monitor(onLine, this.receiveAbort.signal).catch((error) => {
      if (!this.stopping) onUnexpectedExit({ code: 1, signal: null, stderr: error.message });
    }).finally(() => { this.running = false; });
    return this.monitorPromise;
  }

  emit(event, onLine) {
    this.state.recordRoute(event.id, event.nativeId, event.chatId, event.media);
    onLine(JSON.stringify(event.envelope));
  }

  async monitor(onLine, signal) {
    for (const { updateId, event } of this.state.pending(this.account.botId)) {
      if (signal.aborted) return;
      this.emit(event, onLine);
      this.state.delivered(this.account.botId, updateId);
    }
    let offset = this.account.offset;
    let failures = 0;
    while (!signal.aborted) {
      try {
        const batch = await this.api.updates(offset, AbortSignal.any([signal, AbortSignal.timeout(35_000)]));
        if (signal.aborted) break;
        for (const update of batch) {
          let event;
          try { event = normalizeUpdate(update, this.account, this.config.instance ?? 'telegram'); }
          catch { this.logger.warn('telegram_update_rejected'); continue; }
          if (event) this.emit(event, onLine);
        }
        if (batch.length) {
          // Protocol 0.2 has no Core durable ACK. Like the other pollers,
          // checkpoint after emitting; the next poll confirms to Telegram.
          const nextOffset = batch.at(-1).update_id + 1;
          this.state.saveCursor(nextOffset);
          offset = nextOffset;
          this.account.offset = offset;
        } else await delay(250, undefined, { signal });
        failures = 0;
      } catch (error) {
        if (signal.aborted) break;
        if (error instanceof TelegramError && [400, 401, 403, 404, 409].includes(error.code)) {
          throw new Error(error.code === 409 ? 'Telegram polling conflict; use one receiver per bot and remove conflicting webhooks'
            : 'Telegram authorization or configuration failed; run vika login telegram <instance>');
        }
        failures += 1;
        const waitMs = error instanceof TelegramError && error.code === 429 && error.retryAfter !== null
          ? Math.max(1, error.retryAfter) * 1000 : Math.min(60_000, 2000 * 2 ** Math.min(failures - 1, 5));
        this.logger.warn('telegram_poll_failed', { failures, retryInMs: waitMs });
        await delay(waitMs, undefined, { signal }).catch(() => {});
      }
    }
  }

  sendMessage({ chat_id: to, content }) { return this.send(to, content); }

  replyMessage({ message_id: id, content }) {
    const route = this.state.route(id);
    if (!route) throw new Error('Telegram reply target is no longer available');
    return this.send(route.chatId, content, route.nativeId);
  }

  send(to, content, quote = null) {
    if (to !== this.account.chatId) return Promise.reject(new Error('Telegram may only message its verified owner'));
    const signal = AbortSignal.any([this.actions.signal, AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
    const action = this.sendTail.then(async () => {
      signal.throwIfAborted();
      if (this.nextSendAt > Date.now()) await delay(this.nextSendAt - Date.now(), undefined, { signal });
      let method, body;
      if (content.type === 'markdown') {
        if (typeof content.text !== 'string' || !content.text.trim() || [...content.text].length > TEXT_LIMIT) {
          throw new Error('Telegram text must contain 1–4096 characters; send longer content as a file');
        }
        method = 'sendMessage';
        // Plain text deliberately: arbitrary model Markdown is not MarkdownV2.
        body = { chat_id: to, text: content.text, link_preview_options: { is_disabled: true },
          ...(quote ? { reply_parameters: { message_id: quote } } : {}) };
      } else if (content.type === 'file') ({ method, body } = filePayload(content, to, quote));
      else throw new Error(`Telegram does not support ${content.type} messages`);
      signal.throwIfAborted();
      let result;
      try { result = await this.api.send(method, body, signal); }
      finally { this.nextSendAt = Date.now() + 1100; }
      const nativeId = integer(result?.message_id, 'sent message id', 1);
      if (String(result.chat?.id) !== to) throw new Error('Telegram send returned an unexpected chat; delivery is uncertain');
      const id = localMessageId(this.account.botId, to, nativeId);
      this.state.recordRoute(id, nativeId, to);
      return { message_id: id, chat_id: to };
    });
    this.sendTail = action.catch(() => {});
    return action;
  }

  async getMedia({ message_id: id }) {
    const route = this.state.route(id);
    if (!route?.media || route.chatId !== this.account.chatId) throw new Error('No Telegram media exists for that message');
    const signal = AbortSignal.any([this.actions.signal,
      AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
    return { media: [await downloadFile(this.api, route.media, this.state.mediaDirectory, signal)] };
  }

  async stopSubscription() {
    this.stopping = true;
    this.receiveAbort?.abort();
    await this.monitorPromise;
    this.running = false;
  }

  async stop() {
    await this.stopSubscription();
    this.actions.abort();
    await this.sendTail;
    this.state.close();
  }
}
