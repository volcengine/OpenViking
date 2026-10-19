/**
 * SessionStart (DESIGN.md §3.3): replay a bounded batch of the pending queue so
 * a backlog cannot stall the host's startup, and leave the rest parked.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
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
