import { describe, expect, it, vi } from "vitest";
import type { OVResult, RecallHit } from "../ov-client.js";
import {
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
  RecallCache,
} from "../recall/cache.js";

const HITS: RecallHit[] = [{ uri: "viking://m/1", score: 0.9, type: "memory" }];
const HITS_2: RecallHit[] = [{ uri: "viking://m/2", score: 0.7, type: "memory" }];

function makeClock(initial = 0) {
  let t = initial;
  return {
    now: () => t,
    advance: (ms: number) => { t += ms; },
    set: (ms: number) => { t = ms; },
  };
}

describe("RecallCache — defaults", () => {
  it("exports the default TTL (5000ms) and max-entries (64)", () => {
    expect(DEFAULT_TTL_MS).toBe(5000);
    expect(DEFAULT_MAX_ENTRIES).toBe(64);
  });

  it("uses defaults when constructed with no options", () => {
    const c = new RecallCache();
    expect(c.size).toBe(0);
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    expect(c.get({ query: "q", sessionId: "cp-z" })).toEqual(HITS);
  });
});

describe("RecallCache — get / set / hit / miss", () => {
  it("returns null on a fresh miss", () => {
    const c = new RecallCache();
    expect(c.get({ query: "q", sessionId: "cp-z" })).toBeNull();
  });

  it("returns the stored value within TTL", () => {
    const clk = makeClock();
    const c = new RecallCache({ ttlMs: 1000, now: clk.now });
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    clk.advance(500);
    expect(c.get({ query: "q", sessionId: "cp-z" })).toEqual(HITS);
  });

  it("differentiates entries by sessionId, query, and scope", () => {
    const c = new RecallCache();
    c.set({ query: "q1", sessionId: "cp-a" }, HITS);
    c.set({ query: "q2", sessionId: "cp-a" }, HITS_2);
    c.set({ query: "q1", sessionId: "cp-b" }, HITS);
    c.set({ query: "q1", sessionId: "cp-a", scope: "memories" }, HITS_2);

    expect(c.get({ query: "q1", sessionId: "cp-a" })).toEqual(HITS);
    expect(c.get({ query: "q2", sessionId: "cp-a" })).toEqual(HITS_2);
    expect(c.get({ query: "q1", sessionId: "cp-b" })).toEqual(HITS);
    expect(c.get({ query: "q1", sessionId: "cp-a", scope: "memories" })).toEqual(HITS_2);
  });

  it("re-setting the same key replaces the value (and refreshes TTL)", () => {
    const clk = makeClock();
    const c = new RecallCache({ ttlMs: 1000, now: clk.now });
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    clk.advance(900);
    c.set({ query: "q", sessionId: "cp-z" }, HITS_2);
    clk.advance(500); // 1400 since first set, but only 500 since refresh
    expect(c.get({ query: "q", sessionId: "cp-z" })).toEqual(HITS_2);
  });
});

describe("RecallCache — TTL expiry", () => {
  it("returns null after the TTL elapses, and removes the stale entry", () => {
    const clk = makeClock();
    const c = new RecallCache({ ttlMs: 1000, now: clk.now });
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    clk.advance(1001);
    expect(c.get({ query: "q", sessionId: "cp-z" })).toBeNull();
    expect(c.size).toBe(0); // expired entry was deleted on read
  });

  it("a hit promotes the entry's MRU position but does NOT extend its TTL", () => {
    const clk = makeClock();
    const c = new RecallCache({ ttlMs: 1000, now: clk.now });
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    clk.advance(900);
    expect(c.get({ query: "q", sessionId: "cp-z" })).toEqual(HITS);
    clk.advance(200); // 1100 from initial set, 200 since hit
    expect(c.get({ query: "q", sessionId: "cp-z" })).toBeNull();
  });

  it("ttlMs=0 disables caching entirely", () => {
    const c = new RecallCache({ ttlMs: 0 });
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    expect(c.size).toBe(0);
    expect(c.get({ query: "q", sessionId: "cp-z" })).toBeNull();
  });
});

describe("RecallCache — LRU eviction", () => {
  it("evicts the least-recently-used entry once over maxEntries", () => {
    const c = new RecallCache({ maxEntries: 3 });
    c.set({ query: "a", sessionId: "s" }, HITS);
    c.set({ query: "b", sessionId: "s" }, HITS);
    c.set({ query: "c", sessionId: "s" }, HITS);
    c.set({ query: "d", sessionId: "s" }, HITS); // evicts "a" (oldest)
    expect(c.size).toBe(3);
    expect(c.get({ query: "a", sessionId: "s" })).toBeNull();
    expect(c.get({ query: "b", sessionId: "s" })).not.toBeNull();
    expect(c.get({ query: "c", sessionId: "s" })).not.toBeNull();
    expect(c.get({ query: "d", sessionId: "s" })).not.toBeNull();
  });

  it("a hit on an old entry promotes it past the LRU spot", () => {
    const c = new RecallCache({ maxEntries: 3 });
    c.set({ query: "a", sessionId: "s" }, HITS);
    c.set({ query: "b", sessionId: "s" }, HITS);
    c.set({ query: "c", sessionId: "s" }, HITS);
    // Promote "a" to MRU end.
    expect(c.get({ query: "a", sessionId: "s" })).not.toBeNull();
    c.set({ query: "d", sessionId: "s" }, HITS); // now "b" is the LRU
    expect(c.get({ query: "a", sessionId: "s" })).not.toBeNull();
    expect(c.get({ query: "b", sessionId: "s" })).toBeNull();
  });

  it("respects maxEntries=1 (degenerate case)", () => {
    const c = new RecallCache({ maxEntries: 1 });
    c.set({ query: "a", sessionId: "s" }, HITS);
    c.set({ query: "b", sessionId: "s" }, HITS);
    expect(c.size).toBe(1);
    expect(c.get({ query: "a", sessionId: "s" })).toBeNull();
    expect(c.get({ query: "b", sessionId: "s" })).not.toBeNull();
  });
});

describe("RecallCache — getOrFetch", () => {
  it("returns the cached hit without invoking fetch on a hit", async () => {
    const c = new RecallCache();
    c.set({ query: "q", sessionId: "cp-z" }, HITS);
    const fetch = vi.fn();
    const res = await c.getOrFetch({ query: "q", sessionId: "cp-z" }, fetch);
    expect(res).toEqual({ ok: true, value: HITS });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("calls fetch exactly once on a miss and caches the successful result", async () => {
    const c = new RecallCache();
    const fetch = vi.fn(async (): Promise<OVResult<RecallHit[]>> => ({ ok: true, value: HITS }));
    const first = await c.getOrFetch({ query: "q", sessionId: "cp-z" }, fetch);
    const second = await c.getOrFetch({ query: "q", sessionId: "cp-z" }, fetch);
    expect(first).toEqual({ ok: true, value: HITS });
    expect(second).toEqual({ ok: true, value: HITS });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does NOT cache a fetch error result", async () => {
    const c = new RecallCache();
    const fetch = vi.fn(async (): Promise<OVResult<RecallHit[]>> => ({
      ok: false,
      error: { message: "boom" },
    }));
    const r1 = await c.getOrFetch({ query: "q", sessionId: "cp-z" }, fetch);
    const r2 = await c.getOrFetch({ query: "q", sessionId: "cp-z" }, fetch);
    expect(r1.ok).toBe(false);
    expect(r2.ok).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(2); // re-fetched on second call
    expect(c.size).toBe(0);
  });

  it("miss path is bit-identical to calling fetch() directly", async () => {
    const c = new RecallCache();
    const fetchResult: OVResult<RecallHit[]> = { ok: true, value: HITS };
    const fetch = vi.fn(async () => fetchResult);
    const cached = await c.getOrFetch({ query: "q", sessionId: "cp-z" }, fetch);
    // Same shape, same value.
    expect(cached).toEqual(fetchResult);
  });
});

describe("RecallCache — clear", () => {
  it("empties the cache", () => {
    const c = new RecallCache();
    c.set({ query: "a", sessionId: "s" }, HITS);
    c.set({ query: "b", sessionId: "s" }, HITS);
    expect(c.size).toBe(2);
    c.clear();
    expect(c.size).toBe(0);
    expect(c.get({ query: "a", sessionId: "s" })).toBeNull();
  });
});
