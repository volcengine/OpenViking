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

describe("Slice B: prefer abstract over full content fetch", () => {
  it("should use abstract when available instead of calling read()", async () => {
    const { buildMemoryLines } = await import("../index.js");

    const mockRead = vi.fn().mockResolvedValue("Full long content from read()");

    const memories: FindResultItem[] = [
      mockMemory({
        uri: "viking://user/memories/1",
        abstract: "Short abstract text",
        level: 2,
        score: 0.8,
      }),
      mockMemory({
        uri: "viking://user/memories/2",
        abstract: "",
        level: 2,
        score: 0.7,
      }),
    ];

    const lines = await buildMemoryLines(memories, mockRead, {
      recallPreferAbstract: true,
      recallMaxContentChars: 500,
    });

    // Item 1 has abstract — read() should NOT be called for it
    // Item 2 has empty abstract — read() SHOULD be called
    expect(mockRead).toHaveBeenCalledTimes(1);
    expect(mockRead).toHaveBeenCalledWith("viking://user/memories/2");
    expect(lines[0]).toContain("Short abstract text");
  });
});

describe("Slice D: recallMaxContentChars truncation", () => {
  it("should truncate content exceeding recallMaxContentChars", async () => {
    const { buildMemoryLines } = await import("../index.js");

    const longContent = "A".repeat(2000);
    const mockRead = vi.fn().mockResolvedValue(longContent);

    const memories: FindResultItem[] = [
      mockMemory({
        uri: "viking://user/memories/1",
        abstract: "",
        level: 2,
        score: 0.8,
      }),
    ];

    const lines = await buildMemoryLines(memories, mockRead, {
      recallPreferAbstract: false,
      recallMaxContentChars: 500,
    });

    // Content should be truncated to 500 chars + "..."
    const contentPart = lines[0]!.replace("- [memory] ", "");
    expect(contentPart.length).toBeLessThanOrEqual(503); // 500 + "..."
    expect(contentPart.endsWith("...")).toBe(true);
  });

  it("should have recallMaxContentChars and recallPreferAbstract in parsed config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.recallMaxContentChars).toBe(500);
    expect(cfg.recallPreferAbstract).toBe(false);
  });
});

describe("Slice E: tokenBudget enforcement", () => {
  it("should stop injecting when token budget is exhausted", async () => {
    const { buildMemoryLinesWithBudget } = await import("../index.js");

    // Each memory ~200 chars -> ~50 tokens per line (200 chars + "- [memory] " prefix)
    const memories: FindResultItem[] = Array.from({ length: 10 }, (_, i) =>
      mockMemory({
        uri: `viking://user/memories/${i}`,
        abstract: "A".repeat(200),
        level: 2,
        score: 0.8 - i * 0.01,
      }),
    );

    const mockRead = vi.fn().mockResolvedValue("should not be called");

    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      mockRead,
      {
        recallPreferAbstract: true,
        recallMaxContentChars: 500,
        recallTokenBudget: 100,
      },
    );

    // Budget = 100 tokens. Each line ~53 tokens ((200 + 13 prefix chars) / 4).
    // First line is always included even if it exceeds the budget (spec §6.2),
    // so we expect at most 2 lines (~106 tokens).
    expect(lines.length).toBeLessThanOrEqual(2);
    expect(lines.length).toBeGreaterThan(0);
    expect(estimatedTokens).toBeLessThanOrEqual(106);
  });

  it("should estimate tokens as ceil(chars/4)", async () => {
    const { estimateTokenCount } = await import("../index.js");
    expect(estimateTokenCount("")).toBe(0);
    expect(estimateTokenCount("abcd")).toBe(1);
    expect(estimateTokenCount("abcde")).toBe(2);
    expect(estimateTokenCount("A".repeat(100))).toBe(25);
  });

  it("should have recallTokenBudget in parsed config with default 2000", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.recallTokenBudget).toBe(2000);
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
