import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { enqueue, listPending } from "./shared/pending-queue.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";

const originalPendingDir = process.env.OPENVIKING_PENDING_DIR;
const originalDrainInterval = process.env.OPENVIKING_PENDING_DRAIN_INTERVAL_MS;
const tempDirs = [];

afterEach(async () => {
  if (originalPendingDir === undefined) delete process.env.OPENVIKING_PENDING_DIR;
  else process.env.OPENVIKING_PENDING_DIR = originalPendingDir;
  if (originalDrainInterval === undefined) delete process.env.OPENVIKING_PENDING_DRAIN_INTERVAL_MS;
  else process.env.OPENVIKING_PENDING_DRAIN_INTERVAL_MS = originalDrainInterval;
  await Promise.all(tempDirs.splice(0).map(dir => rm(dir, { recursive: true, force: true })));
});

test("drainTick sends queued messages once the server recovers and clears the latch", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;

  let healthy = false;
  let addCalls = 0;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      addCalls += 1;
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async fetchJSON() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async healthResult() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
  }, config(), { debug() {} });
  const session = { id: "recover", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;
  runtime.stateFor(session).sessionReady = true;

  runtime.capture(session, userEvent("Queued during the outage."));
  await runtime.flush(session);
  assert.equal((await listPending()).length, 1);
  assert.equal(runtime.stateFor(session).hasPendingWrites, true);

  healthy = true;
  await runtime.drainTick();

  assert.equal((await listPending()).length, 0);
  assert.equal(runtime.stateFor(session).hasPendingWrites, false);

  runtime.capture(session, userEvent("Sent directly after recovery."));
  await runtime.flush(session);
  assert.equal(addCalls, 2);
  assert.equal((await listPending()).length, 0);
});

test("drainTick creates a session before replaying a capture queued by failed creation", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-create-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;

  const calls = [];
  let created = false;
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async ensureSessionResult() {
      calls.push("create");
      if (calls.filter(call => call === "create").length === 1) {
        return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
      }
      created = true;
      return { ok: true };
    },
    async fetchJSON(path) {
      if (path.endsWith("/messages")) {
        calls.push("replay");
        return created
          ? { ok: true }
          : { ok: false, status: 404, error: { code: "NOT_FOUND" } };
      }
      return { ok: true, result: [] };
    },
  }, config(), { debug() {} });
  const session = { id: "create-retry", header: { cwd: "/workspace" } };

  runtime.capture(session, userEvent("Keep this message."));
  await runtime.flush(session);
  assert.deepEqual(calls, ["create"]);
  assert.equal((await listPending()).length, 1);

  await runtime.drainTick();

  assert.deepEqual(calls, ["create", "create", "replay"]);
  assert.deepEqual(await listPending(), []);
});

test("one uncreatable session does not block another session's pending replay", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-isolated-create-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  const now = Date.now();
  await enqueue("addMessage", "dsh-bad", { content: "retry later" }, { createdAt: now });
  await enqueue("addMessage", "dsh-good", { content: "send now" }, { createdAt: now + 1 });

  const calls = [];
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async ensureSessionResult(sessionId) {
      calls.push(`create:${sessionId}`);
      return sessionId === "dsh-bad"
        ? { ok: false, status: 503, error: { code: "UNAVAILABLE" } }
        : { ok: true };
    },
    async fetchJSON(path) {
      calls.push(`replay:${path}`);
      return { ok: true };
    },
  }, config(), { debug() {} });
  const bad = { id: "bad", header: { cwd: "/workspace" } };
  const good = { id: "good", header: { cwd: "/workspace" } };
  runtime.stateFor(bad).ready = true;
  runtime.stateFor(good).ready = true;

  await runtime.drainTick();

  assert.deepEqual(calls, [
    "create:dsh-bad",
    "create:dsh-good",
    "replay:/api/v1/sessions/dsh-good/messages",
  ]);
  assert.deepEqual((await listPending()).map(item => item.entry.sessionId), ["dsh-bad"]);
  assert.equal(runtime.stateFor(bad).hasPendingWrites, true);
  assert.equal(runtime.stateFor(good).hasPendingWrites, false);
});

test("drainTick keeps the latch while the server stays down and leaves the entry retryable", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-down-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;

  let addCalls = 0;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      addCalls += 1;
      return { ok: false, status: 503, error: { message: "down" } };
    },
    async fetchJSON() {
      return { ok: false, status: 503, error: { message: "down" } };
    },
    async healthResult() {
      return { ok: false, status: 503, error: { message: "down" } };
    },
  }, config(), { debug() {} });
  const session = { id: "still-down", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;
  runtime.stateFor(session).sessionReady = true;

  runtime.capture(session, userEvent("First queued message."));
  await runtime.flush(session);
  await runtime.drainTick();

  assert.equal(runtime.stateFor(session).hasPendingWrites, true);
  const pending = await listPending();
  assert.equal(pending.length, 1);
  assert.equal(pending[0].entry.retries, 0);

  runtime.capture(session, userEvent("Second queued message."));
  await runtime.flush(session);
  assert.equal(addCalls, 1);
  assert.equal((await listPending()).length, 2);
});

test("drainTick does not hit the network while the queue is empty", async () => {
  let fetchCalls = 0;
  const runtime = new OpenVikingRuntime({
    async fetchJSON() {
      fetchCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  runtime.stateFor({ id: "idle", header: { cwd: "/workspace" } });

  await runtime.drainTick();

  assert.equal(fetchCalls, 0);
});

test("commit resumes once the drain clears the backlog", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-commit-resume-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;

  let healthy = false;
  let commitCalls = 0;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async fetchJSON() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async healthResult() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async getSession() {
      return { pending_tokens: 20000 };
    },
    async commitSession() {
      commitCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = { id: "commit-resume", header: { cwd: "/workspace" } };
  const state = runtime.stateFor(session);
  state.ready = true;
  state.sessionReady = true;

  runtime.capture(session, userEvent("Queued during the outage."));
  await runtime.flush(session);
  assert.equal(state.hasPendingWrites, true);

  // While the backlog holds, the commit check keeps its ordering guard.
  runtime.maybeCommit(session, { type: "turn/end" });
  await runtime.flush(session);
  assert.equal(commitCalls, 0);

  // The drainer clears the backlog, and the next turn/end commits again.
  healthy = true;
  await runtime.drainTick();
  assert.equal(state.hasPendingWrites, false);

  runtime.maybeCommit(session, { type: "turn/end" });
  await runtime.flush(session);
  assert.equal(commitCalls, 1);
});

test("startDrainer drains the queue on its interval", async t => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-interval-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  process.env.OPENVIKING_PENDING_DRAIN_INTERVAL_MS = "60000";

  let healthy = false;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async fetchJSON() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
    async healthResult() {
      return healthy
        ? { ok: true }
        : { ok: false, status: 503, error: { message: "down" } };
    },
  }, config(), { debug() {} });
  const session = { id: "interval", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;
  runtime.stateFor(session).sessionReady = true;

  runtime.capture(session, userEvent("Queued."));
  await runtime.flush(session);
  assert.equal((await listPending()).length, 1);

  runtime.startDrainer();
  healthy = true;
  t.mock.timers.tick(60000);
  await runtime.drainPromise;

  assert.equal((await listPending()).length, 0);
  assert.equal(runtime.stateFor(session).hasPendingWrites, false);

  runtime.stopDrainer();
});

test("drainTick runs single-flight", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-single-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  await enqueue("addMessage", "dsh-single", { content: "held" });

  let release;
  let startedResolve;
  const started = new Promise(resolve => {
    startedResolve = resolve;
  });
  let fetchCalls = 0;
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async fetchJSON() {
      fetchCalls += 1;
      startedResolve();
      await new Promise(resolve => {
        release = resolve;
      });
      return { ok: true };
    },
  }, config(), { debug() {} });
  const state = runtime.stateFor({ id: "single", header: { cwd: "/workspace" } });
  state.ready = true;
  state.sessionReady = true;

  const first = runtime.drainTick();
  await started;
  await runtime.drainTick();
  release();
  await first;

  assert.equal(fetchCalls, 1);
});

test("drainTick derives each session's latch from its own entries", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-isolate-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;

  const runtime = new OpenVikingRuntime({
    async addMessage() {
      return { ok: false, status: 503, error: { message: "down" } };
    },
    async healthResult() {
      return { ok: true };
    },
    async fetchJSON() {
      return { ok: false, status: 503, error: { message: "down" } };
    },
  }, config(), { debug() {} });
  const backlogged = { id: "backlogged", header: { cwd: "/workspace" } };
  const clean = { id: "clean", header: { cwd: "/workspace" } };
  runtime.stateFor(backlogged).ready = true;
  runtime.stateFor(backlogged).sessionReady = true;
  runtime.stateFor(clean).ready = true;
  runtime.stateFor(clean).sessionReady = true;

  runtime.capture(backlogged, userEvent("Queued."));
  await runtime.flush(backlogged);
  await runtime.drainTick();

  assert.equal(runtime.stateFor(backlogged).hasPendingWrites, true);
  assert.equal(runtime.stateFor(clean).hasPendingWrites, false);
});

test("startDrainer is idempotent, stopDrainer is safe, and an invalid env falls back to 60s", async t => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-drain-fallback-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  process.env.OPENVIKING_PENDING_DRAIN_INTERVAL_MS = "not-a-number";
  await enqueue("addMessage", "dsh-fallback", { content: "held" });

  let fetchCalls = 0;
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async fetchJSON() {
      fetchCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  const state = runtime.stateFor({ id: "fallback", header: { cwd: "/workspace" } });
  state.ready = true;
  state.sessionReady = true;

  const timer = runtime.startDrainer();
  assert.equal(runtime.startDrainer(), timer);
  t.mock.timers.tick(59999);
  assert.equal(fetchCalls, 0);
  t.mock.timers.tick(1);
  await runtime.drainPromise;
  assert.ok(fetchCalls >= 1);

  runtime.stopDrainer();
  runtime.stopDrainer();
});

function config() {
  return {
    explicitPeerId: "",
    workspacePeer: false,
    peerId: "",
    syncTurns: true,
    captureAssistantTurns: true,
    captureToolResults: false,
    captureToolMaxChars: 1000000,
    captureMaxLength: 24000,
    captureMode: "semantic",
  };
}

function userEvent(text) {
  return {
    type: "user/message",
    data: {
      role: "user",
      content: [{ type: "text", text }],
      source: { kind: "user" },
    },
  };
}
