import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
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
