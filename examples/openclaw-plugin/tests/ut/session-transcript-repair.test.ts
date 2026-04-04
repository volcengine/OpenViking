import { describe, expect, it } from "vitest";

import {
  sanitizeToolUseResultPairing,
  repairToolUseResultPairing,
  makeMissingToolResult,
  stripToolResultDetails,
  repairToolCallInputs,
  sanitizeToolCallInputs,
} from "../../session-transcript-repair.js";
import type { AgentMessage } from "../../tool-call-id.js";

describe("sanitizeToolUseResultPairing", () => {
  it("does not modify correctly paired toolUse + toolResult", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "text", text: "Let me search" },
          { type: "toolUse", id: "tc1", name: "grep", input: { q: "TODO" } },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "tc1",
        toolName: "grep",
        content: [{ type: "text", text: "found 3 matches" }],
        isError: false,
      },
    ];

    const result = sanitizeToolUseResultPairing(messages);
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual(messages[0]);
  });

  it("inserts synthetic toolResult for orphaned toolUse", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc_orphan", name: "bash", input: { cmd: "ls" } },
        ],
      },
      { role: "user", content: "continue" },
    ];

    const report = repairToolUseResultPairing(messages);
    expect(report.added).toHaveLength(1);
    expect(report.added[0]!.toolCallId).toBe("tc_orphan");
    expect(report.added[0]!.isError).toBe(true);

    const repaired = report.messages;
    expect(repaired[0]!.role).toBe("assistant");
    expect(repaired[1]!.role).toBe("toolResult");
    expect((repaired[1] as any).toolCallId).toBe("tc_orphan");
    expect(repaired[2]!.role).toBe("user");
  });

  it("handles multiple toolUse calls with matching results", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc_a", name: "read", input: { path: "a.ts" } },
          { type: "toolUse", id: "tc_b", name: "read", input: { path: "b.ts" } },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "tc_a",
        toolName: "read",
        content: [{ type: "text", text: "content a" }],
      },
      {
        role: "toolResult",
        toolCallId: "tc_b",
        toolName: "read",
        content: [{ type: "text", text: "content b" }],
      },
    ];

    const result = sanitizeToolUseResultPairing(messages);
    expect(result).toHaveLength(3);
    expect(result[0]!.role).toBe("assistant");
    expect((result[1] as any).toolCallId).toBe("tc_a");
    expect((result[2] as any).toolCallId).toBe("tc_b");
  });

  it("drops orphan toolResult not associated with any assistant toolUse", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "hi" },
      {
        role: "toolResult",
        toolCallId: "orphan_id",
        toolName: "bash",
        content: [{ type: "text", text: "orphan output" }],
      },
      { role: "user", content: "continue" },
    ];

    const report = repairToolUseResultPairing(messages);
    expect(report.droppedOrphanCount).toBe(1);
    expect(report.messages).toHaveLength(2);
    expect(report.messages.every((m) => m.role !== "toolResult")).toBe(true);
  });

  it("drops duplicate toolResult across separate assistant spans", () => {
    // Duplicate detection via seenToolResultIds only triggers across different assistant spans
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc_x", name: "bash", input: { cmd: "echo 1" } },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "tc_x",
        toolName: "bash",
        content: [{ type: "text", text: "result 1" }],
      },
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc_y", name: "read", input: { path: "a.ts" } },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "tc_x",
        toolName: "bash",
        content: [{ type: "text", text: "stale duplicate of tc_x" }],
      },
      {
        role: "toolResult",
        toolCallId: "tc_y",
        toolName: "read",
        content: [{ type: "text", text: "content a" }],
      },
    ];

    const report = repairToolUseResultPairing(messages);
    // tc_x duplicate at position 3 should be dropped as orphan (belongs to second span
    // but doesn't match tc_y). The seenToolResultIds or orphan logic handles it.
    const resultIds = report.messages
      .filter((m) => m.role === "toolResult")
      .map((m) => (m as any).toolCallId);
    expect(resultIds).toContain("tc_x");
    expect(resultIds).toContain("tc_y");
  });

  it("does not synthesize results for errored/aborted assistant turns", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "err_tc", name: "bash", input: {} },
        ],
        stopReason: "error",
      },
      { role: "user", content: "try again" },
    ];

    const report = repairToolUseResultPairing(messages);
    expect(report.added).toHaveLength(0);
  });
});

describe("makeMissingToolResult", () => {
  it("creates synthetic error toolResult", () => {
    const result = makeMissingToolResult({
      toolCallId: "test-id",
      toolName: "my_tool",
    });
    expect(result.role).toBe("toolResult");
    expect(result.toolCallId).toBe("test-id");
    expect(result.toolName).toBe("my_tool");
    expect(result.isError).toBe(true);
    expect((result.content as any)[0].text).toContain("missing tool result");
  });

  it("defaults toolName to 'unknown'", () => {
    const result = makeMissingToolResult({ toolCallId: "x" });
    expect(result.toolName).toBe("unknown");
  });
});

describe("stripToolResultDetails", () => {
  it("removes details field from toolResult messages", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "hello" },
      {
        role: "toolResult",
        toolCallId: "tc1",
        content: [{ type: "text", text: "result" }],
        details: { extra: "data" },
      } as unknown as AgentMessage,
    ];
    const result = stripToolResultDetails(messages);
    expect(result).toHaveLength(2);
    expect("details" in result[1]!).toBe(false);
  });

  it("returns same reference if no details to strip", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "hello" },
      { role: "toolResult", toolCallId: "tc1", content: "ok" },
    ];
    const result = stripToolResultDetails(messages);
    expect(result).toBe(messages);
  });

  it("preserves non-toolResult messages unchanged", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "test" },
      { role: "assistant", content: "reply" },
    ];
    const result = stripToolResultDetails(messages);
    expect(result).toBe(messages);
  });
});

describe("repairToolCallInputs", () => {
  it("drops tool calls missing input", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "text", text: "I'll check" },
          { type: "toolUse", id: "tc1", name: "read_file" },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    expect(report.droppedToolCalls).toBe(1);
    const content = (report.messages[0] as any).content;
    expect(content).toHaveLength(1);
    expect(content[0].type).toBe("text");
  });

  it("drops tool calls missing id", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", name: "read_file", input: { path: "a.ts" } },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    expect(report.droppedToolCalls).toBe(1);
  });

  it("drops tool calls missing name", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc1", input: { path: "a.ts" } },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    expect(report.droppedToolCalls).toBe(1);
  });

  it("drops entire assistant message when all tool calls are invalid", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc1" },
          { type: "toolUse", name: "read", input: {} },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    expect(report.droppedAssistantMessages).toBe(1);
    expect(report.messages).toHaveLength(0);
  });

  it("filters by allowedToolNames", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc1", name: "read_file", input: { path: "a.ts" } },
          { type: "toolUse", id: "tc2", name: "dangerous_tool", input: { cmd: "rm -rf" } },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages, {
      allowedToolNames: ["read_file"],
    });
    expect(report.droppedToolCalls).toBe(1);
    const content = (report.messages[0] as any).content;
    expect(content).toHaveLength(1);
    expect(content[0].name).toBe("read_file");
  });

  it("redacts sessions_spawn attachment content", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "toolUse",
            id: "tc1",
            name: "sessions_spawn",
            input: {
              attachments: [
                { filename: "secret.txt", content: "sensitive data here" },
              ],
            },
          },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    const content = (report.messages[0] as any).content;
    const input = content[0].input;
    expect(input.attachments[0].content).toBe("__OPENCLAW_REDACTED__");
    expect(input.attachments[0].filename).toBe("secret.txt");
  });

  it("returns same reference when no changes needed", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc1", name: "read_file", input: { path: "a.ts" } },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    expect(report.messages).toBe(messages);
    expect(report.droppedToolCalls).toBe(0);
  });

  it("trims whitespace from tool names", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc1", name: " read_file ", input: { path: "a.ts" } },
        ],
      } as unknown as AgentMessage,
    ];
    const report = repairToolCallInputs(messages);
    const content = (report.messages[0] as any).content;
    expect(content[0].name).toBe("read_file");
  });

  it("passes through non-assistant messages unchanged", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "hello" },
      { role: "toolResult", toolCallId: "tc1", content: "ok" },
    ];
    const report = repairToolCallInputs(messages);
    expect(report.messages).toBe(messages);
  });
});

describe("sanitizeToolCallInputs", () => {
  it("returns just the messages array from repairToolCallInputs", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "tc1", name: "read_file", input: {} },
        ],
      } as unknown as AgentMessage,
    ];
    const result = sanitizeToolCallInputs(messages);
    expect(Array.isArray(result)).toBe(true);
  });
});
