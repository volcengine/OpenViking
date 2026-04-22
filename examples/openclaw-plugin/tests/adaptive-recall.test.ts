import { describe, expect, it } from "vitest";

import {
  decideRecallTier,
  isFreshRecallCacheEntry,
  makeRecallCacheKey,
  normalizeRecallCacheQuery,
} from "../adaptive-recall.js";
import { memoryOpenVikingConfigSchema } from "../config.js";

const cfg = memoryOpenVikingConfigSchema.parse({
  mode: "remote",
  baseUrl: "http://127.0.0.1:1933",
});

describe("adaptive recall tiering", () => {
  it("skips direct mechanical turns", () => {
    const decision = decideRecallTier({
      queryText: "User input:\nserve me these",
      cfg,
      hasRecentCache: false,
    });

    expect(decision.tier).toBe("none");
    expect(decision.reason).toBe("mechanical");
  });

  it("keeps full recall for memory-sensitive prompts", () => {
    const decision = decideRecallTier({
      queryText: "why did the OpenClaw session stop responding? diagnose root cause",
      cfg,
      hasRecentCache: false,
    });

    expect(decision.tier).toBe("full");
    expect(decision.reason).toBe("memory_intent");
  });

  it("uses fast recall for short follow-ups", () => {
    const decision = decideRecallTier({
      queryText: "continue",
      cfg,
      hasRecentCache: true,
    });

    expect(decision.tier).toBe("fast");
    expect(decision.reason).toBe("short_followup_cached");
  });

  it("honors full and none overrides", () => {
    const overrideCfg = memoryOpenVikingConfigSchema.parse({
      recallTierOverrides: {
        full: ["draft message"],
        none: ["benchmark ping"],
      },
    });

    expect(
      decideRecallTier({
        queryText: "draft message to Steve",
        cfg: overrideCfg,
        hasRecentCache: false,
      }).tier,
    ).toBe("full");
    expect(
      decideRecallTier({
        queryText: "benchmark ping",
        cfg: overrideCfg,
        hasRecentCache: false,
      }).tier,
    ).toBe("none");
  });

  it("normalizes cache keys by query, agent, and recall config", () => {
    const key = makeRecallCacheKey({
      queryText: "User input:\nWhy did this break?",
      agentId: "brianle",
      cfg,
    });

    expect(key).toContain("brianle");
    expect(key).toContain(normalizeRecallCacheQuery("Why did this break?"));
  });

  it("expires cache entries by ttl", () => {
    const entry = {
      key: "k",
      sessionKey: "s",
      query: "q",
      value: { estimatedTokens: 0, memories: [] },
      createdAt: 1_000,
    };

    expect(isFreshRecallCacheEntry(entry, 10_000, 5_000)).toBe(true);
    expect(isFreshRecallCacheEntry(entry, 1_000, 5_000)).toBe(false);
    expect(isFreshRecallCacheEntry(entry, 0, 1_000)).toBe(false);
  });
});
