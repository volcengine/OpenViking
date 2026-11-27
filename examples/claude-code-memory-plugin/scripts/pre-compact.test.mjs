import assert from "node:assert/strict";
import { appendFile, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
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

const SCRIPTS = dirname(fileURLToPath(import.meta.url));

function hookEnv(root, baseUrl) {
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
  return readFile(join(root, ".openviking", "state", "cc-capture-state", "s.json"), "utf-8")
    .then((raw) => JSON.parse(raw).capturedTurnCount, () => undefined);
}

// Records each pushed turn's text and each commit; batch `n` is answered after `delayFor(n)` ms.
function mockServer(events, delayFor = () => 0) {
  let batches = 0;
  return async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const body = req.method === "POST" ? await readRequestBody(req) : null;
    if (url.pathname.endsWith("/messages/batch")) {
      events.push(...body.messages.map((m) => m.parts.map((p) => p.text).join("")));
      await sleep(delayFor(batches++));
      writeJson(res, { status: "ok", result: { added: body.messages.length } });
    } else {
      if (url.pathname === "/api/v1/sessions/cc-s/commit") events.push("commit");
      writeJson(res, { status: "ok", result: {} });
    }
  };
}

test("PreCompact pushes the turn in progress before it commits", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-"));
  const input = { session_id: "s", transcript_path: join(root, "t.jsonl"), cwd: root, trigger: "auto" };
  const events = [];
  try {
    await writeFile(input.transcript_path, [rec("user", "q1"), rec("assistant", "a1 in progress")].join("\n"));
    await withMockOpenViking(mockServer(events), async (baseUrl) => {
      const off = { ...hookEnv(root, baseUrl), OPENVIKING_AUTO_CAPTURE: "0" };
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), { input, env: off }));
      assert.deepEqual(events, []);
      const result = expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), { input, env: hookEnv(root, baseUrl) }));
      assert.deepEqual(JSON.parse(result.stdout), { decision: "approve" });
      // With autoCommitOnCompact off the catch-up still runs; only the commit is skipped.
      await appendFile(input.transcript_path, `\n${rec("user", "q2")}`);
      const noCommit = { ...hookEnv(root, baseUrl), OPENVIKING_AUTO_COMMIT_ON_COMPACT: "0" };
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), { input, env: noCommit }));
    });
    assert.deepEqual(events, ["q1", "a1 in progress", "commit", "q2"]);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("a catch-up that outlasts the wait finishes in the background; the hook still commits", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-"));
  const input = { session_id: "s", transcript_path: join(root, "t.jsonl"), cwd: root, trigger: "auto" };
  const events = [];
  try {
    await writeFile(input.transcript_path, [rec("user", "q1"), rec("assistant", "a1 in progress")].join("\n"));
    await withMockOpenViking(mockServer(events, () => 1500), async (baseUrl) => {
      const env = { ...hookEnv(root, baseUrl), OPENVIKING_CC_PRECOMPACT_CATCH_UP_WAIT_MS: "300" };
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), { input, env }));
      assert.ok(events.includes("commit"));
      assert.equal(await cursor(root), undefined);
      // The worker was not killed: it saves the cursor once the batch lands.
      await until(async () => (await cursor(root)) === 2);
    });
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("a server slow on /health cannot hold PreCompact past its timeout; the commit is queued", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-"));
  const input = { session_id: "s", transcript_path: join(root, "t.jsonl"), cwd: root, trigger: "auto" };
  try {
    await writeFile(input.transcript_path, [rec("user", "q1"), rec("assistant", "a1")].join("\n"));
    await withMockOpenViking(async (req, res) => {
      if (req.method === "POST") await readRequestBody(req);
      if (req.url.endsWith("/health")) await sleep(6000);
      writeJson(res, { status: "ok", result: {} });
    }, async (baseUrl) => {
      const env = { ...hookEnv(root, baseUrl), OPENVIKING_CC_PRECOMPACT_CATCH_UP_WAIT_MS: "200" };
      const started = Date.now();
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), { input, env }));
      assert.ok(Date.now() - started < 5500, `took ${Date.now() - started}ms`);
      const names = await readdir(join(root, "pending")).catch(() => []);
      const queued = await Promise.all(names.map((name) => readFile(join(root, "pending", name), "utf-8")));
      assert.ok(queued.some((e) => e.includes("commitSession") && e.includes('"cc-s"')), queued.join("\n"));
    });
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

// The Stop worker and the PreCompact catch-up read the same cursor; the
// per-session lock keeps them from pushing the same turns twice.
test("a slow Stop worker neither duplicates the catch-up's turns nor moves the cursor back", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-"));
  const input = { session_id: "s", transcript_path: join(root, "t.jsonl"), cwd: root, trigger: "auto" };
  const sent = [];
  try {
    await writeFile(input.transcript_path, [rec("user", "q1"), rec("assistant", "a1")].join("\n"));
    await withMockOpenViking(mockServer(sent, (n) => (n === 0 ? 2500 : 50)), async (baseUrl) => {
      const stop = { ...input, trigger: undefined };
      expectExit(await runHookScript(join(SCRIPTS, "auto-capture.mjs"), { input: stop, env: hookEnv(root, baseUrl) }));
      await sleep(300);
      await appendFile(input.transcript_path, `\n${[rec("user", "q2"), rec("assistant", "a2 in progress")].join("\n")}`);
      expectExit(await runHookScript(join(SCRIPTS, "pre-compact.mjs"), { input, env: hookEnv(root, baseUrl) }));
      await until(async () => (await cursor(root)) === 4);
      await sleep(500);
      assert.equal(await cursor(root), 4);
      await appendFile(input.transcript_path, `\n${rec("assistant", "a2 done")}`);
      expectExit(await runHookScript(join(SCRIPTS, "auto-capture.mjs"), { input: stop, env: hookEnv(root, baseUrl) }));
      await until(async () => (await cursor(root)) === 5);
    });
    assert.deepEqual(sent.filter((e) => e !== "commit"), ["q1", "a1", "q2", "a2 in progress", "a2 done"]);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("SessionEnd commits after a Stop still posting and after turns no Stop captured", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-session-end-"));
  const input = { session_id: "s", transcript_path: join(root, "t.jsonl"), cwd: root };
  const events = [];
  try {
    await writeFile(input.transcript_path, [rec("user", "q1"), rec("assistant", "a1")].join("\n"));
    await withMockOpenViking(mockServer(events, (n) => (n === 0 ? 1500 : 0)), async (baseUrl) => {
      expectExit(await runHookScript(join(SCRIPTS, "auto-capture.mjs"), { input, env: hookEnv(root, baseUrl) }));
      await sleep(300);
      await appendFile(input.transcript_path, `\n${rec("user", "q2 interrupted")}`);
      const end = { ...input, hook_event_name: "SessionEnd", reason: "prompt_input_exit" };
      expectExit(await runHookScript(join(SCRIPTS, "session-end.mjs"), { input: end, env: hookEnv(root, baseUrl) }));
      await until(async () => events.includes("commit"));
    });
    assert.deepEqual(events, ["q1", "a1", "q2 interrupted", "commit"]);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("SessionEnd run inline commits within the hook timeout while a capture holds the lock", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-session-end-"));
  const input = { session_id: "s", transcript_path: join(root, "t.jsonl"), cwd: root };
  const events = [];
  try {
    await writeFile(input.transcript_path, [rec("user", "q1"), rec("assistant", "a1")].join("\n"));
    await mkdir(join(root, ".openviking", "hook-state", "claude-code", "s.json.lock"), { recursive: true });
    await withMockOpenViking(mockServer(events), async (baseUrl) => {
      const env = { ...hookEnv(root, baseUrl), OPENVIKING_WRITE_PATH_ASYNC: "0", OPENVIKING_CAPTURE_TIMEOUT_MS: "8000" };
      const started = Date.now();
      const end = { ...input, hook_event_name: "SessionEnd", reason: "prompt_input_exit" };
      expectExit(await runHookScript(join(SCRIPTS, "session-end.mjs"), { input: end, env }));
      assert.ok(Date.now() - started < 25000, `took ${Date.now() - started}ms`);
    });
    assert.deepEqual(events, ["commit"]);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});
