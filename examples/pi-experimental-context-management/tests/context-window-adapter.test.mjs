import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createContextWindowManager, readPiReserveTokens } from "../context-window.ts";
import { DEFAULT_RESERVE_TOKENS, pickReserveTokens, readReserveTokens, settingsPaths } from "../lib/pi-settings.mjs";
import { enqueue } from "../shared/pending-queue.mjs";

const SID = "pi-adapter-session";

function fakes(overrides = {}) {
  const calls = {
    addMessage: [],
    addPayload: [],
    commit: [],
    syncBranch: [],
    flush: [],
    overview: [],
    task: [],
    entries: [],
    logs: [],
  };
  const client = {
    connected: true,
    async addMessage(sid, role, content) {
      calls.addMessage.push({ sid, role, content });
      return true;
    },
    async readArchiveOverview(uri) {
      calls.overview.push(uri);
      return "# Working Memory\nbody";
    },
    async getTask(id) {
      calls.task.push(id);
      return { status: "completed" };
    },
    ...(overrides.client ?? {}),
  };
  const sync = {
    sessionId: SID,
    syncedCount: 7,
    async syncBranch(branch) {
      calls.syncBranch.push(branch);
      return { added: 0, tokens: 0, allDelivered: true };
    },
    async flushBarrier(opts) {
      calls.flush.push(opts);
      return true;
    },
    async addPayload(payload) {
      calls.addPayload.push(payload);
      return { accepted: true, delivered: true };
    },
    async commit(opts) {
      calls.commit.push(opts);
      return { status: "accepted", archive_uri: "viking://user/u/sessions/s/history/archive_001" };
    },
    ...(overrides.sync ?? {}),
  };
  const pi = {
    appendEntry(customType, data) {
      calls.entries.push({ customType, data });
    },
  };
  const config = {
    contextWindow: {
      resetDeadlineMs: 5000,
      archivePollMs: 250,
      overviewRefreshMaxAttempts: 3,
      overviewBudget: 3000,
      notesBudget: 1500,
      pendingRequestBudget: 400,
      softPercent: 70,
      hardPercent: 85,
      idleGapMinutes: 30,
      statusEveryTurn: true,
      historyItemMaxChars: 8000,
    },
  };
  const logger = { log: (stage, data) => calls.logs.push({ stage, data }) };
  const core = createContextWindowManager({ pi, client, sync, config, logger });
  return { core, calls, client, sync, pi, config };
}

test("the handoff note goes straight to addMessage, never through the queue", async () => {
  const { core, calls } = fakes();
  const ok = await core.io.postHandoff("[Context Window Handoff] w1 -> w2");
  assert.equal(ok, true);
  assert.deepEqual(calls.addMessage, [
    { sid: SID, role: "assistant", content: "[Context Window Handoff] w1 -> w2" },
  ]);
  // addPayload would queue on failure and land in the *next* archive.
  assert.equal(calls.addPayload.length, 0);
});

test("postHandoff fails closed when there is no session or addMessage throws", async () => {
  const noSession = fakes({ sync: { sessionId: null } });
  assert.equal(await noSession.core.io.postHandoff("x"), false);

  const throwing = fakes({
    client: {
      async addMessage() {
        throw new Error("boom");
      },
    },
  });
  assert.equal(await throwing.core.io.postHandoff("x"), false);
});

test("commit keeps nothing and never queues a retry", async () => {
  const { core, calls } = fakes();
  const result = await core.io.commit({ keepRecentCount: 0 });
  assert.equal(result.status, "accepted");
  assert.deepEqual(calls.commit, [{ queueOnFailure: false, keepRecentCount: 0 }]);

  // A core that passes no keepRecentCount still archives the whole window.
  await core.io.commit({});
  assert.deepEqual(calls.commit[1], { queueOnFailure: false, keepRecentCount: 0 });
});

test("a throwing commit becomes null rather than an exception", async () => {
  const { core } = fakes({
    sync: {
      async commit() {
        throw new Error("network down");
      },
    },
  });
  assert.equal(await core.io.commit({ keepRecentCount: 0 }), null);
});

test("syncBranch, watermark, connectivity and session id come from the managers", async () => {
  const { core, calls, client } = fakes();
  const branch = [{ id: "e1" }];
  assert.deepEqual(await core.io.syncBranch(branch), { added: 0, tokens: 0, allDelivered: true });
  assert.deepEqual(calls.syncBranch, [branch]);
  assert.equal(core.io.getWatermark(), 7);
  assert.equal(core.io.sessionId(), SID);
  assert.equal(core.io.connected(), true);
  client.connected = false;
  assert.equal(core.io.connected(), false);
});

test("readArchiveOverview and getTask swallow client failures", async () => {
  const { core } = fakes({
    client: {
      async readArchiveOverview() {
        throw new Error("404");
      },
      async getTask() {
        throw new Error("blocked");
      },
    },
  });
  assert.equal(await core.io.readArchiveOverview("viking://x"), null);
  assert.equal(await core.io.getTask("t1"), null);
});

test("persistEntry and log reach pi and the debug logger", async () => {
  const { core, calls } = fakes();
  core.io.persistEntry("ov-context-window", { windowIndex: 2 });
  assert.deepEqual(calls.entries, [{ customType: "ov-context-window", data: { windowIndex: 2 } }]);
  core.io.log("context-window: opened w2");
  assert.deepEqual(calls.logs, [{ stage: "window", data: "context-window: opened w2" }]);
});

test("pendingCount reports only this session's queued addMessage entries", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "ov-pending-"));
  const previous = process.env.OPENVIKING_PENDING_DIR;
  process.env.OPENVIKING_PENDING_DIR = dir;
  t.after(() => {
    if (previous === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previous;
    rmSync(dir, { recursive: true, force: true });
  });

  await enqueue("addMessage", SID, { role: "user", content: "mine 1" });
  await enqueue("addMessage", SID, { role: "user", content: "mine 2" });
  await enqueue("addMessage", "pi-other-session", { role: "user", content: "not mine" });
  await enqueue("commitSession", SID, { keep_recent_count: 0 });

  const { core } = fakes({ sync: { async flushBarrier() { return false; } } });
  assert.equal(await core.io.flush({ budgetMs: 1000 }), false);
  assert.equal(core.io.pendingCount(), 2);

  // A cleared barrier resets the count without another disk read.
  const clear = fakes();
  assert.equal(await clear.core.io.flush({ budgetMs: 1000 }), true);
  assert.equal(clear.core.io.pendingCount(), 0);
});

test("flush passes the caller's budget through to the barrier", async () => {
  const { core, calls } = fakes();
  await core.io.flush({ budgetMs: 4321 });
  assert.deepEqual(calls.flush, [{ budgetMs: 4321 }]);
});

test("sleep gives up as soon as the tool call is aborted", async () => {
  const { core } = fakes();
  const controller = new AbortController();
  const started = Date.now();
  const waited = core.io.sleep(60_000, controller.signal);
  controller.abort();
  await waited; // must not reject, and must not wait out the delay
  assert.ok(Date.now() - started < 5_000);

  // Already aborted: resolves immediately.
  const done = new AbortController();
  done.abort();
  await core.io.sleep(60_000, done.signal);

  // No signal: still a real (short) sleep.
  await core.io.sleep(1, null);
});

test("the core is constructed with the contextWindow config block", () => {
  const { core } = fakes();
  assert.equal(core.config.archivePollMs, 250);
  assert.equal(core.config.softPercent, 70);
  assert.equal(core.windowId, "w1");
  assert.equal(core.armed, false);
});

// ---- readPiReserveTokens -------------------------------------------------

test("pickReserveTokens takes the first usable file and defaults to 16384", () => {
  assert.equal(pickReserveTokens([JSON.stringify({ compaction: { reserveTokens: 4096 } })]), 4096);
  assert.equal(
    pickReserveTokens([null, JSON.stringify({ compaction: { reserveTokens: 8192 } })]),
    8192,
  );
  // A broken project file must not hide the global one.
  assert.equal(
    pickReserveTokens(["{not json", JSON.stringify({ compaction: { reserveTokens: 2048 } })]),
    2048,
  );
  assert.equal(pickReserveTokens([JSON.stringify({ compaction: {} }), null]), DEFAULT_RESERVE_TOKENS);
  assert.equal(pickReserveTokens([]), DEFAULT_RESERVE_TOKENS);
});

test("settingsPaths puts the project file before the global one", () => {
  assert.deepEqual(settingsPaths({ cwd: "/repo", configDirName: ".pi", homeDir: "/home/u" }), [
    "/repo/.pi/settings.json",
    "/home/u/.pi/agent/settings.json",
  ]);
  assert.deepEqual(settingsPaths({ cwd: "/repo", configDirName: ".pi", agentDir: "/custom/agent" }), [
    "/repo/.pi/settings.json",
    "/custom/agent/settings.json",
  ]);
});

test("readReserveTokens reads the project file first, then the global one", () => {
  const seen = [];
  const files = {
    "/repo/.pi/settings.json": JSON.stringify({ compaction: { reserveTokens: 1000 } }),
    "/home/u/.pi/agent/settings.json": JSON.stringify({ compaction: { reserveTokens: 2000 } }),
  };
  const readFile = (path) => {
    seen.push(path);
    return files[path] ?? null;
  };
  assert.equal(readReserveTokens({ cwd: "/repo", homeDir: "/home/u", readFile }), 1000);
  assert.deepEqual(seen, ["/repo/.pi/settings.json", "/home/u/.pi/agent/settings.json"]);

  delete files["/repo/.pi/settings.json"];
  assert.equal(readReserveTokens({ cwd: "/repo", homeDir: "/home/u", readFile }), 2000);

  delete files["/home/u/.pi/agent/settings.json"];
  assert.equal(readReserveTokens({ cwd: "/repo", homeDir: "/home/u", readFile }), DEFAULT_RESERVE_TOKENS);
});

test("readPiReserveTokens resolves real settings files, project before global", (t) => {
  const root = mkdtempSync(join(tmpdir(), "ov-pi-settings-"));
  const project = join(root, "project");
  const agent = join(root, "agent");
  mkdirSync(join(project, ".pi"), { recursive: true });
  mkdirSync(agent, { recursive: true });
  const previous = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agent;
  t.after(() => {
    if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previous;
    rmSync(root, { recursive: true, force: true });
  });

  // Neither file exists yet.
  assert.equal(readPiReserveTokens(project), DEFAULT_RESERVE_TOKENS);

  writeFileSync(join(agent, "settings.json"), JSON.stringify({ compaction: { reserveTokens: 9000 } }));
  assert.equal(readPiReserveTokens(project), 9000);

  writeFileSync(
    join(project, ".pi", "settings.json"),
    JSON.stringify({ compaction: { reserveTokens: 3000 } }),
  );
  assert.equal(readPiReserveTokens(project), 3000);
});
