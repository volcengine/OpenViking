// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { opaqueRef, redactSensitive, safeError } from '../logger.mjs';
import { MeetingNoteArchive, meetingFolder, noteMarkdown, noteTitle, transcriptMarkdown } from './note-archive.mjs';

// Meeting notes into OpenViking, run by the Lark Connector of the one
// instance that has agents[].meetingNotes on, with the owner's own login (the
// user identity, as for search). Every SCAN_EVERY_MS it lists the meetings the
// owner took part in since the last scan (vc +search) and asks each new one
// whether it has an AI note (vc meeting get names it). A meeting without one
// is asked again at each scan until NOTE_WAIT_MS after it ended, as Feishu
// writes a note a few minutes after the meeting. A note found is read with
// its meeting and transcript and stored, and tried again on RETRY_DELAYS_MS
// when Feishu or OV fails. Nothing reaches the Core or wakes the agent.
//
// Feishu/Lark also has events for a note or a meeting of the owner's, but
// tenants do not all offer them to an app, so the scan is all there is.

const NOTE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const MEETING_ID = /^\d{1,32}$/;
const SCAN_EVERY_MS = 15 * 60_000;
const FIRST_SCAN_MS = 30 * 86_400_000;
// A scan starts this long before the last one ended: the search lists ended
// meetings by when they began, and one still running then has ended since.
const SCAN_OVERLAP_MS = 6 * 3_600_000;
// A meeting still without a note this long after it ended has none.
const NOTE_WAIT_MS = 2 * 3_600_000;
// The same for one whose end its record does not give, or that cannot be read.
const UNKNOWN_END_WAIT_MS = 24 * 3_600_000;
const RETRY_DELAYS_MS = [60_000, 300_000, 900_000, 3_600_000, 10_800_000, 21_600_000, 43_200_000];
const STOP_WAIT_MS = 2_000;
const MAX_SEARCH_PAGES = 40;
const NAME_BATCH = 50;
const DOC_STDOUT_LIMIT = 16_000_000;
const NOTE_ARTIFACT = 1;
const VERBATIM_ARTIFACT = 2;

const iso = (ms) => new Date(ms).toISOString();
const seconds = (value) => {
  const number = Number.parseInt(String(value ?? ''), 10);
  return Number.isSafeInteger(number) && number > 0 ? number * 1_000 : null;
};

// What the Connector keeps: one row per meeting the scan found, and how far
// it has scanned. A meeting is `waiting` for its note, `pending` with a note
// to store, then `stored`, or `none` (no note came), `skipped` (the note is not
// a meeting's) or `failed` (out of retries). Private to the daemon user.
export class MeetingNoteState {
  static path(dataDir) {
    return path.join(dataDir, 'meeting-notes', 'meetings.sqlite3');
  }

  constructor(dataDir) {
    if (!path.isAbsolute(dataDir)) throw new Error('Meeting note data directory must be absolute');
    const databasePath = MeetingNoteState.path(dataDir);
    fs.mkdirSync(path.dirname(databasePath), { recursive: true, mode: 0o700 });
    try { fs.chmodSync(path.dirname(databasePath), 0o700); } catch {}
    this.db = new DatabaseSync(databasePath);
    try { fs.chmodSync(databasePath, 0o600); } catch {}
    this.db.exec('PRAGMA busy_timeout = 5000');
    this.db.exec('PRAGMA journal_mode = WAL');
    this.db.exec('PRAGMA synchronous = FULL');
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS meetings (
        meeting_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        note_id TEXT,
        ended_at_ms INTEGER,
        checks INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_at_ms INTEGER,
        found_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL,
        stored_at_ms INTEGER,
        revision INTEGER,
        error TEXT
      );
      CREATE INDEX IF NOT EXISTS meetings_status ON meetings(status, next_at_ms);
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    `);
  }

  get(key) {
    return this.db.prepare('SELECT value FROM meta WHERE key = ?').get(key)?.value ?? null;
  }

  set(key, value) {
    this.db.prepare('INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
      .run(key, String(value));
  }

  meeting(meetingId) {
    return this.db.prepare('SELECT * FROM meetings WHERE meeting_id = ?').get(meetingId) ?? null;
  }

  // A meeting the search listed; one seen before stays as it is.
  found(meetingId, atMs) {
    return this.db.prepare(`INSERT OR IGNORE INTO meetings(meeting_id, status, found_at_ms, updated_at_ms)
      VALUES (?, 'waiting', ?, ?)`).run(meetingId, atMs, atMs).changes > 0;
  }

  waiting() {
    return this.db.prepare("SELECT * FROM meetings WHERE status = 'waiting' ORDER BY found_at_ms, meeting_id").all();
  }

  // What asking the meeting told: its note, to store now; or none yet, and
  // whether to stop waiting for one.
  checked(meetingId, { noteId = null, endedAtMs = null, givenUp = false, atMs }) {
    this.db.prepare(`UPDATE meetings SET status = ?, note_id = COALESCE(?, note_id), ended_at_ms = COALESCE(?, ended_at_ms),
      checks = checks + 1, next_at_ms = ?, updated_at_ms = ? WHERE meeting_id = ?`)
      .run(noteId ? 'pending' : givenUp ? 'none' : 'waiting', noteId, endedAtMs, noteId ? atMs : null, atMs, meetingId);
  }

  due(atMs) {
    return this.db.prepare(`SELECT * FROM meetings WHERE status = 'pending' AND next_at_ms <= ?
      ORDER BY next_at_ms, meeting_id LIMIT 1`).get(atMs) ?? null;
  }

  nextAt() {
    const row = this.db.prepare("SELECT MIN(next_at_ms) AS at FROM meetings WHERE status = 'pending'").get();
    return row?.at === null || row?.at === undefined ? null : Number(row.at);
  }

  settle(meetingId, { status, revision = null, error = null, attempts, nextAtMs = null, atMs }) {
    this.db.prepare(`UPDATE meetings SET status = ?, revision = COALESCE(?, revision), error = ?, attempts = ?,
      next_at_ms = ?, updated_at_ms = ?, stored_at_ms = CASE WHEN ? = 'stored' THEN ? ELSE stored_at_ms END
      WHERE meeting_id = ?`).run(status, revision, error, attempts, nextAtMs, atMs, status, atMs, meetingId);
  }

  counts() {
    return Object.fromEntries(this.db.prepare('SELECT status, COUNT(*) AS n FROM meetings GROUP BY status').all()
      .map(({ status, n }) => [status, Number(n)]));
  }

  close() {
    try { this.db.close(); } catch {}
  }
}

// A failure that says why without what Feishu or OV answered.
function failure(error) {
  return redactSensitive(safeError(error)).replace(/\b(?:om|oc|ou|on|cli)_[A-Za-z0-9_-]{6,}\b/g, '[LARK_ID]').slice(0, 300);
}

export class MeetingNoteSync {
  static forWorker({ config, registry, logger, profile }) {
    return new MeetingNoteSync({ config, registry, logger: logger.child?.({ component: 'meeting-notes' }) ?? logger, profile });
  }

  constructor({
    config, registry, logger, profile, archive = null, state = null,
    now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout,
  }) {
    Object.assign(this, { config, registry, logger, archive, state, now, setTimer, clearTimer });
    this.profile = typeof profile === 'function' ? profile : () => profile;
    this.timer = null;
    this.ticker = null;
    this.running = null;
    this.stopped = true;
  }

  start() {
    this.stopped = false;
    if (!this.config.openVikingConfigPath) {
      this.logger.error('meeting_notes_unavailable', { reason: 'no OpenViking configuration' });
      return;
    }
    try {
      this.state ??= new MeetingNoteState(this.config.dataDir);
      this.archive ??= MeetingNoteArchive.fromConfigFile(this.config.openVikingConfigPath, { logger: this.logger });
    } catch (error) {
      this.logger.error('meeting_notes_unavailable', { reason: failure(error) });
      this.state?.close();
      this.state = null;
      return;
    }
    this.logger.info('meeting_notes_started', { meetings: this.state.counts() });
    this.repeat();
  }

  // Runs `work` after whatever runs now; a failure is logged, never thrown.
  after(work) {
    this.running = Promise.resolve(this.running).then(() => (this.stopped ? null : work())).catch((error) => {
      this.logger.error('meeting_notes_error', { error: failure(error) });
    });
    return this.running;
  }

  repeat() {
    if (this.stopped) return;
    this.after(() => this.tick());
    this.ticker = this.setTimer(() => this.repeat(), SCAN_EVERY_MS);
    this.ticker?.unref?.();
  }

  // One scan, then the notes it found.
  async tick() {
    await this.scan().catch((error) => {
      if (!this.stopped) this.logger.warn('meeting_notes_scan_failed', { error: failure(error) });
    });
    await this.work();
  }

  // A note being stored stays pending until it settles, so a stop waits only
  // briefly for it; the worker's process group takes its lark-cli along.
  async stop() {
    this.stopped = true;
    if (this.timer) this.clearTimer(this.timer);
    if (this.ticker) this.clearTimer(this.ticker);
    this.timer = null;
    this.ticker = null;
    let waited;
    await Promise.race([Promise.resolve(this.running), new Promise((resolve) => { waited = setTimeout(resolve, STOP_WAIT_MS); })]);
    clearTimeout(waited);
    this.state?.close();
    this.state = null;
  }

  async cli(args, label, { timeoutMs = 60_000, stdoutLimit, cwd = this.config.projectDir } = {}) {
    const profile = this.profile();
    if (!profile) throw new Error('no lark-cli profile');
    const result = await this.registry.run(this.config.larkCliBin, ['--profile', profile, ...args, '--as', 'user', '--format', 'json'],
      { cwd, timeoutMs, ...(stdoutLimit ? { stdoutLimit } : {}) });
    if (result.timedOut) throw new Error(`lark-cli ${label} timed out`);
    const json = (text) => { try { return JSON.parse(text); } catch { return null; } };
    const parsed = json(result.stdout);
    if (result.code !== 0 || parsed?.ok === false || !parsed) {
      // A refusal (a missing scope, say) comes as a JSON envelope on stderr.
      const error = parsed?.error ?? json(result.stderr.slice(result.stderr.indexOf('{')))?.error ?? {};
      const detail = error.message || result.stderr.trim().split('\n').at(-1) || `exit ${result.code}`;
      throw Object.assign(new Error(`lark-cli ${label} failed${error.code ? ` (${error.code})` : ''}: ${detail}`),
        { code: error.code ?? null, subtype: error.subtype ?? null });
    }
    return parsed.data ?? {};
  }

  // The meetings since the last scan, then every meeting still waiting for
  // its note: a meeting names its note itself (vc +detail would need
  // vc:record:readonly as well). One that cannot be read is asked again at the
  // next scan; the scan fails only when none can be read.
  async scan() {
    const now = this.now();
    const until = Number(this.state.get('scanned_until_ms')) || 0;
    const from = until ? Math.max(until - SCAN_OVERLAP_MS, now - FIRST_SCAN_MS) : now - FIRST_SCAN_MS;
    let found = 0;
    let pageToken = null;
    for (let page = 0; page < MAX_SEARCH_PAGES; page += 1) {
      const data = await this.cli(['vc', '+search', '--start', iso(from), '--end', iso(now), '--page-size', '30',
        ...(pageToken ? ['--page-token', pageToken] : [])], 'meeting search');
      if (this.stopped) return;
      for (const item of data.items ?? []) {
        if (MEETING_ID.test(String(item?.id ?? '')) && this.state.found(String(item.id), now)) found += 1;
      }
      if (!data.has_more || !data.page_token) break;
      pageToken = data.page_token;
    }
    const waiting = this.state.waiting();
    let notes = 0;
    let unreadable = 0;
    let firstError = null;
    for (const row of waiting) {
      let meeting;
      try {
        ({ meeting = {} } = await this.cli(['vc', 'meeting', 'get', '--params', JSON.stringify({ meeting_id: row.meeting_id })], 'meeting get'));
      } catch (error) {
        unreadable += 1;
        firstError ??= error;
        // Never readable in a day: it is not going to be.
        if (now - Number(row.found_at_ms) > UNKNOWN_END_WAIT_MS) this.state.checked(row.meeting_id, { givenUp: true, atMs: now });
        continue;
      }
      if (this.stopped) return;
      const noteId = NOTE_ID.test(String(meeting.note_id ?? '')) ? String(meeting.note_id) : null;
      const endedAtMs = seconds(meeting.end_time);
      const givenUp = endedAtMs ? now - endedAtMs > NOTE_WAIT_MS : now - Number(row.found_at_ms) > UNKNOWN_END_WAIT_MS;
      this.state.checked(row.meeting_id, { noteId, endedAtMs, givenUp, atMs: now });
      if (noteId) notes += 1;
    }
    if (firstError && unreadable === waiting.length) throw firstError;
    this.state.set('scanned_until_ms', now);
    this.logger.info('meeting_notes_scanned', {
      since: iso(from), found, checked: waiting.length, notes, ...(unreadable ? { unreadable, error: failure(firstError) } : {}),
    });
  }

  arm() {
    if (this.stopped || !this.state) return;
    if (this.timer) this.clearTimer(this.timer);
    const next = this.state.nextAt();
    if (next === null) return;
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.after(() => this.work());
    }, Math.min(Math.max(0, next - this.now()), SCAN_EVERY_MS));
    this.timer?.unref?.();
  }

  // The notes due, one at a time.
  async work() {
    try {
      while (!this.stopped && this.state) {
        const row = this.state.due(this.now());
        if (!row) break;
        await this.store(row);
      }
    } finally {
      this.arm();
    }
  }

  async store(row) {
    const attempts = Number(row.attempts) + 1;
    try {
      const result = await this.archiveNote(row);
      if (this.stopped) return;
      const atMs = this.now();
      if (result.skipped) {
        this.state.settle(row.meeting_id, { status: 'skipped', error: result.skipped, attempts, atMs });
        this.logger.info('meeting_note_skipped', { meeting: opaqueRef(row.meeting_id), reason: result.skipped });
        return;
      }
      this.state.settle(row.meeting_id, { status: 'stored', revision: result.revision, attempts, atMs });
      this.logger.info('meeting_note_stored', {
        meeting: opaqueRef(row.meeting_id), attempts, noteChars: result.noteChars, transcriptChars: result.transcriptChars,
        chunked: result.chunked, lagMs: atMs - Number(row.found_at_ms),
      });
    } catch (error) {
      if (this.stopped) return;
      const delay = RETRY_DELAYS_MS[attempts - 1];
      const atMs = this.now();
      this.state.settle(row.meeting_id, {
        status: delay === undefined ? 'failed' : 'pending', error: failure(error), attempts,
        nextAtMs: delay === undefined ? null : atMs + delay, atMs,
      });
      this.logger.warn('meeting_note_failed', {
        meeting: opaqueRef(row.meeting_id), attempts, error: failure(error), ...(delay === undefined ? { final: true } : { retryInMs: delay }),
      });
    }
  }

  // Reads one note and its meeting, and stores them. Resolves with
  // { skipped } for a note that is not the meeting's.
  async archiveNote(row) {
    const { note = {} } = await this.cli(['api', 'GET', `/open-apis/vc/v1/notes/${encodeURIComponent(row.note_id)}`], 'note detail');
    const source = note.note_source ?? {};
    if (source.source_type && source.source_type !== 'meeting') return { skipped: `the note comes from ${String(source.source_type).slice(0, 40)}, not a meeting` };
    if (source.source_entity_id && String(source.source_entity_id) !== row.meeting_id) return { skipped: 'the note belongs to another meeting' };
    const artifact = (type) => (note.artifacts ?? []).find((item) => item?.artifact_type === type && item.doc_token)?.doc_token ?? null;
    const docToken = artifact(NOTE_ARTIFACT);
    const verbatimToken = artifact(VERBATIM_ARTIFACT);
    if (!docToken && !verbatimToken) throw new Error('the note has no documents yet');
    const meeting = await this.meeting(row.meeting_id);
    const summary = docToken ? await this.document(docToken) : null;
    let transcript = verbatimToken ? (await this.document(verbatimToken)).content : null;
    // A document that exists but is still empty is not written yet.
    if ((summary && !summary.content.trim()) || (verbatimToken && !transcript.trim())) throw new Error('the note documents are still empty');
    if (!transcript) transcript = await this.unifiedTranscript(row.note_id);
    const folder = meetingFolder(this.archive.root, row.meeting_id);
    const transcriptUri = transcript ? `${folder}/transcript.md` : null;
    const noteText = noteMarkdown({
      meeting, summary: summary?.content ?? null, transcriptUri, storedAtMs: this.now(),
      note: { id: row.note_id, docToken, verbatimToken, revision: summary?.revision ?? null },
    });
    const title = noteTitle(summary?.content) ?? null;
    const transcriptText = transcript ? transcriptMarkdown({ meeting, transcript, noteUri: `${folder}/note.md`, title }) : null;
    const stored = await this.archive.store(row.meeting_id, { note: noteText, transcript: transcriptText });
    return { revision: summary?.revision ?? null, chunked: stored.chunked, noteChars: noteText.length, transcriptChars: transcriptText?.length ?? 0 };
  }

  // The meeting as the note's header shows it. What cannot be read leaves
  // just the id: the note is stored anyway.
  async meeting(meetingId) {
    let meeting;
    try {
      ({ meeting = {} } = await this.cli(['vc', 'meeting', 'get', '--params', JSON.stringify({ meeting_id: meetingId, with_participants: true })], 'meeting get'));
    } catch (error) {
      this.logger.warn('meeting_note_meeting_unreadable', { meeting: opaqueRef(meetingId), error: failure(error) });
      return { id: meetingId };
    }
    const people = (meeting.participants ?? []).filter((person) => person?.user_type === 1 && /^ou_[A-Za-z0-9_-]+$/.test(String(person.id)));
    const host = /^ou_[A-Za-z0-9_-]+$/.test(String(meeting.host_user?.id ?? '')) ? meeting.host_user.id : null;
    const names = await this.names([...new Set([...people.map((person) => person.id), ...(host ? [host] : [])])]);
    return {
      id: meetingId,
      number: /^\d{1,20}$/.test(String(meeting.meeting_no ?? '')) ? String(meeting.meeting_no) : null,
      topic: meeting.topic ?? null,
      startMs: seconds(meeting.start_time),
      endMs: seconds(meeting.end_time),
      host: host ? names.get(host)?.name ?? null : null,
      participants: people.map((person) => ({ name: names.get(person.id)?.name ?? null })),
      participantCount: Number.parseInt(String(meeting.participant_count ?? ''), 10) || people.length || null,
      external: (meeting.participants ?? []).some((person) => person?.is_external) || [...names.values()].some((entry) => entry.external),
      calendarEventId: typeof meeting.calendar_event_id === 'string' && meeting.calendar_event_id ? meeting.calendar_event_id.slice(0, 200) : null,
    };
  }

  // open_id -> { name, external }, for as many as the owner can look up.
  async names(ids) {
    const found = new Map();
    for (let index = 0; index < ids.length; index += NAME_BATCH) {
      try {
        const { users = [] } = await this.cli(['contact', '+search-user', '--user-ids', ids.slice(index, index + NAME_BATCH).join(',')], 'user lookup');
        for (const user of users) {
          if (user?.open_id && user.localized_name) found.set(user.open_id, { name: String(user.localized_name), external: Boolean(user.is_cross_tenant) });
        }
      } catch (error) {
        this.logger.warn('meeting_note_names_unavailable', { error: failure(error) });
        break;
      }
    }
    return found;
  }

  async document(token) {
    const { document = {} } = await this.cli(['docs', '+fetch', '--doc', token, '--doc-format', 'im-markdown'], 'document fetch',
      { timeoutMs: 120_000, stdoutLimit: DOC_STDOUT_LIMIT });
    const revision = Number.parseInt(String(document.revision_id ?? ''), 10);
    return { content: typeof document.content === 'string' ? document.content : '', revision: Number.isSafeInteger(revision) ? revision : null };
  }

  // A note shown as one page has its transcript in the note itself, which
  // only `note +transcript` can write out. No transcript leaves just the note.
  async unifiedTranscript(noteId) {
    const directory = path.join(this.config.tempDir || path.join(this.config.dataDir, 'tmp'), 'meeting-notes');
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const name = `${noteId}.md`;
    try {
      await this.cli(['note', '+transcript', '--note-id', noteId, '--output', `./${name}`, '--overwrite', '--transcript-format', 'markdown'],
        'note transcript', { cwd: directory, timeoutMs: 120_000 });
      const text = fs.readFileSync(path.join(directory, name), 'utf8');
      return text.trim() ? text : null;
    } catch (error) {
      this.logger.info('meeting_note_without_transcript', { note: opaqueRef(noteId), error: failure(error) });
      return null;
    } finally {
      fs.rmSync(path.join(directory, name), { force: true });
    }
  }
}
