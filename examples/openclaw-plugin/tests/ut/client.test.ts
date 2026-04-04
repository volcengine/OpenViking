import { describe, expect, it } from "vitest";

import { isMemoryUri } from "../../client.js";

describe("isMemoryUri", () => {
  it("returns true for valid user memory URI", () => {
    expect(isMemoryUri("viking://user/memories/abc-123")).toBe(true);
  });

  it("returns true for user memory URI with space prefix", () => {
    expect(isMemoryUri("viking://user/default/memories/item-1")).toBe(true);
  });

  it("returns true for valid agent memory URI", () => {
    expect(isMemoryUri("viking://agent/memories/xyz")).toBe(true);
  });

  it("returns true for agent memory URI with space prefix", () => {
    expect(isMemoryUri("viking://agent/abc123/memories/item-2")).toBe(true);
  });

  it("returns true for user memories root", () => {
    expect(isMemoryUri("viking://user/memories")).toBe(true);
  });

  it("returns true for user memories trailing slash", () => {
    expect(isMemoryUri("viking://user/memories/")).toBe(true);
  });

  it("returns false for user skills URI", () => {
    expect(isMemoryUri("viking://user/skills/abc")).toBe(false);
  });

  it("returns false for agent instructions URI", () => {
    expect(isMemoryUri("viking://agent/instructions/rule-1")).toBe(false);
  });

  it("returns false for empty string", () => {
    expect(isMemoryUri("")).toBe(false);
  });

  it("returns false for random URL", () => {
    expect(isMemoryUri("http://example.com/memories")).toBe(false);
  });

  it("returns false for partial viking URI without scope", () => {
    expect(isMemoryUri("viking://memories/abc")).toBe(false);
  });
});
