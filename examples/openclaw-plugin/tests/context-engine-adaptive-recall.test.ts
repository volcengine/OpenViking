import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../client.js";
import { memoryOpenVikingConfigSchema } from "../config.js";
import { createMemoryOpenVikingContextEngine } from "../context-engine.js";

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

function makeEngine() {
  const cfg = memoryOpenVikingConfigSchema.parse({
    mode: "remote",
    baseUrl: "http://127.0.0.1:1933",
    autoCapture: false,
    autoRecall: true,
    adaptiveRecall: true,
  });
  const logger = makeLogger();
  const client = {
    getSessionContext: vi.fn().mockResolvedValue({
      latest_archive_overview: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: makeStats(),
    }),
    find: vi.fn().mockResolvedValue({
      memories: [
        {
          uri: "viking://user/memories/preference",
          level: 2,
          category: "preferences",
          score: 0.9,
          abstract: "User prefers concrete evidence.",
        },
      ],
    }),
    read: vi.fn(),
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
    client: client as unknown as {
      getSessionContext: ReturnType<typeof vi.fn>;
      find: ReturnType<typeof vi.fn>;
      read: ReturnType<typeof vi.fn>;
    },
    logger,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("context-engine adaptive recall", () => {
  it("does not run semantic find for mechanical turns", async () => {
    const { engine, client } = makeEngine();

    const result = await engine.assemble({
      sessionId: "session-mechanical",
      messages: [{ role: "user", content: "User input:\nserve me these" }],
    });

    expect(client.getSessionContext).toHaveBeenCalledOnce();
    expect(client.find).not.toHaveBeenCalled();
    expect(result.systemPromptAddition).toBeUndefined();
  });

  it("runs full recall for memory-sensitive turns", async () => {
    const { engine, client } = makeEngine();

    const result = await engine.assemble({
      sessionId: "session-full",
      messages: [{ role: "user", content: "why did this break? diagnose root cause" }],
    });

    expect(client.find).toHaveBeenCalledTimes(2);
    const firstUser = result.messages.find((m) => m.role === "user");
    const firstUserContent =
      typeof firstUser?.content === "string" ? firstUser.content : JSON.stringify(firstUser?.content);
    expect(firstUserContent).toContain("<relevant-memories>");
    expect(firstUserContent).toContain("User prefers concrete evidence.");
  });

  it("reuses exact cached recall results", async () => {
    const { engine, client } = makeEngine();
    const messages = [{ role: "user", content: "why did this break? diagnose root cause" }];

    await engine.assemble({ sessionId: "session-cache", messages });
    await engine.assemble({ sessionId: "session-cache", messages });

    expect(client.find).toHaveBeenCalledTimes(2);
  });

  it("uses latest session recall for short follow-up turns", async () => {
    const { engine, client } = makeEngine();

    await engine.assemble({
      sessionId: "session-followup",
      messages: [{ role: "user", content: "why did this break? diagnose root cause" }],
    });
    const result = await engine.assemble({
      sessionId: "session-followup",
      messages: [{ role: "user", content: "continue" }],
    });

    expect(client.find).toHaveBeenCalledTimes(2);
    const firstUser = result.messages.find((m) => m.role === "user");
    const firstUserContent =
      typeof firstUser?.content === "string" ? firstUser.content : JSON.stringify(firstUser?.content);
    expect(firstUserContent).toContain("User prefers concrete evidence.");
  });

  it("coalesces duplicate background refreshes for fast turns", async () => {
    const { engine, client } = makeEngine();
    const findResult = deferred<{
      memories: Array<{
        uri: string;
        level: number;
        category: string;
        score: number;
        abstract: string;
      }>;
    }>();
    client.find.mockReturnValue(findResult.promise);

    await engine.assemble({
      sessionId: "session-background",
      messages: [{ role: "user", content: "continue" }],
    });
    await engine.assemble({
      sessionId: "session-background",
      messages: [{ role: "user", content: "continue" }],
    });

    expect(client.find).toHaveBeenCalledTimes(2);
    findResult.resolve({
      memories: [
        {
          uri: "viking://user/memories/background",
          level: 2,
          category: "preferences",
          score: 0.9,
          abstract: "Background refresh memory.",
        },
      ],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
});
