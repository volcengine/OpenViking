import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { OpenVikingClient } from "./client.mjs";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("client sends OpenViking identity headers and preserves response trace ids", async () => {
  let seen;
  globalThis.fetch = async (url, init) => {
    seen = { url, init };
    return new Response(JSON.stringify({
      status: "ok",
      result: { trace_id: "trace-123", archive_uri: "viking://session/x" },
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const client = new OpenVikingClient({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "secret",
    account: "account-a",
    user: "user-a",
    peerId: "peer-a",
    userAgent: "openviking-memory-dsh/0.1.0",
    requestTimeoutMs: 1000,
    commitKeepRecentCount: 10,
  });
  const response = await client.commitSession("dsh-1");

  assert.equal(response.ok, true);
  assert.equal(response.traceId, "trace-123");
  assert.equal(seen.url, "http://127.0.0.1:1933/api/v1/sessions/dsh-1/commit");
  assert.equal(seen.init.headers.Authorization, "Bearer secret");
  assert.equal(seen.init.headers["X-OpenViking-Account"], "account-a");
  assert.equal(seen.init.headers["X-OpenViking-User"], "user-a");
  assert.equal(seen.init.headers["X-OpenViking-Actor-Peer"], "peer-a");
});

test("per-session actor peer overrides the process default", async () => {
  let headers;
  globalThis.fetch = async (_url, init) => {
    headers = init.headers;
    return new Response(JSON.stringify({ status: "ok", result: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = new OpenVikingClient({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "",
    account: "",
    user: "",
    peerId: "process-peer",
    userAgent: "",
    requestTimeoutMs: 1000,
    commitKeepRecentCount: 10,
  });

  await client.ensureSession("dsh-2", "workspace-peer");
  assert.equal(headers["X-OpenViking-Actor-Peer"], "workspace-peer");
});

test("existing OpenViking sessions are reusable on DSH resume", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    status: "error",
    error: { code: "ALREADY_EXISTS", message: "session exists" },
  }), {
    status: 409,
    headers: { "Content-Type": "application/json" },
  });
  const client = new OpenVikingClient({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "",
    account: "",
    user: "",
    peerId: "",
    userAgent: "",
    requestTimeoutMs: 1000,
    commitKeepRecentCount: 10,
  });

  assert.equal(await client.ensureSession("dsh-resume"), true);
});

test("directory listing requests the raw array contract", async () => {
  let seenUrl;
  globalThis.fetch = async (url) => {
    seenUrl = url;
    return new Response(JSON.stringify({
      status: "ok",
      result: [{ name: "notes.md", isDir: false }],
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = new OpenVikingClient({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "",
    account: "",
    user: "",
    peerId: "",
    userAgent: "",
    requestTimeoutMs: 1000,
    commitKeepRecentCount: 10,
  });

  assert.deepEqual(await client.list("viking://resources"), [
    { name: "notes.md", isDir: false },
  ]);
  assert.equal(
    seenUrl,
    "http://127.0.0.1:1933/api/v1/fs/ls?uri=viking%3A%2F%2Fresources&output=original",
  );
});

test("archive lookup uses the dedicated session archive endpoint", async () => {
  let seenUrl;
  globalThis.fetch = async (url) => {
    seenUrl = url;
    return new Response(JSON.stringify({
      status: "ok",
      result: { archive_id: "archive_001", messages: [] },
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = new OpenVikingClient({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "",
    account: "",
    user: "",
    peerId: "",
    userAgent: "",
    requestTimeoutMs: 1000,
    commitKeepRecentCount: 10,
  });

  assert.deepEqual(
    await client.getSessionArchive("dsh session", "archive/001"),
    { archive_id: "archive_001", messages: [] },
  );
  assert.equal(
    seenUrl,
    "http://127.0.0.1:1933/api/v1/sessions/dsh%20session/archives/archive%2F001",
  );
});

test("client normalizes non-2xx OpenViking envelopes", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    status: "error",
    error: { code: "FAILED", message: "nope", trace_id: "trace-error" },
  }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });

  const client = new OpenVikingClient({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "",
    account: "",
    user: "",
    peerId: "",
    userAgent: "",
    requestTimeoutMs: 1000,
    commitKeepRecentCount: 10,
  });
  const response = await client.fetchJSON("/probe");

  assert.equal(response.ok, false);
  assert.equal(response.status, 503);
  assert.equal(response.error.code, "FAILED");
  assert.equal(response.traceId, "trace-error");
});
