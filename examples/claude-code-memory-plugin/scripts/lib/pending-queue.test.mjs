import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import {
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import { addMessage } from "./ov-session.mjs";
import {
  claimForReplay,
  createQueueScope,
  enqueue,
  listPending,
  replayPending,
} from "./pending-queue.mjs";

const originalEnv = {
  dir: process.env.OPENVIKING_PENDING_DIR,
  keyFile: process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE,
  maxRetries: process.env.OPENVIKING_PENDING_MAX_RETRIES,
  replayLimit: process.env.OPENVIKING_PENDING_REPLAY_LIMIT,
  ttlDays: process.env.OPENVIKING_PENDING_TTL_DAYS,
};
const execFileAsync = promisify(execFile);

async function withPendingDir(fn) {
  const dir = await mkdtemp(join(tmpdir(), "openviking-pending-test-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE = join(dir, "scope.key");
  delete process.env.OPENVIKING_PENDING_MAX_RETRIES;
  delete process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
  delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  try {
    const scope = await createQueueScope({
      producer: "claude-code",
      baseUrl: "https://ov.example.test",
      apiKey: "test-key",
    });
    return await fn(dir, scope);
  } finally {
    if (originalEnv.dir === undefined)
      delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = originalEnv.dir;
    if (originalEnv.keyFile === undefined)
      delete process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE;
    else process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE = originalEnv.keyFile;
    if (originalEnv.maxRetries === undefined)
      delete process.env.OPENVIKING_PENDING_MAX_RETRIES;
    else process.env.OPENVIKING_PENDING_MAX_RETRIES = originalEnv.maxRetries;
    if (originalEnv.replayLimit === undefined)
      delete process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
    else process.env.OPENVIKING_PENDING_REPLAY_LIMIT = originalEnv.replayLimit;
    if (originalEnv.ttlDays === undefined)
      delete process.env.OPENVIKING_PENDING_TTL_DAYS;
    else process.env.OPENVIKING_PENDING_TTL_DAYS = originalEnv.ttlDays;
    await rm(dir, { recursive: true, force: true });
  }
}

function queueAwareFetch(scope, implementation) {
  implementation.queueScope = () => Promise.resolve(scope);
  return implementation;
}

test("addMessage queues retryable failures", async () => {
  await withPendingDir(async (_dir, scope) => {
    const payload = { role: "user", content: "remember this" };
    const res = await addMessage(
      queueAwareFetch(scope, async () => ({
        ok: false,
        status: 503,
        error: { message: "unavailable" },
      })),
      "cc-test-session",
      payload,
    );

    assert.equal(res.ok, false);
    assert.equal(res.pendingQueued, true);

    const pending = await listPending(scope);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.type, "addMessage");
    assert.equal(pending[0].entry.sessionId, "cc-test-session");
    assert.deepEqual(pending[0].entry.payload, payload);
  });
});

test("addMessage does not queue non-retryable client failures", async () => {
  await withPendingDir(async (_dir, scope) => {
    for (const status of [401, 403, 404, 422]) {
      const res = await addMessage(
        queueAwareFetch(scope, async () => ({
          ok: false,
          status,
          error: { message: `HTTP ${status}` },
        })),
        `cc-client-error-${status}`,
        { role: "user", content: `bad request ${status}` },
      );

      assert.equal(res.ok, false);
      assert.equal(res.pendingQueued, undefined);
      assert.equal(res.pendingEnqueueFailed, undefined);
    }

    assert.deepEqual(await listPending(scope), []);
  });
});

test("replayPending sends queued entries and removes them after success", async () => {
  await withPendingDir(async (_dir, scope) => {
    const payload = { role: "assistant", content: "queued response" };
    await enqueue(scope, "addMessage", "cc-replay", payload);

    const calls = [];
    const result = await replayPending(
      scope,
      async (path, init) => {
        calls.push({ path, init });
        return { ok: true };
      },
      () => {},
    );

    assert.deepEqual(result, {
      replayed: 1,
      failed: 0,
      skipped: 0,
      deferred: 0,
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/v1/sessions/cc-replay/messages");
    assert.deepEqual(JSON.parse(calls[0].init.body), payload);
    assert.deepEqual(await listPending(scope), []);
  });
});

test("enqueue deduplicates identical payloads", async () => {
  await withPendingDir(async (_dir, scope) => {
    const payload = { role: "user", parts: [{ type: "text", text: "same" }] };
    const first = await enqueue(scope, "addMessage", "cc-dedup", payload);
    const second = await enqueue(scope, "addMessage", "cc-dedup", payload);

    assert.equal(first.ok, true);
    assert.equal(second.ok, true);
    assert.equal(second.deduped, true);
    assert.equal((await listPending(scope)).length, 1);
  });
});

test("enqueue preserves operation identity and promotes manual provenance", async () => {
  await withPendingDir(async (_dir, scope) => {
    const payload = {
      role: "user",
      content: "same operation across an upgrade",
    };
    const legacy = await enqueue(scope, "addMessage", "cc-upgrade", payload);

    const automatic = await enqueue(
      scope,
      "addMessage",
      "cc-upgrade",
      payload,
      { provenance: "autoCapture" },
    );
    assert.equal(automatic.deduped, true);
    assert.equal(automatic.dedupKey, legacy.dedupKey);
    let pending = await listPending(scope);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.provenance, undefined);

    const manual = await enqueue(scope, "addMessage", "cc-upgrade", payload, {
      provenance: "manual",
    });
    assert.equal(manual.deduped, true);
    pending = await listPending(scope);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.provenance, "manual");
  });
});

test("replayPending honors the per-run replay limit", async () => {
  await withPendingDir(async (_dir, scope) => {
    process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "1";
    await enqueue(scope, "addMessage", "cc-limit", {
      role: "user",
      content: "one",
    });
    await enqueue(scope, "addMessage", "cc-limit", {
      role: "user",
      content: "two",
    });

    const calls = [];
    const result = await replayPending(
      scope,
      async (path, init) => {
        calls.push({ path, init });
        return { ok: true };
      },
      () => {},
    );

    assert.equal(result.replayed, 1);
    assert.equal(result.deferred, 1);
    assert.equal(calls.length, 1);
    assert.equal((await listPending(scope)).length, 1);
  });
});

test("claimForReplay atomically claims a file only once", async () => {
  await withPendingDir(async (_dir, scope) => {
    await enqueue(scope, "commitSession", "cc-claim", {});
    const [{ filename }] = await listPending(scope);

    const firstClaim = await claimForReplay(scope, filename);
    const secondClaim = await claimForReplay(scope, filename);

    assert.match(firstClaim, /\.processing$/);
    assert.equal(secondClaim, null);
    assert.deepEqual(await readdir(scope.dir), [firstClaim]);
  });
});

test("queue scopes isolate producers, targets, and legacy unscoped files", async () => {
  await withPendingDir(async (dir, scope) => {
    const otherProducer = await createQueueScope({
      producer: "opencode",
      baseUrl: "https://ov.example.test",
      apiKey: "test-key",
    });
    const otherTarget = await createQueueScope({
      producer: "claude-code",
      baseUrl: "https://other.example.test",
      apiKey: "other-key",
    });
    await enqueue(scope, "addMessage", "cc-isolated", {
      role: "user",
      content: "only here",
    });
    await writeFile(
      join(dir, "legacy_0.json"),
      JSON.stringify({ type: "addMessage" }),
      { mode: 0o600 },
    );

    assert.equal((await listPending(scope)).length, 1);
    assert.deepEqual(await listPending(otherProducer), []);
    assert.deepEqual(await listPending(otherTarget), []);
    assert.notEqual(scope.dir, otherProducer.dir);
    assert.notEqual(scope.dir, otherTarget.dir);
  });
});

test("manual promotion is durable under concurrent enqueue and removed at terminal replay", async () => {
  await withPendingDir(async (_dir, scope) => {
    const payload = { role: "user", content: "same operation" };
    const results = await Promise.all(
      Array.from({ length: 200 }, (_, index) =>
        enqueue(scope, "addMessage", "cc-concurrent", payload, {
          provenance: index % 2 ? "manual" : "autoCapture",
        }),
      ),
    );
    assert.equal(
      results.every((result) => result.ok),
      true,
    );
    const pending = await listPending(scope);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.provenance, "manual");

    const replay = await replayPending(
      scope,
      async () => ({ ok: true }),
      () => {},
    );
    assert.equal(replay.replayed, 1);
    assert.deepEqual(await readdir(scope.dir), []);
  });
});

test("scope key is stable, private, and raw target credentials never enter queue paths", async () => {
  await withPendingDir(async (dir, scope) => {
    const keyPath = process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE;
    const firstKey = await readFile(keyPath);
    const firstMode = (await stat(keyPath)).mode;
    const again = await createQueueScope({
      producer: "claude-code",
      baseUrl: "https://ov.example.test",
      apiKey: "test-key",
    });
    assert.deepEqual(await readFile(keyPath), firstKey);
    assert.equal(again.dir, scope.dir);
    if (process.platform !== "win32") assert.equal(firstMode & 0o077, 0);

    await enqueue(scope, "addMessage", "credential-check", {
      role: "user",
      content: "safe payload",
    });
    const paths = await readdir(dir, { recursive: true });
    assert.equal(
      paths.some(
        (path) =>
          String(path).includes("test-key") ||
          String(path).includes("ov.example.test"),
      ),
      false,
    );
  });
});

test("deduplication and manual promotion are safe across independent processes", async () => {
  await withPendingDir(async (_dir, scope) => {
    const moduleURL = new URL("./pending-queue.mjs", import.meta.url).href;
    const child = String.raw`
      const [moduleURL, provenance] = process.argv.slice(1);
      const { createQueueScope, enqueue } = await import(moduleURL);
      const scope = await createQueueScope({ producer: "claude-code", baseUrl: "https://ov.example.test", apiKey: "test-key" });
      const result = await enqueue(scope, "addMessage", "cc-multiprocess", { role: "user", content: "same operation" }, { provenance });
      if (!result.ok) throw new Error(result.error || "enqueue failed");
    `;
    await Promise.all(
      Array.from({ length: 16 }, (_, index) =>
        execFileAsync(
          process.execPath,
          [
            "--input-type=module",
            "--eval",
            child,
            moduleURL,
            index % 2 ? "manual" : "autoCapture",
          ],
          { env: process.env },
        ),
      ),
    );

    const pending = await listPending(scope);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.provenance, "manual");
  });
});
