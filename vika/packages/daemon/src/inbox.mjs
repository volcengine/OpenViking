// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { chatTag, metadataTag, oneLine } from './runtime-input.mjs';
import { safeError } from './logger.mjs';

// The inbox scheduler (agents[].scheduler = inbox). Under the legacy scheduler
// every accepted input goes into the running turn as soon as it arrives
// (Codex turn/steer, Pi steer, ...), which interrupts long work and pulls the
// model's attention to the newest line. Here an input that arrives while the
// model works waits in this inbox instead:
//
// - The model gets one `[daemon inbox]` notice per burst (after
//   NOTICE_DELAY_MS, which later inputs do not extend): which chats have
//   inputs waiting, never their text.
// - It reads them at a checkpoint with the message_check tool, oldest first.
//   They are delivered through the runtime's own submit path, so the adapter
//   books them like any steered input (journal, inboundSeqs, decisions).
// - When the runtime is idle, waiting inputs start a turn by themselves.
//
// Waiting inputs are kept in SQLite (inbox_pending, created on first use so a
// legacy instance never has the table): a restart delivers them again instead
// of dropping inputs the ledger already accepted.

export const NOTICE_DELAY_MS = 3_000;
// The legacy group burst window: a direct line starts the idle turn at once,
// a group line waits this long for the rest of its burst.
export const WAKE_DELAY_MS = 400;
// Inputs one idle turn starts with; the rest are noticed in that turn.
export const WAKE_BATCH = 20;
export const CHECK_LIMIT = 50;
const RETRY_MS = 1_000;
const NOTICE_SENDERS = 3;

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS inbox_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    seq INTEGER,
    item_json TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
  )
`;

function tag(value) {
  return metadataTag(oneLine(value).trim() || 'unknown');
}

// Where a waiting input is, as the input lines name it.
function placeOf(meta) {
  if (meta.chatId) {
    return [`chat=${chatTag(metadataTag(meta.chatId), meta.threadId)}`, `source=${tag(meta.source)}`,
      ...(meta.instance && meta.instance !== meta.source ? [`instance=${tag(meta.instance)}`] : []),
      ...(meta.chatType ? [`type=${tag(meta.chatType)}`] : [])].join(' ');
  }
  return [`source=${tag(meta.source)}`,
    ...(meta.instance && meta.instance !== meta.source ? [`instance=${tag(meta.instance)}`] : []),
    ...(meta.kind ? [`kind=${tag(meta.kind)}`] : [])].join(' ');
}

// One line per scope with new inputs: how many wait there, who wrote the
// latest ones, and whether the owner or a mention of the agent is among
// them. Never their text: the model reads that with message_check.
export function inboxNoticeText(entries, fresh) {
  const scopes = new Map();
  for (const entry of entries) {
    const scope = scopes.get(entry.scopeKey) ?? { meta: entry.meta, count: 0, senders: [], owner: false, mention: false, fresh: false };
    scope.count += 1;
    if (entry.meta.from && !scope.senders.includes(entry.meta.from)) scope.senders.push(entry.meta.from);
    scope.owner ||= Boolean(entry.meta.owner);
    scope.mention ||= entry.meta.mention === 'me' || entry.meta.mention === 'mixed';
    scopes.set(entry.scopeKey, scope);
  }
  for (const entry of fresh) scopes.get(entry.scopeKey).fresh = true;
  const lines = [...scopes.values()].filter((scope) => scope.fresh).map((scope) => {
    const senders = scope.senders.slice(-NOTICE_SENDERS).map(tag).join(',');
    return `- ${placeOf(scope.meta)} waiting=${scope.count}${senders ? ` from=${senders}` : ''}`
      + `${scope.owner ? ' owner=1' : ''}${scope.mention ? ' mention=me' : ''}`;
  });
  return [
    `[daemon inbox] waiting=${entries.length} chats=${scopes.size}; new since the last notice:`,
    ...lines,
    'This notice is not an input and needs no decision. Finish the step you are on, then call message_check to read waiting inputs, oldest first (chat_id reads one chat).',
  ].join('\n');
}

export class MessageInbox {
  constructor({
    db,
    runtime,
    capabilities = () => ({}),
    logger = null,
    recordActivity = () => {},
    maxAgeMs = 86_400_000,
    noticeDelayMs = NOTICE_DELAY_MS,
    wakeDelayMs = WAKE_DELAY_MS,
    wakeBatch = WAKE_BATCH,
    now = Date.now,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    if (!db) throw new TypeError('db is required');
    if (!runtime) throw new TypeError('runtime is required');
    Object.assign(this, { db, runtime, capabilities, logger, recordActivity, maxAgeMs,
      noticeDelayMs, wakeDelayMs, wakeBatch, now, setTimer, clearTimer });
    this.db.exec(SCHEMA);
    this.insertRow = this.db.prepare(`
      INSERT INTO inbox_pending(scope_key, seq, item_json, meta_json, created_at_ms) VALUES (?, ?, ?, ?, ?)
    `);
    this.deleteRow = this.db.prepare('DELETE FROM inbox_pending WHERE id = ?');
    this.entries = [];
    this.wakeTimer = null;
    this.noticeTimer = null;
    this.stopped = false;
  }

  get size() { return this.entries.length; }

  // The inbox needs the message_check tool: a runtime without the Core tools
  // (Volc) gets every input the legacy way, at once.
  usable() {
    return this.capabilities()?.agentTools === 'core';
  }

  // Only a runtime that takes input mid-turn can be told mid-turn; the rest
  // (OpenCode) get waiting inputs when their turn ends.
  canNotify() {
    return this.usable() && this.capabilities()?.activeInput === 'steer';
  }

  busy() {
    return Boolean(this.runtime.snapshot().work.turnInProgress);
  }

  add(item, meta = {}) {
    if (this.stopped) return false;
    this.publish(this.persist(item, meta));
    return true;
  }

  // A producer may persist an entry inside its own SQLite transaction. It
  // must publish only after COMMIT, so no rolled-back input reaches a runtime.
  persist(item, meta = {}) {
    if (this.stopped) throw new Error('inbox is stopped');
    const createdAtMs = this.now();
    const scopeKey = item.scopeKey ?? item.conversationKey ?? item.scope?.key ?? 'unscoped';
    const { lastInsertRowid } = this.insertRow.run(scopeKey, item.seq ?? null, JSON.stringify(item),
      JSON.stringify(meta), createdAtMs);
    return { id: Number(lastInsertRowid), scopeKey, item, meta, createdAtMs, notified: false };
  }

  publish(entry) {
    if (!this.entries.some((existing) => existing.id === entry.id)) this.entries.push(entry);
    this.schedule();
  }

  // Inputs a previous daemon generation accepted and never delivered. Older
  // than the event age limit, they are dropped like a stale event would be.
  restore() {
    const cutoff = this.now() - this.maxAgeMs;
    const rows = this.db.prepare('SELECT * FROM inbox_pending ORDER BY id').all();
    for (const row of rows) {
      const meta = JSON.parse(row.meta_json);
      // A finished task's result must survive a long outage too.
      if (row.created_at_ms < cutoff && meta.kind !== 'task.report') {
        this.deleteRow.run(row.id);
        continue;
      }
      if (!this.entries.some((entry) => entry.id === Number(row.id))) {
        this.entries.push({ id: Number(row.id), scopeKey: row.scope_key, item: JSON.parse(row.item_json),
          meta, createdAtMs: row.created_at_ms, notified: false });
      }
    }
    if (this.entries.length > 0) {
      this.logger?.info('inbox_restored', { waiting: this.entries.length });
      this.schedule();
    }
    return this.entries.length;
  }

  schedule() {
    if (this.stopped || this.entries.length === 0) return;
    if (this.busy()) {
      this.clearWake();
      if (!this.noticeTimer && this.canNotify() && this.entries.some((entry) => !entry.notified)) {
        // Not extended by later inputs: they join the same notice.
        this.noticeTimer = this.setTimer(() => { this.noticeTimer = null; this.notify(); }, this.noticeDelayMs);
        this.noticeTimer?.unref?.();
      }
      return;
    }
    const dueAtMs = Math.min(...this.entries.map((entry) => entry.createdAtMs
      + (entry.item.chatType === 'group' ? this.wakeDelayMs : 0)));
    this.armWake(Math.max(0, dueAtMs - this.now()));
  }

  armWake(delayMs) {
    this.clearWake();
    this.wakeTimer = this.setTimer(() => { this.wakeTimer = null; this.wake(); }, delayMs);
    this.wakeTimer?.unref?.();
  }

  clearWake() {
    if (this.wakeTimer) this.clearTimer(this.wakeTimer);
    this.wakeTimer = null;
  }

  // The runtime is idle: start a turn with the oldest waiting inputs. The rest
  // are noticed at the end of that turn's input, so they are not a surprise.
  wake() {
    if (this.stopped || this.entries.length === 0) return false;
    if (!this.usable()) return this.drain() > 0;
    if (this.busy()) {
      this.schedule();
      return false;
    }
    const taken = this.entries.slice(0, this.wakeBatch);
    const rest = this.entries.slice(taken.length);
    const items = taken.map((entry) => entry.item);
    if (rest.length > 0) items.push(this.noticeItem(rest, rest));
    let result;
    try {
      result = this.runtime.submit(items, { admission: 'claim-if-idle' });
    } catch (error) {
      this.logger?.warn('inbox_wake_failed', { error: safeError(error) });
      result = { accepted: false };
    }
    if (result?.accepted !== true) {
      // Switching, reconnecting, or a turn won the race: the turn's end (or
      // the retry) comes back here, and a running turn gets the notice.
      if (this.busy()) this.schedule();
      else this.armWake(RETRY_MS);
      return false;
    }
    this.remove(taken);
    for (const entry of rest) entry.notified = true;
    this.record('inbox.delivered', taken, { reason: 'wake', waiting: rest.length });
    return true;
  }

  notify() {
    if (this.stopped || this.entries.length === 0) return false;
    if (!this.busy()) {
      this.schedule();
      return false;
    }
    if (!this.canNotify()) return false;
    const fresh = this.entries.filter((entry) => !entry.notified);
    if (fresh.length === 0) return false;
    let result;
    try {
      result = this.runtime.submit([this.noticeItem(this.entries, fresh)], { admission: 'enqueue' });
    } catch (error) {
      this.logger?.warn('inbox_notice_failed', { error: safeError(error) });
      result = { accepted: false };
    }
    if (result?.accepted !== true) {
      this.noticeTimer = this.setTimer(() => { this.noticeTimer = null; this.notify(); }, RETRY_MS);
      this.noticeTimer?.unref?.();
      return false;
    }
    for (const entry of fresh) entry.notified = true;
    this.recordActivity('inbox.notified', {
      summary: `${this.entries.length} 条输入等待读取`,
      metadata: { waiting: this.entries.length, fresh: fresh.length, scopes: new Set(fresh.map((entry) => entry.scopeKey)).size },
    });
    return true;
  }

  noticeItem(entries, fresh) {
    return { text: inboxNoticeText(entries, fresh), seq: null, scope: { kind: 'inbox' } };
  }

  // message_check: deliver waiting inputs, oldest first, optionally from one
  // chat (and thread) or scope. They go through the runtime's submit path, so
  // a working turn takes them as steered input right after the tool returns.
  check({ chatId = null, threadId = null, source = null, scopeKey = null, limit = CHECK_LIMIT } = {}) {
    const matches = this.entries.filter((entry) => (!scopeKey || entry.scopeKey === scopeKey)
      && (!chatId || entry.meta.chatId === chatId)
      && (!threadId || entry.meta.threadId === threadId)
      && (!source || entry.meta.source === source || entry.meta.instance === source));
    const taken = matches.slice(0, Math.max(1, Math.min(CHECK_LIMIT, limit)));
    if (taken.length === 0) return { delivered: 0, waiting: this.entries.length };
    let result;
    try {
      result = this.runtime.submit(taken.map((entry) => entry.item), { admission: 'enqueue' });
    } catch (error) {
      result = { accepted: false, reason: safeError(error) };
    }
    if (result?.accepted !== true) return { delivered: 0, waiting: this.entries.length, error: result?.reason ?? 'rejected' };
    this.remove(taken);
    this.record('inbox.delivered', taken, { reason: scopeKey ? 'held_delivery' : 'check', waiting: this.entries.length });
    return {
      delivered: taken.length,
      waiting: this.entries.length,
      // Whether they reach the model in this turn or when it ends.
      now: this.canNotify() || !this.busy(),
    };
  }

  hasWaiting(scopeKey) {
    return this.entries.some((entry) => entry.scopeKey === scopeKey);
  }

  // A delivery was held because newer input in its scope is still waiting:
  // deliver that scope now, so the model can decide on what it was told about.
  deliverScope(scopeKey) {
    if (!this.hasWaiting(scopeKey)) return 0;
    return this.check({ scopeKey }).delivered;
  }

  onAvailable() {
    if (this.stopped || this.entries.length === 0) return;
    this.schedule();
  }

  // Everything waiting, at once and in order, the legacy way: for a runtime
  // that cannot read the inbox.
  drain() {
    if (this.stopped || this.entries.length === 0) return 0;
    const taken = [...this.entries];
    const result = this.runtime.submit(taken.map((entry) => entry.item), { admission: 'enqueue' });
    if (result?.accepted !== true) return 0;
    this.remove(taken);
    this.record('inbox.delivered', taken, { reason: 'drain', waiting: 0 });
    return taken.length;
  }

  remove(taken) {
    const ids = new Set(taken.map((entry) => entry.id));
    this.entries = this.entries.filter((entry) => !ids.has(entry.id));
    for (const id of ids) this.deleteRow.run(id);
  }

  record(kind, taken, metadata) {
    const seqs = taken.map((entry) => entry.item.seq).filter((seq) => seq != null);
    this.recordActivity(kind, {
      summary: `${taken.length} 条输入交给模型`,
      inboundSeq: seqs.at(-1) ?? null,
      metadata: { ...metadata, delivered: taken.length, inboundSeqs: seqs,
        waitedMs: Math.max(0, ...taken.map((entry) => this.now() - entry.createdAtMs)) },
    });
  }

  stop() {
    this.stopped = true;
    this.clearWake();
    if (this.noticeTimer) this.clearTimer(this.noticeTimer);
    this.noticeTimer = null;
  }
}
