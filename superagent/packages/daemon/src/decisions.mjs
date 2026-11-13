// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { opaqueRef, safeError } from './logger.mjs';

// Reminder counts by seq in this process. A restart forgets them: the ledger
// is durable, the reminders are best effort.
const REMINDER_HISTORY_LIMIT = 10_000;
// The failure notice for an input a loop-stopped turn held twice.
const LOOP_STOPPED_ERROR = '模型反复做没有效果的调用，这一轮被 daemon 中止了';

// A switch that only means something to a runtime with Core agent tools
// (explicitSilence: stay_silent and the turn-end check; stagedReplies: interim
// messages). On the others it is inert, so say so once per runtime instead of
// letting an operator believe it is on. Returns whether it warned.
function warnIfInert(on, runtime, logger, message, what) {
  if (!on) return false;
  if (getRuntimeDefinition(runtime)?.capabilities.agentTools === 'core') return false;
  logger.warn(message, {
    runtime,
    reason: `runtime has no core agent tools; ${what} is not applied for this runtime`,
  });
  return true;
}

export function warnIfSilenceInert(config, runtime, logger) {
  return warnIfInert(config.explicitSilence, runtime, logger, 'explicit_silence_not_applied', 'explicit silence');
}

export function warnIfStagedRepliesInert(config, runtime, logger) {
  return warnIfInert(config.stagedReplies, runtime, logger, 'staged_replies_not_applied', 'staged replies');
}

// Whether an unsettled input needs a decision: every chat message on every IM
// (direct, group, thread, card answer), as zouk asks a send or no_reply of
// every inbound message. A reminder costs a turn only when the model missed
// one. Non-chat events (mail) stay optional: each is its own subject scope and
// one owner notice may rightly cover several, so a mail without its own
// decision is not reliably a miss, and nobody is waiting in a conversation.
function expected(row) {
  return Boolean(row.chatId) && Boolean(row.kind?.startsWith('message.'));
}

// Turn-end check that every input a successful turn consumed ended in a
// decision: a final delivery or stay_silent (the ledger in `decisions`). It
// never judges the decision, never sends anything, and never touches the
// inbound ledger, freshness or the outbox. An expected input with no decision
// gets up to `decisionReminderLimit` reminder lines (same event= and seq,
// undecided=1). The seq lets each reminder turn settle again without moving
// the freshness fence or counting as a new input. Later misses are recorded.
export class DecisionPolicy {
  constructor({
    store, harness, logger, recordActivity, explicitSilence = true, decisionReminderLimit = 3,
    notifyFailure = null, isStopping = () => false,
  }) {
    Object.assign(this, { store, harness, logger, recordActivity, explicitSilence, decisionReminderLimit, notifyFailure, isStopping });
    this.nudged = new Map();
    this.recovered = new Set();
  }

  onTurnSucceeded({ inboundSeqs = [] } = {}) {
    if (this.isStopping() || !this.explicitSilence) return;
    const runtime = this.store.getCognitionState().runtime;
    if (getRuntimeDefinition(runtime)?.capabilities.agentTools !== 'core') return;
    try {
      this.settle({ inboundSeqs, runtime });
    } catch (error) {
      this.logger.warn('decision_settlement_failed', { error: safeError(error) });
    }
  }

  // The newest accepted input of each scope among a turn's seqs.
  newestPerScope(inboundSeqs) {
    const scopes = new Map();
    for (const seq of Array.isArray(inboundSeqs) ? inboundSeqs : []) {
      if (seq === null || seq === undefined) continue;
      const row = this.store.inboundBySeq(seq);
      if (row?.status !== 'DONE' || !row.scopeKey) continue;
      const current = scopes.get(row.scopeKey);
      if (!current || row.seq > current.seq) scopes.set(row.scopeKey, row);
    }
    return scopes;
  }

  settle({ inboundSeqs, runtime }) {
    const scopes = this.newestPerScope(inboundSeqs);
    const counts = { delivered: 0, silent: 0, superseded: 0, expected_unsettled: 0, optional_unsettled: 0, nudged: 0 };
    for (const [scopeKey, row] of scopes) {
      const outcome = this.store.coveringDecision(scopeKey, row.seq);
      if (outcome) {
        counts[outcome === 'delivered' ? 'delivered' : 'silent'] += 1;
        continue;
      }
      // The newer input is checked when its own turn ends.
      if (this.store.newerAcceptedSeq(scopeKey, row.seq) !== null) {
        counts.superseded += 1;
        continue;
      }
      if (!expected(row)) {
        counts.optional_unsettled += 1;
        continue;
      }
      counts.expected_unsettled += 1;
      const reminders = this.nudged.get(row.seq) ?? 0;
      if (reminders < this.decisionReminderLimit) {
        this.nudged.set(row.seq, reminders + 1);
        if (this.nudged.size > REMINDER_HISTORY_LIMIT) this.nudged.delete(this.nudged.keys().next().value);
        this.harness.redeliverUndecided(row, { seq: row.seq, undecided: true });
        this.missing(row, 'NUDGED', runtime);
        this.logger.info('decision_nudged', { seq: row.seq, reminder: reminders + 1, conversationRef: opaqueRef(scopeKey) });
        counts.nudged += 1;
      } else {
        this.missing(row, 'MISSING', runtime);
      }
    }
    if (scopes.size > 0) this.logger.info('decision_settlement', counts);
    return counts;
  }

  remember(seq, seen) {
    seen.add(seq);
    if (seen.size > REMINDER_HISTORY_LIMIT) seen.delete(seen.values().next().value);
  }

  // A turn the loop breaker stopped (AgentRuntime.breakLoop), on any instance:
  // the model may never have read what that turn held, and nothing else
  // restarts it. Each of its inputs still without a decision (the newest in
  // its scope, nothing newer accepted there) comes back once as a fresh line,
  // with its seq; mail and other optional inputs too, since a stopped turn is
  // no evidence they were looked at. One a stopped turn holds a second time is
  // not shown again: its sender gets the failure notice instead.
  onTurnStopped({ inboundSeqs = [] } = {}) {
    if (this.isStopping()) return;
    try {
      this.recoverStopped(inboundSeqs, this.store.getCognitionState().runtime);
    } catch (error) {
      this.logger.warn('stopped_turn_recovery_failed', { error: safeError(error) });
    }
  }

  recoverStopped(inboundSeqs, runtime) {
    const scopes = this.newestPerScope(inboundSeqs);
    const counts = { decided: 0, superseded: 0, redelivered: 0, missing: 0 };
    for (const [scopeKey, row] of scopes) {
      if (this.store.coveringDecision(scopeKey, row.seq)) {
        counts.decided += 1;
        continue;
      }
      if (this.store.newerAcceptedSeq(scopeKey, row.seq) !== null) {
        counts.superseded += 1;
        continue;
      }
      if (this.recovered.has(row.seq)) {
        counts.missing += 1;
        this.missing(row, 'MISSING', runtime, 'loop_stopped');
        Promise.resolve(this.notifyFailure?.({ inboundSeqs: [row.seq], error: LOOP_STOPPED_ERROR }))
          .catch((error) => this.logger.warn('stopped_turn_notice_failed', { error: safeError(error) }));
        continue;
      }
      this.remember(row.seq, this.recovered);
      this.harness.redeliverUndecided(row, { seq: row.seq, undecided: this.explicitSilence });
      this.missing(row, 'NUDGED', runtime, 'loop_stopped');
      counts.redelivered += 1;
    }
    if (scopes.size > 0) this.logger.info('stopped_turn_settlement', counts);
    return counts;
  }

  missing(row, status, runtime, reason = null) {
    this.recordActivity('agent.decision_missing', {
      status,
      conversationKey: row.conversationKey,
      inboundSeq: row.seq,
      metadata: { expectation: reason ?? 'expected', runtime },
    });
  }
}
