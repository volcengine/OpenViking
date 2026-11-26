// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { formatInboundLine, resolveReplyTarget, eventDeliveryItem } from './runtime-input.mjs';
import { safeChatLookupError, safeError } from './logger.mjs';
import { classifyModelError, resolveRuntimeModelTarget } from './runtime-catalog.mjs';

// After one automatic fallback, no further automatic switch is
// attempted within this window (prevents switch churn if the user moves back).
export const MODEL_FALLBACK_COOLDOWN_MS = 5 * 60 * 1_000;

export function classifyRuntimeFailure(error, modelError) {
  const message = String(error?.message ?? error ?? '');
  if (/\b(?:cancelled|canceled|abort|permission denied|unauthorized|forbidden|invalid api key|tool (?:error|failed)|business error)\b/i.test(message)) {
    return { blocked: true, recoverable: false };
  }
  const classified = modelError || classifyModelError(error);
  if (['no-permission', 'effort'].includes(classified.kind)
    || [400, 401, 403, 404, 422].includes(classified.status)) {
    return { blocked: true, recoverable: false };
  }
  const recoverable = classified.retryable
    || /\b(?:timeout|timed out|rate[_ ]limit(?:ed)?|quota exceeded|too many requests|you(?:'|’)ve hit your (?:\d+[- ]hour|weekly) limit|(?:\d+[- ]hour|weekly|usage) limit(?: reached| exceeded)?|overloaded|temporarily unavailable|service unavailable|network|connection (?:lost|reset|refused)|ECONNRESET|ETIMEDOUT|bad gateway|upstream|empty (?:stream|response)|3083)\b|空流/i.test(message);
  return { blocked: false, recoverable };
}

function affectedInput(event) {
  return [
    `source=${event.source ?? event.connectorInstance ?? 'unknown'}`,
    event.chatId && `chat=${event.chatId}`,
    event.threadId && `thread=${event.threadId}`,
    event.messageId && `msg=${event.messageId}`,
    event.eventId && `event=${event.eventId}`,
  ].filter(Boolean).join(' ');
}

// What happens when a model turn fails: one bounded backup window, then one
// failure notice per conversation until the
// runtime recovers, so a failure is reported once and never repeated.
export class FailurePolicy {
  constructor({
    config,
    store,
    actions,
    logger,
    runtime,
    recordActivity,
    isOwner = () => false,
    mentionTarget = () => null,
    isStopping = () => false,
    clock = () => Date.now(),
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    this.config = config;
    this.store = store;
    this.actions = actions;
    this.logger = logger;
    this.runtime = runtime;
    this.recordActivity = recordActivity;
    this.isOwner = isOwner;
    this.mentionTarget = mentionTarget;
    this.isStopping = isStopping;
    this.clock = clock;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.primaryRuntime = config.primaryRuntime
      || (config.initialModel ? resolveRuntimeModelTarget(config.initialModel).runtime : 'claude');
    this.backupWindow = null;
    this.returnTimer = null;
    // In-memory: chatId (or scope for non-chat events) -> { summary, atMs } of
    // the delivered failure notice. A chat that was told once stays quiet,
    // whatever the error, until any turn succeeds (see onTurnSucceeded); an
    // @-mention of the bot still gets a notice. Not persisted: a restart may
    // report once more.
    this.failureNoticeMutes = new Map();
    // Automatic runtime fallback state (see tryModelFallback).
    this.fallbackInProgress = false;
    this.lastFallbackAtMs = 0;
  }

  // Any successful turn means the runtime has recovered (a usage limit or a
  // broken model is runtime-wide), so every conversation may be told again.
  onTurnSucceeded() {
    this.failureNoticeMutes.clear();
  }

  // A failed turn first gets one shot at the configured fallback;
  // when that is not applicable (or the switch itself fails) the normal failure
  // notice goes out instead.
  async onTurnFailed(info) {
    try {
      if (await this.tryModelFallback(info)) return true;
    } catch (error) {
      this.logger.warn('model_fallback_failed', { error: safeError(error) });
    }
    if (classifyRuntimeFailure(info.error, info.modelError).recoverable) {
      await this.notifyRuntimeFailure(info);
      return false;
    }
    await this.notifyTurnFailure(info);
    return false;
  }

  stop() {
    if (this.returnTimer !== null) this.clearTimer(this.returnTimer);
    this.returnTimer = null;
    this.backupWindow?.release?.();
    this.backupWindow = null;
  }

  // Explicit selections, including the currently active backup, take ownership
  // from the timer. Automatic switches do not change the preferred primary.
  onRuntimeSelected(runtime) {
    this.stop();
    this.primaryRuntime = runtime;
  }

  async returnToPrimary(window) {
    if (window.returning) return;
    this.returnTimer = null;
    if (this.isStopping() || this.backupWindow !== window) return;
    const snapshot = this.runtime.snapshot?.();
    if (this.store.getCognitionState().runtime !== window.backupRuntime
      || (snapshot && snapshot.revision !== window.revision)) {
      this.stop();
      return;
    }
    // Close ingress now, not after whenIdle resolves. The transports can finish
    // queued work; later events remain in the Harness/Inbox's durable queue.
    window.release = this.runtime.holdAdmission?.();
    window.returning = true;
    try {
      const result = await this.runtime.transition({
        kind: 'select-runtime', runtime: window.primaryRuntime, model: window.primaryModel,
        expectedRuntime: window.backupRuntime, expectedRevision: window.revision,
        persistPreference: false, whenIdle: true,
      });
      if (result.ok === false) {
        if (['stale-snapshot', 'runtime-changed'].includes(result.reason)) { this.stop(); return; }
        throw new Error(result.reason || 'runtime return failed');
      }
      this.recordActivity('agent.fallback', {
        status: 'RESTORED', summary: `已恢复主 runtime ${window.primaryRuntime}`,
        metadata: { runtime: window.primaryRuntime },
      });
      this.stop();
    } catch (error) {
      if (this.isStopping() || this.backupWindow !== window) return;
      // Do not spend more backup quota after the deadline when primary cannot
      // start. An operator runtime selection or restart can release the queue.
      window.returning = false;
      this.logger.warn('model_fallback_return_failed', { error: safeError(error) });
      this.recordActivity('agent.fallback', { status: 'FAILED', summary: safeError(error),
        metadata: { runtime: window.primaryRuntime, phase: 'return', admission: 'held' } });
      try { await this.actions.notifyOwnerForEvent(window.last,
        `主 runtime 恢复失败，新请求已暂停：${safeError(error)}`.slice(0, 600), 'fallback-return-failed'); } catch {}
    }
  }

  // Retry the failed turn once on the backup, retaining the primary preference.
  // Backup failures cannot recurse, and a cooldown bounds repeated windows.
  async tryModelFallback({ inboundSeqs, error, modelError }) {
    const primaryRuntime = this.primaryRuntime;
    const backupRuntime = this.config.fallbackRuntime || 'codex';
    if (this.isStopping() || primaryRuntime === backupRuntime
      || this.store.getCognitionState().runtime !== primaryRuntime) return false;
    // Transports also report sanitized strings. Only recognizable model/network
    // outages may activate a configurable route; tools, cancellation and denied
    // credentials must not be retried through a different runtime.
    const failure = classifyRuntimeFailure(error, modelError);
    if (failure.blocked) return false;
    const legacyClaude = !this.config.primaryRuntime && !this.config.initialModel && primaryRuntime === 'claude';
    if (!legacyClaude && !failure.recoverable) return false;
    if (this.fallbackInProgress) return false;
    const now = this.clock();
    if (now - this.lastFallbackAtMs < MODEL_FALLBACK_COOLDOWN_MS) return false;
    const seqs = (Array.isArray(inboundSeqs) ? inboundSeqs : []).filter((seq) => seq != null);
    const rows = [];
    for (const seq of seqs) {
      let row = null;
      try { row = this.store.inboundBySeq(seq); } catch { /* ignore */ }
      if (row?.eventId) rows.push(row);
    }
    if (rows.length === 0) return false; // nothing to retry on the fallback model
    const model = this.config.fallbackModel || (backupRuntime === 'codex' ? 'gpt-5.6-sol' : undefined);
    const primaryModel = this.runtime.model;
    this.stop();
    const errorSummary = safeError(error);
    this.fallbackInProgress = true;
    this.lastFallbackAtMs = now;
    try {
      const result = await this.runtime.transition({
        kind: 'select-runtime', runtime: backupRuntime, model, expectedRuntime: primaryRuntime,
        persistPreference: false,
      });
      if (result.ok === false) throw new Error(result.reason || 'runtime switch failed');
    } catch (switchError) {
      this.recordActivity('agent.fallback', {
        summary: safeError(switchError),
        status: 'FAILED',
        conversationKey: rows.at(-1)?.conversationKey,
        inboundSeq: seqs.at(-1),
        metadata: { model, error: errorSummary },
      });
      this.logger.warn('model_fallback_switch_failed', { error: safeError(switchError) });
      return false; // fall through to the normal failure notice
    } finally {
      this.fallbackInProgress = false;
    }
    this.recordActivity('agent.fallback', {
      summary: `${primaryRuntime} 失败，已自动切换到 ${model || backupRuntime} 重试`,
      status: 'SWITCHED',
      conversationKey: rows.at(-1)?.conversationKey,
      inboundSeq: seqs.at(-1),
      metadata: { model, error: errorSummary, inboundSeqs: seqs },
    });
    const last = rows.at(-1);
    const returnAfterMs = this.config.fallbackReturnAfterMs ?? 600_000;
    const window = { primaryRuntime, primaryModel, backupRuntime, last,
      revision: this.runtime.snapshot?.().revision, returning: false,
      deadline: this.clock() + returnAfterMs };
    this.backupWindow = window;
    const notice = (`${primaryRuntime} 运行异常，已自动切换到 ${model || backupRuntime} 重试。\n`
      + `异常：${errorSummary}\n受影响消息：${affectedInput(last)}`).slice(0, 1200);
    // Preserve input identities and the shared action ledger across runtimes.
    // This is a turn retry, not a promise of exactly-once arbitrary shell tools.
    const admitted = this.runtime.submit(rows.map((row) => eventDeliveryItem(row, row.seq, {
      text: formatInboundLine(row, null, {
        owner: this.isOwner(row), mention: this.mentionTarget(row), replyTo: resolveReplyTarget(this.store, row),
      }),
      seq: row.seq,
    })));
    if (returnAfterMs > 0) {
      this.returnTimer = this.setTimer(() => { void this.returnToPrimary(window); }, Math.max(1, window.deadline - this.clock()));
      this.returnTimer?.unref?.();
    }
    if (admitted?.accepted === false) throw new Error(`fallback retry rejected: ${admitted.reason}`);
    // Delivery cannot delay retry admission or the start of the return clock.
    try { await this.actions.notifyOwnerForEvent(last, notice, 'fallback-notice'); } catch (noticeError) {
      this.logger.warn('model_fallback_notice_failed', { error: safeError(noticeError) });
    }
    return true;
  }

  async notifyRuntimeFailure({ inboundSeqs, error }) {
    if (this.isStopping()) return;
    const seqs = Array.isArray(inboundSeqs) ? inboundSeqs : [];
    const seq = seqs.at(-1) ?? null;
    const inbound = seq === null || seq === undefined ? null : this.store.inboundBySeq(seq);
    const errorSummary = safeError(error);
    this.recordActivity('agent.failure_notice', {
      summary: errorSummary,
      status: inbound ? 'NOTIFYING_OWNER' : 'NO_TARGET',
      conversationKey: inbound?.conversationKey,
      inboundSeq: seq ?? undefined,
      metadata: { notify: 'owner' },
    });
    if (!inbound) return;
    const notice = (`Runtime 处理失败，未向原用户暴露异常。\n异常：${errorSummary}\n`
      + `受影响消息：${affectedInput(inbound)}`).slice(0, 1200);
    const sent = await this.actions.notifyOwnerForEvent(inbound, notice, 'runtime-failure-notice');
    if (!sent.ok) {
      this.recordActivity('agent.failure_notice', {
        summary: safeChatLookupError(sent.error),
        status: 'FAILED',
        conversationKey: inbound.conversationKey,
        inboundSeq: seq ?? undefined,
        metadata: { notify: 'owner' },
      });
    }
  }

  // A failed turn consumes its messages without replying; tell the sender
  // programmatically. Best effort: reply to the last inbound message of the
  // failed turn. A conversation gets this notice once; later failures there are
  // recorded as activity only until the runtime recovers.
  async notifyTurnFailure({ inboundSeqs, error }) {
    if (this.isStopping()) return;
    const seqs = Array.isArray(inboundSeqs) ? inboundSeqs : [];
    const seq = seqs.at(-1) ?? null;
    const inbound = seq === null || seq === undefined ? null : this.store.inboundBySeq(seq);
    const errorSummary = safeError(error);
    // Mention bypass: an inbound of the failed turn that @-mentions the bot
    // always gets the notice. When the Connector does not know the bot's id,
    // any @-mention counts.
    const mentioned = seqs.some((itemSeq) => {
      const row = itemSeq === seq ? inbound : this.store.inboundBySeq(itemSeq);
      const target = row ? this.mentionTarget(row) : null;
      return target ? target !== 'other' : Array.isArray(row?.mentions) && row.mentions.length > 0;
    });
    // Keyed by chat, not by thread: one notice per group (or direct chat).
    const scopeKey = inbound?.chatId ?? inbound?.scopeKey ?? inbound?.conversationKey;
    if (scopeKey && !mentioned && this.failureNoticeMutes.has(scopeKey)) {
      this.recordActivity('agent.failure_notice', {
        summary: errorSummary,
        status: 'MUTED',
        conversationKey: inbound.conversationKey,
        inboundSeq: seq ?? undefined,
        metadata: { reason: 'already_notified_until_recovery' },
      });
      return;
    }
    const chatMessage = inbound?.chatId && inbound?.messageId && inbound.kind?.startsWith('message.');
    const notice = `⚠️ 这一轮处理异常：${error}`.slice(0, 600)
      + (chatMessage ? '\n（消息不会自动重试；可再发一次，或用 /effort、/model 调整后重试）'
        : '\n（事件不会自动重试，请检查运行状态后决定是否重试。）');
    this.recordActivity('agent.failure_notice', {
      summary: errorSummary,
      status: inbound?.replyRoute ? 'NOTIFYING' : 'NO_TARGET',
      conversationKey: inbound?.conversationKey,
      inboundSeq: seq ?? undefined,
    });
    if (!inbound?.replyRoute) return;
    const sent = await this.actions.replyToEvent(inbound, notice, 'failure-notice');
    if (!sent.ok) {
      this.recordActivity('agent.failure_notice', {
        summary: safeChatLookupError(sent.error),
        status: 'FAILED',
        conversationKey: inbound.conversationKey,
        inboundSeq: seq ?? undefined,
      });
      return;
    }
    // Only a delivered notice arms the mute; a failed send stays eligible to retry.
    if (scopeKey) {
      this.failureNoticeMutes.set(scopeKey, { summary: errorSummary, atMs: Date.now() });
    }
  }
}
