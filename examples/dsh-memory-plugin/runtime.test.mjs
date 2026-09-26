import assert from "node:assert/strict";
import { realpathSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { enqueue, listPending } from "./shared/pending-queue.mjs";
import { deriveWorkspacePeerId } from "./shared/workspace-peer.mjs";
import { OPENVIKING_BOUNDARY_NOTICE_MARKER, OPENVIKING_PLUGIN_KIND } from "./capture.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";

const originalPendingDir = process.env.OPENVIKING_PENDING_DIR;
const originalStateDir = process.env.OPENVIKING_STATE_DIR;
const tempDirs = [];

afterEach(async () => {
  if (originalPendingDir === undefined) delete process.env.OPENVIKING_PENDING_DIR;
  else process.env.OPENVIKING_PENDING_DIR = originalPendingDir;
  if (originalStateDir === undefined) delete process.env.OPENVIKING_STATE_DIR;
  else process.env.OPENVIKING_STATE_DIR = originalStateDir;
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

test("existing OpenViking sessions are reusable on DSH resume", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-resume-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;

  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async ensureSessionResult() {
      return {
        ok: false,
        status: 409,
        error: { code: "ALREADY_EXISTS", message: "session exists" },
      };
    },
    async fetchJSON() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
  }, config(), { debug() {} });

  const state = await runtime.initialize({
    session: { id: "resume", header: { cwd: "/workspace" } },
  });

  assert.equal(state.ready, true);
  assert.equal(state.initializationRetryable, false);
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

test("a compaction boundary commits below-threshold messages unconditionally", async () => {
  let commitCalls = 0;
  let commitOptions;
  const runtime = new OpenVikingRuntime({
    async commitSession(_sessionId, _peerId, options) {
      commitCalls += 1;
      commitOptions = options;
      return { ok: true, result: { trace_id: "compaction" } };
    },
  }, config(), { debug() {} });
  const session = { id: "compaction", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(commitCalls, 1);
  // No teardown deadline applies mid-session, so the client's full commit
  // timeout is used rather than dispose's short one.
  assert.equal(commitOptions, undefined);
});

test("only the compaction start boundary commits", async () => {
  let commitCalls = 0;
  const runtime = new OpenVikingRuntime({
    async commitSession() {
      commitCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = { id: "compaction-events", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/end" });
  runtime.maybeCommit(session, { type: "turn/start" });
  await runtime.flush(session);

  assert.equal(commitCalls, 0);
});

test("a retryable compaction-boundary commit failure is queued", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-compact-503-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  const runtime = new OpenVikingRuntime({
    async commitSession() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
  }, config(), { debug() {} });
  const session = { id: "compaction-failure", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "commitSession",
  ]);
});

test("a permanent compaction-boundary commit failure is dropped without throwing", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-compact-400-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  const debugLines = [];
  const runtime = new OpenVikingRuntime({
    async commitSession() {
      return { ok: false, status: 400, error: { code: "FAILED" } };
    },
  }, config(), { debug: line => debugLines.push(line) });
  const session = { id: "compaction-permanent", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.deepEqual(await listPending(), []);
  assert.equal(debugLines.some(line => line.includes("write_error")), false,
    "a permanent boundary failure logs and drops; the write chain must not surface an error");
});

test("a compaction boundary appends a notice event on successful flush", async () => {
  const appends = [];
  const runtime = new OpenVikingRuntime({
    async getSession() {
      return { pending_tokens: 25 };
    },
    async commitSession() {
      return { ok: true, result: { trace_id: "compaction" } };
    },
  }, config(), { debug() {} });
  const session = {
    id: "compaction-notice",
    header: { cwd: "/workspace" },
    append(type, data) {
      appends.push({ type, data });
    },
  };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(appends.length, 1);
  assert.equal(appends[0].type, "user/message");
  assert.deepEqual(appends[0].data.source, {
    kind: OPENVIKING_PLUGIN_KIND,
    plugin: "openviking-memory",
    form: "notice",
    summary: "OpenViking boundary commit: 25 pending token(s) archived to memory before compaction",
  });
  assert.match(appends[0].data.content[0].text, /OpenViking boundary commit: 25 pending token/);
  assert.equal(appends[0].data.source.summary, appends[0].data.content[0].text,
    "the collapsed-row summary must repeat the notice sentence verbatim (dsh notice-summary + ov-viz contract)");
});

test("a boundary commit with nothing pending stays silent", async () => {
  const appends = [];
  const runtime = new OpenVikingRuntime({
    async getSession() {
      return { pending_tokens: 0 };
    },
    async commitSession() {
      return { ok: true, result: { status: "skipped" } };
    },
  }, config(), { debug() {} });
  const session = {
    id: "compaction-silent",
    header: { cwd: "/workspace" },
    append(type, data) {
      appends.push({ type, data });
    },
  };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(appends.length, 0);
});

test("a failed metadata read still commits and stays silent", async () => {
  let commitCalls = 0;
  const appends = [];
  const runtime = new OpenVikingRuntime({
    async getSession() {
      throw new Error("metadata unreachable");
    },
    async commitSession() {
      commitCalls += 1;
      return { ok: true, result: { trace_id: "compaction" } };
    },
  }, config(), { debug() {} });
  const session = {
    id: "compaction-meta-fail",
    header: { cwd: "/workspace" },
    append(type, data) {
      appends.push({ type, data });
    },
  };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(commitCalls, 1);
  assert.equal(appends.length, 0);
});

test("capture skips this plugin's own session messages", async () => {
  let addCalls = 0;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      addCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = { id: "sanitize", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;
  // The notice appends with the canonical producer-owned kind; the legacy
  // wrapper stays covered so a replayed older session behaves the same.
  for (const kind of [OPENVIKING_PLUGIN_KIND, "plugin"]) {
    runtime.capture(session, {
      type: "user/message",
      data: {
        role: "user",
        content: [{ type: "text", text: "OpenViking boundary commit: 25 pending token(s)" }],
        source: { kind, plugin: "openviking-memory", form: "notice" },
      },
    });
    await runtime.flush(session);
  }

  assert.equal(addCalls, 0, "self-sourced plugin messages must never be captured");
});

test("a failing notice append never breaks the committed boundary flush", async () => {
  const debugLines = [];
  const appends = [];
  const runtime = new OpenVikingRuntime({
    async getSession() {
      return { pending_tokens: 25 };
    },
    async commitSession() {
      return { ok: true, result: { trace_id: "compaction" } };
    },
  }, config(), { debug: line => debugLines.push(line) });
  const session = {
    id: "compaction-notice-fail",
    header: { cwd: "/workspace" },
    append() {
      throw new Error("host append failed");
    },
  };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(appends.length, 0);
  assert.equal(debugLines.some(line => line.includes("boundary_notice_error")), true,
    "the notice failure is logged as boundary_notice_error, not surfaced as write_error");
  assert.equal(debugLines.some(line => line.includes("write_error")), false,
    "the write chain must survive a notice append failure");
});

test("a retryable boundary failure queues the commit and emits no notice", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-compact-notice-503-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  const appends = [];
  const runtime = new OpenVikingRuntime({
    async getSession() {
      return { pending_tokens: 25 };
    },
    async commitSession() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
  }, config(), { debug() {} });
  const session = {
    id: "compaction-retryable-notice",
    header: { cwd: "/workspace" },
    append(type, data) {
      appends.push({ type, data });
    },
  };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(appends.length, 0, "a failed commit never claims a flush");
  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "commitSession",
  ]);
});

test("the notice text starts with the pinned cross-repo marker constant", async () => {
  const appends = [];
  const runtime = new OpenVikingRuntime({
    async getSession() {
      return { pending_tokens: 7 };
    },
    async commitSession() {
      return { ok: true, result: { trace_id: "compaction" } };
    },
  }, config(), { debug() {} });
  const session = {
    id: "compaction-marker-binding",
    header: { cwd: "/workspace" },
    append(type, data) {
      appends.push({ type, data });
    },
  };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(appends.length, 1);
  assert.equal(
    appends[0].data.content[0].text.startsWith(OPENVIKING_BOUNDARY_NOTICE_MARKER),
    true,
    "the ov-viz client module classifies rows by this exact marker",
  );
});

test("a compaction boundary while the server never becomes ready drops the commit", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-compact-unready-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  let commitCalls = 0;
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
    async commitSession() {
      commitCalls += 1;
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = { id: "compaction-unready", header: { cwd: "/workspace" } };

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  // Mirrors dispose: nothing commits until the session initializes; replayed
  // messages reach the queue through capture, not through the boundary.
  assert.equal(commitCalls, 0);
  assert.deepEqual(await listPending(), []);
});

test("a compaction boundary during a pending outage queues the commit behind the messages", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-compact-latch-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  const runtime = new OpenVikingRuntime({
    async addMessage() {
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
    async commitSession() {
      return { ok: true };
    },
  }, config(), { debug() {} });
  const session = { id: "compaction-latched", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.capture(session, userEvent("Queued before the compaction."));
  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.deepEqual((await listPending()).map(item => item.entry.type), [
    "addMessage",
    "commitSession",
  ]);
});

test("syncTurns false skips the compaction-boundary commit", async () => {
  let commitCalls = 0;
  const runtime = new OpenVikingRuntime({
    async commitSession() {
      commitCalls += 1;
      return { ok: true };
    },
  }, { ...config(), syncTurns: false }, { debug() {} });
  const session = { id: "compaction-off", header: { cwd: "/workspace" } };
  runtime.stateFor(session).ready = true;

  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  assert.equal(commitCalls, 0);
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
  assert.equal(profile?.source?.kind, OPENVIKING_PLUGIN_KIND);
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

test("profile delivery uses current DSH session-owned history on resume and fork", async () => {
  const profile = {
    type: "user/message",
    data: {
      role: "user",
      content: [{ type: "text", text: "stored profile" }],
      source: { kind: "plugin", plugin: "openviking-memory", form: "instructions" },
    },
  };
  for (const [id, ownEvents, expected] of [
    ["resumed", [profile], null],
    ["forked", [], "instructions"],
    ["forked-resumed", [profile], null],
  ]) {
    const runtime = new OpenVikingRuntime({}, config(), { debug() {} });
    let historyReads = 0;
    const session = {
      id,
      header: { cwd: "/workspace", isSeeded: id !== "resumed" },
      ownEvents() {
        historyReads += 1;
        return ownEvents;
      },
    };
    const state = runtime.stateFor(session);
    state.ready = true;
    state.profileBlock = "current profile";

    const message = await runtime.profileMessage({ session });

    assert.equal(message?.source?.form ?? null, expected, id);
    assert.equal(historyReads, 1, id);
    assert.equal(state.profileDelivered, true, id);
    assert.equal(await runtime.profileMessage({ session }), null, id);
  }
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

// dsh and pi had no recall switch at all: every other harness could turn recall
// off and these two retrieved on every prompt regardless.
test("autoRecall false stops the recall request", async () => {
  const runtime = new OpenVikingRuntime({
    async fetchJSON() {
      throw new Error("recall must not reach the server when it is switched off");
    },
  }, { ...config(), autoRecall: false }, { debug() {} });
  runtime.initialize = async () => ({ ready: true, config: { ...config(), autoRecall: false } });

  assert.equal(await runtime.recallMessage({}, [{ role: "user", content: "what did we decide" }]), null);
});

// recall-core reads options.excludeUris, but the DSH runtime built its options
// without it, so nothing a user configured could stop a subtree from being
// recalled: generated directory files came back as ordinary hits.
test("recallExcludeUris reaches the search request", async () => {
  const bodies = [];
  const runtime = new OpenVikingRuntime({
    async fetchJSON(path, init) {
      if (/\/search\/search$/.test(path)) bodies.push(JSON.parse(init.body));
      return {
        ok: true,
        result: {
          context: "<openviking-context>\nrecalled\n</openviking-context>",
          stats: {},
        },
      };
    },
  }, { ...config(), recallExcludeUris: ["viking://user/default/skills", "viking://agent/skills"] }, { debug() {} });
  runtime.initialize = async () => ({
    ready: true,
    config: { ...config(), recallExcludeUris: ["viking://user/default/skills", "viking://agent/skills"] },
  });

  await runtime.recallMessage({}, [{ role: "user", content: "what did we decide" }]);

  assert.equal(bodies.length, 1);
  assert.deepEqual(bodies[0].exclude_uris, ["viking://user/default/skills", "viking://agent/skills"]);
});

test("recall sends no exclude_uris when recallExcludeUris is unset", async () => {
  const bodies = [];
  const runtime = new OpenVikingRuntime({
    async fetchJSON(path, init) {
      if (/\/search\/search$/.test(path)) bodies.push(JSON.parse(init.body));
      return {
        ok: true,
        result: { context: "<openviking-context>\nrecalled\n</openviking-context>", stats: {} },
      };
    },
  }, config(), { debug() {} });
  runtime.initialize = async () => ({ ready: true, config: config() });

  await runtime.recallMessage({}, [{ role: "user", content: "what did we decide" }]);

  assert.equal(bodies.length, 1);
  assert.equal("exclude_uris" in bodies[0], false);
});

test("syncTurns false sends nothing: no capture, no commit, no dispose flush, no replay", async () => {
  const pendingDir = await mkdtemp(join(tmpdir(), "dsh-memory-sync-off-"));
  tempDirs.push(pendingDir);
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  await enqueue("addMessage", "dsh-earlier", { content: "queued while capture was on" });

  const writes = [];
  const runtime = new OpenVikingRuntime({
    async healthResult() {
      return { ok: true };
    },
    async ensureSessionResult() {
      return { ok: true };
    },
    async fetchJSON(path, init) {
      if (init?.method === "POST" && /\/(messages|commit)$/.test(path)) writes.push(path);
      return { ok: false, status: 503, error: { code: "UNAVAILABLE" } };
    },
    async addMessage() {
      writes.push("addMessage");
      return { ok: true };
    },
    async getSession() {
      return { pending_tokens: 1000000 };
    },
    async commitSession() {
      writes.push("commitSession");
      return { ok: true };
    },
  }, { ...config(), syncTurns: false }, { debug() {} });
  const session = { id: "sync-off", header: { cwd: "/workspace" } };

  runtime.capture(session, userEvent("Never sent."));
  runtime.maybeCommit(session, { type: "turn/end" });
  // The recall path reaches initialization even when nothing is captured, and
  // the toggle takes the replay out of it without taking the reads with it.
  assert.equal((await runtime.initialize({ session })).ready, true);
  await runtime.flush(session);
  await runtime.dispose(session);

  assert.deepEqual(writes, []);
  const pending = await listPending();
  assert.deepEqual(pending.map(item => item.entry.sessionId), ["dsh-earlier"]);
  assert.ok(!pending[0].entry.retries);
});

test("the per-session peer honors peerSource", async () => {
  const root = realpathSync(await mkdtemp(join(tmpdir(), "dsh-memory-peer-")));
  tempDirs.push(root);
  await mkdir(join(root, ".git"), { recursive: true });
  await writeFile(
    join(root, ".git", "config"),
    '[remote "origin"]\n\turl = git@github.com:volcengine/OpenViking.git\n',
  );
  process.env.OPENVIKING_STATE_DIR = join(root, ".state");
  const session = { id: "peer", header: { cwd: root } };

  const byGit = new OpenVikingRuntime({}, {
    ...config(),
    workspacePeer: true,
  }, { debug() {} }).stateFor(session).config;
  const byCwd = new OpenVikingRuntime({}, {
    ...config(),
    workspacePeer: true,
    peerSource: "cwd",
  }, { debug() {} }).stateFor(session).config;

  assert.equal(byGit.peerId, "github.com-volcengine-openviking");
  assert.equal(byGit.legacyPeerId, deriveWorkspacePeerId(root));
  assert.equal(byCwd.peerId, deriveWorkspacePeerId(root));
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
