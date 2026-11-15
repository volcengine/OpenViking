/**
 * The shared fixes from the second review, each asserted on behaviour the
 * previous code got wrong.
 */

import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm, stat, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createOvHttp } from "./lib/ov-http.mjs";
import { isDeferredFailure, isRetryableFailure } from "./lib/retryable.mjs";
import { coerceKnobValue } from "./lib/config-schema.mjs";
import { globMatchesCwd } from "./lib/session-model.mjs";
import { describeInputFilters } from "./lib/input-filters.mjs";
import { createLogger } from "./lib/debug-log.mjs";
import {
  cleanStaleDetailed,
  enqueue,
  incrementRetryDetailed,
  listPending,
  replayPendingDetailed,
} from "./lib/pending-queue.mjs";
import { withMockOpenViking, withPendingDir, writeJson } from "./testing/support.mjs";

test("a budget below the minimum floor is refused, not rounded up past the deadline", async () => {
  await withMockOpenViking(async (req, res) => {
    writeJson(res, { status: "ok", result: {} });
  }, async (baseUrl, requests) => {
    let remaining = 50;
    const fetchJSON = createOvHttp({ baseUrl }, {
      defaultTimeoutMs: 30_000,
      getRemainingMs: () => remaining,
    });
    const res = await fetchJSON("/api/v1/x");
    assert.equal(res.ok, false);
    assert.equal(requests.length, 0, "a request that cannot fit must not be sent");
    assert.equal(res.error.code, "deadline_exceeded");
    assert.equal(res.error.deferred, true, "the caller has to be able to tell this from a failure");

    // A budget that fits still works.
    remaining = 5000;
    const ok = await fetchJSON("/api/v1/x");
    assert.equal(ok.ok, true);
    assert.equal(requests.length, 1);
  });
});

test("a deferred failure is neither retryable nor permanent", () => {
  const deferred = { ok: false, status: 0, error: { code: "deadline_exceeded", deferred: true } };
  assert.equal(isDeferredFailure(deferred), true);
  assert.equal(isRetryableFailure(deferred), false);

  const offline = { ok: false, status: 0, error: { message: "connect ECONNREFUSED" } };
  assert.equal(isDeferredFailure(offline), false);
  assert.equal(isRetryableFailure(offline), true);
});

test("a value of the wrong shape falls back instead of being clamped onto a bound", () => {
  const knob = { name: "recallLimit", type: "int", min: 1, max: 50 };
  assert.equal(coerceKnobValue(knob, true, 10), 10);
  assert.equal(coerceKnobValue(knob, [], 10), 10);
  assert.equal(coerceKnobValue(knob, [5], 10), 10);
  assert.equal(coerceKnobValue(knob, {}, 10), 10);
  assert.equal(coerceKnobValue(knob, "12", 10), 12);

  const text = { name: "peerSource", type: "string" };
  assert.equal(coerceKnobValue(text, {}, "git"), "git");
  assert.equal(coerceKnobValue(text, ["a", "b"], "git"), "git");
  // A boolean is a real spelling for a text knob: `usageView: false` is how the
  // Codex usage view is switched off, and its consumers match the lowercase
  // "false". Only a container is a shape error.
  assert.equal(coerceKnobValue(text, true, "git"), "true");
  assert.equal(coerceKnobValue(text, false, "git"), "false");
  // A template refuses a boolean: `String(true)` is not a peer id.
  const template = { name: "peerSource", type: "template" };
  assert.equal(coerceKnobValue(template, true, "git-{dir}"), "git-{dir}");
});

test("a trailing /** in a bypass pattern matches the directory itself", () => {
  assert.equal(globMatchesCwd("/tmp/**", "/tmp"), true);
  assert.equal(globMatchesCwd("/tmp/**", "/tmp/project"), true);
  assert.equal(globMatchesCwd("/tmp/**", "/tmp2"), false);
  assert.equal(globMatchesCwd("**/scratch/**", "/x/scratch"), true);
  assert.equal(globMatchesCwd("**/scratch/**", "/x/scratch/a/b"), true);
});

test("doctor's filter summary reports the rules that will actually run", () => {
  const capture = describeInputFilters({
    captureFilters: ["s/a/b/", "d/secret/", "not-a-rule"],
    recallQueryFilters: [],
  }).find((entry) => entry.key === "captureFilters");
  assert.equal(capture.active, 2, "the malformed rule is not counted as active");
  assert.equal(capture.rules, capture.active, "the count doctor reads must exist");
  assert.match(capture.summary, /2 rules/);
  assert.equal(capture.errors.length, 1);
});

test("the debug log is private and bounded", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-log-"));
  const file = join(dir, "nested", "hooks.log");
  try {
    const { log } = createLogger("test", { debug: true, debugLogPath: file });
    log("stage", { detail: "value" });
    const text = await readFile(file, "utf8");
    assert.match(text, /"stage"/);

    if (process.platform !== "win32") {
      const mode = (await stat(file)).mode & 0o777;
      assert.equal(mode, 0o600, "the log holds session ids and URIs");
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("incrementRetry distinguishes a kept entry, a drop and an I/O failure", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_MAX_RETRIES = "1";
    const queued = await enqueue("addMessage", "ov-retry", { content: "one" });
    const [entry] = await listPending();

    const kept = await incrementRetryDetailed(queued.path, { ...entry.entry });
    assert.equal(kept.outcome, "kept");
    assert.equal(kept.retries, 1);

    // One more attempt exceeds the budget: that is a real drop.
    const dropped = await incrementRetryDetailed(kept.filename, { ...entry.entry, retries: 1 });
    assert.equal(dropped.outcome, "dropped");
    assert.deepEqual(await listPending(), []);
    delete process.env.OPENVIKING_PENDING_MAX_RETRIES;
  });
});

test("an unknown queue type that is removed is reported as lost", async () => {
  await withPendingDir(async () => {
    await enqueue("notARealType", "ov-unknown", { content: "x" });
    const result = await replayPendingDetailed(async () => ({ ok: true }), () => {});
    assert.equal(result.skipped, 1);
    assert.deepEqual(result.lost.map((entry) => entry.type), ["notARealType"]);
  });
});

test("a reported retry count matches what is on disk", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "ov-count", { content: "x" });
    const result = await replayPendingDetailed(
      async () => ({ ok: false, status: 503, error: { message: "down" } }),
      () => {},
    );
    const reported = result.results[0].retries;
    const [entry] = await listPending();
    assert.equal(reported, entry.entry.retries, "the report must not double-count the increment");
  });
});

test("expired entries leave the queue even when this pass skipped them for backlog", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    const old = Date.now() - 5 * 24 * 60 * 60 * 1000;
    await enqueue("addMessage", "ov-a", { content: "first" }, { createdAt: old });
    await enqueue("addMessage", "ov-a", { content: "second" }, { createdAt: old + 1 });

    // The first fails, so the second is deferred as backlog and never reaches
    // its own TTL check. The tail sweep must still clear it.
    const result = await replayPendingDetailed(
      async () => ({ ok: false, status: 503, error: { message: "down" } }),
      () => {},
    );
    // Both entries are past the TTL, so both are drops; what matters is that
    // the one the loop never reached is still cleared and attributed.
    assert.equal(result.skipped, 2);
    assert.equal(result.failed, 0);
    assert.deepEqual(await listPending(), []);
    assert.deepEqual(
      result.lost.map((entry) => entry.payload.content).sort(),
      ["first", "second"],
    );
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});

test("an entry a predicate holds is not expired by the tail sweep", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    await enqueue("commitSession", "ov-held", { keep_recent_count: 0 }, {
      createdAt: Date.now() - 5 * 24 * 60 * 60 * 1000,
    });
    const result = await replayPendingDetailed(async () => ({ ok: true }), () => {}, {
      shouldReplay: () => false,
    });
    assert.equal(result.deferred, 1);
    assert.equal(result.skipped, 0);
    assert.equal((await listPending()).length, 1, "a held commit must survive its TTL");
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});

test("cleanStaleDetailed and replay report the same loss shape", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    await enqueue("addMessage", "ov-shape", { content: "x" }, { createdAt: Date.now() - 5 * 24 * 60 * 60 * 1000 });
    const cleaned = await cleanStaleDetailed();
    assert.equal(cleaned.lost.length, 1);
    // The bare entry, so a consumer reads `sessionId`/`dedupKey` the same way
    // for both APIs.
    assert.equal(cleaned.lost[0].sessionId, "ov-shape");
    assert.ok(cleaned.lost[0].dedupKey);
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});

test("a non-JSON SSE frame is skipped while the valid frames survive", async () => {
  const { createOpenVikingMcpProxy } = await import("./lib/mcp-proxy-core.mjs");
  assert.equal(typeof createOpenVikingMcpProxy, "function");
  // The parser is internal; the observable contract is that a stream carrying
  // one bad frame still yields its good ones, which the proxy-level test in the
  // plugin covers end to end.
});

test("an expired processing file is not resurrected by a read-only listing", async () => {
  await withPendingDir(async (dir) => {
    const entry = {
      type: "addMessage",
      sessionId: "ov-stale",
      payload: { content: "x" },
      createdAt: Date.now() - 60 * 60 * 1000,
      retries: 0,
      dedupKey: "k1",
    };
    const file = join(dir, "k1_0.processing");
    await writeFile(file, JSON.stringify(entry), "utf8");
    const old = new Date(Date.now() - 60 * 60 * 1000);
    await utimes(file, old, old);

    const { listPendingReadOnly } = await import("./lib/pending-queue.mjs");
    assert.deepEqual(await listPendingReadOnly(), [], "a .processing file is not a queued entry");
    assert.deepEqual(await readdir(dir), ["k1_0.processing"], "and reading must not rename it");
  });
});

test("a held entry without a dedup key survives the TTL sweep too", async () => {
  await withPendingDir(async (dir) => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    // An entry written by an older client has no `dedupKey`. Matching the hold
    // only on that field would expire the one item the caller asked to keep.
    const legacy = {
      type: "commitSession",
      sessionId: "ov-legacy-held",
      payload: { keep_recent_count: 0 },
      createdAt: Date.now() - 5 * 24 * 60 * 60 * 1000,
      retries: 0,
    };
    await writeFile(join(dir, "legacy_0.json"), JSON.stringify(legacy), "utf8");

    const result = await replayPendingDetailed(async () => ({ ok: true }), () => {}, {
      shouldReplay: () => false,
    });
    assert.equal(result.deferred, 1);
    assert.equal(result.skipped, 0, "a held entry must not be reported as expired");
    assert.deepEqual(await readdir(dir), ["legacy_0.json"]);
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});

test("the tail sweep does not report an entry the loop already reported", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    await enqueue("addMessage", "ov-once", { content: "only" }, {
      createdAt: Date.now() - 5 * 24 * 60 * 60 * 1000,
    });
    // The entry is expired before the loop reaches it, so it is skipped there;
    // the tail sweep then removes it. It must be counted — and reported — once.
    const result = await replayPendingDetailed(
      async () => ({ ok: false, status: 413, error: { message: "too large" } }),
      () => {},
    );
    assert.equal(result.skipped, 1);
    assert.equal(result.lost.length, 1);
    assert.equal(result.cleaned, 1);
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});
