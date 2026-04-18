import { describe, expect, it } from "vitest";

import { extractNewTurnMessages } from "../../text-utils.js";
import { convertToAgentMessages, mergeConsecutiveAssistants } from "../../context-engine.js";

describe("extractNewTurnMessages: toolCallId propagation", () => {
  it("propagates toolCallId from toolResult to extracted tool part", () => {
    const messages = [
      {
        role: "assistant",
        content: [
          { type: "text", text: "Let me check." },
          { type: "toolCall", id: "call_abc123", name: "exec", arguments: { command: "ls" } },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "call_abc123",
        toolName: "exec",
        content: [{ type: "text", text: "file1.txt\nfile2.txt" }],
      },
    ];

    const { messages: extracted } = extractNewTurnMessages(messages, 0);

    const toolMsg = extracted.find(
      (m) => m.parts.some((p) => p.type === "tool"),
    );
    expect(toolMsg).toBeDefined();

    const toolPart = toolMsg!.parts.find((p) => p.type === "tool");
    expect(toolPart).toBeDefined();
    expect(toolPart!.type).toBe("tool");
    if (toolPart!.type === "tool") {
      expect(toolPart!.toolCallId).toBe("call_abc123");
      expect(toolPart!.toolName).toBe("exec");
      expect(toolPart!.toolInput).toEqual({ command: "ls" });
      expect(toolPart!.toolOutput).toContain("file1.txt");
    }
  });

  it("sets toolCallId to undefined when original message has no toolCallId", () => {
    const messages = [
      {
        role: "toolResult",
        toolName: "search",
        content: [{ type: "text", text: "no results" }],
      },
    ];

    const { messages: extracted } = extractNewTurnMessages(messages, 0);
    const toolPart = extracted[0]!.parts[0]!;
    expect(toolPart.type).toBe("tool");
    if (toolPart.type === "tool") {
      expect(toolPart.toolCallId).toBeUndefined();
    }
  });

  it("maps toolResult to role=user", () => {
    const messages = [
      {
        role: "toolResult",
        toolCallId: "call_xyz",
        toolName: "exec",
        content: [{ type: "text", text: "hello" }],
      },
    ];

    const { messages: extracted } = extractNewTurnMessages(messages, 0);
    expect(extracted[0]!.role).toBe("user");
  });
});

describe("convertToAgentMessages: structured tool round-trip", () => {
  it("user-role tool with tool_id → assistant(toolUse) + toolResult", () => {
    const msg = {
      role: "user",
      parts: [
        {
          type: "tool",
          tool_id: "call_abc123",
          tool_name: "read",
          tool_status: "completed",
          tool_input: { path: "/tmp/test.txt" },
          tool_output: "file content here",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    expect(result).toHaveLength(2);

    const assistantMsg = result[0]!;
    expect(assistantMsg.role).toBe("assistant");
    const blocks = assistantMsg.content as Array<Record<string, unknown>>;
    expect(blocks[0]!.type).toBe("toolUse");
    expect(blocks[0]!.id).toBe("call_abc123");
    expect(blocks[0]!.name).toBe("read");
    expect(blocks[0]!.input).toEqual({ path: "/tmp/test.txt" });

    const toolResult = result[1]!;
    expect(toolResult.role).toBe("toolResult");
    expect((toolResult as Record<string, unknown>).toolCallId).toBe("call_abc123");
    expect((toolResult as Record<string, unknown>).isError).toBe(false);
  });

  it("assistant-role tool with tool_id → toolUse + toolResult (unchanged)", () => {
    const msg = {
      role: "assistant",
      parts: [
        { type: "text", text: "Let me check." },
        {
          type: "tool",
          tool_id: "call_abc123",
          tool_name: "read",
          tool_status: "completed",
          tool_input: { path: "/tmp/test.txt" },
          tool_output: "file content here",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    expect(result).toHaveLength(2);

    const assistantMsg = result[0]!;
    expect(assistantMsg.role).toBe("assistant");
    const blocks = assistantMsg.content as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.type).toBe("text");
    expect(blocks[1]!.type).toBe("toolUse");
    expect(blocks[1]!.id).toBe("call_abc123");

    expect(result[1]!.role).toBe("toolResult");
  });

  it("no tool_id → degrade to text (user role)", () => {
    const msg = {
      role: "user",
      parts: [
        {
          type: "tool",
          tool_id: "",
          tool_name: "exec",
          tool_status: "completed",
          tool_input: { command: "echo hello" },
          tool_output: "hello",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    expect(result).toHaveLength(1);
    expect(result[0]!.role).toBe("user");

    const content = result[0]!.content as string;
    expect(content).toContain("[exec]");
    expect(content).toContain("hello");
  });

  it("no tool_id → degrade to text (assistant role)", () => {
    const msg = {
      role: "assistant",
      parts: [
        {
          type: "tool",
          tool_id: "",
          tool_name: "exec",
          tool_status: "error",
          tool_input: { command: "bad-cmd" },
          tool_output: "command not found",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    expect(result).toHaveLength(1);
    expect(result[0]!.role).toBe("assistant");

    const blocks = result[0]!.content as Array<Record<string, unknown>>;
    expect(blocks[0]!.type).toBe("text");
    const text = blocks[0]!.text as string;
    expect(text).toContain("[exec]");
    expect(text).toContain("command not found");
  });

  it("user message with text + tool(tool_id) → user(text) + assistant(toolUse) + toolResult", () => {
    const msg = {
      role: "user",
      parts: [
        { type: "text", text: "User said something" },
        {
          type: "tool",
          tool_id: "call_xyz",
          tool_name: "exec",
          tool_status: "completed",
          tool_input: { command: "ls" },
          tool_output: "file.txt",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    expect(result).toHaveLength(3);

    expect(result[0]!.role).toBe("user");
    expect(result[0]!.content).toBe("User said something");

    expect(result[1]!.role).toBe("assistant");
    const blocks = result[1]!.content as Array<Record<string, unknown>>;
    expect(blocks[0]!.type).toBe("toolUse");

    expect(result[2]!.role).toBe("toolResult");
  });

  it("user message with text + tool(no tool_id) → single user message", () => {
    const msg = {
      role: "user",
      parts: [
        { type: "text", text: "User said something" },
        {
          type: "tool",
          tool_id: "",
          tool_name: "exec",
          tool_status: "completed",
          tool_input: { command: "ls" },
          tool_output: "file.txt",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    expect(result).toHaveLength(1);
    expect(result[0]!.role).toBe("user");

    const content = result[0]!.content as string;
    expect(content).toContain("User said something");
    expect(content).toContain("[exec]");
  });

  it("tool with error status sets isError=true", () => {
    const msg = {
      role: "user",
      parts: [
        {
          type: "tool",
          tool_id: "call_err",
          tool_name: "exec",
          tool_status: "error",
          tool_input: { command: "bad" },
          tool_output: "not found",
        },
      ],
    };

    const result = convertToAgentMessages(msg);
    const toolResult = result[1]!;
    expect((toolResult as Record<string, unknown>).isError).toBe(true);
  });
});

describe("mergeConsecutiveAssistants", () => {
  it("merges two consecutive assistant messages", () => {
    const messages = [
      { role: "assistant", content: [{ type: "text", text: "Hello" }] },
      { role: "assistant", content: [{ type: "toolUse", id: "c1", name: "read", input: {} }] },
    ] as Array<{ role: string; content: unknown }>;

    const merged = mergeConsecutiveAssistants(messages);
    expect(merged).toHaveLength(1);
    expect(merged[0]!.role).toBe("assistant");

    const blocks = merged[0]!.content as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.type).toBe("text");
    expect(blocks[1]!.type).toBe("toolUse");
  });

  it("does not merge non-consecutive assistants", () => {
    const messages = [
      { role: "assistant", content: [{ type: "text", text: "A" }] },
      { role: "toolResult", toolCallId: "c1", content: [{ type: "text", text: "result" }] },
      { role: "assistant", content: [{ type: "text", text: "B" }] },
    ] as Array<{ role: string; content: unknown }>;

    const merged = mergeConsecutiveAssistants(messages);
    expect(merged).toHaveLength(3);
  });

  it("handles string content in assistant messages", () => {
    const messages = [
      { role: "assistant", content: "Hello" },
      { role: "assistant", content: [{ type: "toolUse", id: "c1", name: "read", input: {} }] },
    ] as Array<{ role: string; content: unknown }>;

    const merged = mergeConsecutiveAssistants(messages);
    expect(merged).toHaveLength(1);

    const blocks = merged[0]!.content as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.text).toBe("Hello");
    expect(blocks[1]!.type).toBe("toolUse");
  });

  it("simulates real OV sequence: assistant(text) + user(tool→assistant+toolResult)", () => {
    const ovAssistant = { role: "assistant", parts: [{ type: "text", text: "Let me check." }] };
    const ovUserTool = {
      role: "user",
      parts: [{
        type: "tool",
        tool_id: "call_abc",
        tool_name: "read",
        tool_status: "completed",
        tool_input: { path: "/tmp/f.txt" },
        tool_output: "content",
      }],
    };

    const raw = [
      ...convertToAgentMessages(ovAssistant),
      ...convertToAgentMessages(ovUserTool),
    ];
    const merged = mergeConsecutiveAssistants(raw);

    expect(merged).toHaveLength(2);
    expect(merged[0]!.role).toBe("assistant");
    const blocks = merged[0]!.content as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.type).toBe("text");
    expect(blocks[0]!.text).toBe("Let me check.");
    expect(blocks[1]!.type).toBe("toolUse");
    expect(blocks[1]!.id).toBe("call_abc");

    expect(merged[1]!.role).toBe("toolResult");
    expect((merged[1] as Record<string, unknown>).toolCallId).toBe("call_abc");
  });
});
