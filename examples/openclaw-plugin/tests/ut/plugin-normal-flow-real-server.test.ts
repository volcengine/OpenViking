import { once } from "node:events";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { localClientCache, localClientPendingPromises } from "../../client.js";
import plugin from "../../index.js";

type RequestRecord = {
  body?: string;
  method: string;
  path: string;
};

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

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function json(res: ServerResponse, statusCode: number, payload: unknown): void {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(payload));
}

describe("plugin normal flow with healthy backend", () => {
  let server: ReturnType<typeof createServer>;
  let baseUrl = "";
  let requests: RequestRecord[] = [];

  beforeEach(async () => {
    requests = [];
    localClientCache.clear();
    localClientPendingPromises.clear();

    server = createServer(async (req, res) => {
      const method = req.method ?? "GET";
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      const body = method === "POST" ? await readBody(req) : undefined;
      requests.push({ body, method, path: `${url.pathname}${url.search}` });

      if (method === "GET" && url.pathname === "/health") {
        json(res, 200, { status: "ok" });
        return;
      }

      if (method === "GET" && url.pathname === "/api/v1/system/status") {
        json(res, 200, { result: { user: "default" } });
        return;
      }

      if (method === "POST" && url.pathname === "/api/v1/search/find") {
        json(res, 200, {
          result: {
            memories: [
              {
                uri: "viking://user/default/memories/rust-pref",
                level: 2,
                abstract: "User prefers Rust for backend tasks.",
                score: 0.91,
              },
            ],
            total: 1,
          },
          status: "ok",
        });
        return;
      }

      if (method === "GET" && url.pathname === "/api/v1/content/read") {
        json(res, 200, {
          result: "User prefers Rust for backend tasks.",
          status: "ok",
        });
        return;
      }

      if (
        method === "GET" &&
        /^\/api\/v1\/sessions\/[^/]+\/context$/.test(url.pathname)
      ) {
        json(res, 200, {
          result: {
            latest_archive_overview: "Earlier work focused on backend stack choices.",
            pre_archive_abstracts: [],
            messages: [
              {
                id: "msg_1",
                role: "assistant",
                created_at: "2026-04-01T00:00:00Z",
                parts: [{ type: "text", text: "Stored answer from OpenViking." }],
              },
            ],
            estimatedTokens: 64,
            stats: {
              ...makeStats(),
              activeTokens: 64,
            },
          },
          status: "ok",
        });
        return;
      }

      if (
        method === "POST" &&
        /^\/api\/v1\/sessions\/[^/]+\/messages$/.test(url.pathname)
      ) {
        json(res, 200, {
          result: { session_id: url.pathname.split("/")[4] },
          status: "ok",
        });
        return;
      }

      if (
        method === "GET" &&
        /^\/api\/v1\/sessions\/[^/]+$/.test(url.pathname)
      ) {
        json(res, 200, {
          result: { pending_tokens: 25001 },
          status: "ok",
        });
        return;
      }

      if (
        method === "POST" &&
        /^\/api\/v1\/sessions\/[^/]+\/commit$/.test(url.pathname)
      ) {
        json(res, 200, {
          result: {
            session_id: url.pathname.split("/")[4],
            status: "accepted",
            task_id: "task-1",
            archived: false,
          },
          status: "ok",
        });
        return;
      }

      json(res, 404, {
        error: { message: `Unhandled ${method} ${url.pathname}` },
        status: "error",
      });
    });

    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("failed to bind mock server");
    }
    baseUrl = `http://127.0.0.1:${address.port}`;
  });

  afterEach(async () => {
    localClientCache.clear();
    localClientPendingPromises.clear();
    server.close();
    await once(server, "close");
  });

  it("keeps normal prompt-build and context-engine flow working", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: unknown) => unknown>();
    let service:
      | {
          start: () => Promise<void>;
          stop?: () => Promise<void> | void;
        }
      | null = null;
    let contextEngineFactory: (() => unknown) | null = null;

    plugin.register({
      logger: {
        debug: () => {},
        error: () => {},
        info: () => {},
        warn: () => {},
      },
      on: (name, handler) => {
        handlers.set(name, handler);
      },
      pluginConfig: {
        autoCapture: true,
        autoRecall: true,
        baseUrl,
        commitTokenThreshold: 20000,
        mode: "remote",
      },
      registerContextEngine: (_id, factory) => {
        contextEngineFactory = factory as () => unknown;
      },
      registerService: (entry) => {
        service = entry;
      },
      registerTool: () => {},
    });

    expect(service).toBeTruthy();
    expect(contextEngineFactory).toBeTruthy();

    await service!.start();

    const beforePromptBuild = handlers.get("before_prompt_build");
    expect(beforePromptBuild).toBeTruthy();
    const hookResult = await beforePromptBuild!(
      { messages: [{ role: "user", content: "what backend language should we use?" }] },
      { agentId: "main", sessionId: "session-normal", sessionKey: "agent:main:normal" },
    );

    expect(hookResult).toMatchObject({
      prependContext: expect.stringContaining("User prefers Rust for backend tasks."),
    });

    const contextEngine = contextEngineFactory!() as {
      assemble: (params: {
        sessionId: string;
        messages: Array<{ role: string; content: string }>;
      }) => Promise<{ messages: Array<{ role: string; content: unknown }> }>;
      afterTurn: (params: {
        sessionId: string;
        sessionFile: string;
        messages: Array<{ role: string; content: unknown; timestamp?: number }>;
        prePromptMessageCount: number;
      }) => Promise<void>;
    };

    const assembled = await contextEngine.assemble({
      sessionId: "session-normal",
      messages: [{ role: "user", content: "fallback" }],
    });

    expect(assembled.messages[0]).toEqual({
      role: "user",
      content: "[Session History Summary]\nEarlier work focused on backend stack choices.",
    });
    expect(assembled.messages[1]).toEqual({
      role: "assistant",
      content: [{ type: "text", text: "Stored answer from OpenViking." }],
    });

    await contextEngine.afterTurn({
      sessionId: "session-normal",
      sessionFile: "",
      messages: [
        { role: "user", content: "Please keep using Rust.", timestamp: Date.parse("2026-04-07T08:00:00Z") },
        { role: "assistant", content: [{ type: "text", text: "Understood." }], timestamp: Date.parse("2026-04-07T08:00:01Z") },
      ],
      prePromptMessageCount: 0,
    });

    expect(requests.some((entry) => entry.method === "GET" && entry.path === "/health")).toBe(true);
    expect(
      requests.some((entry) => entry.method === "POST" && entry.path === "/api/v1/search/find"),
    ).toBe(true);
    expect(
      requests.some((entry) => entry.method === "GET" && entry.path.startsWith("/api/v1/sessions/session-normal/context")),
    ).toBe(true);
    expect(
      requests.some((entry) => entry.method === "POST" && entry.path === "/api/v1/sessions/session-normal/messages"),
    ).toBe(true);
    const addMessageRequest = requests.find(
      (entry) => entry.method === "POST" && entry.path === "/api/v1/sessions/session-normal/messages",
    );
    expect(addMessageRequest).toBeTruthy();
    expect(JSON.parse(addMessageRequest!.body ?? "{}")).toMatchObject({
      role: "user",
      created_at: "2026-04-07T08:00:01.000Z",
    });
    expect(
      requests.some((entry) => entry.method === "POST" && entry.path === "/api/v1/sessions/session-normal/commit"),
    ).toBe(true);

    await service?.stop?.();
  });
});
