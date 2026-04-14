import { createServer } from "node:net";
import { afterEach, describe, expect, it, vi } from "vitest";

import { quickRecallPrecheck } from "../../process-manager.js";

function listen(server: ReturnType<typeof createServer>): Promise<number> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (typeof address === "object" && address !== null) {
        resolve(address.port);
        return;
      }
      reject(new Error("server did not bind to a TCP port"));
    });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("quickRecallPrecheck", () => {
  it("rejects a local server when TCP accepts connections but health fails", async () => {
    const server = createServer((socket) => {
      socket.end();
    });
    const port = await listen(server);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ status: "down" }), { status: 503 })),
    );

    try {
      const result = await quickRecallPrecheck(
        "local",
        `http://127.0.0.1:${port}`,
        port,
        null,
      );

      expect(result).toEqual({
        ok: false,
        reason: `local health check failed (127.0.0.1:${port})`,
      });
    } finally {
      server.close();
    }
  });
});
