import { describe, expect, it, vi } from "vitest";

import {
  estimateTokenCount,
  buildMemoryLines,
  buildMemoryLinesWithBudget,
} from "../../index.js";
import type { FindResultItem } from "../../client.js";

function makeMemory(overrides?: Partial<FindResultItem>): FindResultItem {
  return {
    uri: "viking://user/memories/test-1",
    level: 2,
    abstract: "Test memory abstract",
    category: "core",
    score: 0.85,
    ...overrides,
  };
}

describe("estimateTokenCount", () => {
  it("returns 0 for empty string", () => {
    expect(estimateTokenCount("")).toBe(0);
  });

  it("estimates tokens as ceil(chars/4)", () => {
    expect(estimateTokenCount("hello")).toBe(2); // ceil(5/4)
    expect(estimateTokenCount("abcd")).toBe(1); // ceil(4/4)
    expect(estimateTokenCount("abcde")).toBe(2); // ceil(5/4)
  });

  it("handles long text", () => {
    const text = "a".repeat(1000);
    expect(estimateTokenCount(text)).toBe(250);
  });
});

describe("buildMemoryLines", () => {
  it("formats memories with category and content", async () => {
    const memories = [
      makeMemory({ category: "preferences", abstract: "User prefers Python" }),
      makeMemory({ category: "facts", abstract: "Works at TechCorp" }),
    ];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
      recallMaxContentChars: 500,
    });

    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe("- [preferences] User prefers Python");
    expect(lines[1]).toBe("- [facts] Works at TechCorp");
  });

  it("uses abstract when recallPreferAbstract=true", async () => {
    const memories = [makeMemory({ abstract: "The abstract text" })];
    const readFn = vi.fn();

    await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
      recallMaxContentChars: 500,
    });

    expect(readFn).not.toHaveBeenCalled();
  });

  it("calls readFn for level=2 when recallPreferAbstract=false", async () => {
    const memories = [makeMemory({ level: 2, abstract: "fallback" })];
    const readFn = vi.fn().mockResolvedValue("Full content from readFn");

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
      recallMaxContentChars: 500,
    });

    expect(readFn).toHaveBeenCalledWith("viking://user/memories/test-1");
    expect(lines[0]).toContain("Full content from readFn");
  });

  it("falls back to abstract when readFn throws", async () => {
    const memories = [makeMemory({ level: 2, abstract: "Fallback abstract" })];
    const readFn = vi.fn().mockRejectedValue(new Error("network error"));

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
      recallMaxContentChars: 500,
    });

    expect(lines[0]).toContain("Fallback abstract");
  });

  it("falls back to abstract when readFn returns empty", async () => {
    const memories = [makeMemory({ level: 2, abstract: "Fallback abstract" })];
    const readFn = vi.fn().mockResolvedValue("");

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
      recallMaxContentChars: 500,
    });

    expect(lines[0]).toContain("Fallback abstract");
  });

  it("truncates content exceeding recallMaxContentChars", async () => {
    const longAbstract = "x".repeat(600);
    const memories = [makeMemory({ abstract: longAbstract })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
      recallMaxContentChars: 100,
    });

    expect(lines[0]).toContain("...");
    expect(lines[0].length).toBeLessThan(600);
  });

  it("uses uri as fallback when no abstract", async () => {
    const memories = [makeMemory({ abstract: "", level: 1 })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
      recallMaxContentChars: 500,
    });

    expect(lines[0]).toContain("viking://user/memories/test-1");
  });

  it("defaults category to 'memory'", async () => {
    const memories = [makeMemory({ category: undefined })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
      recallMaxContentChars: 500,
    });

    expect(lines[0]).toContain("[memory]");
  });
});

describe("buildMemoryLinesWithBudget", () => {
  it("stops adding when budget is exhausted", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(100), category: "a" }),
      makeMemory({ abstract: "b".repeat(100), category: "b" }),
      makeMemory({ abstract: "c".repeat(100), category: "c" }),
    ];
    const readFn = vi.fn();
    // Each line ~100 chars → ~25 tokens. Budget=40 fits 1-2 lines.
    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxContentChars: 500,
        recallTokenBudget: 40,
      },
    );

    expect(lines.length).toBeLessThan(3);
    expect(estimatedTokens).toBeLessThanOrEqual(40 + 30); // first always included even if over
  });

  it("always includes the first memory even if over budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(400) }), // ~100 tokens
    ];
    const readFn = vi.fn();

    const { lines } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxContentChars: 500,
        recallTokenBudget: 10,
      },
    );

    expect(lines).toHaveLength(1);
  });

  it("returns correct estimatedTokens sum", async () => {
    const memories = [
      makeMemory({ abstract: "short" }),
    ];
    const readFn = vi.fn();

    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxContentChars: 500,
        recallTokenBudget: 2000,
      },
    );

    expect(lines).toHaveLength(1);
    expect(estimatedTokens).toBe(estimateTokenCount(lines[0]!));
  });

  it("handles empty memories array", async () => {
    const readFn = vi.fn();
    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      [],
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxContentChars: 500,
        recallTokenBudget: 2000,
      },
    );

    expect(lines).toHaveLength(0);
    expect(estimatedTokens).toBe(0);
  });
});
