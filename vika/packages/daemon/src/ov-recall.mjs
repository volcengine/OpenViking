// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { OVConnection, readOVConfig } from './ov-connection.mjs';
import { OV_PREFERENCES, directoryPerson, findPersonDirectory, personDirectory, personId, scanPeople } from './ov-person.mjs';

// What the daemon shows of OpenViking memory after a line someone sent, the
// same for every runtime. The instance is one OV user; everything it took part
// in is its own memory, and a person's preferences sit in the directory the
// Core keeps for that person (preferences/owner/, preferences/<name>~<id>/;
// ov-person.mjs), found by its key, never by a name.
//
// Always, when OV holds them:
// - the owner's preferences: the owner's standing instructions, which govern
//   the agent everywhere (what it may tell whom, how it answers);
// - the sender's preferences, when the sender is someone else.
// With agents[].ovAutoRecall, also one context search on the line itself.
// Anything else the agent looks up with the openviking_* tools.
//
// Bounded like the roster warm-up beside it: past the deadline, or on any
// failure, the line goes on with what arrived in time. The policy (core.md)
// says what each part means for disclosure.

const DEFAULT_TIMEOUT_MS = 3_000;
const MAX_TOKENS = 1_600;
const SCORE_THRESHOLD = 0.35;
// Entries served for the session's last few turns are not served again.
const DEDUP_TURNS = 5;
// How much of a person's preferences shows after a line: the owner's are the
// standing rules, so they get more. Topics go newest first and whole; the
// ones that do not fit are named, to be read on demand.
const OWNER_CHARS = 3_000;
const PERSON_CHARS = 1_500;
const READ_BATCH = 8;
const CACHE_TTL_MS = 10 * 60_000;
const MIN_QUERY_CHARS = 2;

const escapeAttribute = (value) => String(value).replace(/[&"<>]/g, (char) => (
  { '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;' }[char]));

const leaf = (uri) => String(uri).replace(/\/+$/, '').split('/').pop() ?? '';
// OV appends its bookkeeping to a memory file it stores.
const withoutFields = (text) => String(text).replace(/\n*<!-- MEMORY_FIELDS[\s\S]*?-->\s*$/, '').trim();

export class OVRecall {
  constructor({ configPath, autoRecall = false, store = null, logger, fetchImpl = globalThis.fetch,
    timeoutMs = DEFAULT_TIMEOUT_MS, clock = Date.now } = {}) {
    this.configPath = configPath;
    this.fetchImpl = fetchImpl;
    this.connectionValue = null;
    this.autoRecall = Boolean(autoRecall);
    this.store = store;
    this.logger = logger;
    this.timeoutMs = timeoutMs;
    this.clock = clock;
    this.cache = new Map();
  }

  // The instance's connection, built on first use so a daemon whose OV config
  // is unusable still starts: recall then just shows nothing.
  get connection() {
    this.connectionValue ??= new OVConnection(readOVConfig(this.configPath), { fetch: this.fetchImpl, timeoutMs: this.timeoutMs });
    return this.connectionValue;
  }

  // The recall block to show after an event's line, or '' when there is none.
  // Never rejects.
  async forEvent(event, { owner = false } = {}) {
    const query = String(event.content ?? '').trim();
    const sender = owner ? null : personId(event);
    const senderDirectory = sender ? personDirectory(event) : null;
    const settled = (promise) => Promise.resolve().then(() => promise).catch((error) => {
      this.logger.debug('ov_recall_skipped', { status: error.httpStatus ?? null });
      return null;
    });
    let timer;
    const deadline = new Promise((resolve) => { timer = setTimeout(resolve, this.timeoutMs); });
    const bounded = (promise) => (promise ? Promise.race([settled(promise), deadline.then(() => null)]) : null);
    try {
      const [ownerPreferences, senderPreferences, entries] = await Promise.all([
        bounded(this.preferences({ owner: true })),
        sender ? bounded(this.preferences({ id: sender, directory: senderDirectory })) : null,
        this.autoRecall && query.length >= MIN_QUERY_CHARS ? bounded(this.search(query)) : null,
      ]);
      return this.render(event, { sender, ownerPreferences, senderPreferences, entries: entries ?? [] });
    } finally {
      clearTimeout(timer);
    }
  }

  async search(query) {
    const session = this.sessionId();
    const body = {
      query, mode: 'context', score_threshold: SCORE_THRESHOLD, max_tokens: MAX_TOKENS,
      ...(session ? { session_id: session, dedup_turns: DEDUP_TURNS, query_expansion: 'off' } : {}),
    };
    const { result } = await this.connection.send('/api/v1/search/search',
      { method: 'POST', headers: this.connection.headers, body: JSON.stringify(body) }, this.timeoutMs);
    return Array.isArray(result?.entries) ? result.entries : [];
  }

  // The OV session this daemon mirrors the conversation into, once OV has it:
  // the server keeps which entries it already served there. The ledger is a
  // file of its own, never a session message.
  sessionId() {
    return this.store?.ovSessionFor(this.store.getCognitionState().epoch)?.session_id ?? null;
  }

  // Preferences changed (openviking_remember): read them afresh next time.
  forgetPreferences() {
    for (const key of this.cache.keys()) if (key.startsWith('preferences:')) this.cache.delete(key);
  }

  // A cached read, a missing value too; a failure is not cached.
  async cached(key, load) {
    const hit = this.cache.get(key);
    if (hit && this.clock() - hit.atMs < CACHE_TTL_MS) return hit.value;
    const value = await load();
    this.cache.set(key, { value, atMs: this.clock() });
    return value;
  }

  async read(uri) {
    try {
      const { result } = await this.connection.json(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
      return typeof result === 'string' && result.trim() ? result.trim() : null;
    } catch (error) {
      if (error.httpStatus === 404) return null;
      throw error;
    }
  }

  // One person's preferences ({ owner } or { id, directory }), their topics
  // joined under their names within the person's budget. Their directory is
  // found under their name now, or, renamed since, in a whole-tree scan kept a
  // while like everything else here.
  preferences(person) {
    const key = person.owner ? 'owner' : person.id;
    if (!key) return Promise.resolve(null);
    return this.cached(`preferences:${key}`, async () => {
      const json = (route) => this.connection.json(route);
      const found = await findPersonDirectory(json, person, () => this.cached('preferences:', () => scanPeople(json)));
      if (!found) return null;
      const files = found.entries
        .filter((entry) => !entry?.isDir && /\.md$/.test(entry?.uri ?? '') && !leaf(entry.uri).startsWith('.'))
        .sort((a, b) => String(b.modTime ?? '').localeCompare(String(a.modTime ?? '')));
      return this.within(files, person.owner ? OWNER_CHARS : PERSON_CHARS, `${OV_PREFERENCES}/${found.name}/`);
    });
  }

  // Topics newest first, each whole, while they fit; the rest by name, so the
  // model knows they exist and where to read them. Files past the budget are
  // not read at all.
  async within(files, budget, directory) {
    const topic = (file) => leaf(file.uri).replace(/\.md$/, '');
    const shown = [];
    const rest = [];
    let used = 0;
    let full = false;
    for (let start = 0; start < files.length; start += READ_BATCH) {
      if (full) {
        rest.push(...files.slice(start).map(topic));
        break;
      }
      const batch = files.slice(start, start + READ_BATCH);
      const texts = await Promise.all(batch.map((file) => this.read(file.uri)));
      batch.forEach((file, index) => {
        const text = withoutFields(texts[index] ?? '');
        if (!text) return;
        const block = `## ${topic(file)}\n${text}`;
        if (used + block.length <= budget) {
          shown.push(block);
          used += block.length + 2;
        } else if (shown.length === 0) {
          shown.push(`${block.slice(0, budget)}…`);
          used = budget;
          full = true;
        } else {
          rest.push(topic(file));
          full = true;
        }
      });
    }
    if (shown.length === 0) return null;
    return shown.join('\n\n')
      + (rest.length ? `\n\n(Not shown, older: ${rest.join(', ')}. Read them with openviking_read under ${directory}.)` : '');
  }

  // Whose a searched memory is, where its path says: a person's preferences
  // name them; everything else is the agent's own record.
  who(uri, { sender }) {
    const match = /\/memories\/preferences\/([^/]+)\//.exec(String(uri));
    if (!match) return null;
    const whose = directoryPerson(match[1]);
    if (whose?.owner) return 'owner';
    if (sender && whose?.id === sender) return 'sender';
    return match[1];
  }

  render(event, { sender, ownerPreferences, senderPreferences, entries }) {
    const memories = [];
    if (ownerPreferences) {
      memories.push(`<memory who="owner" type="preferences">\n${ownerPreferences}\n</memory>`);
    }
    if (senderPreferences) {
      memories.push(`<memory who="sender" type="preferences">\n${senderPreferences}\n</memory>`);
    }
    for (const entry of entries) {
      if (!entry.uri) continue;
      const who = this.who(entry.uri, { sender });
      const attributes = `${who ? `who="${escapeAttribute(who)}" ` : ''}type="${escapeAttribute(entry.category ?? '')}"`
        + ` uri="${escapeAttribute(entry.uri)}"`;
      const text = String(entry.text ?? '').trim();
      memories.push(text ? `<memory ${attributes}>\n${text}\n</memory>` : `<memory ${attributes} />`);
    }
    if (!memories.length) return '';
    const line = event.messageId ? `msg=${event.messageId}` : `event=${event.eventId}`;
    return [`[daemon recall ${line}: OpenViking memory that may bear on this line]`, ...memories].join('\n');
  }
}
