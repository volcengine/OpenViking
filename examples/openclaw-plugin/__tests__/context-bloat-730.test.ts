import { describe, it, expect, vi } from "vitest";
import type { FindResultItem } from "../client.js";

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
