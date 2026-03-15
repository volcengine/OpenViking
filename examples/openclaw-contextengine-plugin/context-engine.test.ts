import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { OpenVikingContextEngine } from "./context-engine.js";

describe("OpenVikingContextEngine", () => {
  it("returns assembled messages and systemPromptAddition", async () => {
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "text", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [{ uri: "m://1", content: "Memory A", score: 0.9 }] })),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
      },
    } as never);

    const out = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: [{ type: "text", text: "need memory summary" }] }] as never,
      tokenBudget: 10000,
    });

    expect(out).toHaveProperty("messages");
    expect(out).toHaveProperty("estimatedTokens");
    expect(out.systemPromptAddition).toContain("Memory A");
  });

  it("caps retrieval text injection in text mode", async () => {
    const longContent = "x".repeat(5000);
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "text", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [{ uri: "m://1", content: longContent, score: 0.9 }] })),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
      },
    } as never);

    const out = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: [{ type: "text", text: "need memory summary" }] }] as never,
    });

    expect(out.systemPromptAddition).toContain("x".repeat(200));
    expect(out.systemPromptAddition).not.toContain(longContent);
  });

  it("injects retrieval as simulated tool result messages in simulated_tool_result mode", async () => {
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "simulated_tool_result", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [{ uri: "m://1", content: "Memory A", score: 0.9 }] })),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    const out = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: [{ type: "text", text: "need memory summary" }] }] as never,
      tokenBudget: 10000,
    });

    expect(out.messages).toHaveLength(3);
    expect((out.messages[1] as { role?: string }).role).toBe("assistant");
    expect((out.messages[2] as { role?: string }).role).toBe("toolResult");

    const toolCallBlock = (
      (out.messages[1] as { content?: Array<{ type?: string; name?: string }> }).content ?? []
    ).find((block) => block.type === "toolCall");
    expect(toolCallBlock?.name).toBe("search_memories");

    const toolResultText = JSON.stringify((out.messages[2] as { content?: unknown }).content);
    expect(toolResultText).toContain("OpenViking retrieval results");
    expect(toolResultText).toContain("Memory A");
    expect(out.systemPromptAddition).not.toContain("Memory A");
  });

  it("uses retrieval.lastNUserMessages to build query", async () => {
    const find = vi.fn(async () => ({ memories: [] }));
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: {
          enabled: true,
          injectMode: "text",
          scoreThreshold: 0.15,
          lastNUserMessages: 2,
        },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find,
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    await engine.assemble({
      sessionId: "s1",
      messages: [
        { role: "user", content: "User first context" },
        { role: "assistant", content: "A1" },
        { role: "user", content: "User second context" },
        { role: "assistant", content: "A2" },
        { role: "user", content: "User third context" },
      ] as never,
      tokenBudget: 10000,
    });

    expect(find).toHaveBeenCalledTimes(1);
    expect(find.mock.calls[0]?.[0]).toBe("User second context\nUser third context");
  });

  it("passes retrieval.targetUri to OpenViking search", async () => {
    const find = vi.fn(async () => ({ memories: [] }));
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: {
          enabled: true,
          injectMode: "text",
          scoreThreshold: 0.15,
          targetUri: "viking://agent/memories",
        },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find,
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "where is prior agent memory" }] as never,
    });

    expect(find).toHaveBeenCalledTimes(1);
    expect(find.mock.calls[0]?.[1]).toMatchObject({
      targetUri: "viking://agent/memories",
    });
  });

  it("skips retrieval when query messages are greetings/too short", async () => {
    const find = vi.fn(async () => ({ memories: [] }));
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: {
          enabled: true,
          injectMode: "text",
          scoreThreshold: 0.15,
          lastNUserMessages: 5,
          skipGreeting: true,
          minQueryChars: 4,
        },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find,
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 1 })),
        deleteSession: vi.fn(async () => undefined),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    const out = await engine.assemble({
      sessionId: "s1",
      messages: [
        { role: "user", content: "hello" },
        { role: "user", content: "你好" },
        { role: "user", content: "ok" },
      ] as never,
      tokenBudget: 10000,
    });

    expect(find).not.toHaveBeenCalled();
    expect(out.messages).toHaveLength(3);
  });

  it("returns bootstrapped result from bootstrap", async () => {
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "text", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [] })),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 0 })),
        deleteSession: vi.fn(async () => undefined),
      },
    } as never);

    await expect(engine.bootstrap({ sessionId: "s1", sessionFile: "/tmp/s1.jsonl" })).resolves.toEqual({
      bootstrapped: true,
      importedMessages: 0,
    });
  });

  it("injects profile.md and high-quality summary after bootstrap", async () => {
    const root = await mkdtemp(join(tmpdir(), "ov-profile-"));
    const agentDir = join(root, "agents", "main");
    const sessionsDir = join(agentDir, "sessions");
    await mkdir(sessionsDir, { recursive: true });
    const sessionFile = join(sessionsDir, "s1.jsonl");
    await writeFile(sessionFile, "", "utf-8");
    await writeFile(join(agentDir, "profile.md"), "Always be concise and factual.", "utf-8");

    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: false, injectMode: "text", scoreThreshold: 0.15 },
        profileInjection: { enabled: true, qualityGateMinScore: 0.7, maxChars: 1200 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [{ uri: "m://1", content: "User prefers short answers.", score: 0.9 }] })),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 0 })),
        deleteSession: vi.fn(async () => undefined),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    const boot = await engine.bootstrap({ sessionId: "s1", sessionFile });
    expect(boot.importedMessages).toBe(1);

    const assembled = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "need memory summary" }] as never,
    });

    expect(assembled.systemPromptAddition).toContain("Always be concise and factual.");
    expect(assembled.systemPromptAddition).toContain("User prefers short answers.");
  });

  it("supports no-op afterTurn", async () => {
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "text", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [] })),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 0 })),
        deleteSession: vi.fn(async () => undefined),
      },
    } as never);

    await expect(engine.afterTurn()).resolves.toBeUndefined();
  });

  it("runs compact batch write path in compact", async () => {
    const createSession = vi.fn(async () => "s1");
    const addSessionMessage = vi.fn(async () => undefined);
    const commitSession = vi.fn(async () => ({ extractedCount: 2 }));
    const deleteSession = vi.fn(async () => undefined);

    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "text", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => ({ memories: [] })),
        createSession,
        addSessionMessage,
        commitSession,
        deleteSession,
      },
    } as never);

    const out = await engine.compact({
      sessionId: "s1",
      sessionFile: "./tmp",
      tokenBudget: 10000,
      runtimeContext: {
        messages: [
          { role: "user", content: [{ type: "text", text: "U" }] },
          { role: "assistant", content: [{ type: "text", text: "A" }] },
        ],
      },
    });

    expect(out.ok).toBe(true);
    expect(out.compacted).toBe(true);
    expect(createSession).toHaveBeenCalledTimes(1);
    expect(addSessionMessage).toHaveBeenCalledTimes(2);
    expect(commitSession).toHaveBeenCalledTimes(1);
    expect(deleteSession).toHaveBeenCalledTimes(1);
  });
});
