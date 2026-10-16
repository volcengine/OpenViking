import { createServer, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

// Real loopback sockets: the failures below happen after the status line and
// headers have already arrived, which a mocked Response cannot reproduce.
type Mode = "stall" | "reset" | "eof" | "invalid";

function startBody(res: ServerResponse): void {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.flushHeaders();
  res.write('{"status":"ok","result":');
}

const modes: Record<Mode, (res: ServerResponse) => void> = {
  // Headers and half a body, then silence until the client times out.
  stall: (res) => startBody(res),
  // Headers and half a body, then a TCP reset.
  reset: (res) => {
    startBody(res);
    setTimeout(() => res.socket?.resetAndDestroy(), 20);
  },
  // Headers and half a chunked body, then a clean close without the last chunk.
  eof: (res) => {
    startBody(res);
    setTimeout(() => res.socket?.end(), 20);
  },
  // A complete response whose body is not JSON: unchanged behaviour.
  invalid: (res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("not json");
  },
};

let mode: Mode = "invalid";
let baseUrl = "";
const server = createServer((req, res) => {
  req.resume();
  modes[mode](res);
});

beforeAll(async () => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

function makeClient(): OpenVikingClient {
  return new OpenVikingClient(baseUrl, "", "agent", 300);
}

describe("response body interrupted after HTTP 200 headers", () => {
  it.each(["stall", "reset", "eof"] as const)("rejects a session write (%s)", async (m) => {
    mode = m;
    await expect(
      makeClient().addSessionMessage("s1", "user", [{ type: "text", text: "hello" }]),
    ).rejects.toThrow();
  });

  it.each(["stall", "reset", "eof"] as const)("rejects a search instead of returning nothing (%s)", async (m) => {
    mode = m;
    await expect(makeClient().find("editor", { limit: 5 })).rejects.toThrow();
  });

  it("still treats a complete non-JSON 200 body as an empty result", async () => {
    mode = "invalid";
    await expect(
      makeClient().addSessionMessage("s1", "user", [{ type: "text", text: "hello" }]),
    ).resolves.toBeUndefined();
  });

  it("withholds the durable turn ACK so the host keeps the turn", async () => {
    mode = "reset";
    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Context Engine (OpenViking)",
      version: "test",
      hostVersion: "2026.9.3",
      cfg: memoryOpenVikingConfigSchema.parse({ mode: "remote", baseUrl, autoCapture: true }),
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      getClient: async () => makeClient(),
      resolveAgentId: () => "agent",
    });
    const turn = {
      advancementKey: "body-reset",
      sessionId: "session-1",
      messages: [
        { role: "user", content: "Remember that my preferred editor is Vim." },
        { role: "assistant", content: "Noted." },
      ],
    };
    await expect(engine.commitTurn(turn)).rejects.toThrow();
  });
});
