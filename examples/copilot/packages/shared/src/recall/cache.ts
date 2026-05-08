/**
 * Short-TTL LRU cache for recall results.
 *
 * Use case: in VS Code, the `@openviking` chat participant and the
 * `openviking_recall` language-model tool can both fire on the same
 * user prompt during a single turn. Without a cache that's two
 * round-trips to OpenViking for an identical (query, sessionId)
 * pair. A short-lived (~5s by default) cache amortises the duplicate.
 *
 * Design choices:
 *  - Key = `${query}::${sessionId}::${scope}`. `scope` is the optional
 *    third discriminator (e.g. a target_uri or score threshold) so
 *    callers can keep different recall variants from colliding.
 *  - JavaScript `Map` preserves insertion order, so LRU is implemented
 *    by deleting + re-setting on access (moves the entry to the MRU
 *    end). Eviction picks the first iterator entry (the LRU end).
 *  - Expired entries are deleted on read so subsequent gets see a
 *    clean miss without leaking memory.
 *  - `getOrFetch` only caches successful results. Errors are returned
 *    through unchanged so the caller can retry on the next turn
 *    without having to invalidate.
 *
 * Cache miss path is bit-identical to the no-cache path: getOrFetch
 * calls the supplied `fetch` exactly once on miss and returns its
 * result verbatim. This lets the host wire the cache in front of an
 * OVClient.recall call without behavioural changes — the only effect
 * on a miss is one extra `cache.set` invocation post-resolve.
 */

import type { OVResult, RecallHit } from "../ov-client.js";

/** Default cache lifetime (ms) per entry. */
export const DEFAULT_TTL_MS = 5000;
/** Default upper bound on cache entries before LRU eviction. */
export const DEFAULT_MAX_ENTRIES = 64;

export interface RecallCacheOptions {
  /** TTL in milliseconds. Default `DEFAULT_TTL_MS`. */
  ttlMs?: number;
  /** Max entries before LRU eviction. Default `DEFAULT_MAX_ENTRIES`. */
  maxEntries?: number;
  /** Inject a clock for tests. Default `Date.now`. */
  now?: () => number;
}

export interface RecallCacheKey {
  query: string;
  sessionId: string;
  /** Optional discriminator — e.g. a target_uri or score threshold. */
  scope?: string;
}

interface CacheEntry {
  value: RecallHit[];
  expiresAt: number;
}

export class RecallCache {
  private readonly ttlMs: number;
  private readonly maxEntries: number;
  private readonly now: () => number;
  private readonly entries = new Map<string, CacheEntry>();

  constructor(opts: RecallCacheOptions = {}) {
    this.ttlMs = Math.max(0, Math.floor(opts.ttlMs ?? DEFAULT_TTL_MS));
    this.maxEntries = Math.max(1, Math.floor(opts.maxEntries ?? DEFAULT_MAX_ENTRIES));
    this.now = opts.now ?? Date.now;
  }

  /** Number of entries currently held (for tests / telemetry). */
  get size(): number {
    return this.entries.size;
  }

  /**
   * Look up a key. Returns the cached hits on a fresh hit, or `null`
   * on miss / expiry. A hit promotes the entry to MRU position.
   */
  get(key: RecallCacheKey): RecallHit[] | null {
    if (this.ttlMs === 0) return null;
    const k = this.encodeKey(key);
    const entry = this.entries.get(k);
    if (!entry) return null;
    if (entry.expiresAt <= this.now()) {
      this.entries.delete(k);
      return null;
    }
    // Promote to MRU end via delete + set.
    this.entries.delete(k);
    this.entries.set(k, entry);
    return entry.value;
  }

  /**
   * Store hits under the key with the configured TTL. Evicts the LRU
   * entry when over `maxEntries`. A `ttlMs` of 0 disables caching
   * (set is a no-op).
   */
  set(key: RecallCacheKey, value: RecallHit[]): void {
    if (this.ttlMs === 0) return;
    const k = this.encodeKey(key);
    const entry: CacheEntry = {
      value,
      expiresAt: this.now() + this.ttlMs,
    };
    // Re-set to put at MRU end whether or not it existed.
    this.entries.delete(k);
    this.entries.set(k, entry);

    while (this.entries.size > this.maxEntries) {
      const firstKey = this.entries.keys().next().value;
      if (firstKey === undefined) break;
      this.entries.delete(firstKey);
    }
  }

  /**
   * Look up the key; on miss, call `fetch()` once and (only on a
   * successful result) cache its value. Returns the fetch result
   * verbatim on miss so the no-cache path is preserved.
   */
  async getOrFetch(
    key: RecallCacheKey,
    fetch: () => Promise<OVResult<RecallHit[]>>,
  ): Promise<OVResult<RecallHit[]>> {
    const cached = this.get(key);
    if (cached) return { ok: true, value: cached };

    const result = await fetch();
    if (result.ok) this.set(key, result.value);
    return result;
  }

  /** Force-empty the cache. */
  clear(): void {
    this.entries.clear();
  }

  private encodeKey(key: RecallCacheKey): string {
    // \x1f (Unit Separator) is unlikely to appear in any of the
    // component fields, so it's a safer joiner than `:` or `|`.
    return `${key.query}\x1f${key.sessionId}\x1f${key.scope ?? ""}`;
  }
}
