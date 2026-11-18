// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { formatCognitionJournalLine } from './cognition.mjs';
import { safeError } from './logger.mjs';
import { OVConnection, OVUnavailableError, OV_HTTP_STATUS, readOVConfig } from './ov-connection.mjs';

// A commit keeps the newest complete turns, up to RETAINED_TOKENS, live as raw
// messages and runs once COMMIT_TOKENS more are pending, so the live session
// fits the context read whole.
const RETAINED_TOKENS = 24_000;
const COMMIT_TOKENS = 24_000;
const CONTEXT_TOKENS = RETAINED_TOKENS + COMMIT_TOKENS;

// The identity of a sync row from before rows were bound whose session this
// connection did not find as its own (bindIdentity).
const FOREIGN = 'foreign';
const TASK_OPERATIONS = { create_session: 'create_session', append: 'add_message', commit: 'session_commit' };

export class OVSessionTaskError extends Error {
  constructor(code, { terminal = false, alreadyExists = false } = {}) {
    // Task bodies may contain credentials or private messages. Expose a stable
    // business error code, never the untrusted error message/body.
    super(`OpenViking session task ${code}`);
    this.name = 'OVSessionTaskError';
    this.code = code;
    this.terminal = terminal;
    this.alreadyExists = alreadyExists;
  }
}

const validateSession = (session, id) => {
  if (session?.session_id !== id || typeof session.uri !== 'string' || !session.uri.startsWith('viking://')) {
    throw new Error('OV returned an invalid session identity');
  }
  return session;
};

// No response and no timeout: OV was not reached, so any other request fails
// as well. One request that ran out of time says nothing about the others.
const unreachable = (error) => error instanceof OVUnavailableError && error.httpStatus === undefined && !error.timedOut;

// OV releases up to v0.4.23, the hosted service among them, still write
// Working Memory at every commit, and the daemon leaves OV's settings alone.
// It only ever grows, so none of it is passed on; history is raw messages.
// In /context it is `latest_archive_overview`, plus a `checkpoint` message
// after a turn whose earlier part was archived; in the archive directory it
// is `.overview.md` / `.abstract.md`, and checkpoints in `.meta.json`.
export function withoutWorkingMemory({ latest_archive_overview: _overview, pre_archive_abstracts: _abstracts, ...context }) {
  return { ...context, messages: context.messages?.filter(({ message_kind: kind }) => kind !== 'checkpoint') };
}

// How OV reads a journal line. Something someone sent is a user line, whoever
// sent it: the line's `from=` names the speaker. No peer is attached: the
// instance is the OV user, and every collaboration lands in its own events.
// The agent's deliveries and its own reminders (an event without a sender)
// are assistant lines: context, never anyone's words.
export function ovAttribution(item) {
  if (item.kind !== 'inbound') return { role: 'assistant' };
  const event = item.metadata?.event;
  if (event && !event.actor) return { role: 'assistant' };
  return { role: 'user' };
}

// The memory types extraction may write from the conversation. Left out:
// - preferences: extraction picks whose a preference is from names, so it
//   merges namesakes and edits one person's file with another's request. The
//   Core writes them instead, into the sender's own directory, when the agent
//   calls openviking_remember (ov-person.mjs); this also keeps a later commit
//   from extracting the same request a second time.
// - the agent's own soul and identity: a line that talks the agent into a new
//   name or a global rule would otherwise rewrite who it is (OpenViking SuperAgent's name
//   comes from its configuration, its soul is a file in OpenViking that it
//   rewrites only when the owner asks, and the owner's standing rules are the
//   owner's preferences).
// These exist in every OV release that has memory_policy, so a commit never
// fails on an unknown type.
export const OV_MEMORY_TYPES = Object.freeze(['profile', 'entities', 'events']);

// Daemon control lines (slash commands and their notices) steer the runtime;
// they are neither history worth recalling nor anyone's words.
const syncsToOV = (row) => row.kind !== 'control';

// OV owns archives and assembled context. SQLite retains only the transport
// cursor/intent here; cognition_journal is the existing durable delivery ledger
// and sync outbox, never a prompt source.
export class OVSessionClient extends OVConnection {
  constructor(configPath, options) {
    super(readOVConfig(configPath), options);
    this.namespace = null;
    this.taskTimeoutMs = options?.taskTimeoutMs ?? this.timeoutMs;
    this.taskPollMs = options?.taskPollMs ?? 200;
  }

  // Also settles which namespace this connection writes to: the server, and
  // the account and user OV authenticated (an API key overrides configured
  // headers, so rotating a key keeps the namespace). A server that does not
  // report the account under a key is bound to the key itself.
  async check() {
    const health = await this.json('/health');
    const user = await this.authenticate() ?? health.user_id ?? (this.user || null);
    const account = health.account_id
      ?? (health.auth_mode === 'api_key' ? this.headers['X-API-Key'] : this.headers['X-OpenViking-Account']) ?? null;
    this.namespace = createHash('sha256').update(JSON.stringify([this.url, account, user])).digest('hex');
    return health;
  }

  async request(route, body) {
    const response = await this.json(`/api/v1${route}`, body);
    // A 202 write cannot silently become a successful synchronous append if a
    // buggy server omits accepted/task_id. Preserve it as unresolved acceptance.
    return response[OV_HTTP_STATUS] === 202 && body !== undefined
      ? { ...response.result, accepted: true } : response.result;
  }

  async create(sessionId, options = {}) {
    const result = await this.write('create_session', sessionId, '/sessions', { session_id: sessionId, auto_commit_policy: {
      pending_token_threshold: 0, message_count_threshold: 0, idle_timeout_seconds: 0,
    }, memory_policy: { memory_types: [...OV_MEMORY_TYPES] } }, options);
    return validateSession(result, sessionId);
  }
  append(sessionId, message, options) {
    return this.write('append', sessionId, `/sessions/${encodeURIComponent(sessionId)}/messages`, message, options);
  }

  // Responses, not URL/version switches, select the protocol. A pre-generated
  // URI in an acceptance still does not prove execution. Persist before polling.
  async write(operation, sessionId, route, body, { pending, onAccepted = () => {}, stopped = () => false } = {}) {
    const result = pending ?? await this.request(route, body);
    if (result?.accepted !== true) return result;
    // Even malformed acceptance is an ambiguous write: retain it rather than
    // submitting again on every idle checkpoint.
    if (!pending) onAccepted(result);
    if (typeof result.task_id !== 'string' || !result.task_id.trim()
      || (result.session_id != null && result.session_id !== sessionId)
      || (result.operation != null && result.operation !== TASK_OPERATIONS[operation])) {
      throw new OVSessionTaskError('PROTOCOL_ERROR');
    }
    const task = await this.waitForTask(result.task_id, sessionId, stopped, operation);
    if (operation === 'create_session') return this.request(`/sessions/${encodeURIComponent(sessionId)}`);
    return task.result;
  }

  async waitForTask(taskId, sessionId, stopped, operation) {
    const deadline = Date.now() + this.taskTimeoutMs;
    while (true) {
      if (stopped()) throw new Error('OV sync stopped');
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new OVSessionTaskError('RESULT_UNKNOWN');
      let task;
      try {
        task = (await this.json(`/api/v1/tasks/${encodeURIComponent(taskId)}`, undefined,
          { timeoutMs: Math.min(this.timeoutMs, remaining) })).result;
      } catch (error) {
        if (error.httpStatus === 404) throw new OVSessionTaskError('TASK_NOT_FOUND');
        throw error;
      }
      if (stopped()) throw new Error('OV sync stopped');
      if (task?.task_id !== taskId || typeof task.status !== 'string'
        || (task.resource_id != null && task.resource_id !== sessionId
          && !String(task.resource_id).endsWith(`/sessions/${sessionId}`))) {
        throw new OVSessionTaskError('PROTOCOL_ERROR');
      }
      if (task.task_type != null && task.task_type !== TASK_OPERATIONS[operation]) {
        throw new OVSessionTaskError('PROTOCOL_ERROR');
      }
      // Commit archiving and memory extraction are distinct stages. The Core
      // archived:true result means the archive is durable even if its extraction
      // Task is still consuming. Access acceptance alone never carries this proof.
      if (task.status === 'completed' || (operation === 'commit' && task.result?.archived === true)) return task;
      if (['failed', 'cancelled'].includes(task.status)) {
        const detail = task.error;
        const alreadyExists = detail?.code === 'ALREADY_EXISTS'
          || /(?:resource|session).*already exists/i.test(typeof detail === 'string' ? detail : detail?.message ?? '');
        const code = alreadyExists ? 'ALREADY_EXISTS'
          : (typeof detail?.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(detail.code) ? detail.code : 'FAILED');
        throw new OVSessionTaskError(code, { terminal: true, alreadyExists });
      }
      if (!['submitted', 'pending', 'consuming', 'running'].includes(task.status)) {
        throw new OVSessionTaskError('PROTOCOL_ERROR');
      }
      await delay(Math.min(this.taskPollMs, Math.max(1, deadline - Date.now())));
    }
  }
  context(sessionId) {
    return this.request(`/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${CONTEXT_TOKENS}`);
  }
  // The turn count is OV's maximum, so only the token budget binds.
  commit(sessionId, { final = false, ...options } = {}) {
    return this.write('commit', sessionId, `/sessions/${encodeURIComponent(sessionId)}/commit`, final ? { keep_recent_count: 0 } : {
      retention_mode: 'turn_budget', keep_recent_turn_count: 10_000, retained_message_token_budget: RETAINED_TOKENS,
    }, options);
  }
  // An archive's original messages, read from storage. OV's archive API adds
  // the Working Memory where a release still writes one, and up to v0.4.23
  // answers 404 for an archive without it; it also skips archives still
  // being processed.
  async archive(sessionUri, archiveId) {
    const content = await this.read(`${sessionUri}/history/${encodeURIComponent(archiveId)}/messages.jsonl`, 0, -1);
    let messages;
    try {
      messages = String(content).split('\n').filter(Boolean).map((line) => JSON.parse(line));
    } catch {
      // A parse error would quote the stored content.
      throw new Error('OV archive messages are malformed');
    }
    return { archive_id: archiveId, messages };
  }
  // Archive directories, without the Working Memory abstract OV lists them with.
  async archives(sessionUri) {
    const entries = await this.request(`/fs/ls?uri=${encodeURIComponent(`${sessionUri}/history`)}`);
    return (entries ?? []).map(({ abstract: _abstract, ...entry }) => entry);
  }
  // Matches in archived messages only, not in Working Memory files.
  async grep(sessionUri, pattern) {
    const result = await this.request('/search/grep', { uri: `${sessionUri}/history`, pattern, case_insensitive: true });
    return { matches: (result?.matches ?? []).filter(({ uri }) => String(uri).endsWith('/messages.jsonl')) };
  }
  read(uri, offset = 0, limit = 100) {
    return this.request(`/content/read?uri=${encodeURIComponent(uri)}&offset=${offset}&limit=${limit}&raw=true`);
  }
}

export class OVSessionContext {
  constructor(store, client, logger) {
    this.store = store;
    this.client = client;
    this.logger = logger;
    this.barrier = Promise.resolve();
    // Retired epochs are finalized in a queue of their own (checkpoint).
    this.retiredBarrier = Promise.resolve();
    this.contexts = new Map();
    this.stopped = false;
    this.checked = new Map();
    // The namespace sync rows are bound to (bindIdentity), and the first epoch
    // seen under it: older epochs without a row are not this connection's.
    this.identity = null;
    this.firstEpoch = store.getCognitionState().epoch;
    store.db.exec(`CREATE TABLE IF NOT EXISTS ov_session_sync (
      epoch INTEGER PRIMARY KEY, session_id TEXT NOT NULL, uri TEXT,
      synced_seq INTEGER NOT NULL DEFAULT 0, pending_seq INTEGER
    )`);
    const columns = new Set(store.db.prepare('PRAGMA table_info(ov_session_sync)').all().map(({ name }) => name));
    if (!columns.has('identity')) store.db.exec('ALTER TABLE ov_session_sync ADD COLUMN identity TEXT');
    if (!columns.has('finalized_seq')) store.db.exec('ALTER TABLE ov_session_sync ADD COLUMN finalized_seq INTEGER NOT NULL DEFAULT -1');
    if (!columns.has('pending_task')) store.db.exec('ALTER TABLE ov_session_sync ADD COLUMN pending_task TEXT');
    // The epoch's session in the namespace this connection writes to, once
    // it exists there; null before binding and for another connection's.
    // Recall and the archive tool use nothing else, so neither reaches into
    // another namespace nor creates a session there.
    store.ovSessionFor = (epoch) => {
      if (!this.identity) return null;
      const state = store.db.prepare('SELECT session_id, uri FROM ov_session_sync WHERE epoch=? AND identity=?')
        .get(epoch, this.identity);
      return state?.uri ? state : null;
    };
    store.prepareOVContext = async (epoch) => {
      try {
        return await this.prepare(epoch);
      } catch (error) {
        if (this.stopped) throw error;
        // History is best effort: a fresh session starts without it rather
        // than blocking a runtime switch or an inbound message. Never reuse
        // stale prefetched history after a failure. The local outbox/cursors
        // remain intact, so the idle checkpoint can retry later.
        const empty = { messages: [] };
        this.contexts.set(epoch, empty);
        // A JSON parse error can quote remote content.
        this.logger.warn('ov_context_unavailable', {
          error: error instanceof SyntaxError ? 'OV returned malformed data' : safeError(error),
        });
        return empty;
      }
    };
    store.getOVContext = (epoch) => this.contexts.get(epoch);
  }

  run(fn) {
    const work = this.barrier.then(() => {
      if (this.stopped) throw new Error('OpenViking context transport is stopping');
      return fn();
    });
    this.barrier = work.catch(() => {});
    return work;
  }

  // The epoch's sync row in this connection's namespace, created as needed.
  // Another namespace's session stays where it is: the current epoch moves to
  // a fresh session here (its journal is replayed), an older one is not ours.
  async session(epoch) {
    await this.bindIdentity();
    let state = this.store.db.prepare('SELECT * FROM ov_session_sync WHERE epoch=?').get(epoch);
    if (state && state.identity !== this.identity) {
      if (epoch < this.store.getCognitionState().epoch) throw new Error('This epoch belongs to another OpenViking connection');
      this.store.db.prepare('DELETE FROM ov_session_sync WHERE epoch=?').run(epoch);
      state = null;
      this.checked.delete(epoch);
      this.contexts.delete(epoch);
    }
    if (!state && epoch < this.firstEpoch) throw new Error('This epoch belongs to another OpenViking connection');
    if (!state) {
      this.store.db.prepare('INSERT INTO ov_session_sync(epoch,session_id,identity) VALUES (?,?,?)')
        .run(epoch, `lark-${randomUUID()}`, this.identity);
      state = this.store.db.prepare('SELECT * FROM ov_session_sync WHERE epoch=?').get(epoch);
    }
    if (!state.uri) {
      let remote;
      if (!state.pending_task) {
        try {
          remote = validateSession(await this.client.request(`/sessions/${encodeURIComponent(state.session_id)}`), state.session_id);
        } catch (error) {
          if (error.httpStatus !== 404) throw error;
        }
      }
      if (!remote) {
        try { remote = await this.write(state, 'create', 0, (options) => this.client.create(state.session_id, options)); }
        catch (error) {
          if (!error.alreadyExists && error.httpStatus !== 409 && error.code !== 'TASK_NOT_FOUND') throw error;
          remote = validateSession(await this.client.request(`/sessions/${encodeURIComponent(state.session_id)}`), state.session_id);
        }
      }
      if (this.stopped) throw new Error('OV sync stopped');
      validateSession(remote, state.session_id);
      this.store.db.prepare('UPDATE ov_session_sync SET uri=?,pending_task=NULL WHERE epoch=? AND identity=?')
        .run(remote.uri, epoch, this.identity);
      state.uri = remote.uri;
      state.pending_task = null;
    }
    // Finish a previously accepted commit before any later append/context. The
    // final watermark uses the original target, not a newer journal head.
    if (state.pending_task) {
      const pending = JSON.parse(state.pending_task);
      if (['commit', 'finalize'].includes(pending.operation)) await this.commit(state, pending.operation === 'finalize', pending.seq);
    }
    return state;
  }

  // Intent lives beside the namespace-bound cursor; neither timeout nor restart
  // discards it. Clear only after the caller atomically acknowledges the write.
  async write(state, operation, seq, submit) {
    const saved = state.pending_task ? JSON.parse(state.pending_task) : null;
    if (saved && (saved.operation !== operation || saved.seq !== seq)) {
      throw new OVSessionTaskError('PENDING_OPERATION_CONFLICT');
    }
    try {
      return await submit({ pending: saved?.acceptance, stopped: () => this.stopped,
        onAccepted: (acceptance) => {
          state.pending_task = JSON.stringify({ operation, seq, acceptance: {
            accepted: true, task_id: acceptance.task_id, session_id: acceptance.session_id, operation: acceptance.operation,
          } });
          this.store.db.prepare('UPDATE ov_session_sync SET pending_task=? WHERE epoch=? AND identity=?')
            .run(state.pending_task, state.epoch, this.identity);
        } });
    } catch (error) {
      if (error.terminal) this.clearTask(state);
      throw error;
    }
  }

  clearTask(state) {
    this.store.db.prepare('UPDATE ov_session_sync SET pending_task=NULL WHERE epoch=? AND identity=?')
      .run(state.epoch, this.identity);
    state.pending_task = null;
  }

  // Binds sync rows to the namespace this connection writes to, once per
  // process. A row from before rows were bound is this namespace's only if
  // its session is found here under the same URI; otherwise it is left to its
  // own connection, said once here, and never retried.
  async bindIdentity() {
    if (this.identity) return;
    if (!this.client.namespace) await this.client.check();
    const identity = this.client.namespace;
    const legacy = this.store.db.prepare('SELECT epoch, session_id, uri FROM ov_session_sync WHERE identity IS NULL').all();
    const foreign = [];
    for (const row of legacy) {
      let ours = false;
      try {
        const remote = await this.client.request(`/sessions/${encodeURIComponent(row.session_id)}`);
        ours = remote?.session_id === row.session_id && typeof remote.uri === 'string'
          && remote.uri.startsWith('viking://') && (!row.uri || remote.uri === row.uri);
      } catch (error) {
        // Absent or out of reach here: another namespace's session.
        if (error.httpStatus !== 404 && error.httpStatus !== 403) throw error;
      }
      this.store.db.prepare('UPDATE ov_session_sync SET identity=? WHERE epoch=?').run(ours ? identity : FOREIGN, row.epoch);
      if (!ours) foreign.push(row.epoch);
    }
    if (foreign.length) this.logger.warn('ov_epochs_left_behind', { epochs: foreign });
    const saved = this.store.db.prepare("SELECT value FROM meta WHERE key='ov_session_identity'").get();
    const previous = saved ? JSON.parse(saved.value) : null;
    if (previous?.identity === identity) this.firstEpoch = previous.firstEpoch;
    this.store.db.prepare("INSERT OR REPLACE INTO meta(key,value) VALUES ('ov_session_identity',?)")
      .run(JSON.stringify({ identity, firstEpoch: this.firstEpoch }));
    this.identity = identity;
  }

  // A write timeout is ambiguous. Commits run only after a complete sync, so
  // the uncertain write remains in live messages until it is reconciled.
  // Do not retry an append based only on a timeout: first look for its source ID.
  async hasSource(state, sourceId) {
    for (let offset = 0; ; offset += 100) {
      if (this.stopped) throw new Error('OV sync stopped');
      let content;
      try { content = await this.client.read(`${state.uri}/messages.jsonl`, offset, 100); }
      catch (error) {
        // Confirmed absence is different from a read timeout/permission error.
        if (error.httpStatus === 404 && offset === 0) return false;
        throw error;
      }
      if (typeof content !== 'string') throw new Error('OV live message read returned invalid data');
      const lines = content.split('\n').filter(Boolean);
      for (const line of lines) {
        const message = JSON.parse(line);
        if (message.source_message_ids?.includes(sourceId)) return true;
      }
      if (lines.length < 100) return false;
    }
  }

  // Returns false when an unsettled local delivery stops the cursor; a later
  // checkpoint continues from it once it settles.
  async sync(state, epoch, throughSeq) {
    // Bounded DB reads; no epoch-sized array of bodies is materialized.
    while (state.synced_seq < throughSeq) {
      if (this.stopped) throw new Error('OV sync stopped');
      const row = this.store.db.prepare(`SELECT seq,status,kind FROM cognition_journal
        WHERE epoch=? AND seq>? AND seq<=?
          AND status IN ('PENDING', 'COMMITTED', 'UNCERTAIN')
        ORDER BY seq LIMIT 1`).get(epoch, state.synced_seq, throughSeq);
      if (!row) break;
      if (row.status === 'PENDING') return false;
      if (!syncsToOV(row)) {
        this.store.db.prepare('UPDATE ov_session_sync SET synced_seq=?,pending_seq=NULL WHERE epoch=?').run(row.seq, epoch);
        state.synced_seq = row.seq;
        state.pending_seq = null;
        continue;
      }
      const sourceId = `lark:${epoch}:${row.seq}`;
      if (state.pending_task || !(state.pending_seq === row.seq && await this.hasSource(state, sourceId))) {
        const item = this.store.cognitionJournal(epoch, row.seq, row.seq - 1)[0];
        if (!item) throw new Error('OV sync source record is unavailable');
        this.store.db.prepare('UPDATE ov_session_sync SET pending_seq=? WHERE epoch=?').run(row.seq, epoch);
        state.pending_seq = row.seq;
        await this.write(state, 'append', row.seq, (options) => this.client.append(state.session_id, {
          ...ovAttribution(item),
          content: formatCognitionJournalLine(item),
          created_at: new Date(item.atMs).toISOString(),
          source_message_ids: [sourceId],
        }, options));
      }
      if (this.stopped) throw new Error('OV sync stopped');
      this.store.db.prepare('UPDATE ov_session_sync SET synced_seq=?,pending_seq=NULL,pending_task=NULL WHERE epoch=? AND identity=?')
        .run(row.seq, epoch, this.identity);
      state.synced_seq = row.seq;
      state.pending_seq = null;
      state.pending_task = null;
    }
    return true;
  }

  // OV's recent raw history for a fresh native session. Older history stays in
  // OV archives, which the model reads on demand.
  prepare(epoch) {
    const throughSeq = this.store.cognitionJournalHead(epoch);
    return this.run(async () => {
      const state = await this.session(epoch);
      await this.sync(state, epoch, throughSeq);
      const context = await this.client.context(state.session_id);
      if (this.stopped) throw new Error('OV sync stopped');
      if (!context || !Array.isArray(context.messages)) throw new Error('OV session context is invalid');
      const prepared = { ...withoutWorkingMemory(context), sessionId: state.session_id, throughSeq: state.synced_seq, epoch };
      this.contexts.set(epoch, prepared);
      return prepared;
    });
  }

  // Run only after the local runtime and ingress buffers are idle, so a commit
  // lands on a turn boundary.
  checkpoint(epoch) {
    const throughSeq = this.store.cognitionJournalHead(epoch);
    const current = this.run(async () => {
      await this.bindIdentity();
      if (this.checked.get(epoch) === throughSeq) return;
      const state = await this.session(epoch);
      if (!await this.sync(state, epoch, throughSeq)) return;
      await this.maybeCommit(state);
      this.checked.set(epoch, throughSeq);
    });
    // Retired epochs are finalized in a queue of their own. A full commit is
    // answered only once OV has archived the session, so in the shared queue
    // the history a fresh session needs after /new would wait for all of
    // them. They start once every job already in the shared queue is done
    // (`current` is the last), so an epoch that just retired is never synced
    // by both queues at once; only the current epoch is queued there later.
    const retired = this.runRetired(async () => {
      const failure = await current.then(() => null, (error) => error);
      // Not bound to OV yet, or OV not reached at all: every session would
      // fail alike, so the retired epochs wait for the next check.
      if (this.stopped || !this.identity || unreachable(failure)) return;
      await this.finalizeRetired();
    });
    // Settles once both are done; the current epoch's failure is reported.
    return Promise.allSettled([current, retired]).then(([outcome]) => {
      if (outcome.status === 'rejected') throw outcome.reason;
    });
  }

  runRetired(fn) {
    const work = this.retiredBarrier.then(fn);
    this.retiredBarrier = work.catch(() => {});
    return work;
  }

  // The epoch counter and journal survive /new and crashes, so a retired
  // epoch of this namespace is a durable finalization job even if it was
  // never synced. Retry even if the current head is unchanged, and don't let
  // one failed old session stop another session from making progress.
  async finalizeRetired() {
    const retired = this.store.db.prepare(`SELECT j.epoch, MAX(j.seq) AS head
      FROM cognition_journal j LEFT JOIN ov_session_sync s ON s.epoch=j.epoch
      WHERE j.epoch<? AND j.status IN ('PENDING','COMMITTED','UNCERTAIN')
        AND (s.identity=? OR (s.epoch IS NULL AND j.epoch>=?))
      GROUP BY j.epoch HAVING MAX(j.seq)>COALESCE(s.finalized_seq,-1)
      ORDER BY j.epoch`).all(this.store.getCognitionState().epoch, this.identity, this.firstEpoch);
    for (const row of retired) {
      if (this.stopped) throw new Error('OV sync stopped');
      try {
        const state = await this.session(row.epoch);
        if (state.finalized_seq >= row.head) continue;
        if (!await this.sync(state, row.epoch, row.head)) continue;
        // No retained tail: this session will receive no more user turns.
        await this.commit(state, true, row.head);
      } catch (error) {
        if (this.stopped) throw error;
        this.logger.warn('ov_epoch_finalize_failed', { epoch: row.epoch, error: safeError(error) });
        // A refused connection fails the rest too. A slow or failing session
        // does not: the next one still gets its turn.
        if (unreachable(error)) return;
      }
    }
  }

  async commit(state, final, seq) {
    await this.write(state, final ? 'finalize' : 'commit', seq,
      (options) => this.client.commit(state.session_id, { final, ...options }));
    if (this.stopped) throw new Error('OV sync stopped');
    this.store.db.prepare(`UPDATE ov_session_sync SET pending_task=NULL${final ? ',finalized_seq=?' : ''}
      WHERE epoch=? AND identity=?`).run(...(final ? [seq] : []), state.epoch, this.identity);
    state.pending_task = null;
    if (final) state.finalized_seq = seq;
  }

  // Accepted commits remain durable until archiving completes. A synchronous
  // lost response retains the existing no-op-on-empty retry behavior.
  async maybeCommit(state) {
    const meta = await this.client.request(`/sessions/${encodeURIComponent(state.session_id)}`);
    if (this.stopped) throw new Error('OV sync stopped');
    if ((meta.pending_tokens ?? 0) >= COMMIT_TOKENS) await this.commit(state, false, state.synced_seq);
  }

  stop() { this.stopped = true; }
}
