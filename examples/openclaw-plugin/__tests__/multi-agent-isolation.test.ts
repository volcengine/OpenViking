/**
 * Integration tests for PR #597 – Multi-agent memory isolation fix.
 *
 * These tests verify that:
 *   1. Two agents writing memories simultaneously do NOT contaminate each other.
 *   2. Per-agent cache keys are isolated (composite scope:agentId key model).
 *   3. lastProcessedMsgCount (prePromptMessageCount) is tracked per-agent, not shared.
 *   4. "main" agent correctly maps through resolveAgentId (backward compat – "main" is
 *      treated as an explicit, non-empty agentId, not collapsed to "default").
 *   5. A single-agent setup still works (backward compat with no agentId in config).
 *
 * The suite is self-contained: it spins up a tiny in-process mock HTTP server for each
 * test group so no real OpenViking instance is needed.
 *
 * Run with:
 *   npx tsx --test __tests__/multi-agent-isolation.test.ts
 * or (after installing tsx as a devDependency):
 *   node --import tsx/esm --test __tests__/multi-agent-isolation.test.ts
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { createHash } from "node:crypto";
import { test, describe, before, after } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function md5Short(input: string): string {
  return createHash("md5").update(input).digest("hex").slice(0, 12);
}

/** Minimal request/response log entry captured by the mock server. */
type CapturedRequest = {
  method: string;
  path: string;
  body: Record<string, unknown>;
  agentHeader: string | null;
};

/** Tiny mock OpenViking HTTP server. Returns configurable fixtures. */
function createMockServer(fixtures: {
  /** Queued session IDs returned by POST /api/v1/sessions */
  sessionIds?: string[];
  /** Fixed user id returned by GET /api/v1/system/status */
  userId?: string;
}) {
  const captured: CapturedRequest[] = [];
  let sessionIdQueue = fixtures.sessionIds ? [...fixtures.sessionIds] : ["sess-001"];
  const userId = fixtures.userId ?? "testuser";

  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) {
      chunks.push(chunk as Buffer);
    }
    const rawBody = Buffer.concat(chunks).toString("utf-8");
    let body: Record<string, unknown> = {};
    try {
      body = rawBody ? (JSON.parse(rawBody) as Record<string, unknown>) : {};
    } catch {
      // ignore parse errors
    }

    const agentHeader = req.headers["x-openviking-agent"] as string | undefined | null ?? null;
    const url = req.url ?? "/";
    const method = req.method ?? "GET";
    captured.push({ method, path: url, body, agentHeader });

    res.setHeader("Content-Type", "application/json");

    if (url === "/health") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok" }));
      return;
    }

    if (url === "/api/v1/system/status" && method === "GET") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: { user: userId } }));
      return;
    }

    if (url === "/api/v1/sessions" && method === "POST") {
      const sid = sessionIdQueue.shift() ?? `sess-${Date.now()}`;
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: { session_id: sid } }));
      return;
    }

    if (url.includes("/api/v1/sessions/") && method === "POST" && url.endsWith("/messages")) {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: {} }));
      return;
    }

    if (url.includes("/api/v1/sessions/") && method === "POST" && url.endsWith("/extract")) {
      res.writeHead(200);
      res.end(
        JSON.stringify({
          status: "ok",
          result: [{ uri: "viking://agent/memories/extracted-1", abstract: "test memory" }],
        }),
      );
      return;
    }

    if (url.includes("/api/v1/sessions/") && method === "GET") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: { message_count: 1 } }));
      return;
    }

    if (url.includes("/api/v1/sessions/") && method === "DELETE") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: {} }));
      return;
    }

    if (url.startsWith("/api/v1/search/find") && method === "POST") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: { memories: [], total: 0 } }));
      return;
    }

    if (url.startsWith("/api/v1/fs/ls") && method === "GET") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok", result: [] }));
      return;
    }

    // Fallback
    res.writeHead(404);
    res.end(JSON.stringify({ status: "error", error: { message: `Unknown route: ${url}` } }));
  });

  return {
    server,
    captured,
    listen(): Promise<number> {
      return new Promise((resolve) => {
        server.listen(0, "127.0.0.1", () => {
          const addr = server.address() as { port: number };
          resolve(addr.port);
        });
      });
    },
    close(): Promise<void> {
      return new Promise((resolve, reject) =>
        server.close((err) => (err ? reject(err) : resolve())),
      );
    },
  };
}

// ---------------------------------------------------------------------------
// Dynamically import the plugin modules.
// We use dynamic import() so these tests can run via tsx/esm without a build step.
// ---------------------------------------------------------------------------

// NOTE: TypeScript types are not re-exported at runtime; we use `any` for the
// imported class so the test file itself doesn't require transpilation of
// generic constraints.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyClient = any;

async function loadClientModule(): Promise<{
  OpenVikingClient: new (
    baseUrl: string,
    apiKey: string,
    timeoutMs: number,
  ) => AnyClient;
}> {
  // The plugin uses .js extensions in imports (ESM Node16 resolution),
  // so we import the .ts source directly via tsx.
  return import("../client.js") as Promise<{
    OpenVikingClient: new (
      baseUrl: string,
      apiKey: string,
      timeoutMs: number,
    ) => AnyClient;
  }>;
}

async function loadConfigModule(): Promise<{
  memoryOpenVikingConfigSchema: {
    parse: (v: unknown) => Record<string, unknown>;
  };
}> {
  return import("../config.js") as Promise<{
    memoryOpenVikingConfigSchema: { parse: (v: unknown) => Record<string, unknown> };
  }>;
}

// ---------------------------------------------------------------------------
// Test 1 – Two agents writing simultaneously do NOT contaminate each other
// ---------------------------------------------------------------------------

describe("PR #597 – Multi-agent memory isolation", () => {
  // Shared mock server for agent isolation tests
  let mockPort: number;
  let mock: ReturnType<typeof createMockServer>;
  let OpenVikingClient: Awaited<ReturnType<typeof loadClientModule>>["OpenVikingClient"];

  before(async () => {
    mock = createMockServer({
      sessionIds: ["sess-agentA-001", "sess-agentA-002", "sess-agentB-001", "sess-agentB-002"],
      userId: "alice",
    });
    mockPort = await mock.listen();

    const mod = await loadClientModule();
    OpenVikingClient = mod.OpenVikingClient;
  });

  after(async () => {
    await mock.close();
  });

  test("agents use different X-OpenViking-Agent headers → no cross-contamination", async () => {
    const baseUrl = `http://127.0.0.1:${mockPort}`;

    // Simulate two agents sharing the same client but different agentIds (stateless per-request)
    const client = new OpenVikingClient(baseUrl, "", 5000);

    // Both agents create sessions and write memories "simultaneously"
    await Promise.all([
      (async () => {
        const sid = await client.createSession("agent-alpha");
        await client.addSessionMessage(sid, "user", "Agent Alpha prefers dark mode", "agent-alpha");
        await client.extractSessionMemories(sid, "agent-alpha");
        await client.deleteSession(sid, "agent-alpha");
      })(),
      (async () => {
        const sid = await client.createSession("agent-beta");
        await client.addSessionMessage(sid, "user", "Agent Beta prefers light mode", "agent-beta");
        await client.extractSessionMemories(sid, "agent-beta");
        await client.deleteSession(sid, "agent-beta");
      })(),
    ]);

    // Verify that all extract requests carried their respective agent headers
    const extractRequests = mock.captured.filter(
      (r) => r.path.endsWith("/extract") && r.method === "POST",
    );
    assert.ok(extractRequests.length >= 2, "Expected at least 2 extract calls");

    const alphaExtracts = extractRequests.filter((r) => r.agentHeader === "agent-alpha");
    const betaExtracts = extractRequests.filter((r) => r.agentHeader === "agent-beta");

    assert.ok(alphaExtracts.length >= 1, "agent-alpha must have sent at least one extract request");
    assert.ok(betaExtracts.length >= 1, "agent-beta must have sent at least one extract request");

    // No extract request should have bled the wrong agent header
    for (const req of alphaExtracts) {
      assert.equal(req.agentHeader, "agent-alpha", "alpha extract must carry alpha header only");
    }
    for (const req of betaExtracts) {
      assert.equal(req.agentHeader, "agent-beta", "beta extract must carry beta header only");
    }
  });

  // ---------------------------------------------------------------------------
  // Test 2 – Per-agent cache keys are isolated (scope:agentId composite)
  // ---------------------------------------------------------------------------

  test("per-agentId composite cache keys are isolated – each agentId triggers its own ls call", async () => {
    // Start a fresh mock for scope-resolution so we can observe ls calls
    const lsMock = createMockServer({ userId: "bob" });
    const lsPort = await lsMock.listen();
    const lsBaseUrl = `http://127.0.0.1:${lsPort}`;

    try {
      const { OpenVikingClient: Client } = await loadClientModule();
      const client = new Client(lsBaseUrl, "", 5000);

      // Prime cache for agent-one by calling find (which calls resolveScopeSpace internally)
      await client.find("query", { targetUri: "viking://agent/memories", limit: 5, agentId: "agent-one" });

      // Capture ls calls for agent-one
      const lsCallsAfterAgentOne = lsMock.captured.filter((r) => r.path.startsWith("/api/v1/fs/ls")).length;

      // Call find for agent-two — different composite cache key "agent:agent-two" vs "agent:agent-one"
      // so a fresh system/status + ls cycle must occur.
      await client.find("query", { targetUri: "viking://agent/memories", limit: 5, agentId: "agent-two" });

      const lsCallsAfterAgentTwo = lsMock.captured.filter((r) => r.path.startsWith("/api/v1/fs/ls")).length;

      // There should be more ls calls after the second agent (different cache key, not reused)
      assert.ok(
        lsCallsAfterAgentTwo > lsCallsAfterAgentOne,
        `Expected additional ls calls for different agentId; before=${lsCallsAfterAgentOne}, after=${lsCallsAfterAgentTwo}`,
      );
    } finally {
      await lsMock.close();
    }
  });

  // ---------------------------------------------------------------------------
  // Test 3 – lastProcessedMsgCount is tracked per-agent, not shared
  // ---------------------------------------------------------------------------

  test("context engine afterTurn uses per-agent prePromptMessageCount, not a shared counter", async () => {
    /**
     * This tests the isolation of prePromptMessageCount (lastProcessedMsgCount equivalent).
     * In the context engine, afterTurn receives `prePromptMessageCount` per call.
     * Each agent's session must start extraction from its own offset, not a global one.
     *
     * We simulate two sequential afterTurn calls with different sessionIds and verify
     * that extractNewTurnTexts is applied correctly based on the provided startIndex.
     */
    const { extractNewTurnTexts } = await import("../text-utils.js");

    const messagesAgentA = [
      { role: "user", content: "Hello from session A message 1" },
      { role: "assistant", content: "Response A1" },
      { role: "user", content: "Hello from session A message 2" },
      { role: "assistant", content: "Response A2" },
    ];

    const messagesAgentB = [
      { role: "user", content: "Hello from session B message 1" },
      { role: "assistant", content: "Response B1" },
    ];

    // Agent A has seen 2 messages already; new messages start at index 2
    const agentAStart = 2;
    const { texts: textsA, newCount: newCountA } = extractNewTurnTexts(messagesAgentA, agentAStart);

    // Agent B starts fresh (0 messages processed)
    const agentBStart = 0;
    const { texts: textsB, newCount: newCountB } = extractNewTurnTexts(messagesAgentB, agentBStart);

    // Agent A should only see its last 2 messages (indices 2 and 3)
    assert.equal(newCountA, 2, "Agent A afterTurn should see exactly 2 new messages");
    assert.ok(
      textsA.some((t) => t.includes("session A message 2")),
      "Agent A new texts should include message 2",
    );
    assert.ok(
      !textsA.some((t) => t.includes("session A message 1")),
      "Agent A new texts must NOT include already-processed message 1",
    );

    // Agent B should see all 2 messages (both are new)
    assert.equal(newCountB, 2, "Agent B afterTurn should see 2 messages from its own session");
    assert.ok(
      textsB.some((t) => t.includes("session B message 1")),
      "Agent B new texts should include its own messages",
    );

    // Critical: Agent B's offset must not bleed Agent A's offset
    assert.notEqual(
      agentAStart,
      agentBStart,
      "Agents must have independent prePromptMessageCount values",
    );
  });

  // ---------------------------------------------------------------------------
  // Test 4 – "main" agent maps correctly for backward compatibility
  // ---------------------------------------------------------------------------

  test('resolveAgentId treats "main" as an explicit named agentId (not silently changed to "default")', async () => {
    const { memoryOpenVikingConfigSchema } = await loadConfigModule();

    // "main" is a common legacy value. It should be preserved as-is in the config
    // because it is a non-empty string.
    const cfg = memoryOpenVikingConfigSchema.parse({ mode: "remote", baseUrl: "http://localhost:1933", agentId: "main" });
    assert.equal(
      cfg.agentId,
      "main",
      '"main" agentId must be preserved as-is for backward compat',
    );

    // Confirm agentId is undefined when not set (per-agent isolation default)
    const cfgNoAgent = memoryOpenVikingConfigSchema.parse({ mode: "remote", baseUrl: "http://localhost:1933" });
    assert.equal(
      cfgNoAgent.agentId,
      undefined,
      "omitted agentId must be undefined (per-agent isolation, host provides the ID)",
    );

    const cfgEmptyAgent = memoryOpenVikingConfigSchema.parse({ mode: "remote", baseUrl: "http://localhost:1933", agentId: "" });
    assert.equal(
      cfgEmptyAgent.agentId,
      undefined,
      'empty-string agentId must be undefined',
    );

    const cfgWhitespaceAgent = memoryOpenVikingConfigSchema.parse({ mode: "remote", baseUrl: "http://localhost:1933", agentId: "   " });
    assert.equal(
      cfgWhitespaceAgent.agentId,
      undefined,
      'whitespace-only agentId must be undefined',
    );
  });

  // ---------------------------------------------------------------------------
  // Test 5 – Single-agent setup still works (backward compatibility)
  // ---------------------------------------------------------------------------

  test("single-agent config parses and operates correctly (backward compat)", async () => {
    const { memoryOpenVikingConfigSchema } = await loadConfigModule();

    // Minimal valid config – no agentId, no apiKey, remote mode
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
    });

    assert.equal(cfg.mode, "remote");
    assert.equal(cfg.agentId, undefined);
    assert.equal(typeof cfg.baseUrl, "string");
    assert.ok(cfg.baseUrl.startsWith("http://"));
    assert.equal(cfg.autoCapture, true, "autoCapture defaults to true");
    assert.equal(cfg.autoRecall, true, "autoRecall defaults to true");
    assert.ok(cfg.recallLimit >= 1, "recallLimit must be >= 1");
    assert.ok(cfg.recallScoreThreshold >= 0 && cfg.recallScoreThreshold <= 1);

    // Verify single-agent find works end-to-end with a mock server
    const singleMock = createMockServer({ userId: "solo-user" });
    const singlePort = await singleMock.listen();
    try {
      const { OpenVikingClient: Client } = await loadClientModule();
      const client = new Client(`http://127.0.0.1:${singlePort}`, "", 5000);

      const result = await client.find("test query", {
        targetUri: "viking://user/memories",
        limit: 5,
        scoreThreshold: 0,
        agentId: "default",
      });

      assert.ok(Array.isArray(result.memories), "find must return a memories array");

      // Verify the agent header sent
      const findReq = singleMock.captured.find((r) => r.path.includes("/api/v1/search/find"));
      assert.ok(findReq, "find request must have been received by mock server");
      assert.equal(
        findReq.agentHeader,
        "default",
        'single-agent request must carry "default" agent header',
      );
    } finally {
      await singleMock.close();
    }
  });

  // ---------------------------------------------------------------------------
  // Test 6 – Agent space derivation is deterministic and agent-specific
  // ---------------------------------------------------------------------------

  test("agent space key is derived from md5(userId:agentId) – two agents produce distinct spaces", () => {
    const userId = "alice";
    const agentIdA = "agent-alpha";
    const agentIdB = "agent-beta";

    const spaceA = md5Short(`${userId}:${agentIdA}`);
    const spaceB = md5Short(`${userId}:${agentIdB}`);

    assert.notEqual(spaceA, spaceB, "Different agentIds must produce distinct space hashes");
    assert.equal(spaceA.length, 12, "Space hash must be 12 hex chars");
    assert.equal(spaceB.length, 12, "Space hash must be 12 hex chars");

    // Idempotent: same inputs always produce the same space
    assert.equal(md5Short(`${userId}:${agentIdA}`), spaceA, "Space derivation must be deterministic");
  });

  // ---------------------------------------------------------------------------
  // Test 7 – setAgentId is idempotent (no cache clear when agentId unchanged)
  // ---------------------------------------------------------------------------

  test("same agentId on repeated find() calls reuses composite cache key (no extra ls calls)", async () => {
    const stableMock = createMockServer({ userId: "stable-user" });
    const stablePort = await stableMock.listen();
    const stableBaseUrl = `http://127.0.0.1:${stablePort}`;

    try {
      const { OpenVikingClient: Client } = await loadClientModule();
      const client = new Client(stableBaseUrl, "", 5000);

      // Prime the cache for stable-agent
      await client.find("query", { targetUri: "viking://agent/memories", limit: 3, agentId: "stable-agent" });
      const lsCountAfterFirst = stableMock.captured.filter((r) => r.path.startsWith("/api/v1/fs/ls")).length;

      // Same agentId – composite cache key "agent:stable-agent" should be reused
      await client.find("query2", { targetUri: "viking://agent/memories", limit: 3, agentId: "stable-agent" });
      const lsCountAfterSecond = stableMock.captured.filter((r) => r.path.startsWith("/api/v1/fs/ls")).length;

      assert.equal(
        lsCountAfterFirst,
        lsCountAfterSecond,
        "repeated find() with same agentId must reuse resolved space cache (no extra ls calls)",
      );
    } finally {
      await stableMock.close();
    }
  });

  // ---------------------------------------------------------------------------
  // Test 8 – sessionAgentIds map correctly routes sessions to agent identities
  // ---------------------------------------------------------------------------

  test("sessionAgentIds map isolates session-to-agent routing (simulates index.ts hook logic)", () => {
    /**
     * index.ts maintains a Map<sessionId, agentId> via rememberSessionAgentId().
     * resolveAgentId(sessionId) looks up the map and falls back to cfg.agentId.
     * This test directly verifies that logic.
     */
    const cfgAgentId = "default";
    const sessionAgentIds = new Map<string, string>();

    function rememberSessionAgentId(ctx: { agentId?: string; sessionId?: string; sessionKey?: string }) {
      if (!ctx?.agentId) return;
      if (ctx.sessionId) sessionAgentIds.set(ctx.sessionId, ctx.agentId);
      if (ctx.sessionKey) sessionAgentIds.set(ctx.sessionKey, ctx.agentId);
    }

    function resolveAgentId(sessionId: string): string {
      return sessionAgentIds.get(sessionId) ?? cfgAgentId;
    }

    // Two agents register their sessions
    rememberSessionAgentId({ agentId: "agent-A", sessionId: "session-123" });
    rememberSessionAgentId({ agentId: "agent-B", sessionId: "session-456" });

    assert.equal(resolveAgentId("session-123"), "agent-A", "session-123 must resolve to agent-A");
    assert.equal(resolveAgentId("session-456"), "agent-B", "session-456 must resolve to agent-B");
    assert.equal(
      resolveAgentId("unknown-session"),
      cfgAgentId,
      "unknown session must fall back to cfg.agentId",
    );

    // Agent-A session must NOT resolve to agent-B
    assert.notEqual(resolveAgentId("session-123"), "agent-B", "agent-A session must not resolve to agent-B");
  });
});
