// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { formatInboundLine, resolveReplyTarget, eventDeliveryItem } from './runtime-input.mjs';
import { safeChatLookupError, safeError } from './logger.mjs';

// After one automatic claude → codex fallback, no further automatic switch is
// attempted within this window (prevents switch churn if the user moves back).
export const MODEL_FALLBACK_COOLDOWN_MS = 5 * 60 * 1_000;

// What happens when a model turn fails: one shot at the automatic
// claude → codex fallback, then one failure notice per conversation until the
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
    // In-memory: scopeKey -> { summary, atMs } of the delivered failure notice.
    // A conversation that was told once stays quiet, whatever the error or
    // mention, until any turn succeeds (see onTurnSucceeded). Not persisted: a
    // restart may report once more.
    this.failureNoticeMutes = new Map();
    // Automatic claude → codex fallback state (see tryModelFallback).
    this.fallbackInProgress = false;
    this.lastFallbackAtMs = 0;
  }

  // Any successful turn means the runtime has recovered (a usage limit or a
  // broken model is runtime-wide), so every conversation may be told again.
  onTurnSucceeded() {
    this.failureNoticeMutes.clear();
  }

  // A failed turn first gets one shot at the automatic claude → codex fallback;
  // when that is not applicable (or the switch itself fails) the normal failure
  // notice goes out instead.
  async onTurnFailed(info) {
    try {
      if (await this.tryModelFallback(info)) return true;
    } catch (error) {
      this.logger.warn('model_fallback_failed', { error: safeError(error) });
    }
    await this.notifyTurnFailure(info);
    return false;
  }

  // Claude CLI/API errors auto-switch to the codex fallback model (hard switch)
  // and retry the failed turn by redelivering its inbound rows. Loop guard: the
  // retry runs on the codex runtime, so its failure can never fall back again;
  // a cooldown blocks repeated automatic switches if the user moves back to
  // claude while it is still broken. Returns true when the fallback owns the
  // failure (no separate failure notice is sent).
  async tryModelFallback({ inboundSeqs, error }) {
    if (this.isStopping() || this.store.getCognitionState().runtime !== 'claude') return false;
    if (this.fallbackInProgress) return false;
    const now = Date.now();
    if (now - this.lastFallbackAtMs < MODEL_FALLBACK_COOLDOWN_MS) return false;
    const seqs = (Array.isArray(inboundSeqs) ? inboundSeqs : []).filter((seq) => seq != null);
    const rows = [];
    for (const seq of seqs) {
      let row = null;
      try { row = this.store.inboundBySeq(seq); } catch { /* ignore */ }
      if (row?.eventId) rows.push(row);
    }
    if (rows.length === 0) return false; // nothing to retry on the fallback model
    const model = this.config.fallbackModel || 'gpt-5.6-sol';
    const errorSummary = safeError(error);
    this.fallbackInProgress = true;
    this.lastFallbackAtMs = now;
    try {
      const result = await this.runtime.transition({
        kind: 'select-runtime', runtime: 'codex', model, expectedRuntime: 'claude',
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
      summary: `claude 失败，已自动切换到 ${model} 重试`,
      status: 'SWITCHED',
      conversationKey: rows.at(-1)?.conversationKey,
      inboundSeq: seqs.at(-1),
      metadata: { model, error: errorSummary, inboundSeqs: seqs },
    });
    const last = rows.at(-1);
    const notice = `⚠️ 因 ${errorSummary} 报错，已自动切换到 ${model} 重试`.slice(0, 600);
    await this.actions.replyToEvent(last, notice, 'fallback-notice');
    // Redeliver the failed turn to the fallback runtime. A failure of the retry
    // surfaces through the normal onTurnFailed path (runtime is codex now).
    this.runtime.submit(rows.map((row) => eventDeliveryItem(row, row.seq, {
      text: formatInboundLine(row, null, {
        owner: this.isOwner(row), mention: this.mentionTarget(row), replyTo: resolveReplyTarget(this.store, row),
      }),
      seq: row.seq,
    })));
    return true;
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
    const scopeKey = inbound?.scopeKey ?? inbound?.conversationKey;
    if (scopeKey && this.failureNoticeMutes.has(scopeKey)) {
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
    const notice = `⚠️ 这一轮处理失败，没有生成回复：${error}`.slice(0, 600)
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
