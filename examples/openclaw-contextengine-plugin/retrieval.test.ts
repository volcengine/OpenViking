import { describe, expect, it } from "vitest";

import { buildTurnQuery, filterAndRank } from "./retrieval.js";

describe("retrieval", () => {
  it("uses last N user text turns to build query", () => {
    const query = buildTurnQuery(
      [
        { role: "user", content: [{ type: "text", text: "A" }] },
        { role: "assistant", content: [{ type: "text", text: "B" }] },
        { role: "user", content: [{ type: "text", text: "C" }] },
      ],
      2,
    );

    expect(query).toContain("A");
    expect(query).toContain("C");
    expect(query).not.toContain("B");
  });

  it("skips greetings and short user messages when filters are enabled", () => {
    const query = buildTurnQuery(
      [
        { role: "user", content: [{ type: "text", text: "hello" }] },
        { role: "user", content: [{ type: "text", text: "你好" }] },
        { role: "user", content: [{ type: "text", text: "ok" }] },
        { role: "assistant", content: [{ type: "text", text: "assistant text" }] },
        { role: "user", content: [{ type: "text", text: "请帮我总结这次会话重点" }] },
      ],
      5,
      {
        skipGreeting: true,
        minQueryChars: 4,
      },
    );

    expect(query).toBe("请帮我总结这次会话重点");
  });

  it("filters by threshold, dedupes by uri, sorts by score, and limits top K", () => {
    const ranked = filterAndRank(
      [
        { uri: "u1", score: 0.2, content: "low" },
        { uri: "u2", score: 0.9, content: "high" },
        { uri: "u1", score: 0.8, content: "dup-better" },
        { uri: "u3", score: 0.7, content: "mid" },
        { uri: "u4", score: undefined, content: "no-score" },
      ],
      0.5,
      2,
    );

    expect(ranked).toEqual([
      { uri: "u2", score: 0.9, content: "high" },
      { uri: "u1", score: 0.8, content: "dup-better" },
    ]);
  });
});
