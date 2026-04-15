import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

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

function makeEngine(
  contextResult: unknown,
  opts?: {
    cfgOverrides?: Record<string, unknown>;
    quickPrecheck?: () => Promise<{ ok: true } | { ok: false; reason: string }>;
  },
) {
  const logger = makeLogger();
  const client = {
    getSessionContext: vi.fn().mockResolvedValue(contextResult),
  } as unknown as OpenVikingClient;
  const getClient = vi.fn().mockResolvedValue(client);
  const resolveAgentId = vi.fn((sessionId: string) => `agent:${sessionId}`);
  const localCfg = opts?.cfgOverrides
    ? memoryOpenVikingConfigSchema.parse({
        ...cfg,
        ...opts.cfgOverrides,
      })
    : cfg;

  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Context Engine (OpenViking)",
    version: "test",
    cfg: localCfg,
    logger,
    getClient,
    quickPrecheck: opts?.quickPrecheck,
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
    expect(result.estimatedTokens).toBe(roughEstimate(result.messages));
    expect(result.systemPromptAddition).toContain("Session Context Guide");
    expect(result.messages[0]).toEqual({
      role: "user",
      content: "[Session History Summary]\n# Session Summary\nPreviously discussed repository setup.",
    });
    expect(result.messages[1]).toEqual({
      role: "user",
      content: "[Archive Index]\narchive_001: Previously discussed repository setup.",
    });
    expect(result.messages[2]).toEqual({
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
    });
    expect(result.messages[3]).toEqual({
      role: "toolResult",
      toolCallId: "tool_123",
      toolName: "read_file",
      content: [{ type: "text", text: "export const value = 1;" }],
      isError: false,
    });
  });

  it("passes through live messages when the session matches bypassSessionPatterns", async () => {
    const { engine, client, getClient } = makeEngine(
      {
        latest_archive_overview: "unused",
        pre_archive_abstracts: [],
        messages: [],
        estimatedTokens: 123,
        stats: makeStats(),
      },
      {
        cfgOverrides: {
          bypassSessionPatterns: ["agent:*:cron:**"],
        },
      },
    );

    const liveMessages = [{ role: "user", content: "fallback live message" }];
    const result = await engine.assemble({
      sessionId: "runtime-session",
      sessionKey: "agent:main:cron:nightly:run:1",
      messages: liveMessages,
      tokenBudget: 4096,
    });

    expect(getClient).not.toHaveBeenCalled();
    expect(client.getSessionContext).not.toHaveBeenCalled();
    expect(result).toEqual({
      messages: liveMessages,
      estimatedTokens: roughEstimate(liveMessages),
    });
  });

  it("falls back immediately when local precheck reports OpenViking unavailable", async () => {
    const quickPrecheck = vi.fn().mockResolvedValue({
      ok: false as const,
      reason: "local process is not running",
    });
    const { engine, client, getClient, logger } = makeEngine(
      {
        latest_archive_overview: "unused",
        pre_archive_abstracts: [],
        messages: [],
        estimatedTokens: 123,
        stats: makeStats(),
      },
      {
        cfgOverrides: {
          mode: "local",
          port: 1933,
        },
        quickPrecheck,
      },
    );

    const liveMessages = [{ role: "user", content: "fallback live message" }];
    const result = await engine.assemble({
      sessionId: "session-local",
      messages: liveMessages,
      tokenBudget: 4096,
    });

    expect(quickPrecheck).toHaveBeenCalledTimes(1);
    expect(getClient).not.toHaveBeenCalled();
    expect(client.getSessionContext).not.toHaveBeenCalled();
    expect(result).toEqual({
      messages: liveMessages,
      estimatedTokens: roughEstimate(liveMessages),
    });
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("assemble precheck failed"),
    );
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
    expect((result.messages[1] as { content: Array<{ text: string }> }).content[0]?.text).toContain(
      "interrupted",
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

  it("passes through when OV has no archives and no active messages (new user)", async () => {
    const { engine } = makeEngine({
      latest_archive_overview: "",
      latest_archive_id: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: makeStats(),
    });

    const liveMessages = [
      { role: "user", content: "hello, first message" },
    ];

    const result = await engine.assemble({
      sessionId: "session-new-user",
      messages: liveMessages,
    });

    expect(result.messages).toBe(liveMessages);
    expect(result.estimatedTokens).toBe(roughEstimate(liveMessages));
    expect(result.systemPromptAddition).toBeUndefined();
  });

  it("still produces non-empty output when OV messages have empty parts (overview fills it)", async () => {
    const { engine } = makeEngine({
      latest_archive_overview: "Some overview of previous sessions",
      latest_archive_id: "archive_001",
      pre_archive_abstracts: [],
      messages: [
        {
          id: "msg_empty",
          role: "assistant",
          created_at: "2026-03-29T00:00:00Z",
          parts: [],
        },
      ],
      estimatedTokens: 10,
      stats: {
        ...makeStats(),
        totalArchives: 1,
        includedArchives: 1,
      },
    });

    const liveMessages = [
      { role: "user", content: "what was that thing?" },
    ];

    const result = await engine.assemble({
      sessionId: "session-empty-parts",
      messages: liveMessages,
    });

    // Even with empty parts, the overview and archive index still produce messages
    // so sanitized.length > 0 and we get the assembled result (not fallback)
    expect(result.messages.length).toBeGreaterThanOrEqual(2);
    expect(result.messages[0]).toMatchObject({
      role: "user",
      content: expect.stringContaining("Session History Summary"),
    });
    expect(result.systemPromptAddition).toContain("Session Context Guide");
  });

  it("falls back to original messages when getClient throws", async () => {
    const logger = makeLogger();
    const getClient = vi.fn().mockRejectedValue(new Error("OV connection refused"));
    const resolveAgentId = vi.fn((_s: string) => "agent");

    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test",
      version: "test",
      cfg,
      logger,
      getClient,
      resolveAgentId,
    });

    const liveMessages = [
      { role: "user", content: "hello" },
    ];

    const result = await engine.assemble({
      sessionId: "session-error",
      messages: liveMessages,
    });

    expect(result.messages).toBe(liveMessages);
    expect(result.estimatedTokens).toBe(roughEstimate(liveMessages));
    expect(result.systemPromptAddition).toBeUndefined();
  });
});
