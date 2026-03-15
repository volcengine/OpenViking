import { describe, expect, it } from "vitest";

import {
  buildSimulatedToolResultInjection,
  buildSystemPromptAddition,
  truncateToMaxChars,
} from "./injection.js";

describe("injection", () => {
  it("builds prompt addition with profile, tool memory, and ov guidance", () => {
    const text = buildSystemPromptAddition({
      profile: "User profile",
      toolMemory: "Tool hints",
      ovCliGuidance: "Use ov find",
    });

    expect(text).toContain("User profile");
    expect(text).toContain("Tool hints");
    expect(text).toContain("Use ov find");
    expect(text).toBe("User profile\n\nTool hints\n\nUse ov find");
  });

  it("omits empty prompt sections", () => {
    const text = buildSystemPromptAddition({
      profile: "   ",
      toolMemory: "Tool hints",
      ovCliGuidance: "",
    });

    expect(text).toBe("Tool hints");
  });

  it("builds simulated tool result payload", () => {
    const payload = buildSimulatedToolResultInjection([
      { uri: "m://1", content: "First", score: 0.9 },
      { uri: "m://2", content: "Second", score: 0.8 },
    ]);

    expect(payload).toContain("m://1");
    expect(payload).toContain("First");
    expect(payload).toContain("m://2");
  });

  it("builds simulated payload header when memory list is empty", () => {
    const payload = buildSimulatedToolResultInjection([]);
    expect(payload).toBe("OpenViking retrieval results:");
  });

  it("handles memory items without optional fields", () => {
    const payload = buildSimulatedToolResultInjection([{ uri: "m://1" }]);
    expect(payload).toContain("- uri=m://1");
    expect(payload).not.toContain("content=");
    expect(payload).not.toContain("score=");
  });

  it("truncates text to max chars", () => {
    expect(truncateToMaxChars("abcdef", 4)).toBe("abcd");
  });

  it("returns empty string when max chars is zero or less", () => {
    expect(truncateToMaxChars("abcdef", 0)).toBe("");
    expect(truncateToMaxChars("abcdef", -1)).toBe("");
  });

  it("returns input when text length is within max chars", () => {
    expect(truncateToMaxChars("abcd", 4)).toBe("abcd");
    expect(truncateToMaxChars("abcd", 10)).toBe("abcd");
  });
});
