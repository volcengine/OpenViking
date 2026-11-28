// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { opaqueRef, safeError } from './logger.mjs';
import { formatCognitionJournalLine } from './cognition.mjs';

// Reminder counts by seq in this process. A restart forgets them: the ledger
// is durable, the reminders are best effort.
const REMINDER_HISTORY_LIMIT = 10_000;
// The failure notice for an input a loop-stopped turn held twice.
const LOOP_STOPPED_ERROR = '模型反复做没有效果的调用，这一轮被 daemon 中止了';
// What a switch notice quotes of the stopped turn's own deliveries.
const SENT_LINES = 10;
const SENT_CHARS = 300;

// What the turn a switch stopped had already sent: the outbound journal from
// the first input it hands on. A fresh session's history ends before that
// input (cognitionHistoryPrefix), so the next runtime sees these nowhere else.
function sentSince(store, firstSeq) {
  const epoch = store.getCognitionState().epoch;
  const from = store.cognitionJournalSeqForInbound?.(firstSeq, epoch) ?? 0;
  if (!from) return [];
  return store.cognitionJournal(epoch, Number.MAX_SAFE_INTEGER, from)
    .filter((row) => row.kind === 'outbound')
    .map((row) => {
      const line = formatCognitionJournalLine(row);
      return line.length > SENT_CHARS ? `${line.slice(0, SENT_CHARS - 1)}…` : line;
    });
}

// The daemon notice in front of the inputs a /runtime switch hands on.
export function switchNotice({ from, to, failed = false, sent = [] }) {
  if (failed) {
    return `[daemon notice] A /runtime switch to ${to} failed, so you stay on ${from}. It stopped your previous turn;`
      + ' the inputs that turn was working on are shown again below. Your session still has what you did:'
      + ' go on from where you were, without sending again what you already sent.';
  }
  const shown = sent.slice(-SENT_LINES);
  const lines = [
    `[daemon notice] /runtime switched from ${from} to you while ${from} was working. Its turn was stopped before it finished,`
      + ' and the inputs it was working on are shown again below. Its reasoning and tool results did not carry over.'
      + ' Before redoing anything, check what it already did: files it may have changed and commands it may have run'
      + ' (look at their current state), and what it sent.',
  ];
  if (shown.length === 0) lines.push('It sent nothing after the first of these inputs.');
  else {
    lines.push(`What it sent after the first of these inputs${sent.length > shown.length ? ` (last ${shown.length} of ${sent.length})` : ''}:`,
      ...shown,
      'Do not send these again; a line with outcome=uncertain may or may not have arrived.');
  }
  return lines.join('\n');
}

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
// one. Reactions (message.reaction.*) ask for none: a nod at the bot's own
// message is not a question. Non-chat events (mail) stay optional: each is
// its own subject scope and one owner notice may rightly cover several, so a
// mail without its own decision is not reliably a miss, and nobody is waiting
// in a conversation.
function expected(row) {
  return Boolean(row.chatId) && Boolean(row.kind?.startsWith('message.')) && !row.kind.startsWith('message.reaction.');
}

// Turn-end reminders an undecided input gets before it is recorded MISSING
// (OV_SUPERAGENT_DECISION_REMINDER_LIMIT). One whose delivery the freshness
// fence held first gets one more, from a timer this long after the block
// (OV_SUPERAGENT_DECISION_REMINDER_DELAY_MS).
export const DECISION_REMINDER_LIMIT = 2;
export const DECISION_REMINDER_DELAY_MS = 60_000;

// Turn-end check that every input a successful turn consumed ended in its own
// decision: a final delivery or stay_silent on it, or one that listed it in
// covers (the ledger in `decisions`). A newer input in the same chat no longer
// settles it. It never judges the decision, never sends anything, and never
// touches the inbound ledger, freshness or the outbox. An expected input with
// no decision is shown again (same event= and seq, undecided=1) at the end of
// each turn that held it, up to `decisionReminderLimit` times, then recorded
// MISSING. The seq lets each reminder turn settle again without moving the
// freshness fence or counting as a new input. One a delivery was blocked on
// as stale first waits `blockedDelayMs` on a timer (its first reminder,
// mid-turn too), then follows the same turn-end steps: one reminder more in
// all.
export class DecisionPolicy {
  constructor({
    store, harness, logger, recordActivity, explicitSilence = true, decisionReminderLimit = DECISION_REMINDER_LIMIT,
    notifyFailure = null, isStopping = () => false,
    blockedDelayMs = DECISION_REMINDER_DELAY_MS, setTimer = setTimeout, clearTimer = clearTimeout,
  }) {
    Object.assign(this, {
      store, harness, logger, recordActivity, explicitSilence, decisionReminderLimit, notifyFailure, isStopping,
      blockedDelayMs, setTimer, clearTimer,
    });
    // seq -> { reminded, limit, timer }: undecided inputs still being reminded.
    this.pending = new Map();
    this.recovered = new Set();
  }

  // Whether this instance checks decisions at all.
  checking() {
    if (this.isStopping() || !this.explicitSilence) return null;
    const runtime = this.store.getCognitionState().runtime;
    return getRuntimeDefinition(runtime)?.capabilities.agentTools === 'core' ? runtime : null;
  }

  onTurnSucceeded({ inboundSeqs = [] } = {}) {
    const runtime = this.checking();
    if (!runtime) return;
    try {
      this.settle({ inboundSeqs, runtime });
    } catch (error) {
      this.logger.warn('decision_settlement_failed', { error: safeError(error) });
    }
  }

  // A delivery on this input was refused as stale. Still undecided after the
  // delay, it is reminded about from the timer, without waiting for a turn end.
  onStaleDecision({ seq } = {}) {
    try {
      if (!this.checking()) return;
      const row = this.store.inboundBySeq(seq);
      if (row?.status !== 'DONE' || !expected(row) || this.store.decisionOutcome(row.seq)) return;
      const entry = this.track(row.seq);
      // Already blocked once, or out of reminders even counting the timer's.
      const limit = this.decisionReminderLimit + 1;
      if (entry.blocked || entry.reminded >= limit) return;
      Object.assign(entry, { blocked: true, limit });
      entry.timer = this.setTimer(() => this.remindBlocked(row.seq), this.blockedDelayMs);
      entry.timer?.unref?.();
    } catch (error) {
      this.logger.warn('decision_block_tracking_failed', { error: safeError(error) });
    }
  }

  remindBlocked(seq) {
    const entry = this.pending.get(seq);
    if (!entry) return;
    entry.timer = null;
    try {
      const runtime = this.checking();
      const row = runtime ? this.store.inboundBySeq(seq) : null;
      if (!row || this.store.decisionOutcome(seq)) {
        this.pending.delete(seq);
        return;
      }
      this.remind(row, entry, runtime);
    } catch (error) {
      this.logger.warn('decision_reminder_failed', { error: safeError(error) });
    }
  }

  track(seq) {
    let entry = this.pending.get(seq);
    if (!entry) {
      entry = { reminded: 0, limit: this.decisionReminderLimit, blocked: false, timer: null };
      this.pending.set(seq, entry);
      if (this.pending.size > REMINDER_HISTORY_LIMIT) this.forget(this.pending.keys().next().value);
    }
    return entry;
  }

  forget(seq) {
    const entry = this.pending.get(seq);
    if (entry?.timer) this.clearTimer(entry.timer);
    this.pending.delete(seq);
  }

  remind(row, entry, runtime) {
    entry.reminded += 1;
    this.harness.redeliverUndecided(row, { seq: row.seq, undecided: true });
    this.missing(row, 'NUDGED', runtime);
    this.logger.info('decision_nudged', { seq: row.seq, reminder: entry.reminded, conversationRef: opaqueRef(row.scopeKey) });
  }

  // Every accepted input this turn consumed, each on its own.
  settle({ inboundSeqs, runtime }) {
    const counts = { delivered: 0, silent: 0, expected_unsettled: 0, optional_unsettled: 0, nudged: 0, missing: 0 };
    let checked = 0;
    for (const seq of new Set(Array.isArray(inboundSeqs) ? inboundSeqs : [])) {
      if (seq === null || seq === undefined) continue;
      const row = this.store.inboundBySeq(seq);
      if (row?.status !== 'DONE' || !row.scopeKey) continue;
      checked += 1;
      const outcome = this.store.decisionOutcome(row.seq);
      if (outcome) {
        this.forget(row.seq);
        counts[outcome] += 1;
        continue;
      }
      if (!expected(row)) {
        counts.optional_unsettled += 1;
        continue;
      }
      counts.expected_unsettled += 1;
      const entry = this.track(row.seq);
      // A blocked input's first reminder is its timer's.
      if (entry.timer) continue;
      if (entry.reminded < entry.limit) {
        this.remind(row, entry, runtime);
        counts.nudged += 1;
      } else {
        this.missing(row, 'MISSING', runtime);
        counts.missing += 1;
      }
    }
    if (checked > 0) this.logger.info('decision_settlement', counts);
    return counts;
  }

  remember(seq, seen) {
    seen.add(seq);
    if (seen.size > REMINDER_HISTORY_LIMIT) seen.delete(seen.values().next().value);
  }

  // A turn the loop breaker stopped (AgentRuntime.breakLoop), on any instance:
  // the model may never have read what that turn held, and nothing else
  // restarts it. Each of its inputs still without its own decision comes back
  // once as a fresh line, with its seq; mail and other optional inputs too,
  // since a stopped turn is no evidence they were looked at. One a stopped turn holds a second time is
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
    const counts = { decided: 0, redelivered: 0, missing: 0 };
    let checked = 0;
    for (const seq of new Set(Array.isArray(inboundSeqs) ? inboundSeqs : [])) {
      if (seq === null || seq === undefined) continue;
      const row = this.store.inboundBySeq(seq);
      if (row?.status !== 'DONE' || !row.scopeKey) continue;
      checked += 1;
      if (this.store.decisionOutcome(row.seq)) {
        counts.decided += 1;
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
    if (checked > 0) this.logger.info('stopped_turn_settlement', counts);
    return counts;
  }

  // A /runtime switch stopped the turn that held these inputs
  // (AgentRuntime._resumeCut). Each one still without a decision goes on, in
  // order, in the runtime that stays (the new one, or the old one when the
  // switch failed), behind a notice of what happened and what was already
  // sent; an answered one stays answered. Unlike a loop stop, every one of
  // them is shown: the new session never saw them, and its history ends
  // before the first. Returns { items, notice }, or null when none is left.
  resumeInterrupted({ inboundSeqs = [], from, to, failed = false } = {}) {
    if (this.isStopping()) return null;
    const rows = [];
    let decided = 0;
    for (const seq of new Set(inboundSeqs)) {
      const row = this.store.inboundBySeq(seq);
      if (row?.status !== 'DONE' || !row.scopeKey) continue;
      if (this.store.decisionOutcome(row.seq)) {
        decided += 1;
        continue;
      }
      rows.push(row);
    }
    rows.sort((a, b) => a.seq - b.seq);
    this.logger.info('switch_interrupted_settlement', { decided, resumed: rows.length, failed });
    if (rows.length === 0) return null;
    const last = rows.at(-1);
    this.recordActivity('agent.switch_resumed', {
      summary: failed
        ? `切换到 ${to} 失败，被打断的 ${rows.length} 条输入交回 ${from} 继续处理`
        : `${from} 切换到 ${to}，被打断的 ${rows.length} 条输入交给 ${to} 继续处理`,
      status: failed ? 'RESUMED_ON_PREVIOUS' : 'RESUMED',
      conversationKey: last.conversationKey,
      inboundSeq: last.seq,
      metadata: { from, to, inboundSeqs: rows.map((row) => row.seq), decided },
    });
    return {
      items: rows.map((row) => this.harness.redelivery(row, { seq: row.seq, undecided: false }).item),
      notice: switchNotice({ from, to, failed, sent: failed ? [] : sentSince(this.store, rows[0].seq) }),
    };
  }

  // Timers die with the daemon.
  stop() {
    for (const seq of [...this.pending.keys()]) this.forget(seq);
  }

  missing(row, status, runtime, reason = null) {
    if (status === 'MISSING') this.store.recordDecision({ row, outcome: 'missing' });
    this.recordActivity('agent.decision_missing', {
      status,
      conversationKey: row.conversationKey,
      inboundSeq: row.seq,
      metadata: { expectation: reason ?? 'expected', runtime },
    });
  }
}
