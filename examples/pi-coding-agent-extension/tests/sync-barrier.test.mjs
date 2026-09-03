import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SyncManager } from "../sync.ts";
import { enqueue, listPending } from "../shared/pending-queue.mjs";
import { createTakeoverManager } from "../takeover.ts";

function config(overrides = {}) {
  return {
    commitTokenThreshold: 20000,
    commitKeepRecentCount: 10,
    captureAssistantTurns: true,
    captureToolMaxChars: 2000,
    captureMaxLength: 24000,
    takeoverEnabled: true,
    ...overrides,
  };
}

function client(overrides = {}) {
  return {
    connected: true,
    addMessagePayload: async () => true,
    getSession: async () => ({ pending_tokens: 0 }),
    commitSession: async () => ({ task_id: "t-1", archive_uri: "viking://archive/1" }),
    commitSessionResponse: async () => ({
      result: { task_id: "t-1", archive_uri: "viking://archive/1" },
    }),
    fetchJSON: async () => ({ ok: true, result: {} }),
    ...overrides,
  };
}

async function withPendingDir(fn) {
  const previousDir = process.env.OPENVIKING_PENDING_DIR;
  const previousReplayLimit = process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
  const previousMaxRetries = process.env.OPENVIKING_PENDING_MAX_RETRIES;
  const previousTtlDays = process.env.OPENVIKING_PENDING_TTL_DAYS;
  const dir = await mkdtemp(join(tmpdir(), "ov-pi-pending-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  process.env.OPENVIKING_PENDING_MAX_RETRIES = "3";
  process.env.OPENVIKING_PENDING_TTL_DAYS = "7";
  try {
    return await fn(dir);
  } finally {
    if (previousDir === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previousDir;
    if (previousReplayLimit === undefined) delete process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
    else process.env.OPENVIKING_PENDING_REPLAY_LIMIT = previousReplayLimit;
    if (previousMaxRetries === undefined) delete process.env.OPENVIKING_PENDING_MAX_RETRIES;
    else process.env.OPENVIKING_PENDING_MAX_RETRIES = previousMaxRetries;
    if (previousTtlDays === undefined) delete process.env.OPENVIKING_PENDING_TTL_DAYS;
    else process.env.OPENVIKING_PENDING_TTL_DAYS = previousTtlDays;
    await rm(dir, { recursive: true, force: true });
  }
}

test("syncBranch returns added token accounting and delivered status", async () => {
  await withPendingDir(async () => {
    const c = client();
    const sync = new SyncManager(c, config({ takeoverEnabled: false }));
    await sync.ensureSession("pi-session");

    const result = await sync.syncBranch([
      { type: "message", message: { role: "user", content: "Remember this implementation decision for the next run." } },
    ]);

    assert.equal(result.added, 1);
    assert.ok(result.tokens > 0);
    assert.equal(result.allDelivered, true);
    assert.equal(sync.syncedCount, 1);
  });
});

test("commit writes success trace_id to the pi debug log", async () => {
  await withPendingDir(async (dir) => {
    const previous = process.env.OV_DEBUG_LOG;
    const debugLogPath = join(dir, "pi-debug.log");
    process.env.OV_DEBUG_LOG = debugLogPath;
    try {
      const c = client({
        commitSessionResponse: async () => ({
          result: {
            task_id: "t-trace",
            archive_uri: "viking://archive/trace",
            trace_id: "trace-pi-commit",
          },
          traceId: "trace-pi-commit",
        }),
      });
      const sync = new SyncManager(c, config());
      await sync.ensureSession("pi-trace-session");

      const result = await sync.commit();
      assert.equal(result.trace_id, "trace-pi-commit");
      assert.match(await readFile(debugLogPath, "utf8"), /trace_id=trace-pi-commit/);
    } finally {
      if (previous === undefined) delete process.env.OV_DEBUG_LOG;
      else process.env.OV_DEBUG_LOG = previous;
    }
  });
});

test("commit writes failure trace_id to the pi debug log", async () => {
  await withPendingDir(async (dir) => {
    const previous = process.env.OV_DEBUG_LOG;
    const debugLogPath = join(dir, "pi-debug-error.log");
    process.env.OV_DEBUG_LOG = debugLogPath;
    try {
      const c = client({
        commitSessionResponse: async () => ({
          result: null,
          status: 500,
          traceId: "trace-pi-error",
          error: { message: "commit failed" },
        }),
      });
      const sync = new SyncManager(c, config());
      await sync.ensureSession("pi-trace-error");

      assert.equal(await sync.commit({ queueOnFailure: false }), null);
      const raw = await readFile(debugLogPath, "utf8");
      assert.match(raw, /trace_id=trace-pi-error/);
      assert.match(raw, /error=commit failed/);
    } finally {
      if (previous === undefined) delete process.env.OV_DEBUG_LOG;
      else process.env.OV_DEBUG_LOG = previous;
    }
  });
});

test("queued addMessage makes takeover flush barrier false until replay succeeds", async () => {
  await withPendingDir(async () => {
    let replayOk = false;
    const c = client({
      addMessagePayload: async () => false,
      fetchJSON: async () => ({ ok: replayOk, status: replayOk ? 200 : 500, result: {} }),
    });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");

    const result = await sync.syncBranch([
      { type: "message", message: { role: "user", content: "This should be queued for takeover barrier testing." } },
    ]);

    assert.equal(result.added, 1);
    assert.equal(result.allDelivered, false);
    assert.equal((await listPending()).length, 1);
    assert.equal(await sync.flushForTakeover(), false);

    replayOk = true;
    assert.equal(await sync.flushForTakeover(), true);
    assert.equal((await listPending()).length, 0);
  });
});

test("replay-limit backlog does not make takeover retry on a sub-threshold turn (#4504)", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "1";
    let committed = 0;
    let replayRequests = 0;
    const c = client({
      addMessagePayload: async () => false,
      fetchJSON: async () => {
        replayRequests++;
        return { ok: true, status: 200, result: {} };
      },
      commitSessionResponse: async () => {
        committed++;
        return { result: { task_id: "t-1", archive_uri: "viking://archive/1" } };
      },
      getSessionContext: async () => ({ latest_archive_overview: "overview ready" }),
    });
    const cfg = config({
      takeoverTokenThreshold: 100,
      takeoverKeepRecentTurns: 1,
      takeoverOverviewBudget: 1000,
      takeoverOverviewPollMs: 0,
      takeoverOverviewPollMax: 1,
    });
    const sync = new SyncManager(c, cfg);
    await sync.ensureSession("pi-session");
    await sync.addPayload({ role: "user", content: "queued one" });
    await sync.addPayload({ role: "user", content: "queued two" });
    const queuedBeforeTakeover = (await listPending()).length;

    const takeover = createTakeoverManager({ pi: {}, client: c, sync, config: cfg });
    takeover.transformContext([
      { role: "user", content: "one" },
      { role: "user", content: "two" },
    ]);

    const firstAttempt = await takeover.onTurnSynced(120);
    const pendingAfterFirst = (await listPending()).length;
    const commitsAfterFirst = committed;
    const smallTurn = await takeover.onTurnSynced(10);

    assert.equal(queuedBeforeTakeover, 2);
    assert.equal(firstAttempt, false);
    assert.equal(replayRequests, 1);
    assert.equal(pendingAfterFirst, 1);
    assert.equal(commitsAfterFirst, 0);
    assert.equal(smallTurn, false);
    assert.equal(committed, commitsAfterFirst);
  });
});

test("current-session addMessage 500 remains queued and keeps barrier closed", async () => {
  await withPendingDir(async () => {
    const c = client({
      addMessagePayload: async () => false,
      fetchJSON: async () => ({ ok: false, status: 500 }),
    });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");

    await sync.addPayload({ role: "user", content: "Queued content with retryable server failure." });

    assert.equal(await sync.flushForTakeover(), false);
    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.type, "addMessage");
    assert.equal(pending[0].entry.sessionId, sync.sessionId);
  });
});

test("disconnected takeover does not consume a queued message retry", async () => {
  await withPendingDir(async () => {
    let calls = 0;
    const c = client({
      connected: false,
      addMessagePayload: async () => false,
      fetchJSON: async () => {
        calls++;
        return { ok: false, status: 500 };
      },
    });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");
    await sync.addPayload({ role: "user", content: "Keep this queued while offline." });

    assert.equal(await sync.flushForTakeover(), false);
    assert.equal(calls, 0);
    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.retries, 0);
  });
});

test("other-session addMessage and commit queue entries do not block takeover barrier", async () => {
  await withPendingDir(async () => {
    const c = client({
      fetchJSON: async () => ({ ok: false, status: 500 }),
    });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");

    await enqueue("addMessage", "different-session", { role: "user", content: "other" });
    await enqueue("commitSession", sync.sessionId, { keep_recent_count: 1 });

    assert.equal(await sync.flushForTakeover(), true);
  });
});

test("restoreWatermark prevents pi -c from re-syncing already captured entries", async () => {
  await withPendingDir(async () => {
    const calls = [];
    const c = client({
      addMessagePayload: async (_sid, payload) => {
        calls.push(payload);
        return true;
      },
    });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");
    sync.restoreWatermark(1);

    const result = await sync.syncBranch([
      { type: "message", message: { role: "user", content: "Already captured entry should be skipped." } },
      { type: "message", message: { role: "user", content: "Fresh entry should be captured now." } },
    ]);

    assert.equal(result.added, 1);
    assert.equal(calls.length, 1);
    assert.match(calls[0].parts[0].text, /Fresh entry/);
  });
});
