import { describe, expect, it } from "vitest";

import {
  sanitizeToolCallId,
  extractToolCallsFromAssistant,
  extractToolResultId,
  isValidCloudCodeAssistToolId,
  sanitizeToolCallIdsForCloudCodeAssist,
} from "../../tool-call-id.js";
import type { AgentMessage } from "../../tool-call-id.js";

describe("sanitizeToolCallId", () => {
  describe("strict mode", () => {
    it("keeps alphanumeric IDs unchanged", () => {
      expect(sanitizeToolCallId("abc123")).toBe("abc123");
    });

    it("strips non-alphanumeric characters", () => {
      expect(sanitizeToolCallId("tool_call-123")).toBe("toolcall123");
    });

    it("returns default for empty string", () => {
      expect(sanitizeToolCallId("")).toBe("defaulttoolid");
    });

    it("returns default for all-symbol input", () => {
      expect(sanitizeToolCallId("---")).toBe("sanitizedtoolid");
    });
  });

  describe("strict9 mode", () => {
    it("truncates long alphanumeric IDs to 9 chars", () => {
      const result = sanitizeToolCallId("abcdef123456", "strict9");
      expect(result).toHaveLength(9);
      expect(result).toBe("abcdef123");
    });

    it("hashes short alphanumeric IDs to 9 chars", () => {
      const result = sanitizeToolCallId("ab", "strict9");
      expect(result).toHaveLength(9);
      expect(/^[a-f0-9]+$/.test(result)).toBe(true);
    });

    it("returns default for empty string", () => {
      expect(sanitizeToolCallId("", "strict9")).toBe("defaultid");
    });

    it("produces consistent results", () => {
      const a = sanitizeToolCallId("my-tool-id", "strict9");
      const b = sanitizeToolCallId("my-tool-id", "strict9");
      expect(a).toBe(b);
    });
  });
});

describe("extractToolCallsFromAssistant", () => {
  it("extracts toolUse blocks", () => {
    const msg: Extract<AgentMessage, { role: "assistant" }> = {
      role: "assistant",
      content: [
        { type: "text", text: "Let me check" },
        { type: "toolUse", id: "call_1", name: "read_file" },
      ],
    };
    const calls = extractToolCallsFromAssistant(msg);
    expect(calls).toEqual([{ id: "call_1", name: "read_file" }]);
  });

  it("extracts functionCall blocks", () => {
    const msg: Extract<AgentMessage, { role: "assistant" }> = {
      role: "assistant",
      content: [{ type: "functionCall", id: "fc_1", name: "search" }],
    };
    const calls = extractToolCallsFromAssistant(msg);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.id).toBe("fc_1");
  });

  it("extracts toolCall blocks", () => {
    const msg: Extract<AgentMessage, { role: "assistant" }> = {
      role: "assistant",
      content: [{ type: "toolCall", id: "tc_1", name: "grep" }],
    };
    expect(extractToolCallsFromAssistant(msg)).toHaveLength(1);
  });

  it("returns empty for non-array content", () => {
    const msg: Extract<AgentMessage, { role: "assistant" }> = {
      role: "assistant",
      content: "plain text",
    };
    expect(extractToolCallsFromAssistant(msg)).toEqual([]);
  });

  it("skips blocks without id", () => {
    const msg: Extract<AgentMessage, { role: "assistant" }> = {
      role: "assistant",
      content: [{ type: "toolUse" }, { type: "text", text: "hello" }],
    };
    expect(extractToolCallsFromAssistant(msg)).toEqual([]);
  });

  it("skips blocks with unknown type", () => {
    const msg: Extract<AgentMessage, { role: "assistant" }> = {
      role: "assistant",
      content: [{ type: "unknownType", id: "x" }],
    };
    expect(extractToolCallsFromAssistant(msg)).toEqual([]);
  });
});

describe("extractToolResultId", () => {
  it("returns toolCallId when present", () => {
    const msg: Extract<AgentMessage, { role: "toolResult" }> = {
      role: "toolResult",
      toolCallId: "call_1",
    };
    expect(extractToolResultId(msg)).toBe("call_1");
  });

  it("falls back to toolUseId", () => {
    const msg: Extract<AgentMessage, { role: "toolResult" }> = {
      role: "toolResult",
      toolUseId: "use_1",
    };
    expect(extractToolResultId(msg)).toBe("use_1");
  });

  it("prefers toolCallId over toolUseId", () => {
    const msg: Extract<AgentMessage, { role: "toolResult" }> = {
      role: "toolResult",
      toolCallId: "call_1",
      toolUseId: "use_1",
    };
    expect(extractToolResultId(msg)).toBe("call_1");
  });

  it("returns null when neither is present", () => {
    const msg: Extract<AgentMessage, { role: "toolResult" }> = {
      role: "toolResult",
    };
    expect(extractToolResultId(msg)).toBeNull();
  });
});

describe("isValidCloudCodeAssistToolId", () => {
  describe("strict mode", () => {
    it("accepts alphanumeric strings", () => {
      expect(isValidCloudCodeAssistToolId("abc123")).toBe(true);
    });

    it("rejects strings with special chars", () => {
      expect(isValidCloudCodeAssistToolId("abc-123")).toBe(false);
      expect(isValidCloudCodeAssistToolId("abc_123")).toBe(false);
    });

    it("rejects empty string", () => {
      expect(isValidCloudCodeAssistToolId("")).toBe(false);
    });
  });

  describe("strict9 mode", () => {
    it("accepts exactly 9-char alphanumeric strings", () => {
      expect(isValidCloudCodeAssistToolId("abcdef123", "strict9")).toBe(true);
    });

    it("rejects strings not exactly 9 chars", () => {
      expect(isValidCloudCodeAssistToolId("abc", "strict9")).toBe(false);
      expect(isValidCloudCodeAssistToolId("abcdefghij", "strict9")).toBe(false);
    });
  });
});

describe("sanitizeToolCallIdsForCloudCodeAssist", () => {
  it("returns same reference when no changes needed", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "hi" },
    ];
    const result = sanitizeToolCallIdsForCloudCodeAssist(messages);
    expect(result).toBe(messages);
  });

  it("sanitizes assistant tool call IDs", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [{ type: "toolUse", id: "call_with-dashes", name: "test" }],
      },
      {
        role: "toolResult",
        toolCallId: "call_with-dashes",
        content: [{ type: "text", text: "ok" }],
      },
    ];
    const result = sanitizeToolCallIdsForCloudCodeAssist(messages);
    const assistantContent = (result[0] as Extract<AgentMessage, { role: "assistant" }>).content as Array<{ id: string }>;
    const sanitizedId = assistantContent[0]!.id;
    expect(sanitizedId).toMatch(/^[a-zA-Z0-9]+$/);

    const toolResult = result[1] as Extract<AgentMessage, { role: "toolResult" }>;
    expect(toolResult.toolCallId).toBe(sanitizedId);
  });

  it("handles duplicate tool call IDs by making them unique", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [
          { type: "toolUse", id: "same_id", name: "tool1" },
          { type: "toolUse", id: "same_id", name: "tool2" },
        ],
      },
      { role: "toolResult", toolCallId: "same_id", content: "result1" },
      { role: "toolResult", toolCallId: "same_id", content: "result2" },
    ];
    const result = sanitizeToolCallIdsForCloudCodeAssist(messages);
    const assistantContent = (result[0] as Extract<AgentMessage, { role: "assistant" }>).content as Array<{ id: string }>;
    const id1 = assistantContent[0]!.id;
    const id2 = assistantContent[1]!.id;
    expect(id1).not.toBe(id2);
  });

  it("strict9 mode produces 9-char IDs", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [{ type: "toolUse", id: "long-tool-call-id-here", name: "test" }],
      },
      { role: "toolResult", toolCallId: "long-tool-call-id-here", content: "ok" },
    ];
    const result = sanitizeToolCallIdsForCloudCodeAssist(messages, "strict9");
    const assistantContent = (result[0] as Extract<AgentMessage, { role: "assistant" }>).content as Array<{ id: string }>;
    expect(assistantContent[0]!.id).toHaveLength(9);
  });

  it("preserves user messages unchanged", () => {
    const messages: AgentMessage[] = [
      { role: "user", content: "hello" },
    ];
    const result = sanitizeToolCallIdsForCloudCodeAssist(messages);
    expect(result).toBe(messages);
  });

  it("matches assistant and toolResult IDs after sanitization", () => {
    const messages: AgentMessage[] = [
      {
        role: "assistant",
        content: [{ type: "toolUse", id: "tc-abc-123", name: "read" }],
      },
      {
        role: "toolResult",
        toolCallId: "tc-abc-123",
        content: [{ type: "text", text: "file content" }],
      },
    ];
    const result = sanitizeToolCallIdsForCloudCodeAssist(messages);
    const assistantContent = (result[0] as Extract<AgentMessage, { role: "assistant" }>).content as Array<{ id: string }>;
    const toolResult = result[1] as Extract<AgentMessage, { role: "toolResult" }>;
    expect(assistantContent[0]!.id).toBe(toolResult.toolCallId);
  });
});
