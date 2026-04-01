import { describe, expect, it } from "vitest";

import {
  clampScore,
  postProcessMemories,
  formatMemoryLines,
  trimForLog,
  toJsonLog,
  summarizeInjectionMemories,
  summarizeExtractedMemories,
  pickMemoriesForInjection,
} from "../../memory-ranking.js";
import type { FindResultItem } from "../../client.js";

function mem(overrides?: Partial<FindResultItem>): FindResultItem {
  return {
    uri: "viking://user/default/memories/m1",
    level: 2,
    abstract: "User prefers Python",
    category: "preferences",
    score: 0.85,
    ...overrides,
  };
}

describe("clampScore", () => {
  it("clamps values to [0, 1]", () => {
    expect(clampScore(0.5)).toBe(0.5);
    expect(clampScore(1.5)).toBe(1);
    expect(clampScore(-0.3)).toBe(0);
    expect(clampScore(0)).toBe(0);
    expect(clampScore(1)).toBe(1);
  });

  it("returns 0 for undefined and NaN", () => {
    expect(clampScore(undefined)).toBe(0);
    expect(clampScore(NaN)).toBe(0);
  });
});

describe("postProcessMemories", () => {
  it("sorts by score descending", () => {
    const items = [
      mem({ uri: "a", score: 0.3, abstract: "item A" }),
      mem({ uri: "b", score: 0.9, abstract: "item B" }),
      mem({ uri: "c", score: 0.6, abstract: "item C" }),
    ];
    const result = postProcessMemories(items, { limit: 10, scoreThreshold: 0 });
    expect(result.map((r) => r.uri)).toEqual(["b", "c", "a"]);
  });

  it("filters below scoreThreshold", () => {
    const items = [
      mem({ uri: "a", score: 0.8 }),
      mem({ uri: "b", score: 0.3 }),
    ];
    const result = postProcessMemories(items, { limit: 10, scoreThreshold: 0.5 });
    expect(result).toHaveLength(1);
    expect(result[0]!.uri).toBe("a");
  });

  it("respects limit", () => {
    const items = Array.from({ length: 5 }, (_, i) =>
      mem({ uri: `u${i}`, score: 0.9 - i * 0.1, abstract: `abstract ${i}` }),
    );
    const result = postProcessMemories(items, { limit: 2, scoreThreshold: 0 });
    expect(result).toHaveLength(2);
  });

  it("deduplicates by abstract+category", () => {
    const items = [
      mem({ uri: "a", score: 0.9, abstract: "same abstract", category: "facts" }),
      mem({ uri: "b", score: 0.8, abstract: "same abstract", category: "facts" }),
    ];
    const result = postProcessMemories(items, { limit: 10, scoreThreshold: 0 });
    expect(result).toHaveLength(1);
    expect(result[0]!.uri).toBe("a");
  });

  it("does not deduplicate events/cases by abstract", () => {
    const items = [
      mem({ uri: "a", score: 0.9, abstract: "same text", category: "events" }),
      mem({ uri: "b", score: 0.8, abstract: "same text", category: "events" }),
    ];
    const result = postProcessMemories(items, { limit: 10, scoreThreshold: 0 });
    expect(result).toHaveLength(2);
  });

  it("leafOnly filters non-leaf items", () => {
    const items = [
      mem({ uri: "leaf", level: 2, score: 0.5 }),
      mem({ uri: "non-leaf", level: 1, score: 0.9 }),
    ];
    const result = postProcessMemories(items, { limit: 10, scoreThreshold: 0, leafOnly: true });
    expect(result).toHaveLength(1);
    expect(result[0]!.uri).toBe("leaf");
  });
});

describe("formatMemoryLines", () => {
  it("formats items as numbered lines", () => {
    const items = [
      mem({ abstract: "Python preference", category: "preferences", score: 0.85 }),
      mem({ abstract: "Uses VSCode", category: "tools", score: 0.72 }),
    ];
    const output = formatMemoryLines(items);
    expect(output).toContain("1. [preferences] Python preference (85%)");
    expect(output).toContain("2. [tools] Uses VSCode (72%)");
  });

  it("falls back to overview then uri when abstract is empty", () => {
    const item = mem({ abstract: "", overview: "fallback overview", score: 0.5 });
    const output = formatMemoryLines([item]);
    expect(output).toContain("fallback overview");
  });

  it("uses uri when both abstract and overview are empty", () => {
    const item = mem({ abstract: "", overview: "", uri: "viking://test/uri", score: 0.5 });
    const output = formatMemoryLines([item]);
    expect(output).toContain("viking://test/uri");
  });
});

describe("trimForLog", () => {
  it("returns short strings unchanged", () => {
    expect(trimForLog("hello")).toBe("hello");
  });

  it("trims whitespace", () => {
    expect(trimForLog("  hello  ")).toBe("hello");
  });

  it("truncates long strings with ellipsis", () => {
    const long = "a".repeat(300);
    const result = trimForLog(long, 260);
    expect(result.length).toBe(263);
    expect(result.endsWith("...")).toBe(true);
  });

  it("respects custom limit", () => {
    const result = trimForLog("abcdefghij", 5);
    expect(result).toBe("abcde...");
  });
});

describe("toJsonLog", () => {
  it("serializes small objects directly", () => {
    const result = toJsonLog({ key: "value" });
    expect(result).toBe('{"key":"value"}');
  });

  it("truncates large objects", () => {
    const large = { data: "x".repeat(10000) };
    const result = toJsonLog(large, 100);
    const parsed = JSON.parse(result);
    expect(parsed.truncated).toBe(true);
    expect(parsed.preview.endsWith("...")).toBe(true);
  });

  it("handles circular references gracefully", () => {
    const obj: Record<string, unknown> = {};
    obj.self = obj;
    const result = toJsonLog(obj);
    expect(result).toContain("stringify_failed");
  });
});

describe("summarizeInjectionMemories", () => {
  it("maps items to summary objects", () => {
    const items = [mem({ uri: "u1", category: "facts", abstract: "Python dev", score: 0.9, level: 2 })];
    const result = summarizeInjectionMemories(items);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({
      uri: "u1",
      category: "facts",
      abstract: "Python dev",
      score: 0.9,
      is_leaf: true,
    });
  });

  it("uses null for missing category", () => {
    const items = [mem({ category: undefined })];
    const result = summarizeInjectionMemories(items);
    expect(result[0]!.category).toBeNull();
  });
});

describe("summarizeExtractedMemories", () => {
  it("limits to 10 items", () => {
    const items = Array.from({ length: 15 }, (_, i) => ({
      uri: `u${i}`,
      abstract: `Memory ${i}`,
      level: 2,
    }));
    const result = summarizeExtractedMemories(items);
    expect(result).toHaveLength(10);
  });

  it("falls back to overview then title for abstract", () => {
    const result = summarizeExtractedMemories([
      { uri: "u1", overview: "from overview", level: 2 },
      { uri: "u2", title: "from title", level: 2 },
      { uri: "u3", level: 2 },
    ]);
    expect(result[0]!.abstract).toBe("from overview");
    expect(result[1]!.abstract).toBe("from title");
    expect(result[2]!.abstract).toBe("");
  });
});

describe("pickMemoriesForInjection", () => {
  it("returns empty for empty input", () => {
    expect(pickMemoriesForInjection([], 5, "test")).toEqual([]);
  });

  it("returns empty for limit <= 0", () => {
    expect(pickMemoriesForInjection([mem()], 0, "test")).toEqual([]);
  });

  it("deduplicates by abstract", () => {
    const items = [
      mem({ uri: "a", abstract: "same thing", score: 0.9 }),
      mem({ uri: "b", abstract: "same thing", score: 0.8 }),
    ];
    const result = pickMemoriesForInjection(items, 10, "query");
    expect(result).toHaveLength(1);
  });

  it("prefers leaf-level memories", () => {
    const items = [
      mem({ uri: "non-leaf", level: 1, score: 0.95, abstract: "general" }),
      mem({ uri: "leaf1", level: 2, score: 0.7, abstract: "specific 1" }),
      mem({ uri: "leaf2", level: 2, score: 0.6, abstract: "specific 2" }),
    ];
    const result = pickMemoriesForInjection(items, 2, "query");
    const leafUris = result.filter((r) => r.level === 2).map((r) => r.uri);
    expect(leafUris.length).toBeGreaterThanOrEqual(1);
  });

  it("boosts preference memories for preference queries", () => {
    const items = [
      mem({ uri: "fact", category: "facts", score: 0.8, abstract: "Python history" }),
      mem({ uri: "pref", category: "preferences", score: 0.78, abstract: "prefers TypeScript" }),
    ];
    const result = pickMemoriesForInjection(items, 1, "What does the user prefer?");
    expect(result[0]!.uri).toBe("pref");
  });

  it("boosts event memories for temporal queries", () => {
    const items = [
      mem({ uri: "fact", category: "facts", score: 0.8, abstract: "knows Python" }),
      mem({ uri: "event", category: "events", score: 0.78, abstract: "deployed yesterday" }),
    ];
    const result = pickMemoriesForInjection(items, 1, "When did the user deploy?");
    expect(result[0]!.uri).toBe("event");
  });
});
