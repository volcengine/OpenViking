import assert from "node:assert/strict";
import { appendFile, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  expectExit,
  readRequestBody,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

// The Stop worker of one turn and the PreCompact catch-up that follows it read
// the same cursor; without the per-session lock both pushed the same turns, and
// the older one wrote its cursor back over the newer one's.

const SCRIPTS = dirname(fileURLToPath(import.meta.url));

function env(root, baseUrl) {
  return {
    HOME: root,
    TMPDIR: root,
    OPENVIKING_AUTO_CAPTURE: "1",
    OPENVIKING_CAPTURE_ASSISTANT_TURNS: "1",
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_PENDING_DIR: join(root, "pending"),
    OPENVIKING_WRITE_PATH_ASYNC: "1",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    OPENVIKING_CAPTURE_LAST_ASSISTANT_ON_STOP: "0",
  };
}

const rec = (role, content) => JSON.stringify({ type: role, message: { role, content } });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check, ms = 10000) {
  const deadline = Date.now() + ms;
  while (!(await check())) {
    assert.ok(Date.now() < deadline, "timed out waiting");
    await sleep(50);
  }
}

function cursor(root) {
  return readFile(join(root, "openviking-cc-capture-state", "race.json"), "utf-8")
    .then((raw) => JSON.parse(raw).capturedTurnCount, () => undefined);
}

function mockServer(sent, delayFor) {
  let batches = 0;
  return async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
      const body = await readRequestBody(req);
      sent.push(...body.messages.map((m) => m.parts.map((p) => p.text).join("")));
      await sleep(delayFor(batches++));
      writeJson(res, { status: "ok", result: { added: body.messages.length } });
    } else {
      if (req.method === "POST") await readRequestBody(req);
      writeJson(res, { status: "ok", result: {} });
    }
  };
}

test("/compact while the Stop worker is still pushing sends each turn once", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-race-"));
  const transcriptPath = join(root, "t.jsonl");
  const sent = [];
  try {
    await writeFile(transcriptPath, [rec("user", "q1"), rec("assistant", "a1")].join("\n"));
    await withMockOpenViking(mockServer(sent, () => 800), async (baseUrl) => {
      const input = { session_id: "race", transcript_path: transcriptPath, cwd: root };
      expectExit(await runHookScript(join(SCRIPTS, "auto-capture.mjs"), { input, env: env(root, baseUrl) }));
      await sleep(300);
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), {
        input: { ...input, trigger: "manual" },
        env: env(root, baseUrl),
      }));
      await until(async () => (await cursor(root)) === 2);
      await sleep(500);
    });
    assert.deepEqual(sent, ["q1", "a1"]);
    assert.equal(await cursor(root), 2);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("a slow Stop worker neither duplicates the catch-up's turns nor moves the cursor back", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-race2-"));
  const transcriptPath = join(root, "t.jsonl");
  const sent = [];
  try {
    await writeFile(transcriptPath, [rec("user", "q1"), rec("assistant", "a1")].join("\n"));
    await withMockOpenViking(mockServer(sent, (n) => (n === 0 ? 2500 : 50)), async (baseUrl) => {
      const input = { session_id: "race", transcript_path: transcriptPath, cwd: root };
      expectExit(await runHookScript(join(SCRIPTS, "auto-capture.mjs"), { input, env: env(root, baseUrl) }));
      await sleep(300);
      // The next turn starts and auto-compacts mid-way.
      await appendFile(transcriptPath, `\n${[rec("user", "q2"), rec("assistant", "a2 in progress")].join("\n")}`);
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), {
        input: { ...input, trigger: "auto" },
        env: env(root, baseUrl),
      }));
      await until(async () => (await cursor(root)) === 4);
      await sleep(500);
      assert.equal(await cursor(root), 4);

      await appendFile(transcriptPath, `\n${rec("assistant", "a2 done")}`);
      expectExit(await runHookScript(join(SCRIPTS, "auto-capture.mjs"), { input, env: env(root, baseUrl) }));
      await until(async () => (await cursor(root)) === 5);
    });
    assert.deepEqual(sent, ["q1", "a1", "q2", "a2 in progress", "a2 done"]);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});
