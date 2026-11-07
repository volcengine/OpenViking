// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { OVConnection, readOVConfig } from './ov-connection.mjs';
import { normalizeEvent } from './event.mjs';
import { senderLabel } from './runtime-input.mjs';

// What the daemon shows of OpenViking memory after a line someone sent, the
// same for every runtime. The instance is one OV user; everything it took part
// in is its own memory, and a person's preferences sit under that person's
// name (preferences/<name>/), the name the line's `from=` shows.
//
// Always, when OV holds them:
// - the owner's preferences: the owner's standing instructions, which govern
//   the agent everywhere (what it may tell whom, how it answers);
// - the sender's preferences, and the sender's section of profile.md, when
//   the sender is someone else.
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
const PERSON_CHARS = 1_500;
const CACHE_TTL_MS = 10 * 60_000;
const MIN_QUERY_CHARS = 2;
const PREFERENCES = 'viking://~/memories/preferences';
const PROFILE = 'viking://~/memories/profile.md';

const escapeAttribute = (value) => String(value).replace(/[&"<>]/g, (char) => (
  { '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;' }[char]));

// One person's key across the forms a name takes: extraction lowercases the
// directory, and a `from=` name has `_` where the display name had spaces.
export const personKey = (name) => String(name ?? '').toLowerCase().replace(/[\s_]+/g, '');

const clip = (text) => (text.length > PERSON_CHARS ? `${text.slice(0, PERSON_CHARS)}…` : text);
const leaf = (uri) => String(uri).replace(/\/+$/, '').split('/').pop() ?? '';

// The section of a multi-person profile.md under one person's H1 heading.
export function profileSection(profile, name) {
  const key = personKey(name);
  if (!key) return null;
  const sections = String(profile ?? '').split(/^(?=# )/m);
  const found = sections.find((section) => personKey(section.split('\n', 1)[0].replace(/^#\s*/, '')) === key);
  const body = found?.split('\n').slice(1).join('\n').trim();
  return body || null;
}

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
    const sender = senderLabel(event);
    const ownerName = owner ? sender : this.ownerName();
    const settled = (promise) => Promise.resolve().then(() => promise).catch((error) => {
      this.logger.debug('ov_recall_skipped', { status: error.httpStatus ?? null });
      return null;
    });
    let timer;
    const deadline = new Promise((resolve) => { timer = setTimeout(resolve, this.timeoutMs); });
    const bounded = (promise) => (promise ? Promise.race([settled(promise), deadline.then(() => null)]) : null);
    try {
      const [ownerPreferences, senderPreferences, senderProfile, entries] = await Promise.all([
        ownerName ? bounded(this.preferences(ownerName)) : null,
        !owner && sender ? bounded(this.preferences(sender)) : null,
        !owner && sender ? bounded(this.profile().then((text) => profileSection(text, sender))) : null,
        this.autoRecall && query.length >= MIN_QUERY_CHARS ? bounded(this.search(query)) : null,
      ]);
      return this.render(event, { ownerName, sender: owner ? null : sender, ownerPreferences, senderPreferences,
        senderProfile, entries: entries ?? [] });
    } finally {
      clearTimeout(timer);
    }
  }

  // The name the owner's lines carry, from the newest line the owner sent.
  ownerName() {
    try {
      const envelope = this.store?.latestOwnerEnvelope?.();
      return envelope ? senderLabel(normalizeEvent(envelope)) : null;
    } catch {
      return null;
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

  async list(uri) {
    try {
      const { result } = await this.connection.json(`/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`);
      return Array.isArray(result) ? result : [];
    } catch (error) {
      if (error.httpStatus === 404) return [];
      throw error;
    }
  }

  profile() {
    return this.cached('profile', () => this.read(PROFILE));
  }

  // One person's preferences, every topic file under their directory joined.
  preferences(name) {
    const key = personKey(name);
    if (!key) return Promise.resolve(null);
    return this.cached(`preferences:${key}`, async () => {
      const people = await this.cached('preferences:', () => this.list(PREFERENCES));
      const directory = people.find((entry) => entry?.isDir && personKey(leaf(entry.uri)) === key);
      if (!directory) return null;
      const files = (await this.list(directory.uri))
        .filter((entry) => !entry?.isDir && /\.md$/.test(entry?.uri ?? '') && !leaf(entry.uri).startsWith('.'));
      const texts = await Promise.all(files.map(async (file) => {
        const text = await this.read(file.uri);
        return text ? `## ${leaf(file.uri).replace(/\.md$/, '')}\n${text}` : null;
      }));
      const joined = texts.filter(Boolean).join('\n\n');
      return joined ? clip(joined) : null;
    });
  }

  // Whose a searched memory is, where its path says: a person's preferences
  // name them; everything else is the agent's own record.
  who(uri, { ownerName, sender }) {
    const match = /\/memories\/preferences\/([^/]+)\//.exec(String(uri));
    if (!match) return null;
    const key = personKey(match[1]);
    if (ownerName && key === personKey(ownerName)) return 'owner';
    if (sender && key === personKey(sender)) return 'sender';
    return match[1];
  }

  render(event, { ownerName, sender, ownerPreferences, senderPreferences, senderProfile, entries }) {
    const memories = [];
    if (ownerPreferences) {
      memories.push(`<memory who="owner" type="preferences">\n${ownerPreferences}\n</memory>`);
    }
    if (senderPreferences) {
      memories.push(`<memory who="sender" type="preferences">\n${senderPreferences}\n</memory>`);
    }
    if (senderProfile) {
      memories.push(`<memory who="sender" type="profile">\n${clip(senderProfile)}\n</memory>`);
    }
    for (const entry of entries) {
      if (!entry.uri) continue;
      const who = this.who(entry.uri, { ownerName, sender });
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
