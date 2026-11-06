// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { lookupMemberName } from './roster.mjs';
import { OVConnection, readOVConfig } from './ov-connection.mjs';
import { peerForEvent } from './ov-peer.mjs';

// Automatic recall, one OpenViking context search per line someone sent, run by
// the daemon so every runtime gets the same and the sender can be named: the
// runtime plugins see one process, never who wrote a line.
//
// The search covers the owner's own memory and stored material, and the peers'
// (ov-peer.mjs). With scope `all` every peer is searched and the sender's
// memories rank first; with `actor` only the sender's are (an instance facing
// outsiders, where one person's memory is never another's context). The
// sender's profile goes first when OV has one: search never returns profiles.
//
// Bounded like the roster warm-up beside it: past the deadline, or on any
// failure, the line goes on without recall. Each entry says whose it is; the
// policy (core.md) says what that means for disclosure.

const DEFAULT_TIMEOUT_MS = 3_000;
const MAX_TOKENS = 1_600;
const SCORE_THRESHOLD = 0.35;
// Entries served for the session's last few turns are not served again.
const DEDUP_TURNS = 5;
const PROFILE_CHARS = 1_500;
const PROFILE_TTL_MS = 10 * 60_000;
const MIN_QUERY_CHARS = 2;

const escapeAttribute = (value) => String(value).replace(/[&"<>]/g, (char) => (
  { '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;' }[char]));

// The peer a memory URI is filed under, or null for the owner's own.
function peerOfUri(uri) {
  const match = /^viking:\/\/(?:~|user\/[^/]+)\/peers\/([^/]+)\//.exec(String(uri));
  return match ? match[1] : null;
}

export class OVRecall {
  constructor({ configPath, scope = 'all', store = null, logger, fetchImpl = globalThis.fetch,
    timeoutMs = DEFAULT_TIMEOUT_MS, clock = Date.now } = {}) {
    this.connection = new OVConnection(readOVConfig(configPath), { fetch: fetchImpl, timeoutMs });
    this.scope = scope === 'actor' ? 'actor' : 'all';
    this.store = store;
    this.logger = logger;
    this.timeoutMs = timeoutMs;
    this.clock = clock;
    this.profiles = new Map();
  }

  // The recall block to show after an event's line, or '' when there is none.
  // Never rejects.
  async forEvent(event, { owner = false } = {}) {
    const query = String(event.content ?? '').trim();
    if (query.length < MIN_QUERY_CHARS) return '';
    const peer = owner ? null : peerForEvent(event);
    try {
      // A profile that cannot be read leaves the search results standing.
      const [entries, profile] = await Promise.all([this.search(query, peer),
        peer ? this.profile(peer).catch(() => null) : null]);
      return this.render(event, { peer, entries, profile });
    } catch (error) {
      this.logger.debug('ov_recall_skipped', { status: error.httpStatus ?? null });
      return '';
    }
  }

  async search(query, peer) {
    const session = this.sessionId();
    const body = {
      query, mode: 'context', score_threshold: SCORE_THRESHOLD, max_tokens: MAX_TOKENS,
      ...(this.scope === 'actor' ? { peer_scope: 'actor' } : {}),
      ...(session ? { session_id: session, dedup_turns: DEDUP_TURNS, query_expansion: 'off' } : {}),
    };
    // The actor peer ranks the sender's memories first (all) or limits the
    // peers searched to the sender (actor). The owner is no peer.
    const headers = { ...this.connection.headers, ...(peer ? { 'X-OpenViking-Actor-Peer': peer } : {}) };
    const { result } = await this.connection.send('/api/v1/search/search',
      { method: 'POST', headers, body: JSON.stringify(body) }, this.timeoutMs);
    return Array.isArray(result?.entries) ? result.entries : [];
  }

  // The OV session this daemon mirrors the conversation into, once OV has it:
  // the server keeps which entries it already served there. The ledger is a
  // file of its own, never a session message.
  sessionId() {
    return this.store?.ovSessionFor(this.store.getCognitionState().epoch)?.session_id ?? null;
  }

  // The sender's profile, cached a while (a missing one too).
  async profile(peer) {
    const cached = this.profiles.get(peer);
    if (cached && this.clock() - cached.atMs < PROFILE_TTL_MS) return cached.text;
    const uri = `viking://~/peers/${peer}/memories/profile.md`;
    let text = null;
    try {
      const { result } = await this.connection.json(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
      text = typeof result === 'string' && result.trim() ? result.trim() : null;
    } catch (error) {
      if (error.httpStatus !== 404) throw error;
    }
    this.profiles.set(peer, { text, atMs: this.clock() });
    return text;
  }

  // Who a memory is from, for the model: the owner, the sender of this line,
  // or another person, by the name this chat knows them by where it can.
  who(event, uri, peer) {
    const filed = peerOfUri(uri);
    if (!filed) return 'owner';
    if (filed === peer) return 'sender';
    const lark = /^lark-(ou_[A-Za-z0-9_-]+)$/.exec(filed);
    return (lark && event.chatId && lookupMemberName(event.chatId, lark[1])) || filed;
  }

  render(event, { peer, entries, profile }) {
    const memories = [];
    if (profile) {
      const text = profile.length > PROFILE_CHARS ? `${profile.slice(0, PROFILE_CHARS)}…` : profile;
      memories.push(`<memory who="sender" type="profile">\n${text}\n</memory>`);
    }
    for (const entry of entries) {
      if (!entry.uri) continue;
      const attributes = `who="${escapeAttribute(this.who(event, entry.uri, peer))}" type="${escapeAttribute(entry.category ?? '')}"`
        + ` uri="${escapeAttribute(entry.uri)}"`;
      const text = String(entry.text ?? '').trim();
      memories.push(text ? `<memory ${attributes}>\n${text}\n</memory>` : `<memory ${attributes} />`);
    }
    if (!memories.length) return '';
    const line = event.messageId ? `msg=${event.messageId}` : `event=${event.eventId}`;
    return [`[daemon recall ${line}: OpenViking memory that may bear on this line]`, ...memories].join('\n');
  }
}
