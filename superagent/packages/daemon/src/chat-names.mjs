// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { opaqueRef, safeChatLookupError } from './logger.mjs';

const CHAT_NAME_REFRESH_MS = 24 * 60 * 60 * 1_000;
const CHAT_NAME_RETRY_BASE_MS = 5 * 60 * 1_000;

// Background chat-title resolution: best-effort lark-cli lookups with
// persistent refresh/backoff state in the store. Never blocks the inbound pipe.
export class ChatNameTracker {
  constructor({ store, lark, logger, isActive = () => true }) {
    this.store = store;
    this.lark = lark;
    this.logger = logger;
    this.isActive = isActive;
    this.lookups = new Map(); // chatId -> in-flight lookup promise
  }

  refresh(limit = 20) {
    if (!this.isActive() || !this.lark.profile) return;
    for (const source of this.store.dueChatNameLookups(Date.now(), limit)) {
      this.schedule(source.chatId);
    }
  }

  schedule(chatId) {
    if (!this.isActive() || !this.lark.profile || this.lookups.has(chatId)) return false;
    const source = this.store.chatNameLookupCandidate(chatId);
    if (!source) return false;
    const lookup = this._lookup(source)
      .catch((error) => {
        this.logger.warn('chat_name_lookup_internal_failed', {
          chatRef: opaqueRef(chatId),
          error: safeChatLookupError(error),
        });
      })
      .finally(() => {
        this.lookups.delete(chatId);
        if (this.isActive()) queueMicrotask(() => this.refresh());
      });
    this.lookups.set(chatId, lookup);
    return true;
  }

  async _lookup(source) {
    try {
      const name = await this.lark.getChatName(source.chatId);
      if (!this.isActive()) return;
      const now = Date.now();
      if (name) {
        this.store.saveChatSourceName(source.chatId, name, now + CHAT_NAME_REFRESH_MS, now);
        this.logger.info('chat_name_resolved', { chatRef: source.sourceRef });
        return;
      }
      this.store.deferChatSourceName(source.chatId, 'EMPTY', now + CHAT_NAME_REFRESH_MS, now);
      this.logger.debug('chat_name_empty', { chatRef: source.sourceRef });
    } catch (error) {
      if (!this.isActive()) return;
      const now = Date.now();
      const backoffMs = Math.min(
        CHAT_NAME_REFRESH_MS,
        CHAT_NAME_RETRY_BASE_MS * (2 ** Math.min(source.failureCount, 8)),
      );
      this.store.deferChatSourceName(source.chatId, 'RETRY', now + backoffMs, now);
      this.logger.warn('chat_name_lookup_failed', {
        chatRef: source.sourceRef,
        retryInMs: backoffMs,
        error: safeChatLookupError(error),
      });
    }
  }
}
