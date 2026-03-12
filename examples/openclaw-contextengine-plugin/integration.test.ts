import { describe, expect, it, vi } from "vitest";

import { createOpenVikingClient } from "./client.js";
import { OpenVikingContextEngine } from "./context-engine.js";
import { createTools } from "./tools.js";

async function waitForCondition(
  check: () => Promise<boolean>,
  timeoutMs: number,
  intervalMs = 500,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`condition not met within ${timeoutMs}ms`);
}

describe("integration", () => {
  it("degrades gracefully when retrieval times out", async () => {
    const engine = new OpenVikingContextEngine({
      config: {
        mode: "local",
        retrieval: { enabled: true, injectMode: "text", scoreThreshold: 0.15 },
        ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
      },
      client: {
        find: vi.fn(async () => {
          throw new Error("timeout");
        }),
        createSession: vi.fn(async () => "s1"),
        addSessionMessage: vi.fn(async () => undefined),
        commitSession: vi.fn(async () => ({ extractedCount: 0 })),
        deleteSession: vi.fn(async () => undefined),
        baseUrl: "http://127.0.0.1:1933",
      },
    } as never);

    await expect(
      engine.assemble({
        sessionId: "s1",
        messages: [{ role: "user", content: [{ type: "text", text: "need memory summary" }] }] as never,
      }),
    ).resolves.toMatchObject({
      messages: [{ role: "user", content: [{ type: "text", text: "need memory summary" }] }],
    });

    const out = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: [{ type: "text", text: "need memory summary" }] }] as never,
    });

    expect(out.systemPromptAddition).toContain("openviking_retrieval_fallback");
    expect(out.systemPromptAddition).toContain("retrieval_timeout");
  });

  it.runIf(process.env.OPENVIKING_E2E === "1")(
    "runs real online retrieval and assemble",
    async () => {
      const client = createOpenVikingClient({
        baseUrl: "http://127.0.0.1:1933",
        timeoutMs: 15000,
      });

      await expect(client.health()).resolves.toBe(true);

      const assembleQuery = "OpenViking";
      const precheck = await client.find(assembleQuery, { limit: 10, scoreThreshold: 0 });
      const precheckMemories = precheck.memories ?? [];
      expect(precheckMemories.length).toBeGreaterThan(0);

      const engine = new OpenVikingContextEngine({
        config: {
          mode: "local",
          retrieval: { enabled: true, injectMode: "simulated_tool_result", scoreThreshold: 0 },
          ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
        },
        client,
      });

      const assembled = await engine.assemble({
        sessionId: "e2e",
        messages: [{ role: "user", content: `帮我回忆 ${assembleQuery}` }],
      });

      expect(assembled.messages.length).toBeGreaterThan(1);
      expect(JSON.stringify(assembled.messages)).toContain("toolCall");
      expect(JSON.stringify(assembled.messages)).toContain("OpenViking retrieval results");
      expect(assembled.systemPromptAddition).not.toContain("openviking_retrieval_fallback");
    },
    20000,
  );

  it.runIf(process.env.OPENVIKING_E2E === "1")(
    "runs live tool commit/search flow and ingestion to retrieval path",
    async () => {
      const client = createOpenVikingClient({
        baseUrl: "http://127.0.0.1:1933",
        timeoutMs: 90000,
      });

      await expect(client.health()).resolves.toBe(true);

      const tools = createTools({ client });
      const commitTool = tools.find((t) => t.name === "commit_memory");
      const searchTool = tools.find((t) => t.name === "search_memories");
      expect(commitTool).toBeDefined();
      expect(searchTool).toBeDefined();

      const marker = `ov-e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const commitResult = await commitTool!.execute("tc1", {
        role: "user",
        content: `请记住这个唯一标记：${marker}`,
      });
      expect(JSON.stringify(commitResult.content)).toContain("Committed memory batch");

      await waitForCondition(async () => {
        const found = await client.find(marker, { limit: 10, scoreThreshold: 0 });
        return (found.memories ?? []).length > 0;
      }, 30000);

      const searchResult = await searchTool!.execute("tc2", {
        query: marker,
        limit: 10,
        scoreThreshold: 0,
      });
      expect(JSON.stringify(searchResult.content)).toContain("Found");
      const details = searchResult.details as { memories?: unknown[] };
      expect((details.memories ?? []).length).toBeGreaterThan(0);

      const engine = new OpenVikingContextEngine({
        config: {
          mode: "local",
          retrieval: { enabled: true, injectMode: "simulated_tool_result", scoreThreshold: 0 },
          ingestion: { writeMode: "compact_batch", maxBatchMessages: 200 },
        },
        client,
      });

      const assembled = await engine.assemble({
        sessionId: "e2e-live-tools",
        messages: [{ role: "user", content: `请回忆我刚才说的唯一标记 ${marker}` }],
      });

      expect(assembled.messages.length).toBeGreaterThan(1);
      expect(JSON.stringify(assembled.messages)).toContain("toolCall");
      expect(JSON.stringify(assembled.messages)).toContain("OpenViking retrieval results");
      expect(assembled.systemPromptAddition).not.toContain("openviking_retrieval_fallback");
    },
    45000,
  );
});
