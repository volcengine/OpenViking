import assert from "node:assert/strict";
import test from "node:test";
import { createOpenVikingMcpProxy } from "./lib/mcp-proxy-core.mjs";

function buildHarness({ fetchImpl, timeoutMs = 40 }) {
  const lines = [];
  const stdout = {
    write(line, cb) {
      lines.push(line);
      if (cb) cb();
      return true;
    },
  };
  const proxy = createOpenVikingMcpProxy({
    stdin: process.stdin,
    stdout,
    fetchImpl,
    readConfig: () => ({
      mcpUrl: "http://127.0.0.1:1933/mcp",
      timeoutMs,
      watchedPaths: [],
    }),
    loggerFactory: () => ({ log() {}, logError() {} }),
  });
  const responses = () => lines.map((l) => JSON.parse(l));
  const send = (obj) => proxy.handleMessage(obj);
  const waitFor = async (predicate, label) => {
    for (let i = 0; i < 100; i++) {
      const found = responses().find(predicate);
      if (found) return found;
      await new Promise((r) => setTimeout(r, 20));
    }
    throw new Error(`timed out waiting for ${label}; got: ${lines.join("|")}`);
  };
  return { proxy, send, waitFor, responses };
}

test("an aborted request maps to -32004 naming the timeout budget", async () => {
  const harness = buildHarness({
    // Stalls until aborted, like a real signal-aware fetch against a slow server.
    fetchImpl: (_url, { signal } = {}) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => {
          const err = new Error("This operation was aborted");
          err.name = "AbortError";
          reject(err);
        });
      }),
    timeoutMs: 40,
  });
  harness.send({ jsonrpc: "2.0", id: 7, method: "initialize", params: {} });
  const res = await harness.waitFor(
    (m) => m.id === 7 && m.error,
    "timeout error response",
  );
  assert.equal(res.error.code, -32004);
  assert.match(res.error.message, /timed out after 40ms/);
  assert.match(res.error.message, /OPENVIKING_TIMEOUT_MS/);
  assert.equal(res.error.data.timeoutMs, 40);
  assert.equal(res.error.data.mcpUrl, "http://127.0.0.1:1933/mcp");
});

test("a genuine connection failure still maps to -32001", async () => {
  const harness = buildHarness({
    fetchImpl: () => {
      throw new TypeError("fetch failed");
    },
  });
  harness.send({ jsonrpc: "2.0", id: 8, method: "initialize", params: {} });
  const res = await harness.waitFor(
    (m) => m.id === 8 && m.error,
    "connection error response",
  );
  assert.equal(res.error.code, -32001);
  assert.match(res.error.message, /reachable/);
});
