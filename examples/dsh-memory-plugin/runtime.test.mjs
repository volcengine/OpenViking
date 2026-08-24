import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { enqueue, listPending } from "./shared/pending-queue.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";

const originalPendingDir = process.env.OPENVIKING_PENDING_DIR;
const tempDirs = [];

afterEach(async () => {
  if (originalPendingDir === undefined) delete process.env.OPENVIKING_PENDING_DIR;
  else process.env.OPENVIKING_PENDING_DIR = originalPendingDir;
  await Promise.all(tempDirs.splice(0).map(dir => rm(dir, { recursive: true, force: true })));
});

test("capture queues retryable failures but drops permanent client errors", async () => {
  for (const [status, expectedPending] of [[400, 0], [503, 1]]) {
    const pendingDir = await mkdtemp(join(tmpdir(), `dsh-memory-${status}-`));
    tempDirs.push(pendingDir);
    process.env.OPENVIKING_PENDING_DIR = pendingDir;

    const runtime = new OpenVikingRuntime({
      async addMessage() {
        return { ok: false, status, error: { code: "FAILED" } };
      },
    }, config(), { debug() {} });
    const session = { id: `session-${status}`, header: { cwd: "/workspace" } };
    runtime.stateFor(session).ready = true;

    runtime.capture(session, userEvent(`Remember the ${status} behavior.`));
    await runtime.flush(session);

    assert.equal((await listPending()).length, expectedPending, `HTTP ${status}`);
  }
});

test("initialization queues capture only when the failure is retryable", async () => {
  for (const [status, expectedPending] of [[401, 0], [503, 1]]) {
    const pendingDir = await mkdtemp(join(tmpdir(), `dsh-memory-init-${status}-`));
    tempDirs.push(pendingDir);
    process.env.OPENVIKING_PENDING_DIR = pendingDir;

    const runtime = new OpenVikingRuntime({
      async healthResult() {
        return { ok: false, status, error: { code: "FAILED" } };
      },
    }, config(), { debug() {} });
    const session = { id: `init-${status}`, header: { cwd: "/workspace" } };

    runtime.capture(session, userEvent(`Remember the init ${status} behavior.`));
    await runtime.flush(session);

    assert.equal((await listPending()).length, expectedPending, `HTTP ${status}`);
  }
});

test("a retryable threshold commit failure is queued", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-commit-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  const runtime = new OpenVikingRuntime({
    async getSession() {
      return { pending_tokens: 20000 };
    },
    async commitSession() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
  }, config(), { debug() {} });
  const session = { id: "commit-failure", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "turn/end" });
  await runtime.flush(session);

  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "commitSession",
  ]);
});

test("once a write is queued, later messages and the final commit stay ordered on disk", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-order-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  let addCalls = 0;
  let commitCalls = 0;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      addCalls += 1;
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
    async commitSession() {
      commitCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = { id: "ordered", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.capture(session, userEvent("First queued message."));
  runtime.capture(session, userEvent("Second queued message."));
  runtime.maybeCommit(session, { type: "turn/end" });
  await runtime.flush(session);
  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "addMessage",
    "addMessage",
  ]);
  await runtime.dispose(session);

  const pending = await listPending();
  assert.deepEqual(pending.map(item => item.entry.type), [
    "addMessage",
    "addMessage",
    "commitSession",
  ]);
  assert.deepEqual(
    pending.map(item => (
      item.entry.payload.parts?.[0]?.text
      || item.entry.payload.content
      || item.entry.payload.keep_recent_count
    )),
    ["First queued message.", "Second queued message.", 10],
  );
  assert.deepEqual(
    pending.map(item => item.entry.createdAt),
    [...pending.map(item => item.entry.createdAt)].sort((left, right) => left - right),
  );
  assert.equal(addCalls, 1);
  assert.equal(commitCalls, 0);
});

test("new queued messages move an older pending commit behind them", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-reorder-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  await enqueue("commitSession", "dsh-reorder", { keep_recent_count: 10 });
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async ensureSessionResult() {
      return { ok: true };
    },
    async fetchJSON() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
  }, config(), { debug() {} });
  const session = { id: "reorder", header: { cwd: "/workspace" } };

  runtime.capture(session, userEvent("Message after an offline commit."));
  await runtime.flush(session);
  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "addMessage",
  ]);

  await runtime.dispose(session);
  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "addMessage",
    "commitSession",
  ]);
});

test("flush waits only for the requested session", async () => {
  const runtime = new OpenVikingRuntime({}, config(), { debug() {} });
  const first = { id: "first", header: { cwd: "/workspace/first" } };
  const second = { id: "second", header: { cwd: "/workspace/second" } };
  let releaseSecond;
  runtime.stateFor(first).writes = Promise.resolve();
  runtime.stateFor(second).writes = new Promise(resolve => {
    releaseSecond = resolve;
  });

  await runtime.flush(first);
  releaseSecond();
  await runtime.flush(second);
});

test("dispose waits for the final commit before deleting session state", async () => {
  let releaseCommit;
  let commitOptions;
  const committed = new Promise(resolve => {
    releaseCommit = resolve;
  });
  const runtime = new OpenVikingRuntime({
    async commitSession(_sessionId, _peerId, options) {
      commitOptions = options;
      await committed;
      return { ok: true, result: { trace_id: "shutdown" } };
    },
  }, config(), { debug() {} });
  const session = { id: "dispose", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  let settled = false;
  const disposing = runtime.dispose(session).then(() => {
    settled = true;
  });
  await Promise.resolve();

  assert.equal(settled, false);
  assert.equal(runtime.states.has(session.id), true);
  assert.deepEqual(commitOptions, { timeoutMs: 3000 });
  releaseCommit();
  await disposing;
  assert.equal(runtime.states.has(session.id), false);
});

test("persisted profile delivery survives dispose and re-seed", async () => {
  const runtime = new OpenVikingRuntime({
    async commitSession() {
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = {
    id: "profile-resume",
    header: { cwd: "/workspace" },
    events: [],
  };
  const firstState = runtime.stateFor(session);
  firstState.ready = true;
  firstState.profileBlock = "profile v1";

  const profile = await runtime.profileMessage({ session });
  assert.equal(profile?.source?.form, "instructions");
  session.events.push({ type: "user/message", data: profile });
  await runtime.dispose(session);

  const resumedSession = {
    id: session.id,
    header: session.header,
    events: [...session.events],
  };
  const resumedState = runtime.stateFor(resumedSession);
  resumedState.ready = true;
  resumedState.profileBlock = "profile v2";
  assert.equal(await runtime.profileMessage({ session: resumedSession }), null);
  assert.equal(resumedState.profileDelivered, true);

  const pendingSession = {
    id: "profile-pending",
    header: { cwd: "/workspace" },
    events: [],
  };
  const pendingState = runtime.stateFor(pendingSession);
  pendingState.ready = true;
  pendingState.profileBlock = "pending profile";
  assert.equal(await runtime.profileMessage({
    session: pendingSession,
    inbox: { nextTurn: [], nextStep: [profile] },
  }), null);

  const otherSession = {
    id: "profile-other",
    header: { cwd: "/workspace", seedLength: 1 },
    events: [{ type: "user/message", data: profile }],
  };
  const otherState = runtime.stateFor(otherSession);
  otherState.ready = true;
  otherState.profileBlock = "other profile";
  assert.equal(
    (await runtime.profileMessage({ session: otherSession }))?.source?.form,
    "instructions",
  );
});

test("disposeAll drains every live session", async () => {
  const committed = [];
  const runtime = new OpenVikingRuntime({
    async commitSession(sessionId) {
      committed.push(sessionId);
      return { ok: true };
    },
  }, config(), { debug() {} });
  for (const id of ["one", "two"]) {
    runtime.stateFor({ id, header: { cwd: `/workspace/${id}` } }).ready = true;
  }

  await runtime.disposeAll();

  assert.deepEqual(committed.sort(), ["dsh-one", "dsh-two"]);
  assert.equal(runtime.states.size, 0);
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
    commitKeepRecentCount: 10,
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
