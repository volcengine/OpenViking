import { describe, it, expect, vi } from "vitest";
import type { FindResultItem } from "../client.js";
import { postProcessMemories } from "../memory-ranking.js";
import { memoryOpenVikingConfigSchema } from "../config.js";

/** Helper: create a mock FindResultItem */
function mockMemory(overrides: Partial<FindResultItem> & { uri: string }): FindResultItem {
  return {
    level: 2,
    score: 0.5,
    category: "memory",
    ...overrides,
  };
}

describe("context-bloat #730 — placeholder", () => {
  it("mockMemory helper returns expected shape", () => {
    const m = mockMemory({ uri: "mem://test/1" });
    expect(m.uri).toBe("mem://test/1");
    expect(m.level).toBe(2);
  });
});

describe("Slice A: recallScoreThreshold default", () => {
  it("should filter memories below 0.15 threshold with default config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});

    const memories = [
      mockMemory({ uri: "viking://user/memories/1", score: 0.05 }),
      mockMemory({ uri: "viking://user/memories/2", score: 0.10 }),
      mockMemory({ uri: "viking://user/memories/3", score: 0.20 }),
      mockMemory({ uri: "viking://user/memories/4", score: 0.50 }),
    ];

    const result = postProcessMemories(memories, {
      limit: 10,
      scoreThreshold: cfg.recallScoreThreshold,
    });

    // Only scores >= 0.15 should pass
    expect(result).toHaveLength(2);
    expect(result.map((m) => m.uri)).toEqual([
      "viking://user/memories/4",
      "viking://user/memories/3",
    ]);
  });

  it("should respect explicit recallScoreThreshold: 0.01 for backward compat", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallScoreThreshold: 0.01 });
    expect(cfg.recallScoreThreshold).toBe(0.01);
  });
});
