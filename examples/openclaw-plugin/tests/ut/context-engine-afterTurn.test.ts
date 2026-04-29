import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

function makeLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}

function makeEngine(opts?: {
  autoCapture?: boolean;
  commitTokenThreshold?: number;
  getSession?: Record<string, unknown>;
  addSessionMessageError?: Error;
  cfgOverrides?: Record<string, unknown>;
}) {
  const cfg = memoryOpenVikingConfigSchema.parse({
    mode: "remote",
    baseUrl: "http://127.0.0.1:1933",
    autoCapture: opts?.autoCapture ?? true,
    autoRecall: false,
    commitTokenThreshold: opts?.commitTokenThreshold ?? 20000,
    emitStandardDiagnostics: true,
    ...(opts?.cfgOverrides ?? {}),
  });
  const logger = makeLogger();

  const addSessionMessage = opts?.addSessionMessageError
    ? vi.fn().mockRejectedValue(opts.addSessionMessageError)
    : vi.fn().mockResolvedValue(undefined);

  const client = {
    addSessionMessage,
    commitSession: vi.fn().mockResolvedValue({
      status: "accepted",
      task_id: "task-1",
      archived: false,
    }),
    getSession: vi.fn().mockResolvedValue(
      opts?.getSession ?? { pending_tokens: 100 },
    ),
    getSessionContext: vi.fn().mockResolvedValue({
      latest_archive_overview: "",
      latest_archive_id: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: { totalArchives: 0, includedArchives: 0, droppedArchives: 0, failedArchives: 0, activeTokens: 0, archiveTokens: 0 },
    }),
  } as unknown as OpenVikingClient;

  const getClient = vi.fn().mockResolvedValue(client);
  const resolveAgentId = vi.fn((_sid: string) => "test-agent");

  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Test Engine",
    version: "test",
    cfg,
    logger,
    getClient,
    resolveAgentId,
  });

  return {
    engine,
    client: client as unknown as {
      addSessionMessage: ReturnType<typeof vi.fn>;
      commitSession: ReturnType<typeof vi.fn>;
      getSession: ReturnType<typeof vi.fn>;
    },
    logger,
    getClient,
  };
}

describe("context-engine afterTurn()", () => {
  it("does nothing when autoCapture is disabled", async () => {
    const { engine, client } = makeEngine({ autoCapture: false });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });

  it("skips afterTurn completely when the session matches bypassSessionPatterns", async () => {
    const { engine, client, getClient, logger } = makeEngine({
      cfgOverrides: {
        bypassSessionPatterns: ["agent:*:cron:**"],
      },
    });

    await engine.afterTurn!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:cron:nightly:run:1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello" }],
      prePromptMessageCount: 0,
    });

    expect(getClient).not.toHaveBeenCalled();
    expect(client.addSessionMessage).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("\"reason\":\"session_bypassed\""),
    );
  });

  it("skips when messages array is empty", async () => {
    const { engine, client, logger } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("no_messages"),
    );
  });

  it("skips when no new user/assistant messages after prePromptMessageCount", async () => {
    const { engine, client, logger } = makeEngine();

    const messages = [
      { role: "system", content: "system prompt" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("no_new_turn_messages"),
    );
  });

  it("stores new messages via addSessionMessage with proper roles", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "old message" },
      { role: "user", content: "hello world, this is a new message" },
      { role: "assistant", content: [{ type: "text", text: "hi there, nice to meet you" }] },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 1,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    // First call: user message
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[0][2][0].text).toContain("hello world");
    // Second call: assistant message
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][2][0].text).toContain("hi there");
  });

  it("passes the latest non-system message timestamp to addSessionMessage as ISO string", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "old message", timestamp: 1775037600000 },
        { role: "user", content: "new message", timestamp: 1775037660000 },
        { role: "assistant", content: "new reply", timestamp: 1775037720000 },
        { role: "toolResult", toolName: "bash", content: "exit 0", timestamp: 1775037780000 },
        { role: "system", content: "ignored system message", timestamp: 1775037840000 },
      ],
      prePromptMessageCount: 1,
    });

    // user + assistant + toolResult(→user) = 3 calls (toolResult merges with no adjacent user)
    expect(client.addSessionMessage).toHaveBeenCalled();
    const lastCallIdx = client.addSessionMessage.mock.calls.length - 1;
    const createdAt = client.addSessionMessage.mock.calls[lastCallIdx][4] as string;
    expect(createdAt).toBe("2026-04-01T10:03:00.000Z");
  });

  it("records senderId from runtimeContext in afterTurn diagnostics", async () => {
    const { engine, logger } = makeEngine({
      commitTokenThreshold: 50,
      getSession: { pending_tokens: 5000 },
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello world" }],
      prePromptMessageCount: 0,
      runtimeContext: { senderId: "telegram:12345" },
    });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("\"senderIdFound\":true"),
    );
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("\"senderId\":\"telegram:12345\""),
    );
  });

  it("passes sanitized senderId as role_id", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello world" }],
      prePromptMessageCount: 0,
      runtimeContext: { senderId: "telegram:12345" },
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.addSessionMessage.mock.calls[0][5]).toBe("telegram_12345");
  });

  it("sanitizes <relevant-memories> from user content but not from assistant", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      {
        role: "user",
        content: "my question <relevant-memories>injected memory data</relevant-memories> more text",
      },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    const storedContent = (client.addSessionMessage.mock.calls[0][2] as Array<{ text?: string }>)[0].text;
    expect(storedContent).not.toContain("relevant-memories");
    expect(storedContent).not.toContain("injected memory data");
    expect(storedContent).toContain("my question");
  });

  it("does not commit when pendingTokens < threshold", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 20000,
      getSession: { pending_tokens: 100 },
    });

    const messages = [
      { role: "user", content: "some meaningful content here for testing" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.commitSession).not.toHaveBeenCalled();
  });

  it("commits when pendingTokens >= threshold", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 20000,
      getSession: { pending_tokens: 25000 },
    });

    const messages = [
      { role: "user", content: "some meaningful content here for testing" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.commitSession).toHaveBeenCalledTimes(1);
    const commitCall = client.commitSession.mock.calls[0];
    expect(commitCall[1]).toMatchObject({ wait: false });
  });

  it("catches errors without throwing", async () => {
    const { engine, logger } = makeEngine({
      addSessionMessageError: new Error("network timeout"),
    });

    const messages = [
      { role: "user", content: "this will fail when storing to OV" },
    ];

    await expect(
      engine.afterTurn!({
        sessionId: "s1",
        sessionFile: "",
        messages,
        prePromptMessageCount: 0,
      }),
    ).resolves.toBeUndefined();

    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("afterTurn failed"),
    );
  });

  it("commit uses OV session ID derived from sessionId", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 100,
      getSession: { pending_tokens: 5000 },
    });

    const messages = [
      { role: "user", content: "enough content to trigger commit logic path" },
    ];

    await engine.afterTurn!({
      sessionId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    const commitSessionId = client.commitSession.mock.calls[0][0] as string;
    expect(commitSessionId).toBe("a1b2c3d4-e5f6-7890-abcd-ef1234567890");
  });

  it("commit passes wait=false for afterTurn (async Phase 2)", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 100,
      getSession: { pending_tokens: 5000 },
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "triggering commit with enough tokens" }],
      prePromptMessageCount: 0,
    });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(client.commitSession.mock.calls[0][1]).toMatchObject({ wait: false });
  });

  it("calls addSessionMessage with OV session ID as first arg", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "my-session",
      sessionFile: "",
      messages: [{ role: "user", content: "content for session storage" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    const ovSessionId = client.addSessionMessage.mock.calls[0][0] as string;
    expect(ovSessionId).toBe("my-session");
  });

  it("preserves code snippets and file paths in captured content", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      {
        role: "user",
        content: "Look at src/app.ts and run `npm install`",
      },
      {
        role: "assistant",
        content: [{ type: "text", text: "Here's the code:\n```typescript\nexport const x = 1;\n```" }],
      },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    const userParts = client.addSessionMessage.mock.calls[0][2] as Array<{ text?: string }>;
    const assistantParts = client.addSessionMessage.mock.calls[1][2] as Array<{ text?: string }>;
    expect(userParts.map(p => p.text).join(" ")).toContain("src/app.ts");
    expect(userParts.map(p => p.text).join(" ")).toContain("npm install");
    expect(assistantParts.map(p => p.text).join(" ")).toContain("export const x = 1");
  });

  it("passes agentId to addSessionMessage", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "test message for agent routing" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    const agentId = client.addSessionMessage.mock.calls[0][3] as string;
    expect(agentId).toBe("test-agent");
  });

  it("checks pending tokens after addSessionMessage", async () => {
    const { engine, client } = makeEngine({
      getSession: { pending_tokens: 500 },
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "check pending token flow" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalled();
    expect(client.getSession).toHaveBeenCalled();
  });

  it("maps toolResult to user role", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "assistant", content: [
        { type: "text", text: "running tool" },
        { type: "toolUse", name: "bash", input: { cmd: "ls" } },
      ] },
      { role: "toolResult", toolName: "bash", content: "file1.txt\nfile2.txt" },
      { role: "assistant", content: "done" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(3);
    // assistant → user(toolResult) → assistant
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[1][2][0].tool_output).toContain("file1.txt");
    expect(client.addSessionMessage.mock.calls[1][2][0].tool_output).toContain("file2.txt");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("assistant");
  });

  it("stores adjacent same-role messages as separate entries with current extractor behavior", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "first question" },
      { role: "user", content: "second question" },
      { role: "assistant", content: "answer" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(3);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    const firstCallParts = client.addSessionMessage.mock.calls[0][2] as Array<{ text?: string; type?: string }>;
    expect(firstCallParts.map(p => p.text).join(" ")).toContain("first question");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    const secondCallParts = client.addSessionMessage.mock.calls[1][2] as Array<{ text?: string; type?: string }>;
    expect(secondCallParts.map(p => p.text).join(" ")).toContain("second question");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("assistant");
  });

  it("stores adjacent toolResults as separate user groups with current extractor behavior", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "assistant", content: [
        { type: "text", text: "calling tools" },
        { type: "toolUse", name: "read", input: { path: "a.txt" } },
      ] },
      { role: "toolResult", toolName: "read", content: "content of a" },
      { role: "toolResult", toolName: "write", content: "ok" },
      { role: "assistant", content: "all done" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(4);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    expect((client.addSessionMessage.mock.calls[1][2] as Array<{ tool_output?: string }>)[0]?.tool_output).toContain("content of a");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("user");
    expect((client.addSessionMessage.mock.calls[2][2] as Array<{ tool_output?: string }>)[0]?.tool_output).toContain("ok");
    expect(client.addSessionMessage.mock.calls[3][1]).toBe("assistant");
  });

  it("sanitizes <relevant-memories> from assistant content", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "question" },
      { role: "assistant", content: "Here is context <relevant-memories>data</relevant-memories> end" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    const assistantParts = client.addSessionMessage.mock.calls[1][2] as Array<{ text?: string }>;
    expect(assistantParts.map(p => p.text).join(" ")).not.toContain("relevant-memories");
    expect(assistantParts.map(p => p.text).join(" ")).toContain("Here is context");
  });

  it("skips heartbeat messages from being stored", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK." },
      { role: "assistant", content: "HEARTBEAT_OK" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });

  it("skips heartbeat via isHeartbeat flag", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "regular message" },
      { role: "assistant", content: "reply" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
      isHeartbeat: true,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });

  it("skips store when all new messages are system only", async () => {
    const { engine, client } = makeEngine();

    // Only system messages after prePromptMessageCount → no user/assistant texts extracted
    const messages = [
      { role: "user", content: "previous message" },
      { role: "system", content: "system prompt injection" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 1,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });
});
