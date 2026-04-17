import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../client.js";
import { memoryOpenVikingConfigSchema } from "../config.js";
import { createMemoryOpenVikingContextEngine } from "../context-engine.js";

const cfg = memoryOpenVikingConfigSchema.parse({
  mode: "remote",
  baseUrl: "http://127.0.0.1:1933",
  autoCapture: false,
  autoRecall: false,
  ingestReplyAssist: false,
});

function roughEstimate(messages: unknown[]): number {
  return Math.ceil(JSON.stringify(messages).length / 4);
}

function systemPromptTokens(text?: string): number {
  return text ? Math.ceil(text.length / 4) : 0;
}

function makeLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}

function makeStats() {
  return {
    totalArchives: 0,
    includedArchives: 0,
    droppedArchives: 0,
    failedArchives: 0,
    activeTokens: 0,
    archiveTokens: 0,
  };
}

function makeEngine(contextResult: unknown) {
  const logger = makeLogger();
  const client = {
    getSessionContext: vi.fn().mockResolvedValue(contextResult),
  } as unknown as OpenVikingClient;
  const getClient = vi.fn().mockResolvedValue(client);
  const resolveAgentId = vi.fn((sessionId: string) => `agent:${sessionId}`);

  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Context Engine (OpenViking)",
    version: "test",
    cfg,
    logger,
    getClient,
    resolveAgentId,
  });

  return {
    engine,
    client: client as unknown as { getSessionContext: ReturnType<typeof vi.fn> },
    getClient,
    logger,
    resolveAgentId,
  };
}

describe("context-engine assemble()", () => {
  it("assembles summary archive and completed tool parts into agent messages", async () => {
    const { engine, client, resolveAgentId } = makeEngine({
      latest_archive_overview: "# Session Summary\nPreviously discussed repository setup.",
      pre_archive_abstracts: [
        {
          archive_id: "archive_001",
          abstract: "Previously discussed repository setup.",
        },
      ],
      messages: [
        {
          id: "msg_1",
          role: "assistant",
          created_at: "2026-03-24T00:00:00Z",
          parts: [
            { type: "text", text: "I checked the latest context." },
            { type: "context", abstract: "User prefers concise answers." },
            {
              type: "tool",
              tool_id: "tool_123",
              tool_name: "read_file",
              tool_input: { path: "src/app.ts" },
              tool_output: "export const value = 1;",
              tool_status: "completed",
            },
          ],
        },
      ],
      estimatedTokens: 321,
      stats: {
        ...makeStats(),
        totalArchives: 1,
        includedArchives: 1,
        archiveTokens: 40,
        activeTokens: 281,
      },
    });

    const liveMessages = [{ role: "user", content: "fallback live message" }];
    const result = await engine.assemble({
      sessionId: "session-1",
      messages: liveMessages,
      tokenBudget: 4096,
    });

    expect(resolveAgentId).toHaveBeenCalledWith("session-1", undefined, "session-1");
    expect(client.getSessionContext).toHaveBeenCalledWith("session-1", 4096, "agent:session-1");
    expect(result.estimatedTokens).toBe(
      roughEstimate(result.messages) + systemPromptTokens(result.systemPromptAddition),
    );
    expect(result.systemPromptAddition).toContain("Session Context Guide");
    expect(result.messages).toEqual([
      {
        role: "user",
        content: "[Session History Summary]\n# Session Summary\nPreviously discussed repository setup.",
      },
      {
        role: "user",
        content: "[Archive Index]\narchive_001: Previously discussed repository setup.",
      },
      {
        role: "assistant",
        content: [
          { type: "text", text: "I checked the latest context." },
          { type: "text", text: "User prefers concise answers." },
          {
            type: "toolUse",
            id: "tool_123",
            name: "read_file",
            input: { path: "src/app.ts" },
          },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "tool_123",
        toolName: "read_file",
        content: [{ type: "text", text: "export const value = 1;" }],
        isError: false,
      },
    ]);
  });

  it("emits a non-error toolResult for a running tool (not a synthetic error)", async () => {
    const { engine } = makeEngine({
      latest_archive_overview: "",
      pre_archive_abstracts: [],
      messages: [
        {
          id: "msg_2",
          role: "assistant",
          created_at: "2026-03-24T00:00:00Z",
          parts: [
            {
              type: "tool",
              tool_id: "tool_running",
              tool_name: "bash",
              tool_input: { command: "npm test" },
              tool_output: "",
              tool_status: "running",
            },
          ],
        },
      ],
      estimatedTokens: 88,
      stats: {
        ...makeStats(),
        activeTokens: 88,
      },
    });

    const result = await engine.assemble({
      sessionId: "session-running",
      messages: [],
    });

    expect(result.systemPromptAddition).toBeUndefined();
    expect(result.messages).toHaveLength(2);
    expect(result.messages[0]).toEqual({
      role: "assistant",
      content: [
        {
          type: "toolUse",
          id: "tool_running",
          name: "bash",
          input: { command: "npm test" },
        },
      ],
    });
    expect(result.messages[1]).toMatchObject({
      role: "toolResult",
      toolCallId: "tool_running",
      toolName: "bash",
      isError: false,
    });
    const text = (result.messages[1] as any).content?.[0]?.text ?? "";
    expect(text).toContain("interrupted");
    expect((result.messages[1] as { content: Array<{ text: string }> }).content[0]?.text).not.toContain(
      "missing tool result",
    );
  });

  it("degrades tool parts without tool_id into assistant text blocks", async () => {
    const { engine } = makeEngine({
      latest_archive_overview: "",
      pre_archive_abstracts: [],
      messages: [
        {
          id: "msg_3",
          role: "assistant",
          created_at: "2026-03-24T00:00:00Z",
          parts: [
            { type: "text", text: "Tool state snapshot:" },
            {
              type: "tool",
              tool_id: "",
              tool_name: "grep",
              tool_input: { pattern: "TODO" },
              tool_output: "src/app.ts:17 TODO refine this",
              tool_status: "completed",
            },
          ],
        },
      ],
      estimatedTokens: 71,
      stats: {
        ...makeStats(),
        activeTokens: 71,
      },
    });

    const result = await engine.assemble({
      sessionId: "session-missing-id",
      messages: [],
    });

    expect(result.messages).toEqual([
      {
        role: "assistant",
        content: [
          { type: "text", text: "Tool state snapshot:" },
          {
            type: "text",
            text: "[grep] (completed)\nInput: {\"pattern\":\"TODO\"}\nOutput: src/app.ts:17 TODO refine this",
          },
        ],
      },
    ]);
  });

  it("falls back to live messages when assembled active messages look truncated", async () => {
    const { engine } = makeEngine({
      latest_archive_overview: "",
      pre_archive_abstracts: [],
      messages: [
        {
          id: "msg_4",
          role: "user",
          created_at: "2026-03-24T00:00:00Z",
          parts: [{ type: "text", text: "Only one stored message" }],
        },
      ],
      estimatedTokens: 12,
      stats: {
        ...makeStats(),
        activeTokens: 12,
      },
    });

    const liveMessages = [
      { role: "user", content: "message one" },
      { role: "assistant", content: [{ type: "text", text: "message two" }] },
    ];

    const result = await engine.assemble({
      sessionId: "session-fallback",
      messages: liveMessages,
      tokenBudget: 1024,
    });

    expect(result).toEqual({
      messages: liveMessages,
      estimatedTokens: roughEstimate(liveMessages),
    });
  });

  it("keeps assembled output within the requested token budget", async () => {
    const longText = "A".repeat(2500);
    const { engine } = makeEngine({
      latest_archive_overview: "# Session Summary\nA short overview",
      pre_archive_abstracts: [],
      messages: [
        {
          id: "msg_long_1",
          role: "user",
          created_at: "2026-03-24T00:00:00Z",
          parts: [{ type: "text", text: longText }],
        },
        {
          id: "msg_long_2",
          role: "assistant",
          created_at: "2026-03-24T00:00:01Z",
          parts: [{ type: "text", text: longText }],
        },
      ],
      estimatedTokens: 2000,
      stats: {
        ...makeStats(),
        totalArchives: 1,
        includedArchives: 1,
        activeTokens: 2000,
        archiveTokens: 10,
      },
    });

    const result = await engine.assemble({
      sessionId: "session-budgeted",
      messages: [],
      tokenBudget: 1024,
    });

    expect(result.estimatedTokens).toBeLessThanOrEqual(1024);
    expect(result.messages.length).toBeGreaterThan(0);
    expect(result.systemPromptAddition).toContain("Session Context Guide");
  });
});
