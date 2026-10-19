/**
 * SessionStart (DESIGN.md §3.3): replay a bounded batch of the pending queue so
 * a backlog cannot stall the host's startup, and leave the rest parked.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { enqueue, listPending } from "./shared/pending-queue.mjs";
import { runHookScript, scrubOpenVikingEnv, withMockOpenViking, writeJson } from "../../memory-plugin-shared/testing/support.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

test("the replay stops at its bound and leaves the rest parked", async () => {
  const restore = scrubOpenVikingEnv();
  const root = mkdtempSync(join(tmpdir(), "kimicode-replay-"));
  const previousPending = process.env.OPENVIKING_PENDING_DIR;
  process.env.OPENVIKING_PENDING_DIR = join(root, "pending");
  const seen = [];
  try {
    for (let i = 0; i < 5; i += 1) {
      const queued = await enqueue("addMessage", "kc-many", { role: "user", content: `parked-${i}` }, {
        createdAt: Date.now() + i,
      });
      assert.equal(queued.ok, true);
    }

    await withMockOpenViking(
      async (req, res) => {
        const path = new URL(req.url, "http://127.0.0.1").pathname;
        if (path === "/health") {
          writeJson(res, { status: "ok", result: { ok: true } });
          return;
        }
        seen.push(path);
        writeJson(res, { status: "ok", result: { ok: true } });
      },
      async (base) => {
        const result = await runHookScript(join(SCRIPT_DIR, "session-start.mjs"), {
          input: JSON.stringify({ session_id: "s1", cwd: root }),
          env: {
            KIMI_CODE_HOME: join(root, "kimi-home"),
            OPENVIKING_KIMICODE_STATE_DIR: join(root, "state"),
            OPENVIKING_URL: base,
          },
        });
        assert.equal(result.code, 0, result.stderr);
      },
    );

    const messageCalls = seen.filter((path) => path.endsWith("/messages"));
    assert.ok(messageCalls.length <= 2, `expected at most 2 replays, saw ${messageCalls.length}`);
    assert.ok(messageCalls.length >= 1, "the queue should have been drained at least once");
    const remaining = await listPending();
    assert.ok(remaining.length >= 3, `expected the backlog to stay parked, ${remaining.length} left`);
  } finally {
    if (previousPending === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previousPending;
    rmSync(root, { recursive: true, force: true });
    restore();
  }
});

test("a replayed message is acknowledged so the next capture does not resend it", async () => {
  const restore = scrubOpenVikingEnv();
  const root = mkdtempSync(join(tmpdir(), "kimicode-replay-ack-"));
  const previousPending = process.env.OPENVIKING_PENDING_DIR;
  process.env.OPENVIKING_PENDING_DIR = join(root, "pending");
  try {
    // Queued by a hook whose send failed. `kc-s1` is the OpenViking session id
    // deriveOvSessionId gives for Kimi session `s1`.
    const queued = await enqueue(
      "addMessage",
      "kc-s1",
      { role: "assistant", content: "delivered late", turn_id: "42" },
      { createdAt: Date.now() },
    );
    assert.equal(queued.ok, true);

    await withMockOpenViking(
      async (req, res) => {
        writeJson(res, { status: "ok", result: { ok: true } });
        void req;
      },
      async (base) => {
        const result = await runHookScript(join(SCRIPT_DIR, "session-start.mjs"), {
          input: JSON.stringify({ session_id: "s1", cwd: root }),
          env: {
            KIMI_CODE_HOME: join(root, "kimi-home"),
            OPENVIKING_KIMICODE_STATE_DIR: join(root, "state"),
            OPENVIKING_URL: base,
          },
        });
        assert.equal(result.code, 0, result.stderr);
      },
    );

    assert.deepEqual(await listPending(), [], "the queued message should have been replayed");
    // Without this ack the next Stop would plan the same turn again, and the
    // server — which has no turn-id idempotency — would store it twice.
    const state = JSON.parse(readFileSync(join(root, "state", "s1.json"), "utf-8"));
    assert.ok(
      state.capturedTurnIds.includes("42:assistant"),
      `expected the delivered turn to be acknowledged, got ${JSON.stringify(state.capturedTurnIds)}`,
    );
  } finally {
    if (previousPending === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previousPending;
    rmSync(root, { recursive: true, force: true });
    restore();
  }
});

test("a delivery for another session never moves this session's cursor", async () => {
  const restore = scrubOpenVikingEnv();
  const root = mkdtempSync(join(tmpdir(), "kimicode-replay-other-"));
  const previousPending = process.env.OPENVIKING_PENDING_DIR;
  process.env.OPENVIKING_PENDING_DIR = join(root, "pending");
  try {
    const queued = await enqueue(
      "addMessage",
      "kc-someone-else",
      { role: "assistant", content: "not ours", turn_id: "42" },
      { createdAt: Date.now() },
    );
    assert.equal(queued.ok, true);

    await withMockOpenViking(
      async (req, res) => {
        writeJson(res, { status: "ok", result: { ok: true } });
        void req;
      },
      async (base) => {
        const result = await runHookScript(join(SCRIPT_DIR, "session-start.mjs"), {
          input: JSON.stringify({ session_id: "s1", cwd: root }),
          env: {
            KIMI_CODE_HOME: join(root, "kimi-home"),
            OPENVIKING_KIMICODE_STATE_DIR: join(root, "state"),
            OPENVIKING_URL: base,
          },
        });
        assert.equal(result.code, 0, result.stderr);
      },
    );

    assert.deepEqual(await listPending(), [], "the other session's entry still gets replayed");
    let state = null;
    try {
      state = JSON.parse(readFileSync(join(root, "state", "s1.json"), "utf-8"));
    } catch {
      // No state file is the expected outcome: nothing was acknowledged.
    }
    assert.deepEqual(state?.capturedTurnIds ?? [], []);
  } finally {
    if (previousPending === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previousPending;
    rmSync(root, { recursive: true, force: true });
    restore();
  }
});
