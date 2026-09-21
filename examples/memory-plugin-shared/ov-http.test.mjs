/**
 * The wire shape lives in one module now, so it is pinned in one place too.
 * `wire-headers.test.mjs` proves each harness still sends what this builds;
 * this proves what it builds.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { buildOvHeaders, createOvHttp } from "./lib/ov-http.mjs";

const CFG = {
  baseUrl: "http://127.0.0.1:1933",
  apiKey: "secret",
  account: "acct-a",
  user: "user-a",
  sendIdentityHeaders: true,
  userAgent: "openviking-memory-test/9.9.9",
};

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function respond(status, body, { json = true } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(json ? JSON.stringify(body) : body, {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return calls;
}

test("a successful response unwraps result and carries the trace id", async () => {
  const calls = respond(200, { status: "ok", result: { session_id: "s-1", trace_id: "trace-ok" } });
  const fetchJSON = createOvHttp(CFG, { defaultTimeoutMs: 5000 });

  const res = await fetchJSON("/api/v1/sessions/s-1");

  assert.equal(calls[0].url, "http://127.0.0.1:1933/api/v1/sessions/s-1");
  assert.equal(res.ok, true);
  assert.equal(res.status, 200);
  assert.deepEqual(res.result, { session_id: "s-1", trace_id: "trace-ok" });
  assert.equal(res.traceId, "trace-ok");
});

test("a body without a result envelope is returned whole", async () => {
  respond(200, { status: "healthy", version: "1.2.3" });
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/health");

  assert.equal(res.ok, true);
  assert.deepEqual(res.result, { status: "healthy", version: "1.2.3" });
});

test("a non-2xx response keeps the server's error and trace id", async () => {
  respond(503, { status: "error", error: { code: "FAILED", message: "nope", trace_id: "trace-err" } });
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/api/v1/sessions/s-1/commit");

  assert.equal(res.ok, false);
  assert.equal(res.status, 503);
  assert.equal(res.result, null);
  assert.equal(res.error.code, "FAILED");
  assert.equal(res.traceId, "trace-err");
});

test("an error envelope inside a 200 is still a failure", async () => {
  respond(200, { status: "error", error: { message: "nope" } });
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/api/v1/sessions/s-1/commit");

  assert.equal(res.ok, false);
  assert.equal(res.status, 200);
  assert.equal(res.error.message, "nope");
});

test("a non-2xx with no error field is described by its status", async () => {
  respond(404, { detail: "Not Found" });
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/api/v1/sessions/missing");

  assert.equal(res.ok, false);
  assert.equal(res.error.message, "HTTP 404");
});

test("a network error is an envelope, not a throw", async () => {
  globalThis.fetch = async () => {
    throw new Error("connect ECONNREFUSED 127.0.0.1:1933");
  };
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/health");

  // Status 0 is what retryable.mjs reads as "the server never answered".
  assert.deepEqual(res, {
    ok: false,
    status: 0,
    result: null,
    error: { message: "connect ECONNREFUSED 127.0.0.1:1933" },
  });
});

test("an unparseable body is an empty result, or a failure when the caller requires JSON", async () => {
  respond(200, "not json at all", { json: false });
  const lenient = await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/health");
  assert.equal(lenient.ok, true);
  assert.deepEqual(lenient.result, {});

  respond(200, "not json at all", { json: false });
  const strict = await createOvHttp(CFG, { defaultTimeoutMs: 5000, requireJsonBody: true })("/health");
  assert.equal(strict.ok, false);
  assert.equal(strict.status, 200);
  assert.equal(strict.error.message, "empty or invalid JSON response");
});

test("the request aborts on the configured timeout", async () => {
  let seenSignal = null;
  globalThis.fetch = (_url, init) => new Promise((_resolve, reject) => {
    seenSignal = init.signal;
    init.signal.addEventListener("abort", () => reject(new Error("The operation was aborted.")));
  });

  const started = Date.now();
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 1000 })("/health");

  assert.equal(seenSignal.aborted, true);
  assert.ok(Date.now() - started < 5000, "the default timeout did not fire");
  assert.equal(res.ok, false);
  assert.equal(res.status, 0);
});

test("a per-call timeout overrides the default", async () => {
  globalThis.fetch = (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("The operation was aborted.")));
  });

  const started = Date.now();
  // The default would outlast this test; the per-call budget is what fires.
  const res = await createOvHttp(CFG, { defaultTimeoutMs: 600000 })("/health", {}, { timeoutMs: 1000 });

  assert.equal(res.ok, false);
  assert.ok(Date.now() - started < 5000, "the per-call timeout did not fire");
});

test("a budget below the floor is raised to it", async () => {
  let aborted = false;
  globalThis.fetch = (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => {
      aborted = true;
      reject(new Error("The operation was aborted."));
    });
  });

  const started = Date.now();
  const pending = createOvHttp(CFG, { defaultTimeoutMs: 10 })("/health");
  await new Promise((resolve) => setTimeout(resolve, 100));
  // A request that gives up in milliseconds reads a busy server as a dead one.
  assert.equal(aborted, false, "the request gave up before the floor");

  const res = await pending;
  const elapsed = Date.now() - started;

  assert.equal(res.ok, false);
  assert.ok(elapsed >= 900, `aborted after ${elapsed}ms, well short of the floor`);
  assert.ok(elapsed < 5000, "the floor did not fire");
});

test("a timed-out request says so in the envelope, not in its message", async () => {
  globalThis.fetch = (_url, init) => new Promise((_resolve, reject) => {
    // Whatever fetch throws on abort is the runtime's business, not a contract.
    init.signal.addEventListener("abort", () => reject(new Error("terminated")));
  });

  const res = await createOvHttp(CFG, { defaultTimeoutMs: 1000 })("/health");

  assert.equal(res.status, 0);
  assert.equal(res.error.name, "AbortError");
  assert.equal(res.error.aborted, true);
});

test("a trusted server is told who the operator is; an untrusted one only gets the key", async () => {
  const trusted = respond(200, { status: "ok", result: {} });
  await createOvHttp(CFG, { defaultTimeoutMs: 5000, resolveActorPeerId: () => "peer-a" })("/health");
  assert.deepEqual(trusted[0].init.headers, {
    "Content-Type": "application/json",
    "Authorization": "Bearer secret",
    "X-OpenViking-Account": "acct-a",
    "X-OpenViking-User": "user-a",
    "X-OpenViking-Actor-Peer": "peer-a",
    "User-Agent": "openviking-memory-test/9.9.9",
  });

  const apiKeyOnly = respond(200, { status: "ok", result: {} });
  await createOvHttp({ ...CFG, sendIdentityHeaders: false }, {
    defaultTimeoutMs: 5000,
    resolveActorPeerId: () => "peer-a",
  })("/health");
  assert.deepEqual(apiKeyOnly[0].init.headers, {
    "Content-Type": "application/json",
    "Authorization": "Bearer secret",
    "X-OpenViking-Actor-Peer": "peer-a",
    "User-Agent": "openviking-memory-test/9.9.9",
  });
});

test("the api key rides on Authorization alone", () => {
  const headers = buildOvHeaders(CFG, { actorPeerId: "peer-a" });

  assert.equal(headers["Authorization"], "Bearer secret");
  assert.equal(headers["X-API-Key"], undefined);
});

test("identityHeaders overrides what the config says about trust", () => {
  assert.equal(buildOvHeaders(CFG, { identityHeaders: false })["X-OpenViking-Account"], undefined);
  assert.equal(
    buildOvHeaders({ ...CFG, sendIdentityHeaders: false }, { identityHeaders: true })["X-OpenViking-Account"],
    "acct-a",
  );
});

test("extraHeaders are added to every request", async () => {
  const calls = respond(200, { status: "ok", result: {} });
  await createOvHttp(CFG, { defaultTimeoutMs: 5000, extraHeaders: { Accept: "text/event-stream" } })("/mcp");

  assert.equal(calls[0].init.headers.Accept, "text/event-stream");
});

test("the actor peer header is absent until something names a peer", async () => {
  const anonymous = respond(200, { status: "ok", result: {} });
  await createOvHttp(CFG, { defaultTimeoutMs: 5000 })("/health");
  assert.equal("X-OpenViking-Actor-Peer" in anonymous[0].init.headers, false);

  const empty = respond(200, { status: "ok", result: {} });
  await createOvHttp(CFG, { defaultTimeoutMs: 5000, resolveActorPeerId: () => "" })("/health");
  assert.equal("X-OpenViking-Actor-Peer" in empty[0].init.headers, false);
});

test("a per-call peer beats the resolver, which is read per request", async () => {
  let peer = "first-peer";
  const calls = respond(200, { status: "ok", result: {} });
  const fetchJSON = createOvHttp(CFG, { defaultTimeoutMs: 5000, resolveActorPeerId: () => peer });

  await fetchJSON("/health");
  await fetchJSON("/health", {}, { actorPeerId: "session-peer" });
  // Codex only learns its peer after loading state under the session lock.
  peer = "second-peer";
  await fetchJSON("/health");

  assert.deepEqual(calls.map((call) => call.init.headers["X-OpenViking-Actor-Peer"]), [
    "first-peer",
    "session-peer",
    "second-peer",
  ]);
});

test("dsh and pi name the base url `endpoint`", async () => {
  const calls = respond(200, { status: "ok", result: {} });
  await createOvHttp({ endpoint: "http://127.0.0.1:1934" }, { defaultTimeoutMs: 5000 })("/health");

  assert.equal(calls[0].url, "http://127.0.0.1:1934/health");
});
