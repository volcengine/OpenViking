import { describe, expect, it } from "vitest";

import {
  mergeConsecutiveAssistants,
  mergeConsecutiveUsers,
  ensureAlternation,
} from "../../context-engine.js";

// Local AgentMessage type matches the (lax) one used inside context-engine.ts.
type AgentMessage = {
  role?: string;
  content?: unknown;
  timestamp?: unknown;
};

// =====================================================================
// mergeConsecutiveUsers — core fix for issue #1724
// =====================================================================

describe("mergeConsecutiveUsers", () => {
  it("merges two consecutive user messages into one (issue #1724 source 1+session)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "[Session History Summary]\nold archive" },
      { role: "user", content: "Hello, what's up?" },
      { role: "assistant", content: "Hi!" },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(2);
    expect(result[0]?.role).toBe("user");
    expect(result[1]?.role).toBe("assistant");
  });

  it("merges three consecutive user turns (archive + yield + new user)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "[Session History Summary]\nold archive" },
      { role: "user", content: "[sessions_yield interrupt]" },
      { role: "user", content: "Hello" },
      { role: "assistant", content: "Hi" },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(2);
    expect(result[0]?.role).toBe("user");
  });

  it("merges four consecutive user turns (3-source stack)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "[Session History Summary]" },
      { role: "user", content: "[sessions_yield interrupt]" },
      { role: "user", content: "[Audio] User text: [Telegram User] hello" },
      { role: "user", content: "new message" },
      { role: "assistant", content: "ok" },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(2);
  });

  it("preserves existing alternation untouched", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "Q1" },
      { role: "assistant", content: "A1" },
      { role: "user", content: "Q2" },
      { role: "assistant", content: "A2" },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toEqual(input);
  });

  it("does not merge user with assistant (only same-role)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "Q" },
      { role: "assistant", content: "A" },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(2);
    expect(result[0]?.role).toBe("user");
    expect(result[1]?.role).toBe("assistant");
  });

  it("merges content as array of parts (preserves part structure)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: [{ type: "text", text: "part 1" }] },
      { role: "user", content: [{ type: "text", text: "part 2" }] },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(1);
    expect(Array.isArray(result[0]?.content)).toBe(true);
    const content = result[0]?.content as Array<{ type?: string; text?: string }>;
    expect(content).toHaveLength(2);
    expect(content[0]?.text).toBe("part 1");
    expect(content[1]?.text).toBe("part 2");
  });

  it("merges string content with array content (normalizes string to text part)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "first message as string" },
      { role: "user", content: [{ type: "text", text: "second as array" }] },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(1);
    const content = result[0]?.content as Array<{ type?: string; text?: string }>;
    expect(content).toHaveLength(2);
    expect(content[0]?.text).toBe("first message as string");
    expect(content[1]?.text).toBe("second as array");
  });

  it("handles empty input", () => {
    expect(mergeConsecutiveUsers([])).toEqual([]);
  });

  it("handles single message input", () => {
    const input: AgentMessage[] = [{ role: "user", content: "alone" }];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(1);
    expect(result[0]?.content).toBe("alone");
  });

  it("does not mutate the input messages array", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "a" },
      { role: "user", content: "b" },
    ];
    const snapshot = JSON.parse(JSON.stringify(input));
    mergeConsecutiveUsers(input);
    expect(input).toEqual(snapshot);
  });
});

// =====================================================================
// mergeConsecutiveUsers + tool_result handling (indirectly tests
// hoistToolResults)
// =====================================================================

describe("mergeConsecutiveUsers + tool_result hoisting", () => {
  it("hoists tool_result to the front when merging user with text + tool_result", () => {
    const input: AgentMessage[] = [
      { role: "user", content: [{ type: "text", text: "user text" }] },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "tu_001", content: "result data" },
          { type: "text", text: "more text" },
        ],
      },
    ];
    const result = mergeConsecutiveUsers(input);
    expect(result).toHaveLength(1);
    const content = result[0]?.content as Array<{ type?: string }>;
    expect(content).toHaveLength(3);
    expect(content[0]?.type).toBe("tool_result");
    expect(content[1]?.type).toBe("text");
    expect(content[2]?.type).toBe("text");
  });

  it("preserves tool_result order when both messages contain tool_results", () => {
    const input: AgentMessage[] = [
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "tu_a", content: "a" },
          { type: "text", text: "between" },
        ],
      },
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "tu_b", content: "b" }],
      },
    ];
    const result = mergeConsecutiveUsers(input);
    const content = result[0]?.content as Array<{
      type?: string;
      tool_use_id?: string;
    }>;
    expect(content).toHaveLength(3);
    expect(content[0]?.type).toBe("tool_result");
    expect(content[0]?.tool_use_id).toBe("tu_a");
    expect(content[1]?.type).toBe("tool_result");
    expect(content[1]?.tool_use_id).toBe("tu_b");
    expect(content[2]?.type).toBe("text");
  });
});

// =====================================================================
// ensureAlternation — defensive invariant for assistant-assistant
// =====================================================================

describe("ensureAlternation", () => {
  it("preserves correctly alternating sequences", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "Q1" },
      { role: "assistant", content: "A1" },
      { role: "user", content: "Q2" },
      { role: "assistant", content: "A2" },
    ];
    const result = ensureAlternation(input);
    expect(result).toEqual(input);
  });

  it("inserts placeholder user between consecutive assistants", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "Q" },
      { role: "assistant", content: "A1" },
      { role: "assistant", content: "A2" },
    ];
    const result = ensureAlternation(input);
    expect(result).toHaveLength(4);
    expect(result[0]?.role).toBe("user");
    expect(result[1]?.role).toBe("assistant");
    expect(result[2]?.role).toBe("user");
    expect(result[2]?.content).toBe("(no content)");
    expect(result[3]?.role).toBe("assistant");
  });

  it("does NOT touch consecutive user (that is mergeConsecutiveUsers' job)", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "U1" },
      { role: "user", content: "U2" },
      { role: "assistant", content: "A" },
    ];
    const result = ensureAlternation(input);
    expect(result).toEqual(input);
  });

  it("handles empty input", () => {
    expect(ensureAlternation([])).toEqual([]);
  });

  it("handles single message", () => {
    const input: AgentMessage[] = [{ role: "assistant", content: "alone" }];
    expect(ensureAlternation(input)).toEqual(input);
  });

  it("inserts multiple placeholders for triple consecutive assistants", () => {
    const input: AgentMessage[] = [
      { role: "user", content: "Q" },
      { role: "assistant", content: "A1" },
      { role: "assistant", content: "A2" },
      { role: "assistant", content: "A3" },
    ];
    const result = ensureAlternation(input);
    // Q, A1, [user], A2, [user], A3
    expect(result).toHaveLength(6);
    expect(result.map((m) => m.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
  });
});

// =====================================================================
// Issue #1724 end-to-end scenario: combined fix verification
// =====================================================================

describe("issue #1724 end-to-end", () => {
  it("fixes the canonical archive + yield + audio + new-user stack", () => {
    // Reproduce the actual stack from issue #1724 and its follow-up analysis.
    const assembled: AgentMessage[] = [
      // archive injection
      { role: "user", content: "[Session History Summary]\n# Working Memory\n..." },
      // retained tail (5 alternating, ending in assistant — standard cadence)
      { role: "assistant", content: "previous response" },
      { role: "user", content: "got it" },
      { role: "assistant", content: "anything else?" },
      { role: "user", content: "write iterative version" },
      { role: "assistant", content: "ok working on it..." },
      // OC yield events (2 consecutive user injections)
      { role: "user", content: "[sessions_yield interrupt]" },
      { role: "user", content: "Turn yielded. [Context: previous turn ...]" },
      { role: "assistant", content: "I have resumed, continuing..." },
      { role: "user", content: "good" },
      // Audio metadata + new user message (2 more consecutive user injections)
      { role: "user", content: "[Audio] User text: [Telegram User] wrap it in a class" },
      { role: "user", content: "also add comments" },
    ];

    // Simulate the full pipeline that buildAssembledContext now runs:
    //   sanitizeToolUseResultPairing → mergeConsecutiveUsers → ensureAlternation
    // (sanitize step is a no-op for this scenario since there are no tool calls)
    const merged = mergeConsecutiveUsers(assembled);
    const final = ensureAlternation(merged);

    // After fix: no two adjacent same-role messages
    for (let i = 1; i < final.length; i++) {
      expect(final[i]?.role).not.toBe(final[i - 1]?.role);
    }

    // The 12 input messages collapse to 9 alternating turns
    expect(final).toHaveLength(9);
    expect(final[0]?.role).toBe("user");
    expect(final[final.length - 1]?.role).toBe("user");
  });

  it("compact-path scenario: keep_recent_count=0 with no retained tail", () => {
    // /compact path archives everything, retained tail is empty.
    // [Session History Summary] (user) followed directly by new user message
    // would have been a guaranteed failure; verify we now produce alternation.
    const assembled: AgentMessage[] = [
      { role: "user", content: "[Session History Summary]\n..." },
      { role: "user", content: "what was the last thing we did?" },
    ];

    const merged = mergeConsecutiveUsers(assembled);
    const final = ensureAlternation(merged);

    expect(final).toHaveLength(1);
    expect(final[0]?.role).toBe("user");
  });
});

// =====================================================================
// Symmetry sanity: mergeConsecutiveAssistants + mergeConsecutiveUsers
// behave as mirror images
// =====================================================================

describe("merge symmetry (assistants vs users)", () => {
  it("mergeConsecutiveUsers handles user same way mergeConsecutiveAssistants handles assistant", () => {
    const userInput: AgentMessage[] = [
      { role: "user", content: [{ type: "text", text: "u1" }] },
      { role: "user", content: [{ type: "text", text: "u2" }] },
      { role: "assistant", content: "a" },
    ];
    const asstInput: AgentMessage[] = [
      { role: "user", content: "u" },
      { role: "assistant", content: [{ type: "text", text: "a1" }] },
      { role: "assistant", content: [{ type: "text", text: "a2" }] },
    ];

    const mergedUsers = mergeConsecutiveUsers(userInput);
    const mergedAssistants = mergeConsecutiveAssistants(asstInput);

    expect(mergedUsers).toHaveLength(2);
    expect(mergedAssistants).toHaveLength(2);

    // Both merged role messages have 2-part content arrays
    const u = mergedUsers[0]?.content as Array<unknown>;
    const a = mergedAssistants[1]?.content as Array<unknown>;
    expect(u).toHaveLength(2);
    expect(a).toHaveLength(2);
  });
});
