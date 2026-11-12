// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Chat member roster: resolves ou_ open_ids to human display names (and back)
// so the model sees readable names instead of id stubs and can mention by name.
// Names are untrusted remote data; everything stored here is pre-sanitized so a
// malicious display name can never forge bracket metadata in a formatted line.

const DEFAULT_TTL_MS = 10 * 60 * 1_000;
const DEFAULT_WARM_TIMEOUT_MS = 1_500;
const MAX_NAME_CHARS = 40;

// A from= label must stay one whitespace-free token inside the daemon-authenticated
// leading bracket. Strategy: control chars and the bracket-metadata syntax chars
// `[`, `]`, `=` collapse to spaces, then all inner whitespace becomes `_`. A name
// like "x] [chat=oc_evil" therefore renders as the harmless token "x_chat_oc_evil".
export function sanitizeMemberName(raw) {
  const cleaned = String(raw ?? '')
    .replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ')
    .replace(/[[\]=]/g, ' ')
    .trim()
    .replace(/\s+/g, '_')
    .slice(0, MAX_NAME_CHARS);
  return cleaned || null;
}

// Case-insensitive match key treating `_` (our whitespace substitute) and real
// whitespace as equivalent, so the model can echo either the sanitized or the
// original spelling of a name.
function nameKey(name) {
  return String(name ?? '').toLocaleLowerCase().replace(/[\s_]+/g, ' ').trim();
}

// Defensive parse of `lark-cli im +chat-members-list` JSON: users[]/bots[]
// buckets (plus common fallbacks) of entries carrying a name and an open_id.
export function collectRosterMembers(data) {
  const members = [];
  const seen = new Set();
  const buckets = [data?.users, data?.bots, data?.members, data?.items];
  for (const bucket of buckets) {
    if (!Array.isArray(bucket)) continue;
    for (const entry of bucket) {
      if (!entry || typeof entry !== 'object') continue;
      const id = [entry.open_id, entry.member_id, entry.id]
        .find((value) => typeof value === 'string' && /^ou_[A-Za-z0-9_-]{8,}$/.test(value));
      const name = sanitizeMemberName(entry.name);
      if (!id || !name || seen.has(id)) continue;
      seen.add(id);
      members.push({ id, name });
    }
  }
  return members;
}

export class ChatRoster {
  constructor({
    fetchMembers,
    ttlMs = DEFAULT_TTL_MS,
    warmTimeoutMs = DEFAULT_WARM_TIMEOUT_MS,
    logger = null,
    now = Date.now,
  }) {
    this.fetchMembers = fetchMembers; // async (chatId) => raw lark-cli data or member array
    this.ttlMs = ttlMs;
    this.warmTimeoutMs = warmTimeoutMs;
    this.logger = logger;
    this.now = now;
    this.cache = new Map();    // chatId -> { atMs, members, byId, byKey }
    this.inflight = new Map(); // chatId -> Promise<boolean>
  }

  _fresh(chatId) {
    const entry = this.cache.get(chatId);
    return entry && this.now() - entry.atMs < this.ttlMs ? entry : null;
  }

  // Sync, cache-only lookup used by the line formatters. Null means "unknown":
  // callers fall back to the shortened id stub.
  nameForId(chatId, openId) {
    if (!chatId || !openId) return null;
    return this.cache.get(chatId)?.byId.get(openId) ?? null;
  }

  // Bounded best-effort refresh for the ingest path. Resolves within
  // warmTimeoutMs even if lark-cli is slow; never rejects; a failed fetch keeps
  // any stale cache so formatting degrades to stubs instead of breaking a turn.
  async warm(chatId, { force = false } = {}) {
    if (!chatId) return false;
    if (!force && this._fresh(chatId)) return true;
    const refresh = this._refresh(chatId);
    let timer = null;
    const timeout = new Promise((resolve) => {
      // Deliberately not unref'd: this short timer is the guarantee that warm()
      // resolves even when lark-cli hangs, so it must keep the loop alive.
      timer = setTimeout(() => resolve(false), this.warmTimeoutMs);
    });
    try {
      return await Promise.race([refresh, timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  _refresh(chatId) {
    const running = this.inflight.get(chatId);
    if (running) return running;
    const task = (async () => {
      try {
        const raw = await this.fetchMembers(chatId);
        const members = Array.isArray(raw)
          ? raw
            .map((member) => ({ id: member?.id, name: sanitizeMemberName(member?.name) }))
            .filter((member) => typeof member.id === 'string' && member.id && member.name)
          : collectRosterMembers(raw);
        const byId = new Map(members.map((member) => [member.id, member.name]));
        const byKey = new Map();
        for (const member of members) {
          const key = nameKey(member.name);
          if (!byKey.has(key)) byKey.set(key, []);
          byKey.get(key).push(member);
        }
        this.cache.set(chatId, { atMs: this.now(), members, byId, byKey });
        return true;
      } catch (error) {
        // Roster is a best-effort enrichment: keep any stale entry and move on.
        this.logger?.warn?.('roster_fetch_failed', {
          error: error instanceof Error ? error.message : String(error),
        });
        return false;
      } finally {
        this.inflight.delete(chatId);
      }
    })();
    this.inflight.set(chatId, task);
    return task;
  }

  // Resolve display names to open_ids for mention rendering. One forced refresh
  // on an unknown name before failing; unknown and ambiguous names throw clear
  // errors listing what the caller can do instead.
  async resolveNames(chatId, names) {
    if (!this._fresh(chatId)) await this._refresh(chatId);
    const lookup = () => {
      const entry = this.cache.get(chatId);
      return names.map((name) => ({
        name,
        matches: entry?.byKey.get(nameKey(name)) ?? [],
      }));
    };
    let results = lookup();
    if (results.some((result) => result.matches.length === 0)) {
      await this._refresh(chatId);
      results = lookup();
    }
    if (!this.cache.has(chatId)) {
      throw new Error('mention_names could not be resolved: the chat member roster is unavailable; use mention_user_ids with full ou_ IDs from trusted metadata');
    }
    const unknown = results.filter((result) => result.matches.length === 0);
    if (unknown.length > 0) {
      throw new Error(`mention_names contains unknown member name(s) for this chat: ${unknown.map((result) => result.name).join(', ')}. Use a name exactly as it appears in from= metadata or trusted tool output.`);
    }
    const ambiguous = results.find((result) => result.matches.length > 1);
    if (ambiguous) {
      const candidates = ambiguous.matches.map((member) => `${member.name} (${member.id})`).join(', ');
      throw new Error(`mention_names entry "${ambiguous.name}" is ambiguous in this chat; candidates: ${candidates}. Use mention_user_ids with the intended open_id.`);
    }
    // Pair each requested name with its resolved id (not deduplicated to ids
    // alone) so callers can substitute an inline "@Name" occurrence in the
    // message text with that specific person's mention, rather than only
    // being able to prefix every resolved id as one block up front.
    return results.map((result) => ({ name: result.name, id: result.matches[0].id }));
  }
}

// Process-wide sync resolver used by the line formatters (codex.mjs and
// cognition.mjs render lines synchronously, deep below the daemon; threading a
// resolver through every call site would touch the whole turn pipeline). The
// daemon registers its roster here on start; when nothing is registered every
// lookup misses and formatting falls back to shortened id stubs.
let globalResolver = null;

export function registerNameResolver(resolver) {
  globalResolver = typeof resolver === 'function' ? resolver : null;
}

export function lookupMemberName(chatId, userId) {
  if (!globalResolver || !chatId || !userId) return null;
  try {
    return globalResolver(chatId, userId) ?? null;
  } catch {
    return null;
  }
}
