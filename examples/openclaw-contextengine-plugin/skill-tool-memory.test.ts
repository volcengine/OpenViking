import { describe, expect, it } from "vitest";

import { buildSkillMemoryAugmentation, buildToolMemoryHints, buildOvCliGuidance } from "./skill-tool-memory.js";

describe("skill/tool memory", () => {
  it("builds tool hints with usage stats from messages", () => {
    const txt = buildToolMemoryHints(
      ["search_memories"],
      "debug",
      [
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tc1",
              name: "search_memories",
              input: { query: "q1", limit: 5 },
            },
          ],
        },
        {
          role: "toolResult",
          toolCallId: "tc1",
          toolName: "search_memories",
          isError: false,
          content: [{ type: "text", text: "Found 2 memories." }],
        },
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tc2",
              name: "search_memories",
              input: { query: "q2" },
            },
          ],
        },
        {
          role: "toolResult",
          toolCallId: "tc2",
          toolName: "search_memories",
          isError: true,
          content: [{ type: "text", text: "timeout while searching" }],
        },
      ],
    );

    expect(typeof txt).toBe("string");
    expect(txt).toContain("calls=2");
    expect(txt).toContain("successRate=50%");
    expect(txt).toContain("limit+query");
    expect(txt).toContain("timeout");
  });

  it("builds skill memory augmentation with mention and outcome stats", () => {
    const txt = buildSkillMemoryAugmentation(
      ["superpowers:test-driven-development"],
      "bugfix",
      [
        { role: "user", content: "请用 superpowers:test-driven-development 修复这个问题" },
        { role: "assistant", content: "Fixed with tests added and all passed." },
        { role: "user", content: "test-driven-development 再试一次" },
        { role: "assistant", content: "error: timeout while generating tests" },
      ],
    );

    expect(txt).toContain("superpowers:test-driven-development");
    expect(txt).toContain("mentions=2");
    expect(txt).toContain("successRate=50%");
    expect(txt).toContain("timeout");
  });

  it("builds OV CLI guidance with fallback note", () => {
    const txt = buildOvCliGuidance({
      baseUrl: "http://127.0.0.1:1933",
      fallbackNote: "If OV is unavailable, continue without retrieval.",
    });

    expect(txt).toContain("ov");
    expect(txt).toContain("127.0.0.1:1933");
    expect(txt).toContain("continue without retrieval");
  });
});
