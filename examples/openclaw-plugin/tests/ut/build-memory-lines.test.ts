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
    });

    expect(readFn).not.toHaveBeenCalled();
  });

  it("calls readFn for level=2 when recallPreferAbstract=false", async () => {
    const memories = [makeMemory({ level: 2, abstract: "fallback" })];
    const readFn = vi.fn().mockResolvedValue("Full content from readFn");

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
    });

    expect(readFn).toHaveBeenCalledWith("viking://user/memories/test-1");
    expect(lines[0]).toContain("Full content from readFn");
  });

  it("falls back to abstract when readFn throws", async () => {
    const memories = [makeMemory({ level: 2, abstract: "Fallback abstract" })];
    const readFn = vi.fn().mockRejectedValue(new Error("network error"));

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
    });

    expect(lines[0]).toContain("Fallback abstract");
  });

  it("falls back to abstract when readFn returns empty", async () => {
    const memories = [makeMemory({ level: 2, abstract: "Fallback abstract" })];
    const readFn = vi.fn().mockResolvedValue("");

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
    });

    expect(lines[0]).toContain("Fallback abstract");
  });

  it("keeps individual memory content intact", async () => {
    const longAbstract = "x".repeat(600);
    const memories = [makeMemory({ abstract: longAbstract })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines[0]).toBe(`- [core] ${longAbstract}`);
  });

  it("uses uri as fallback when no abstract", async () => {
    const memories = [makeMemory({ abstract: "", level: 1 })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines[0]).toContain("viking://user/memories/test-1");
  });

  it("defaults category to 'memory'", async () => {
    const memories = [makeMemory({ category: undefined })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines[0]).toContain("[memory]");
  });
});

describe("buildMemoryLinesWithBudget", () => {
  it("stops adding before total injected characters exceed the budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(3000), category: "a" }),
      makeMemory({ abstract: "b".repeat(1500), category: "b" }),
    ];
    const readFn = vi.fn();
    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxInjectedChars: 4000,
      },
    );

    expect(lines).toHaveLength(1);
    expect(lines[0]!.length).toBeLessThanOrEqual(4000);
    expect(estimatedTokens).toBe(estimateTokenCount(lines[0]!));
  });

  it("skips memories that do not fit the remaining character budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(400), category: "large" }),
      makeMemory({ abstract: "short", category: "small" }),
    ];
    const readFn = vi.fn();

    const { lines } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxInjectedChars: 20,
      },
    );

    expect(lines).toHaveLength(1);
    expect(lines[0]).toBe("- [small] short");
  });

  it("returns no lines when no complete memory fits the character budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(400), category: "large" }),
    ];
    const readFn = vi.fn();

    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxInjectedChars: 20,
      },
    );

    expect(lines).toHaveLength(0);
    expect(estimatedTokens).toBe(0);
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
        recallTokenBudget: 2000,
      },
    );

    expect(lines).toHaveLength(0);
    expect(estimatedTokens).toBe(0);
  });
});
