// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { getUpdates, notifyStart, notifyStop, WEIXIN_CHANNEL_VERSION } from './api.mjs';
import { downloadInboundMedia, inboundText } from './media.mjs';
import { sendWeixinMessage } from './send.mjs';
import { WeixinState } from './state.mjs';
import { eventMessage } from '../connectors/protocol.mjs';

function delay(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
    signal?.addEventListener('abort', () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

function messageIdOf(message) {
  if (message?.message_id) return String(message.message_id);
  for (const item of message?.item_list ?? []) {
    if (item?.msg_id) return String(item.msg_id);
  }
  return null;
}

function localMessageId(accountId, providerMessageId) {
  const account = createHash('sha256').update(accountId).digest('hex').slice(0, 10);
  const safeProvider = /^[A-Za-z0-9_-]{1,128}$/.test(providerMessageId)
    ? providerMessageId
    : createHash('sha256').update(providerMessageId).digest('hex');
  return `wx_${account}_${safeProvider}`;
}

function profileId(accountId) {
  return `weixin_${createHash('sha256').update(accountId).digest('hex').slice(0, 12)}`;
}

export class WeixinClient {
  constructor(config, _registry, logger) {
    this.config = config;
    this.logger = logger;
    this.state = new WeixinState(config.dataDir);
    this.account = null;
    this.profile = null;
    this.abortController = null;
    this.monitorPromise = null;
    this.running = false;
    this.stopping = false;
    this.stopNotified = false;
  }

  async preflight() {
    this.account = this.state.account();
    if (!this.account?.token) {
      throw new Error('Weixin is not logged in; run vika login weixin <instance-id> first');
    }
    this.profile = profileId(this.account.accountId);
    // The bot talks only with the account that scanned the login QR code.
    if (!this.account.userId) {
      throw new Error('Weixin login returned no scanning user id; scan the QR code again (vika login weixin)');
    }
    this.state.pruneMedia(Date.now() - this.config.retentionMs);
    return {
      profile: this.profile,
      version: `openclaw-weixin-protocol/${WEIXIN_CHANNEL_VERSION}`,
      // The account that scanned the login QR code; its chat is its user id.
      owner: this.account.userId ? { id: this.account.userId, target: { chat_id: this.account.userId } } : null,
    };
  }

  allowed(peerId) {
    return this.account.userId === peerId;
  }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.running) throw new Error('Weixin subscriber is already running');
    this.stopping = false;
    this.running = true;
    this.abortController = new AbortController();
    this.monitorPromise = this.monitor(onLine, this.abortController.signal)
      .catch((error) => {
        if (this.stopping) return;
        this.running = false;
        onUnexpectedExit({ code: 1, signal: null, stderr: String(error?.message ?? error) });
      });
    return this.monitorPromise;
  }

  get subscriptionRunning() {
    return this.running && !this.abortController?.signal.aborted;
  }

  async monitor(onLine, signal) {
    try {
      const started = await notifyStart({
        apiBaseUrl: this.account.baseUrl,
        token: this.account.token,
        signal,
      });
      if ((started.ret !== undefined && started.ret !== 0)
        || (started.errcode !== undefined && started.errcode !== 0)) {
        this.logger.warn('weixin_notify_start_failed', {
          ret: started.ret,
          errcode: started.errcode,
        });
      }
    } catch (error) {
      if (signal.aborted) return;
      this.logger.warn('weixin_notify_start_failed', { error: String(error?.message ?? error) });
    }

    let cursor = this.state.cursor();
    let pollTimeoutMs = this.config.weixinLongPollTimeoutMs;
    let failures = 0;
    while (!signal.aborted) {
      try {
        const response = await getUpdates({
          apiBaseUrl: this.account.baseUrl,
          token: this.account.token,
          cursor,
          timeoutMs: pollTimeoutMs,
          signal,
        });
        if (signal.aborted) break;
        const failed = (response.ret !== undefined && response.ret !== 0)
          || (response.errcode !== undefined && response.errcode !== 0);
        if (failed) {
          const code = response.errcode ?? response.ret;
          if (code === -14) throw new Error('Weixin session expired; run the QR login command again');
          throw new Error(`Weixin getUpdates failed: ${code} ${response.errmsg ?? ''}`.trim());
        }
        failures = 0;
        if (Number.isFinite(response.longpolling_timeout_ms)
          && response.longpolling_timeout_ms >= 5_000
          && response.longpolling_timeout_ms <= 120_000) {
          pollTimeoutMs = response.longpolling_timeout_ms;
        }
        for (const message of response.msgs ?? []) {
          await this.acceptMessage(message, onLine, signal);
        }
        if (response.get_updates_buf) {
          this.state.saveCursor(response.get_updates_buf);
          cursor = response.get_updates_buf;
        }
      } catch (error) {
        if (signal.aborted) break;
        if (/session expired/i.test(String(error?.message))) throw error;
        failures += 1;
        const waitMs = failures >= 3 ? 30_000 : 2_000;
        this.logger.warn('weixin_poll_failed', {
          failures,
          retryInMs: waitMs,
          error: String(error?.message ?? error),
        });
        await delay(waitMs, signal);
      }
    }
    this.running = false;
  }

  async acceptMessage(message, onLine, signal = null) {
    if (message?.message_type !== 1) return false;
    const peerId = typeof message.from_user_id === 'string' ? message.from_user_id.trim() : '';
    const providerMessageId = messageIdOf(message);
    const text = inboundText(message);
    const hasSupportedMedia = (message?.item_list ?? []).some((item) => (
      (item?.type === 2 && item.image_item?.media)
      || (item?.type === 4 && item.file_item?.media)
    ));
    if (!peerId || !providerMessageId || (!text && !hasSupportedMedia)) return false;
    if (!this.allowed(peerId)) {
      this.logger.info('weixin_message_ignored_sender');
      return false;
    }
    const messageId = localMessageId(this.account.accountId, providerMessageId);
    this.state.recordInbound({
      localMessageId: messageId,
      providerMessageId,
      peerId,
      contextToken: message.context_token || null,
    });
    let media = this.state.mediaForMessage(messageId);
    let markers = media.map((item) => item.kind === 'image'
      ? '[image attachment]'
      : `[file attachment name=${JSON.stringify(item.fileName)}]`);
    if (media.length === 0 && hasSupportedMedia) {
      const downloaded = await downloadInboundMedia(message, {
        localMessageId: messageId,
        mediaDirectory: this.state.mediaDirectory,
        signal,
      });
      media = downloaded.records;
      markers = downloaded.markers;
      for (const failure of downloaded.failures) {
        this.logger.warn('weixin_media_download_failed', {
          kind: failure.kind,
          error: String(failure.error?.message ?? failure.error),
        });
      }
      try {
        this.state.replaceInboundMedia(messageId, media);
      } catch (error) {
        for (const item of media) {
          try { fs.rmSync(item.localPath, { force: true }); } catch {}
        }
        throw error;
      }
    }
    if (signal?.aborted) return false;
    const content = [text, ...markers].filter(Boolean).join('\n').trim();
    if (!content) return false;
    onLine(JSON.stringify(eventMessage('weixin', {
      id: `${this.account.accountId}:${providerMessageId}`,
      kind: 'message.created',
      occurred_at: Number(message.create_time_ms ?? Date.now()),
      subject: { type: 'conversation', id: peerId },
      actor: { id: peerId, type: 'user' },
      content: { text: content, attachments: media.map((item, ordinal) => ({
        id: `${messageId}:${ordinal}`, type: item.kind, name: item.fileName,
      })) },
      conversation: { id: peerId, type: 'p2p', tenant_id: this.account.accountId },
      message: { id: messageId, type: 'text' },
      reply_route: { action: 'message.reply', input: { message_id: messageId } },
    })));
    return true;
  }

  sendMessage({ chat_id: to, content, idempotency_key: idempotencyKey }) {
    return sendWeixinMessage({ state: this.state, to, content, idempotencyKey });
  }

  replyMessage({ message_id: messageId, content, idempotency_key: idempotencyKey }) {
    const route = this.state.routeForMessage(messageId);
    if (!route) throw new Error('Weixin reply target is no longer available');
    return sendWeixinMessage({ state: this.state, to: route.peerId, content, idempotencyKey });
  }

  getMedia({ message_id: messageId }) {
    const media = this.state.mediaForMessage(messageId);
    if (media.length === 0) throw new Error('No downloaded Weixin media exists for that message');
    return { media: media.map((item) => ({
      path: item.path, name: item.fileName, kind: item.kind, mime_type: item.mimeType, size: item.byteSize,
    })) };
  }

  async stopSubscription() {
    this.stopping = true;
    this.abortController?.abort();
    await this.monitorPromise?.catch(() => {});
    this.running = false;
    if (this.account?.token && !this.stopNotified) {
      this.stopNotified = true;
      try {
        await notifyStop({ apiBaseUrl: this.account.baseUrl, token: this.account.token });
      } catch (error) {
        this.logger.warn('weixin_notify_stop_failed', { error: String(error?.message ?? error) });
      }
    }
  }

  async stop() {
    await this.stopSubscription();
    this.state.close();
  }
}
