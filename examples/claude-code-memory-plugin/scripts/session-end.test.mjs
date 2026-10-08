import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  readRequestBody,
  withMockOpenViking,
  withPendingDir,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";
import { enqueue, listPending } from "../../memory-plugin-shared/lib/pending-queue.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function runHook(script, input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, script)], {
      env: { ...cleanEnv, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`${script} exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function hookEnv(root, baseUrl, pendingDir) {
  return {
    HOME: root,
    TMPDIR: root,
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_AUTO_CAPTURE: "1",
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_WRITE_PATH_ASYNC: "0",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    OPENVIKING_PENDING_DIR: pendingDir,
  };
}

test("session-end parks the commit behind pending writes for its own session", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-session-end-pending-"));
  try {
    await withPendingDir(async (pendingDir) => {
      const sessionId = "endsess-pending";
      const ovSessionId = `cc-${sessionId}`;
      // A Stop-hook write that failed while the server was reachable: parked
      // in the queue, so SessionEnd must not commit before it is replayed.
      await enqueue("addMessage", ovSessionId, { role: "user", content: "queued earlier" }, {
        createdAt: Date.now() - 60_000,
      });

      let sawCommit = false;
      await withMockOpenViking(async (req, res) => {
        const url = new URL(req.url, "http://127.0.0.1");
        if (req.method === "POST" && url.pathname.endsWith("/commit")) sawCommit = true;
        writeJson(res, { status: "ok" });
      }, async (baseUrl) => {
        await runHook(
          "session-end.mjs",
          { session_id: sessionId, hook_event_name: "SessionEnd" },
          hookEnv(root, baseUrl, pendingDir),
        );
      });

      assert.equal(sawCommit, false, "session-end committed while writes were still pending");
      const pending = await listPending();
      const commit = pending.find(({ entry }) => entry.type === "commitSession");
      assert.ok(commit, "expected the commit to be parked in the queue");
      assert.equal(commit.entry.sessionId, ovSessionId);
      assert.ok(
        pending.some(({ entry }) => entry.type === "addMessage" && entry.sessionId === ovSessionId),
        "the pending write must stay queued ahead of the commit",
      );
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("session-end still commits directly when the queue holds no writes for the session", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-session-end-direct-"));
  try {
    await withPendingDir(async (pendingDir) => {
      const sessionId = "endsess-direct";
      const ovSessionId = `cc-${sessionId}`;

      let commitBody = null;
      await withMockOpenViking(async (req, res) => {
        const url = new URL(req.url, "http://127.0.0.1");
        if (req.method === "GET" && url.pathname === "/health") {
          writeJson(res, { status: "ok", result: { healthy: true } });
          return;
        }
        if (req.method === "POST" && url.pathname === `/api/v1/sessions/${ovSessionId}/commit`) {
          commitBody = await readRequestBody(req);
          writeJson(res, { status: "ok", result: { status: "accepted" } });
          return;
        }
        writeJson(res, { status: "ok" });
      }, async (baseUrl) => {
        await runHook(
          "session-end.mjs",
          { session_id: sessionId, hook_event_name: "SessionEnd" },
          hookEnv(root, baseUrl, pendingDir),
        );
      });

      assert.ok(commitBody !== null, "expected a direct commit from session-end");
      assert.deepEqual(await listPending(), []);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
