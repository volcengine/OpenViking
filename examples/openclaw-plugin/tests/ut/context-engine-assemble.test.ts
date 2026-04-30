import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

const cfg = memoryOpenVikingConfigSchema.parse({
  mode: "remote",
  baseUrl: "http://127.0.0.1:1933",
  autoCapture: false,
  autoRecall: false,
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

function makeEngine(
  contextResult: unknown,
  opts?: {
    cfgOverrides?: Record<string, unknown>;
  },
) {
  const logger = makeLogger();
  const client = {
    getSessionContext: vi.fn().mockResolvedValue(contextResult),
    find: vi.fn().mockResolvedValue({ memories: [], resources: [], total: 0 }),
    read: vi.fn().mockResolvedValue(""),
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
    resolveAgentId,
  });

  return {
    engine,
    client: client as unknown as {
      getSessionContext: ReturnType<typeof vi.fn>;
      find: ReturnType<typeof vi.fn>;
      read: ReturnType<typeof vi.fn>;
    },
    getClient,
    logger,
    resolveAgentId,
  };
}

describe("context-engine assemble()", () => {
  it("prepends auto-recall to the latest user message during transformContext", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok" }),
      }),
    );
    try {
      const { engine, client } = makeEngine(
        {
          latest_archive_overview: "This OV context must not be rebuilt during transformContext.",
          pre_archive_abstracts: [],
          messages: [
            {
              id: "stored-current-user",
              role: "user",
              created_at: "2026-04-30T00:00:00Z",
              parts: [{ type: "text", text: "stale stored prompt" }],
            },
          ],
          estimatedTokens: 12,
          stats: makeStats(),
        },
        {
          cfgOverrides: {
            autoRecall: true,
            recallPreferAbstract: true,
          },
        },
      );
      client.find
        .mockResolvedValueOnce({
          memories: [
            {
              uri: "viking://user/default/memories/rust-pref",
              level: 2,
              category: "preferences",
              abstract: "User prefers Rust for backend tasks.",
              score: 0.93,
            },
          ],
          total: 1,
        })
        .mockResolvedValueOnce({ memories: [], total: 0 });

      const sourceMessages = [
        { role: "user", content: "[Session History Summary]\nOlder archive summary." },
        { role: "assistant", content: [{ type: "text", text: "Previous answer." }] },
        { role: "user", content: "what backend language should we use?" },
      ];

      const result = await engine.assemble({
        sessionId: "session-transform",
        messages: sourceMessages,
      });

      expect(client.getSessionContext).not.toHaveBeenCalled();
      expect(result.messages).toHaveLength(sourceMessages.length);
      expect(result.messages[0]).toBe(sourceMessages[0]);
      expect(result.messages[1]).toBe(sourceMessages[1]);
      expect(result.messages[2]?.role).toBe("user");
      expect(result.messages[2]?.content).toMatch(/^<relevant-memories>/);
      expect(result.messages[2]?.content).toContain("Source: openviking-auto-recall");
      expect(result.messages[2]?.content).toContain("User prefers Rust for backend tasks.");
      expect(result.messages[2]?.content).toContain("what backend language should we use?");
      expect(result.systemPromptAddition).toBeUndefined();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("passes through transformContext messages when the latest message is not user", async () => {
    const { engine, getClient } = makeEngine(
      {
        latest_archive_overview: "unused",
        pre_archive_abstracts: [],
        messages: [],
        estimatedTokens: 0,
        stats: makeStats(),
      },
      {
        cfgOverrides: {
          autoRecall: true,
        },
      },
    );
    const sourceMessages = [
      { role: "user", content: "run the tool" },
      {
        role: "assistant",
        content: [{ type: "toolCall", id: "tool_1", name: "bash", arguments: {} }],
      },
    ];

    const result = await engine.assemble({
      sessionId: "session-tool-loop",
      messages: sourceMessages,
    });

    expect(getClient).not.toHaveBeenCalled();
    expect(result.messages).toBe(sourceMessages);
    expect(result.estimatedTokens).toBe(roughEstimate(sourceMessages));
  });

  it("passes through transformContext latest user messages when auto-recall is disabled", async () => {
    const { engine, getClient } = makeEngine({
      latest_archive_overview: "unused",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: makeStats(),
    });
    const sourceMessages = [
      { role: "assistant", content: [{ type: "text", text: "Previous answer." }] },
      { role: "user", content: "what backend language should we use?" },
    ];

    const result = await engine.assemble({
      sessionId: "session-auto-recall-disabled",
      messages: sourceMessages,
    });

    expect(getClient).not.toHaveBeenCalled();
    expect(result.messages).toBe(sourceMessages);
    expect(result.estimatedTokens).toBe(roughEstimate(sourceMessages));
  });

  it("treats prompt-less assemble with availableTools as main assemble", async () => {
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
          id: "msg_main_no_prompt",
          role: "assistant",
          created_at: "2026-03-24T00:00:00Z",
          parts: [{ type: "text", text: "Stored answer from OpenViking." }],
        },
      ],
      estimatedTokens: 120,
      stats: {
        ...makeStats(),
        totalArchives: 1,
        includedArchives: 1,
        archiveTokens: 40,
        activeTokens: 80,
      },
    });

    const liveMessages = [{ role: "user", content: "fallback live message" }];
    const result = await engine.assemble({
      sessionId: "session-main-no-prompt",
      messages: liveMessages,
      tokenBudget: 4096,
      availableTools: new Set(),
    });

    expect(resolveAgentId).toHaveBeenCalledWith(
      "session-main-no-prompt",
      undefined,
      "session-main-no-prompt",
    );
    expect(client.getSessionContext).toHaveBeenCalledWith(
      "session-main-no-prompt",
      4096,
      "agent:session-main-no-prompt",
    );
    expect(client.find).not.toHaveBeenCalled();
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
      content: [{ type: "text", text: "Stored answer from OpenViking." }],
    });
    expect(result.systemPromptAddition).toContain("Session Context Guide");
  });

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
      prompt: "current user prompt",
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
          type: "toolCall",
          id: "tool_123",
          name: "read_file",
          arguments: { path: "src/app.ts" },
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
      prompt: "current user prompt",
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
      prompt: "current user prompt",
      sessionId: "session-running",
      messages: [],
    });

    expect(result.systemPromptAddition).toBeUndefined();
    expect(result.messages).toHaveLength(2);
    expect(result.messages[0]).toEqual({
      role: "assistant",
      content: [
        {
          type: "toolCall",
          id: "tool_running",
          name: "bash",
          arguments: { command: "npm test" },
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
      prompt: "current user prompt",
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
      prompt: "current user prompt",
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
      prompt: "current user prompt",
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
      prompt: "current user prompt",
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
      prompt: "current user prompt",
      sessionId: "session-budgeted",
      messages: [],
      tokenBudget: 1024,
    });

    expect(result.estimatedTokens).toBeLessThanOrEqual(1024);
    expect(result.messages.length).toBeGreaterThan(0);
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
      prompt: "current user prompt",
      sessionId: "session-error",
      messages: liveMessages,
    });

    expect(result.messages).toBe(liveMessages);
    expect(result.estimatedTokens).toBe(roughEstimate(liveMessages));
    expect(result.systemPromptAddition).toBeUndefined();
  });

  it("drops tool-only user messages instead of emitting empty content (issue #1485)", async () => {
    const { engine } = makeEngine({
      latest_archive_overview: "",
      pre_archive_abstracts: [],
      messages: [
        {
          id: "msg_tool_only_user",
          role: "user",
          created_at: "2026-04-17T00:00:00Z",
          parts: [
            {
              type: "tool",
              tool_id: "tool_abc",
              tool_name: "bash",
              tool_input: { command: "ls" },
              tool_output: "file.txt",
              tool_status: "completed",
            },
          ],
        },
      ],
      estimatedTokens: 50,
      stats: { ...makeStats(), activeTokens: 50 },
    });

    const result = await engine.assemble({
      prompt: "current user prompt",
      sessionId: "session-tool-only",
      messages: [],
    });

    const emptyContentMsg = result.messages.find(
      (m) => typeof m.content === "string" && m.content === "",
    );
    expect(emptyContentMsg).toBeUndefined();
  });
});
