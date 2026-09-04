import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SyncManager } from "../sync.ts";
import { enqueue, listPending } from "../shared/pending-queue.mjs";

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
  const previous = process.env.OPENVIKING_PENDING_DIR;
  const dir = await mkdtemp(join(tmpdir(), "ov-pi-pending-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    return await fn(dir);
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previous;
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

async function readLastRecord(path) {
  const lines = (await readFile(path, "utf8")).trim().split("\n");
  return JSON.parse(lines[lines.length - 1]);
}

test("commit writes success trace_id to the pi debug log", async () => {
  await withPendingDir(async (dir) => {
    const debugLogPath = join(dir, "pi-debug.log");
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
    const sync = new SyncManager(c, config({ debugLogPath }));
    await sync.ensureSession("pi-trace-session");

    const result = await sync.commit();
    assert.equal(result.trace_id, "trace-pi-commit");
    const record = await readLastRecord(debugLogPath);
    assert.equal(record.hook, "pi");
    assert.equal(record.stage, "commit");
    assert.equal(record.data.ok, true);
    assert.equal(record.data.trace_id, "trace-pi-commit");
  });
});

test("commit writes failure trace_id to the pi debug log", async () => {
  await withPendingDir(async (dir) => {
    const debugLogPath = join(dir, "pi-debug-error.log");
    const c = client({
      commitSessionResponse: async () => ({
        result: null,
        status: 500,
        traceId: "trace-pi-error",
        error: { message: "commit failed" },
      }),
    });
    const sync = new SyncManager(c, config({ debugLogPath }));
    await sync.ensureSession("pi-trace-error");

    assert.equal(await sync.commit({ queueOnFailure: false }), null);
    const record = await readLastRecord(debugLogPath);
    assert.equal(record.stage, "commit");
    assert.equal(record.data.ok, false);
    assert.equal(record.data.trace_id, "trace-pi-error");
    assert.equal(record.data.error, "commit failed");
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
      fetchJSON: async (_path, init) => {
        calls.push(...JSON.parse(init.body).messages);
        return { ok: true, result: {} };
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

function batchClient(overrides = {}) {
  const calls = [];
  const c = client({
    fetchJSON: async (path, init) => {
      calls.push({ path: String(path), body: init?.body ? JSON.parse(init.body) : null });
      return overrides.respond ? overrides.respond(calls.length, path) : { ok: true, result: {} };
    },
  });
  return { c, calls };
}

test("syncBranch sends the whole turn in one batch request", async () => {
  await withPendingDir(async () => {
    const { c, calls } = batchClient();
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");

    const result = await sync.syncBranch([
      { type: "message", message: { role: "user", content: "First user message for the batch write test." } },
      { type: "message", message: { role: "assistant", content: "Assistant reply for the batch write test." } },
      { type: "message", message: { role: "user", content: "Second user message for the batch write test." } },
    ]);

    assert.equal(result.added, 3);
    assert.equal(result.allDelivered, true);
    assert.equal(calls.length, 1);
    assert.match(calls[0].path, /\/messages\/batch$/);
    assert.equal(calls[0].body.messages.length, 3);
  });
});

test("takeover barrier drains a large backlog through the batch endpoint", async () => {
  await withPendingDir(async () => {
    const { c, calls } = batchClient();
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");
    const t0 = Date.now();
    for (let i = 0; i < 250; i++) {
      await enqueue("addMessage", sync.sessionId, { role: "user", content: `m${i}` }, { createdAt: t0 + i });
    }
    await enqueue("addMessage", "other-session", { role: "user", content: "other" }, { createdAt: t0 + 999 });

    assert.equal(await sync.flushForTakeover(), true);
    assert.deepEqual(calls.map((call) => call.body.messages.length), [100, 100, 50]);
    // Order preserved across batches.
    assert.equal(calls[0].body.messages[0].content, "m0");
    assert.equal(calls[2].body.messages[49].content, "m249");
    const left = await listPending();
    assert.equal(left.length, 1);
    assert.equal(left[0].entry.sessionId, "other-session");
  });
});

test("failed batch keeps its entries queued with one retry and leaves the rest untouched", async () => {
  await withPendingDir(async () => {
    const { c, calls } = batchClient({ respond: () => ({ ok: false, status: 500 }) });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");
    const t0 = Date.now();
    for (let i = 0; i < 120; i++) {
      await enqueue("addMessage", sync.sessionId, { role: "user", content: `m${i}` }, { createdAt: t0 + i });
    }

    assert.equal(await sync.flushForTakeover(), false);
    assert.equal(calls.length, 1);
    const retries = (await listPending()).map((p) => p.entry.retries);
    assert.equal(retries.length, 120);
    assert.equal(retries.filter((r) => r === 1).length, 100);
    assert.equal(retries.filter((r) => r === 0).length, 20);
  });
});

test("non-retryable batch failure still enqueues and advances the sync watermark", async () => {
  await withPendingDir(async () => {
    const { c, calls } = batchClient({ respond: () => ({ ok: false, status: 400, error: { message: "bad" } }) });
    const sync = new SyncManager(c, config());
    await sync.ensureSession("pi-session");

    const result = await sync.syncBranch([
      { type: "message", message: { role: "user", content: "Poison payload one for watermark enqueue test." } },
      { type: "message", message: { role: "user", content: "Poison payload two for watermark enqueue test." } },
    ]);

    assert.equal(result.added, 2);
    assert.equal(result.allDelivered, false);
    assert.equal(sync.syncedCount, 2);
    assert.equal(calls.length, 1);
    assert.equal((await listPending()).length, 2);
  });
});

test("drainSessionBacklog stops after maxBatches and leaves remainder for later turns", async () => {
  await withPendingDir(async () => {
    const previous = process.env.OPENVIKING_PENDING_DRAIN_MAX_BATCHES;
    process.env.OPENVIKING_PENDING_DRAIN_MAX_BATCHES = "1";
    try {
      const { c, calls } = batchClient();
      const sync = new SyncManager(c, config());
      await sync.ensureSession("pi-session");
      const t0 = Date.now();
      for (let i = 0; i < 250; i++) {
        await enqueue("addMessage", sync.sessionId, { role: "user", content: `m${i}` }, { createdAt: t0 + i });
      }

      assert.equal(await sync.flushForTakeover(), false);
      assert.equal(calls.length, 1);
      assert.equal(calls[0].body.messages.length, 100);
      assert.equal((await listPending()).length, 150);
    } finally {
      if (previous === undefined) delete process.env.OPENVIKING_PENDING_DRAIN_MAX_BATCHES;
      else process.env.OPENVIKING_PENDING_DRAIN_MAX_BATCHES = previous;
    }
  });
});
