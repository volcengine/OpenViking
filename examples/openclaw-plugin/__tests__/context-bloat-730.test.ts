import { describe, it, expect, vi } from "vitest";
import type { FindResultItem } from "../client.js";
import { postProcessMemories, pickMemoriesForInjection } from "../memory-ranking.js";
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

describe("Slice C: isLeafLikeMemory narrowing", () => {
  it("should NOT boost .md URI items that are not level 2", () => {
    const mdButNotLeaf = mockMemory({
      uri: "viking://user/resources/notes.md",
      level: 1,
      score: 0.30,
      abstract: "Some notes file",
    });
    const actualLeaf = mockMemory({
      uri: "viking://user/memories/real-memory",
      level: 2,
      score: 0.30,
      abstract: "Actual leaf memory",
    });

    const result = pickMemoriesForInjection(
      [mdButNotLeaf, actualLeaf],
      2,
      "test query",
    );

    // The level-2 item should rank higher (gets boost), .md non-leaf should not
    expect(result[0]!.uri).toBe("viking://user/memories/real-memory");
  });
});
