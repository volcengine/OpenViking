// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { setTimeout as delay } from 'node:timers/promises';
import { QQApi, QQ_VERSION, TEXT_LIMIT, identifier } from './api.mjs';
import { QQState } from './state.mjs';
import { createGateway, gatewayFailure } from './gateway.mjs';
import { normalizeMessage, localMessageId } from './events.mjs';
import { downloadFile, uploadContent, uploadFile } from './media.mjs';

export class QQClient {
  constructor(config, _registry, logger, { fetchImpl = globalThis.fetch, gatewayFactory = createGateway } = {}) {
    this.config = config;
    this.logger = logger;
    this.fetchImpl = fetchImpl;
    this.gatewayFactory = gatewayFactory;
    this.state = new QQState(config.dataDir);
    this.running = false;
    this.stopping = false;
    this.actions = new AbortController();
    this.sendTail = Promise.resolve();
    this.nextSendAt = 0;
  }

  async preflight() {
    this.account = this.state.account();
    if (!this.account) throw new Error('QQ is not connected; run vika login qq <instance> first');
    this.state.acquire();
    try {
      this.api = new QQApi(this.account, { fetchImpl: this.fetchImpl });
      await this.api.gateway();
      this.state.prune(Date.now() - this.config.retentionMs);
      return { version: QQ_VERSION, owner: { id: this.account.ownerId, target: { chat_id: this.account.ownerId } } };
    } catch (error) { this.state.release(); throw error; }
  }

  get subscriptionRunning() { return this.running; }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.running) throw new Error('QQ receiver is already started');
    this.running = true;
    this.stopping = false;
    this.receiveAbort = new AbortController();
    const signal = this.receiveAbort.signal;
    const emit = (event) => {
      this.state.recordRoute(event);
      onLine(JSON.stringify(event.envelope));
    };
    this.monitorPromise = Promise.resolve().then(async () => {
      for (const event of this.state.pending()) {
        if (signal.aborted) return;
        emit(event); this.state.delivered(event.id);
      }
      const gateway = this.gatewayFactory({ api: this.api, signal, logger: this.logger,
        onMessage: (message) => {
          if (signal.aborted) return;
          let event;
          try { event = normalizeMessage(message, this.account, this.state, this.config.instance ?? 'qq'); }
          catch { this.logger.warn('qq_message_rejected'); return; }
          if (!event) return;
          try { emit(event); }
          catch { onUnexpectedExit({ code: 1, stderr: 'QQ inbound delivery failed' }); this.receiveAbort.abort(); }
        },
      });
      await gateway.start();
      if (!signal.aborted) throw new Error('QQ receiver ended');
    }).catch((error) => {
      if (!this.stopping) onUnexpectedExit({ code: 1, signal: null, stderr: gatewayFailure(error).message });
    }).finally(() => { this.running = false; });
    return this.monitorPromise;
  }

  sendMessage({ chat_id: to, content }) { return this.send(to, content); }

  replyMessage({ message_id: id, content }) {
    const route = this.state.route(id);
    if (!route) throw new Error('QQ reply target is no longer available');
    return this.send(route.chatId, content, route);
  }

  send(to, content, route = null) {
    if (!this.account || to !== this.account.ownerId) return Promise.reject(new Error('QQ may only message its verified owner'));
    const signal = AbortSignal.any([this.actions.signal, AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
    const action = this.sendTail.then(async () => {
      signal.throwIfAborted();
      let body;
      let file;
      if (content.type === 'markdown') {
        if (typeof content.text !== 'string' || !content.text.trim() || [...content.text].length > TEXT_LIMIT) {
          throw new Error('QQ text must contain 1–4000 characters; send longer content as a file');
        }
        body = { msg_type: 0, content: content.text };
      } else if (content.type === 'file') file = uploadContent(content);
      else throw new Error(`QQ does not support ${content.type} messages`);
      if (route?.incoming) this.state.checkReply(route.nativeId);
      if (this.nextSendAt > Date.now()) await delay(this.nextSendAt - Date.now(), undefined, { signal });
      if (file) body = { msg_type: 7, media: { file_info: await uploadFile(this.api, to, file, signal) } };
      signal.throwIfAborted();
      if (route?.incoming) {
        body.msg_id = route.nativeId;
        body.msg_seq = this.state.reserveReply(route.nativeId);
      }
      if (route?.refIdx) body.message_reference = { message_id: route.refIdx };
      let sent;
      try { sent = await this.api.request(`/v2/users/${to}/messages`, body, signal); }
      // Below QQ's 20/min per-owner proactive limit; passive replies also paced.
      finally { this.nextSendAt = Date.now() + 3100; }
      const nativeId = identifier(sent.id, 'sent message id');
      const refIdx = sent.ext_info?.ref_idx ? identifier(sent.ext_info.ref_idx, 'sent reference index') : null;
      const id = localMessageId(this.account.appId, nativeId, refIdx ?? '');
      this.state.recordRoute({ id, nativeId, chatId: to, refIdx, at: Date.now() });
      return { message_id: id, chat_id: to };
    });
    this.sendTail = action.catch(() => {});
    return action;
  }

  async getMedia({ message_id: id }) {
    const route = this.state.route(id);
    if (!route?.media.length || route.chatId !== this.account.ownerId) throw new Error('No QQ media exists for that message');
    const signal = AbortSignal.any([this.actions.signal, AbortSignal.timeout(Math.max(1, (this.config.sendTimeoutMs ?? 30_000) - 500))]);
    const media = [];
    for (const item of route.media) media.push(await downloadFile(this.api, item, this.state.mediaDirectory, signal));
    return { media };
  }

  async stopSubscription() {
    this.stopping = true;
    this.receiveAbort?.abort();
    await this.monitorPromise;
    this.running = false;
  }

  async stop() {
    this.actions.abort();
    await this.stopSubscription();
    await this.sendTail;
    this.state.close();
  }
}
