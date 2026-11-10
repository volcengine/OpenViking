// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash, randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix, CognitionLimitError } from './cognition.mjs';
import { assembleVolcSelfHostedConversationPolicy } from './conversation-policy.mjs';
import { safeError as safe } from './logger.mjs';
import { activityPayload } from './activity.mjs';
import { deliveryItem, receiptIds, withReceiptIds } from './runtime-input.mjs';
import { getRuntimeDefinition, staticModelEntries } from './runtime-catalog.mjs';

const RECONNECT_BASE_MS = 500;
const RECONNECT_CAP_MS = 5_000;
const MAX_EVENT_BYTES = 5_000_000;
class VolcApiError extends Error {
  constructor(status, requestId = '') {
    super(`Volc API request failed (${status})${requestId ? `, request_id=${requestId}` : ''}`);
    this.name = 'VolcApiError';
    this.status = status;
    this.retryable = status === 429 || status >= 500;
  }
}

const VOLC_RUNTIME = getRuntimeDefinition('volc');

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function textContent(event) {
  if (!Array.isArray(event?.content)) return '';
  return event.content
    .filter((block) => block?.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('')
    .trim();
}

function parseSseFrames(buffer, onEvent) {
  let boundary;
  while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
    const frame = buffer.slice(0, boundary);
    const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? '\n\n';
    buffer = buffer.slice(boundary + separator.length);
    const data = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).replace(/^ /, ''))
      .join('\n');
    if (!data || data === '[DONE]') continue;
    let event;
    try { event = JSON.parse(data); } catch { continue; }
    onEvent(event);
  }
  return buffer;
}

export class VolcAgent {
  constructor(config, store, logger, options = {}) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this.fetch = options.fetch ?? globalThis.fetch;
    this.onTurnFailed = options.onTurnFailed ?? null;
    this.onTurnSucceeded = options.onTurnSucceeded ?? null;
    this.environmentId = config.volcEnvironmentId ?? '';
    this.agentName = config.agentName || 'the bot';
    this.transportPolicy = assembleVolcSelfHostedConversationPolicy(config);
    this.onTurnAbandoned = options.onTurnAbandoned ?? null;
    this.onAvailable = options.onAvailable ?? null;

    const cognition = store.getCognitionState?.()
      ?? { runtime: VOLC_RUNTIME.id, epoch: 1, authorityToken: null };
    this.runtime = VOLC_RUNTIME.id;
    this.capabilities = VOLC_RUNTIME.capabilities;
    this.model = VOLC_RUNTIME.defaultModel;
    this.effort = VOLC_RUNTIME.efforts[0];
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;

    this.baseUrl = String(config.volcBaseUrl ?? 'https://ark.cn-beijing.volces.com/api/v3').replace(/\/+$/, '');
    this.apiKey = config.volcApiKey ?? '';
    this.templateSessionId = config.volcSessionId ?? '';
    this.requestTimeoutMs = config.volcRequestTimeoutMs ?? 30_000;
    this.streamIdleTimeoutMs = config.volcStreamIdleTimeoutMs ?? 900_000;
    this.reconnectBaseMs = options.reconnectBaseMs ?? RECONNECT_BASE_MS;
    this.reconnectCapMs = options.reconnectCapMs ?? RECONNECT_CAP_MS;

    this.sessionId = null;
    this.template = null;
    this.policyHashValue = null;
    this.connected = false;
    this.stopping = false;
    this.turnActive = false;
    this.turnCompleting = false;
    this.completionPromise = null;
    this.rotationPending = false;
    this.sessionPoisoned = false;
    this.poisonRecoveryPromise = null;
    this.poisonRecoveryTimer = null;
    this.poisonRecoveryBackoffMs = this.reconnectBaseMs;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.activeItems = [];
    this.activeConversationKey = null;
    this.queue = [];
    this.abortController = null;
    this.streamAbortController = null;
    this.streamTurnAbort = null;
    this.seenEventIds = new Set();
    this.lastMessage = '';
    this.lastMessageEventId = null;
    this.expectedUserEventId = null;
    this.turnFenceSeen = false;
    this.transportPolicyInjected = false;
    this.activitySeq = 0;
    this.idleWaiters = [];
  }

  get threadId() { return this.sessionId; }
  get policyHash() { return this.policyHashValue ?? createHash('sha256').update('volc-managed-agent-v1').digest('hex'); }
  get state() {
    return {
      connected: this.connected && !this.sessionPoisoned && !this.poisonRecoveryPromise,
      activeTurn: this.turnActive ? 1 : 0,
      queued: this.queue.reduce((count, batch) => count + batch.length, 0),
    };
  }
  get turnInProgress() { return this.turnActive || this.turnCompleting; }
  get sessionDescriptor() {
    return {
      runtime: this.runtime,
      sessionId: this.sessionId,
      policyHash: this.policyHash,
      epoch: this.cognitionEpoch,
      syncedThroughSeq: this.syncedThroughSeq,
      sessionReady: Boolean(this.sessionId),
    };
  }

  activateSession() { this.sessionPersistence = true; }

  async start() {
    if (typeof this.fetch !== 'function') throw new Error('Volc managed-agent runtime requires fetch');
    if (!this.apiKey) throw new Error('ARK_API_KEY is required for the Volc managed-agent runtime');
    if (!this.templateSessionId) {
      throw new Error('VIKA_VOLC_SESSION_ID (or SESSION_ID) is required for the Volc managed-agent runtime');
    }
    if (!this.environmentId) {
      throw new Error('VIKA_VOLC_ENVIRONMENT_ID is required for the self-hosted Volc runtime');
    }
    this.stopping = false;
    this.template = await this._getSession(this.templateSessionId);
    this._validateSessionTemplate(this.template);
    if (this.template.environment_id !== this.environmentId) {
      throw new Error('Volc template session does not use the configured self-hosted Environment');
    }
    const environment = await this._getEnvironment(this.environmentId);
    if (environment?.config?.type !== 'self_hosted') {
      throw new Error('configured Volc Environment must have config.type=self_hosted');
    }
    this.policyHashValue = createHash('sha256')
      .update('volc-managed-agent-v2').update('\0')
      .update(this.baseUrl).update('\0')
      .update(String(this.template.agent.id)).update('\0')
      .update(String(this.template.agent.version ?? '')).update('\0')
      .update(String(this.template.environment_id)).update('\0')
      .update(this.transportPolicy).digest('hex');

    const global = this.store.getGlobalSession?.() ?? {};
    const resumable = this.allowResume
      && global.sessionId
      && global.policyHash === this.policyHash
      && global.runtime === this.runtime
      && global.epoch === this.cognitionEpoch;
    let resumedStatus = null;
    if (resumable) {
      const session = await this._getSession(global.sessionId);
      if (session?.status === 'terminated') throw new Error('saved Volc managed-agent session is terminated');
      this.sessionId = global.sessionId;
      resumedStatus = session?.status ?? null;
      this.syncedThroughSeq = global.syncedThroughSeq ?? 0;
      this.deliveredThroughSeq = this.syncedThroughSeq;
    } else {
      await this.store.prepareOVContext?.(this.cognitionEpoch);
      if (this.template.status === 'terminated') throw new Error('configured Volc managed-agent session is terminated');
      const session = await this._createSession();
      this.sessionId = session.id;
    }
    this.connected = true;
    this._persistSession();
    this.transportPolicyInjected = this.store.getManagedSessionPolicy?.(
      this.runtime,
      this.sessionId,
    ) === this.policyHash;
    const poison = this.store.getManagedSessionPoison?.(this.runtime);
    if (poison?.sessionId === this.sessionId && poison.epoch === this.cognitionEpoch) {
      this.sessionPoisoned = true;
    }
    const pending = this.store.getManagedTurn?.(this.runtime);
    if (['running', 'rescheduling', 'rescheduled'].includes(resumedStatus)
      && !(pending?.status === 'ACCEPTED'
        && pending.sessionId === this.sessionId
        && pending.epoch === this.cognitionEpoch
        && pending.expectedUserEventId)) {
      this._poisonSession();
    }
    if (this.allowResume && this.sessionPersistence) this._recoverManagedTurn();
    if (this.sessionPoisoned) void this._ensureFreshSession();
    this._drain();
  }

  _recoverManagedTurn() {
    const pending = this.store.getManagedTurn?.(this.runtime);
    if (!pending) return;
    if (this.sessionPoisoned) {
      const seqs = Array.isArray(pending.inboundSeqs)
        ? pending.inboundSeqs.filter(Number.isSafeInteger)
        : [];
      this.store.clearManagedTurn?.(this.runtime, { sessionId: pending.sessionId });
      const error = new Error('Volc session was quarantined after an ambiguous user.message acceptance');
      queueMicrotask(() => this._failItems(seqs.map((seq) => ({ text: '', seq })), error));
      return;
    }
    const matches = pending.sessionId === this.sessionId
      && pending.epoch === this.cognitionEpoch;
    if (!matches) {
      this.store.clearManagedTurn?.(this.runtime, { sessionId: pending.sessionId });
      return;
    }
    const seqs = Array.isArray(pending.inboundSeqs)
      ? pending.inboundSeqs.filter(Number.isSafeInteger)
      : [];
    const accepted = pending.status === 'ACCEPTED' && pending.expectedUserEventId;
    if (!accepted) {
      this._poisonSession();
      this.store.clearManagedTurn?.(this.runtime, { sessionId: this.sessionId });
      const error = new Error('Volc user.message acceptance was unknown after restart; the request was not resent');
      queueMicrotask(() => this._failItems(seqs.map((seq) => ({ text: '', seq })), error));
      return;
    }
    const conversationKey = this._conversationKey(seqs.map((seq) => ({ text: '', seq })));
    if (!conversationKey || conversationKey !== pending.conversationKey) {
      this._poisonSession();
      this.store.clearManagedTurn?.(this.runtime, {
        sessionId: this.sessionId,
        expectedUserEventId: pending.expectedUserEventId,
      });
      const error = new Error('Volc managed turn recovery lost its event scope');
      queueMicrotask(() => this._failItems(seqs.map((seq) => ({ text: '', seq })), error));
      return;
    }
    this.activeItems = seqs.map((seq) => ({ text: '', seq }));
    this.activeConversationKey = conversationKey;
    this.turnActive = true;
    this.turnCompleting = false;
    this.turnStartedAtMs = Date.now();
    this.modelWorking = true;
    this.modelStartedAtMs = this.turnStartedAtMs;
    this.expectedUserEventId = pending.expectedUserEventId;
    this.turnFenceSeen = false;
    const acceptedThroughSeq = Number(pending.deliveredThroughSeq) || 0;
    const acceptanceAdvanced = this.store.advanceOwnedSessionAcceptance?.({
      runtime: this.runtime,
      authorityToken: this.authorityToken,
      sessionId: this.sessionId,
      epoch: this.cognitionEpoch,
      acceptedThroughSeq,
    }) ?? true;
    if (!acceptanceAdvanced) {
      this._poisonSession();
      this.store.clearManagedTurn?.(this.runtime, {
        sessionId: this.sessionId,
        expectedUserEventId: pending.expectedUserEventId,
      });
      const error = new Error('Volc managed turn recovery no longer owns its accepted session');
      queueMicrotask(() => this._failItems(seqs.map((seq) => ({ text: '', seq })), error));
      return;
    }
    this.syncedThroughSeq = Math.max(this.syncedThroughSeq, acceptedThroughSeq);
    this.deliveredThroughSeq = Math.max(this.deliveredThroughSeq, acceptedThroughSeq);
    this.abortController = new AbortController();
    this._recordActivity('agent.turn_recovered', {
      inboundSeq: seqs.at(-1) ?? null,
      metadata: { runtime: this.runtime, inboundSeqs: seqs },
    });
    void this._runTurn({ deliveredThroughSeq: this.deliveredThroughSeq })
      .catch((error) => this._completeTurn(error));
  }

  _validateSessionTemplate(session) {
    if (!session || typeof session !== 'object') throw new Error('Volc managed-agent session response is invalid');
    if (!session.agent?.id || !session.environment_id) {
      throw new Error('Volc managed-agent template session is missing agent or environment');
    }
  }

  _headers(accept = 'application/json') {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      Accept: accept,
    };
  }

  async _request(path, { method = 'GET', body = null, accept = 'application/json', signal = null } = {}) {
    const controller = signal ? null : new AbortController();
    const timer = controller
      ? setTimeout(() => controller.abort(new Error('request timed out')), this.requestTimeoutMs)
      : null;
    timer?.unref?.();
    try {
      const headers = this._headers(accept);
      if (body !== null) headers['Content-Type'] = 'application/json';
      const response = await this.fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === null ? undefined : JSON.stringify(body),
        signal: signal ?? controller.signal,
      });
      if (!response.ok) {
        let requestId = response.headers.get('x-request-id')
          ?? response.headers.get('x-tt-logid')
          ?? '';
        try {
          const detail = JSON.parse(await response.text());
          requestId ||= String(detail?.request_id ?? detail?.requestId ?? '');
        } catch {}
        throw new VolcApiError(response.status, requestId.replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 128));
      }
      return response;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async _json(path, options) {
    const response = await this._request(path, options);
    try { return await response.json(); } catch { throw new Error('Volc API returned invalid JSON'); }
  }

  _getSession(sessionId) {
    return this._json(`/sessions/${encodeURIComponent(sessionId)}`);
  }

  _getEnvironment(environmentId) {
    return this._json(`/environments/${encodeURIComponent(environmentId)}`);
  }

  async _createSession() {
    const body = {
      agent: Number.isInteger(this.template.agent.version)
        ? { type: 'agent', id: this.template.agent.id, version: this.template.agent.version }
        : this.template.agent.id,
      environment_id: this.template.environment_id,
      title: `vika ${new Date().toISOString()}`,
    };
    if (Array.isArray(this.template.vault_ids) && this.template.vault_ids.length > 0) {
      body.vault_ids = this.template.vault_ids;
    }
    const session = await this._json('/sessions', { method: 'POST', body });
    if (!session?.id || session.status === 'terminated') {
      throw new Error('Volc API did not create a usable managed-agent session');
    }
    return session;
  }

  _conversationKey(items) {
    const resolved = items.map((item) => {
      if (item.seq === null || item.seq === undefined) return null;
      try {
        const event = this.store.inboundBySeq?.(item.seq);
        return event?.scopeKey ?? event?.conversationKey ?? null;
      } catch { return null; }
    });
    if (resolved.some((key) => !key)) return null;
    const keys = new Set(resolved);
    return keys.size === 1 ? [...keys][0] : null;
  }

  deliver(text, seq = null, receiptId = null) {
    return this.deliverBatch([deliveryItem({ text, seq, receiptId })]);
  }

  deliverBatch(items) {
    if (this.stopping || !Array.isArray(items) || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    if (!this.connected || this.sessionPoisoned || this.poisonRecoveryPromise
      || this.turnActive || this.turnCompleting
      || this.rotationPending || this.queue.length > 0) {
      this.queue.push(batch);
      if (this.sessionPoisoned) void this._ensureFreshSession();
      this._drain();
      return true;
    }
    this._startBatch(batch);
    return true;
  }

  tryStartBatch(items) {
    if (this.stopping || !this.connected || this.sessionPoisoned || this.poisonRecoveryPromise
      || this.turnActive || this.turnCompleting
      || this.rotationPending
      || this.queue.length > 0 || !Array.isArray(items) || items.length === 0) return false;
    this._startBatch(items.map(deliveryItem));
    return true;
  }

  whenIdle() {
    if (!this.turnActive && !this.turnCompleting && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  _bodyFor(items) {
    const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
    const beforeSeq = seqs.length > 0 ? Math.min(...seqs) : null;
    const throughSeq = this.store.cognitionJournalBeforeInbound?.(beforeSeq, this.cognitionEpoch) ?? 0;
    let body = items.map((item) => item.text).join('\n');
    let deliveredThroughSeq = this.deliveredThroughSeq;
    if (throughSeq > this.deliveredThroughSeq) {
      const catchup = cognitionHistoryPrefix(
        this.store,
        this.cognitionEpoch,
        throughSeq,
        this.deliveredThroughSeq,
      );
      deliveredThroughSeq = catchup.throughSeq;
      body = `${catchup.text}\n${body}`;
    }
    for (const seq of seqs) {
      deliveredThroughSeq = Math.max(
        deliveredThroughSeq,
        this.store.cognitionJournalSeqForInbound?.(seq, this.cognitionEpoch) ?? 0,
      );
    }
    return { body, seqs, deliveredThroughSeq };
  }

  _startBatch(items) {
    const conversationKey = this._conversationKey(items);
    if (!conversationKey) {
      this._failItems(items, new Error('Volc managed-agent turn requires exactly one event scope'));
      this._drain();
      return;
    }
    let prepared;
    try { prepared = this._bodyFor(items); } catch (error) {
      this._failItems(items, error);
      this._drain();
      return;
    }
    this.activeItems = [...items];
    this.activeConversationKey = conversationKey;
    this.turnActive = true;
    this.turnCompleting = false;
    this.turnStartedAtMs = Date.now();
    this.modelWorking = true;
    this.modelStartedAtMs = this.turnStartedAtMs;
    this.lastMessage = '';
    this.lastMessageEventId = null;
    this.expectedUserEventId = null;
    this.turnFenceSeen = false;
    this.abortController = new AbortController();
    this.toolStarts = new Map();
    this._recordActivity('agent.turn_started', {
      inboundSeq: prepared.seqs.at(-1) ?? null,
      metadata: {
        runtime: this.runtime,
        input: items.map((item) => item.text).join('\n'),
        inboundSeqs: prepared.seqs,
      },
    });
    void this._runTurn(prepared).catch((error) => this._completeTurn(error));
  }

  async _openStream() {
    const streamController = new AbortController();
    this.streamAbortController = streamController;
    if (this.abortController.signal.aborted) streamController.abort();
    else {
      if (this.streamTurnAbort) {
        this.abortController.signal.removeEventListener('abort', this.streamTurnAbort);
      }
      this.streamTurnAbort = () => streamController.abort();
      this.abortController.signal.addEventListener('abort', this.streamTurnAbort, { once: true });
    }
    const timer = setTimeout(
      () => streamController.abort(new Error('Volc SSE connection timed out')),
      this.requestTimeoutMs,
    );
    timer.unref?.();
    try {
      return await this._request(
        `/sessions/${encodeURIComponent(this.sessionId)}/events/stream`,
        { accept: 'text/event-stream', signal: streamController.signal },
      );
    } finally {
      clearTimeout(timer);
    }
  }

  async _history({ snapshotOnly = false } = {}) {
    const events = [];
    let page = null;
    for (let index = 0; index < 100; index += 1) {
      const suffix = page ? `&page=${encodeURIComponent(page)}` : '';
      const payload = await this._json(
        `/sessions/${encodeURIComponent(this.sessionId)}/events?limit=100&order=desc${suffix}`,
      );
      const batch = Array.isArray(payload?.data) ? payload.data : [];
      events.push(...batch);
      // Streams emit only events created after attachment. On first attach one
      // newest-page snapshot is enough to establish the overlap fence. During
      // reconnect, stop once history reaches any event already processed.
      if (snapshotOnly || batch.some((event) => event?.id && this.seenEventIds.has(event.id))) {
        return events.reverse();
      }
      page = payload?.next_page ?? null;
      if (!page) return events.reverse();
    }
    throw new Error('Volc session history exceeded the local pagination limit');
  }

  async _postEvents(events) {
    return await this._json(`/sessions/${encodeURIComponent(this.sessionId)}/events`, {
      method: 'POST',
      body: { events },
    });
  }

  async _runTurn(prepared) {
    let backoff = this.reconnectBaseMs;
    let first = !this.expectedUserEventId;
    let postAttempted = false;
    this.logger.info('volc_turn_timing', { stage: 'run_turn_entered', elapsedMs: Date.now() - this.turnStartedAtMs });
    while (!this.stopping && this.turnActive) {
      let response;
      try {
        if (first) {
          // Post before attaching the stream: the Volc API buffers events for a
          // session until a stream attaches, so posting first does not risk
          // missing output, and it avoids the extra latency of opening a
          // stream against a session with nothing queued yet.
          const t1 = Date.now();
          const history = await this._history({ snapshotOnly: true });
          this.logger.info('volc_turn_timing', { stage: 'history_snapshot', elapsedMs: Date.now() - t1 });
          for (const event of history) if (event?.id) this.seenEventIds.add(event.id);
          try {
            this.store.beginManagedTurn?.({
              runtime: this.runtime,
              authorityToken: this.authorityToken,
              sessionId: this.sessionId,
              epoch: this.cognitionEpoch,
              inboundSeqs: prepared.seqs,
              conversationKey: this.activeConversationKey,
              deliveredThroughSeq: prepared.deliveredThroughSeq,
            });
          } catch (error) {
            error.volcTerminal = true;
            throw error;
          }
          let posted;
          try {
            postAttempted = true;
            const t2 = Date.now();
            // "system.message" is not a valid event type on this API: a
            // fresh session posting only a system.message event gets back
            // 400 InvalidPayload every time (verified directly against the
            // Ark Sessions API outside the daemon). Fold the policy into the
            // user.message text itself instead.
            const messageText = this.transportPolicyInjected
              ? prepared.body
              : `${this.transportPolicy}\n\n${prepared.body}`;
            const events = [{
              type: 'user.message',
              content: [{ type: 'text', text: messageText }],
            }];
            posted = await this._postEvents(events);
            this.logger.info('volc_turn_timing', { stage: 'post_user_message', elapsedMs: Date.now() - t2 });
          } catch (error) {
            error.volcPostUncertain = true;
            throw error;
          }
          const userEvent = Array.isArray(posted?.data)
            ? posted.data.find((event) => event?.type === 'user.message' && event.id)
            : null;
          if (!userEvent?.id) {
            throw new Error('Volc API did not return the accepted user.message event id');
          }
          this.expectedUserEventId = userEvent.id;
          this.turnFenceSeen = false;
          if (!this.transportPolicyInjected) {
            this.store.setManagedSessionPolicy?.(this.runtime, this.sessionId, this.policyHash);
            this.transportPolicyInjected = true;
          }
          let accepted;
          try {
            accepted = this.store.acceptManagedTurn?.(
              this.runtime,
              this.sessionId,
              this.expectedUserEventId,
            );
          } catch (error) {
            error.volcPostUncertain = true;
            throw error;
          }
          const acceptedThroughSeq = Number.isSafeInteger(accepted?.deliveredThroughSeq)
            ? accepted.deliveredThroughSeq
            : prepared.deliveredThroughSeq;
          this.syncedThroughSeq = Math.max(this.syncedThroughSeq, acceptedThroughSeq);
          this.deliveredThroughSeq = Math.max(this.deliveredThroughSeq, acceptedThroughSeq);

          // A fast model can already have produced output by the time we
          // attach a stream. Reconcile against history (which is strictly
          // ordered, unlike the live buffered replay) before attaching, so
          // the fence is found on the first try instead of needing a
          // reconnect once the live stream turns out to start mid-output.
          const tCatchup = Date.now();
          const catchup = await this._history();
          this.logger.info('volc_turn_timing', { stage: 'post_post_history', elapsedMs: Date.now() - tCatchup });
          for (const event of catchup) this._handleEvent(event);
          if (!this.turnActive) return;

          const tStream = Date.now();
          response = await this._openStream();
          this.logger.info('volc_turn_timing', { stage: 'stream_open', elapsedMs: Date.now() - tStream });

          first = false;
        } else {
          const tStream = Date.now();
          response = await this._openStream();
          this.logger.info('volc_turn_timing', { stage: 'stream_open', elapsedMs: Date.now() - tStream });
          const history = await this._history();
          for (const event of history) this._handleEvent(event);
          if (!this.turnActive) return;
        }
        await this._consumeStream(response);
        if (!this.turnActive) return;
        throw new Error('Volc SSE stream ended before session became idle');
      } catch (error) {
        if (this.stopping || !this.turnActive || this.abortController?.signal.aborted) return;
        if ((error instanceof VolcApiError && !error.retryable)
          || (first && postAttempted)
          || error?.volcPostUncertain
          || error?.volcTerminal) {
          if ((first && postAttempted) || error?.volcPostUncertain) this._poisonSession();
          await this._completeTurn(error);
          return;
        }
        this.logger.warn('volc_stream_reconnecting', { error: safe(error), backoffMs: backoff });
        try {
          const history = await this._history();
          for (const event of history) this._handleEvent(event);
          if (!this.turnActive) return;
        } catch (historyError) {
          this.logger.warn('volc_history_reconcile_failed', { error: safe(historyError) });
        }
        await delay(backoff);
        backoff = Math.min(this.reconnectCapMs, backoff * 2);
      }
    }
  }

  async _consumeStream(response) {
    if (!response.body) throw new Error('Volc SSE response has no body');
    let buffer = '';
    let bytes = 0;
    let idleTimer = null;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => this.streamAbortController?.abort(new Error('Volc SSE idle timeout')), this.streamIdleTimeoutMs);
      idleTimer.unref?.();
    };
    resetIdle();
    try {
      const decoder = new TextDecoder();
      for await (const chunk of response.body) {
        resetIdle();
        bytes += chunk.byteLength;
        if (bytes > MAX_EVENT_BYTES) throw new Error('Volc SSE turn exceeded local event limit');
        buffer += decoder.decode(chunk, { stream: true });
        buffer = parseSseFrames(buffer, (event) => this._handleEvent(event));
        if (!this.turnActive) return;
      }
      buffer += decoder.decode();
      parseSseFrames(`${buffer}\n\n`, (event) => this._handleEvent(event));
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      if (this.streamTurnAbort && this.abortController) {
        this.abortController.signal.removeEventListener('abort', this.streamTurnAbort);
      }
      this.streamTurnAbort = null;
    }
  }

  _handleEvent(event) {
    if (!event || typeof event !== 'object') return;
    if (event.id && this.seenEventIds.has(event.id)) return;
    if (!this.turnActive) return;
    if (!this.turnFenceSeen) {
      if (event.type === 'user.message' && event.id === this.expectedUserEventId) {
        this.turnFenceSeen = true;
      } else {
        // Do not mark pre-fence agent output as seen. If the exact user.message
        // echo is missing from this stream before agent output starts, force
        // history reconciliation so that output can be replayed after its
        // causal fence is found. Session-lifecycle events (status/thread
        // status) routinely arrive alongside or before the user.message echo
        // and carry no risk of belonging to a prior turn, so they don't
        // indicate desync and must not trigger a reconnect.
        if (event.type?.startsWith('agent.')) {
          this.streamAbortController?.abort(new Error('Volc stream output arrived before its user.message fence'));
        }
        return;
      }
    }
    if (event.id) {
      this.seenEventIds.add(event.id);
      if (this.seenEventIds.size > 20_000) {
        this.seenEventIds = new Set([...this.seenEventIds].slice(-10_000));
      }
    }
    const inboundSeq = this.activeItems.map((item) => item.seq).filter((seq) => seq != null).at(-1) ?? null;
    if (event.type === 'agent.message') {
      const text = textContent(event);
      if (text) {
        this.lastMessage = text;
        this.lastMessageEventId = event.id ?? null;
        this._recordActivity('agent.note', {
          inboundSeq,
          summary: text,
          metadata: {
            runtime: this.runtime,
            delivery: 'telemetry_only',
          },
        });
      }
    } else if (event.type === 'agent.thinking') {
      this._recordActivity('agent.thinking', {
        inboundSeq,
        metadata: { runtime: this.runtime, thinking: textContent(event) || undefined },
      });
    } else if (event.type === 'agent.tool_use') {
      this.modelWorking = false;
      this.modelStartedAtMs = null;
      this.toolStarts?.set(event.id, { name: String(event.name ?? 'unknown tool'), atMs: Date.now() });
      this._recordActivity('agent.tool_called', {
        inboundSeq,
        summary: String(event.name ?? 'unknown tool'),
        status: 'RUNNING',
        metadata: { runtime: this.runtime, toolId: event.id, toolName: event.name, input: activityPayload(event.input) },
      });
    } else if (event.type === 'agent.tool_result' || event.type === 'user.tool_result') {
      this.modelWorking = true;
      this.modelStartedAtMs = Date.now();
      const started = this.toolStarts?.get(event.tool_use_id);
      this.toolStarts?.delete(event.tool_use_id);
      const output = activityPayload(event.content);
      this._recordActivity('agent.tool_completed', {
        inboundSeq,
        status: event.is_error === true ? 'FAILED' : 'COMPLETED',
        summary: started?.name,
        metadata: {
          runtime: this.runtime,
          toolId: event.tool_use_id,
          toolName: started?.name,
          ...(started ? { durationMs: Date.now() - started.atMs } : {}),
          ...(event.is_error === true ? { error: output } : { output }),
        },
      });
    } else if (event.type === 'session.status_idle' || event.type === 'status_idle') {
      const stopType = typeof event.stop_reason === 'string'
        ? event.stop_reason
        : event.stop_reason?.type;
      if (stopType === 'requires_action') {
        this._recordActivity('agent.tool_waiting', {
          inboundSeq,
          metadata: { runtime: this.runtime },
        });
      } else if (stopType !== 'end_turn') {
        void this._completeTurn(new Error(`Volc session stopped with ${stopType ?? 'unknown stop reason'}`));
      } else {
        void this._completeTurn(null);
      }
    } else if (event.type === 'session.status_terminated' || event.type === 'status_terminated'
      || event.type === 'session.error' || event.type === 'error') {
      void this._completeTurn(new Error(`Volc session terminated: ${event.error?.message ?? event.message ?? event.type}`));
    }
  }

  _completeTurn(error = null) {
    if (!this.turnActive) return this.completionPromise ?? Promise.resolve();
    const completion = this._completeTurnInner(error);
    this.completionPromise = completion;
    void completion.finally(() => {
      if (this.completionPromise === completion) this.completionPromise = null;
    }).catch(() => {});
    return completion;
  }

  async _completeTurnInner(error = null) {
    this.turnActive = false;
    this.turnCompleting = true;
    this.abortController?.abort();
    this.abortController = null;
    this.streamAbortController?.abort();
    this.streamAbortController = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    const completionError = error;

    const items = this.activeItems.splice(0);
    const seqs = [...new Set(items.map((item) => item.seq).filter((seq) => seq != null))];
    const receipts = receiptIds(items);
    this.activeConversationKey = null;
    const turnStartedAtMs = this.turnStartedAtMs;
    this.turnStartedAtMs = null;
    if (!completionError) {
      this.syncedThroughSeq = Math.max(this.syncedThroughSeq, this.deliveredThroughSeq);
      this._persistSession();
    } else {
      this.deliveredThroughSeq = this.syncedThroughSeq;
    }
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: completionError ? 'failed' : 'completed',
      summary: completionError ? safe(completionError) : undefined,
      metadata: {
        runtime: this.runtime,
        inboundSeqs: seqs,
        ...(turnStartedAtMs ? { durationMs: Date.now() - turnStartedAtMs } : {}),
      },
    });
    if (completionError && this.onTurnFailed) {
      try { this.onTurnFailed(withReceiptIds({ inboundSeqs: seqs, error: safe(completionError) }, receipts)); } catch {}
    }
    if (!completionError && this.onTurnSucceeded) {
      try { this.onTurnSucceeded(withReceiptIds({ inboundSeqs: seqs }, receipts)); } catch {}
    }
    try {
      this.store.clearManagedTurn?.(this.runtime, {
        sessionId: this.sessionId,
        ...(this.expectedUserEventId ? { expectedUserEventId: this.expectedUserEventId } : {}),
      });
    } catch (clearError) {
      this.logger.warn('volc_managed_turn_clear_failed', { error: safe(clearError) });
    }
    this.turnCompleting = false;
    this.lastMessage = '';
    this.lastMessageEventId = null;
    this.expectedUserEventId = null;
    this.turnFenceSeen = false;
    if (this.sessionPoisoned) void this._ensureFreshSession();
    else this._drain();
    this._notifyAvailable();
  }

  _failItems(items, error) {
    const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
    const receipts = receiptIds(items);
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: 'failed',
      summary: safe(error),
      metadata: { runtime: this.runtime, inboundSeqs: seqs, terminal: error instanceof CognitionLimitError },
    });
    if (this.onTurnFailed) {
      try { this.onTurnFailed(withReceiptIds({ inboundSeqs: seqs, error: safe(error) }, receipts)); } catch {}
    }
  }

  _drain() {
    if (this.stopping || !this.connected || this.sessionPoisoned || this.poisonRecoveryPromise
      || this.turnActive || this.turnCompleting
      || this.rotationPending) return;
    const next = this.queue.shift();
    if (next) this._startBatch(next);
  }

  async interruptTurn() {
    this.rotationPending = true;
    if (this.turnCompleting && this.completionPromise) {
      try { await this.completionPromise; } catch {}
    }
    if (!this.turnActive && !this.turnCompleting) return;
    const items = this.activeItems.splice(0);
    const inboundSeqs = [...new Set(items.map((item) => item.seq).filter((seq) => seq != null))];
    const receipts = receiptIds(items);
    if (this.turnActive) {
      try { await this._postEvents([{ type: 'user.interrupt' }]); } catch (error) {
        this.logger.warn('volc_interrupt_failed', { error: safe(error) });
      }
    }
    // The interrupt POST itself yields. An idle event can start completion in
    // that window, so fence the second side of the await as well.
    if (this.turnCompleting && this.completionPromise) {
      try { await this.completionPromise; } catch {}
    }
    if (!this.turnActive && !this.turnCompleting) return;
    this.turnActive = false;
    this.turnCompleting = false;
    this.abortController?.abort();
    this.abortController = null;
    this.streamAbortController?.abort();
    this.streamAbortController = null;
    this.activeConversationKey = null;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.store.clearManagedTurn?.(this.runtime, {
      sessionId: this.sessionId,
      ...(this.expectedUserEventId ? { expectedUserEventId: this.expectedUserEventId } : {}),
    });
    this.expectedUserEventId = null;
    this.turnFenceSeen = false;
    if ((inboundSeqs.length > 0 || receipts.length > 0) && this.onTurnAbandoned) {
      try { this.onTurnAbandoned(withReceiptIds({ inboundSeqs }, receipts)); } catch {}
    }
    this._resolveIdleWaiters();
  }

  async prepareSessionRotation() {
    if (this.turnActive || this.turnCompleting) throw new Error('Volc session rotation requires an idle runtime');
    return await this._createSession();
  }

  commitSessionRotation(session, { persist = true } = {}) {
    if (!session?.id) throw new Error('prepared Volc session is invalid');
    this.sessionId = session.id;
    this.syncedThroughSeq = 0;
    this.deliveredThroughSeq = 0;
    this.seenEventIds.clear();
    this.transportPolicyInjected = false;
    this.expectedUserEventId = null;
    this.turnFenceSeen = false;
    this.sessionPoisoned = false;
    this._clearPoisonRecoveryTimer();
    this.poisonRecoveryBackoffMs = this.reconnectBaseMs;
    this.rotationPending = false;
    if (persist) this._persistSession();
    this._drain();
    return this.sessionId;
  }

  async rotateSession({ resume = false } = {}) {
    if (resume) return this.sessionId;
    const session = await this.prepareSessionRotation();
    return this.commitSessionRotation(session);
  }

  cancelSessionRotation() {
    this.rotationPending = false;
    this._drain();
    this._notifyAvailable();
  }

  _poisonSession() {
    if (!this.sessionId) return;
    this.sessionPoisoned = true;
    try {
      this.store.poisonManagedSession?.(
        this.runtime,
        this.sessionId,
        this.cognitionEpoch,
      );
    } catch (error) {
      this.logger.warn('volc_session_quarantine_persist_failed', { error: safe(error) });
    }
  }

  _clearPoisonRecoveryTimer() {
    if (this.poisonRecoveryTimer) clearTimeout(this.poisonRecoveryTimer);
    this.poisonRecoveryTimer = null;
  }

  _schedulePoisonRecovery() {
    if (this.stopping || !this.sessionPoisoned || this.poisonRecoveryTimer) return;
    const waitMs = this.poisonRecoveryBackoffMs;
    this.poisonRecoveryBackoffMs = Math.min(this.reconnectCapMs, waitMs * 2);
    this.poisonRecoveryTimer = setTimeout(() => {
      this.poisonRecoveryTimer = null;
      void this._ensureFreshSession();
    }, waitMs);
    this.poisonRecoveryTimer.unref?.();
  }

  _ensureFreshSession() {
    if (!this.sessionPoisoned || this.stopping || this.turnActive || this.turnCompleting) {
      return Promise.resolve();
    }
    if (this.poisonRecoveryPromise) return this.poisonRecoveryPromise;
    this._poisonSession();
    const expectedSessionId = this.sessionId;
    const expectedEpoch = this.cognitionEpoch;
    const expectedAuthorityToken = this.authorityToken;
    const replacement = (async () => {
      const session = await this._createSession();
      if (this.stopping || !this.sessionPoisoned
        || this.sessionId !== expectedSessionId
        || this.cognitionEpoch !== expectedEpoch
        || this.authorityToken !== expectedAuthorityToken) return;
      if (!this.store.replacePoisonedManagedSession) {
        throw new Error('managed session quarantine store support is unavailable');
      }
      const cognition = this.store.replacePoisonedManagedSession({
        runtime: this.runtime,
        authorityToken: expectedAuthorityToken,
        nextAuthorityToken: randomUUID(),
        expectedSessionId,
        sessionId: session.id,
        policyHash: this.policyHash,
        expectedEpoch,
      });
      this.sessionId = session.id;
      this.cognitionEpoch = cognition.epoch;
      this.authorityToken = cognition.authorityToken;
      this.syncedThroughSeq = 0;
      this.deliveredThroughSeq = 0;
      this.seenEventIds.clear();
      this.expectedUserEventId = null;
      this.turnFenceSeen = false;
      this.transportPolicyInjected = false;
      this.sessionPoisoned = false;
      this.poisonRecoveryBackoffMs = this.reconnectBaseMs;
      this._clearPoisonRecoveryTimer();
    })();
    this.poisonRecoveryPromise = replacement;
    void replacement.catch((error) => {
      const cognition = this.store.getCognitionState?.();
      if (this.sessionPoisoned && this.sessionId === expectedSessionId
        && cognition?.runtime === this.runtime && cognition?.epoch === expectedEpoch
        && cognition?.authorityToken === expectedAuthorityToken) {
        this.logger.warn('volc_session_quarantine_replace_failed', { error: safe(error) });
        this._schedulePoisonRecovery();
      }
    }).finally(() => {
      if (this.poisonRecoveryPromise === replacement) this.poisonRecoveryPromise = null;
      if (!this.sessionPoisoned) {
        this._drain();
        this._notifyAvailable();
      }
    });
    return replacement;
  }

  async setModel(model) {
    if (model && !VOLC_RUNTIME.models.includes(model)) {
      throw new Error(`unsupported Volc managed-agent model selector: ${model}`);
    }
  }
  setEffort(effort) {
    if (!VOLC_RUNTIME.efforts.includes(effort)) {
      throw new Error('Volc managed-agent effort is controlled by the remote Agent');
    }
  }
  async listModels() { return staticModelEntries(VOLC_RUNTIME.id); }

  _persistSession() {
    if (!this.sessionPersistence || !this.sessionId) return;
    try {
      if (this.store.publishOwnedSession) {
        const published = this.store.publishOwnedSession({
          runtime: this.runtime,
          authorityToken: this.authorityToken,
          sessionId: this.sessionId,
          policyHash: this.policyHash,
          epoch: this.cognitionEpoch,
          acceptedThroughSeq: this.syncedThroughSeq,
          sessionReady: true,
        });
        if (!published) this.logger.info('volc_session_publish_stale');
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: true,
      });
    } catch (error) {
      this.logger.warn('volc_session_persist_failed', { error: safe(error) });
    }
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity?.({
        eventKey: fields.eventId
          ? `volc:${this.sessionId}:${fields.eventId}`
          : `volc:${this.sessionId ?? 'na'}:${kind}:${this.activitySeq}`,
        kind,
        summary: fields.summary,
        status: fields.status,
        inboundSeq: fields.inboundSeq,
        metadata: fields.metadata,
      });
    } catch (error) {
      this.logger.warn('activity_persist_failed', { kind, error: safe(error) });
    }
  }

  _resolveIdleWaiters() {
    if (this.turnActive || this.turnCompleting || this.queue.length > 0) return;
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
  }

  _notifyAvailable() {
    if (this.turnActive || this.turnCompleting || this.rotationPending
      || this.sessionPoisoned || this.poisonRecoveryPromise
      || this.queue.length > 0 || !this.connected) return;
    this._resolveIdleWaiters();
    try { this.onAvailable?.(); } catch (error) {
      this.logger.warn('volc_available_callback_failed', { error: safe(error) });
    }
  }

  async stop() {
    this.stopping = true;
    this._clearPoisonRecoveryTimer();
    const queued = this.queue.flat();
    this.queue = [];
    if (queued.length > 0) this._failItems(queued, new Error('Volc runtime stopped before queued work started'));
    if (this.turnActive || this.turnCompleting) await this.interruptTurn();
    this.connected = false;
    this._resolveIdleWaiters();
  }
}
