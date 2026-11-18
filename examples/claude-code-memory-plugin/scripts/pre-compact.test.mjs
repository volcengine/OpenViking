import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
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

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "pre-compact.mjs");

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
  };
}

async function writeTurn(path) {
  await writeFile(path, [
    JSON.stringify({ type: "user", message: { role: "user", content: "rename the config loader" } }),
    JSON.stringify({ type: "assistant", message: { role: "assistant", content: "Renamed it to loadSettings." } }),
  ].join("\n"));
}

function precompactInput(root, transcriptPath) {
  return { session_id: "compacting", transcript_path: transcriptPath, cwd: root, trigger: "auto" };
}

test("PreCompact pushes the turn in progress before it commits", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const events = [];
  try {
    await writeTurn(transcriptPath);
    const result = await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        events.push(["batch", body.messages.map((m) => m.parts.map((p) => p.text).join(""))]);
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
      } else if (req.method === "POST" && url.pathname === "/api/v1/sessions/cc-compacting/commit") {
        await readRequestBody(req);
        events.push(["commit"]);
        writeJson(res, { status: "ok", result: { status: "accepted" } });
      } else {
        writeJson(res, { status: "ok", result: {} });
      }
    }, (baseUrl) => runHookScript(SCRIPT, {
      input: precompactInput(root, transcriptPath),
      env: hookEnv(root, baseUrl),
    }));

    expectExit(result);
    assert.deepEqual(JSON.parse(result.stdout), { decision: "approve" });
    assert.deepEqual(events, [
      ["batch", ["rename the config loader", "Renamed it to loadSettings."]],
      ["commit"],
    ]);
    // The next Stop starts after what PreCompact already sent.
    const state = JSON.parse(
      await readFile(join(root, "openviking-cc-capture-state", "compacting.json"), "utf-8"),
    );
    assert.equal(state.capturedTurnCount, 2);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("PreCompact still commits and approves when the push fails", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-fail-"));
  const transcriptPath = join(root, "transcript.jsonl");
  let commits = 0;
  try {
    await writeTurn(transcriptPath);
    const result = await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        writeJson(res, { status: "error", error: { code: "INVALID_ARGUMENT", message: "bad" } }, 400);
      } else if (req.method === "POST" && url.pathname.endsWith("/commit")) {
        await readRequestBody(req);
        commits++;
        writeJson(res, { status: "ok", result: {} });
      } else {
        writeJson(res, { status: "ok", result: {} });
      }
    }, (baseUrl) => runHookScript(SCRIPT, {
      input: precompactInput(root, transcriptPath),
      env: hookEnv(root, baseUrl),
    }));

    expectExit(result);
    assert.deepEqual(JSON.parse(result.stdout), { decision: "approve" });
    assert.equal(commits, 1);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("PreCompact with capture disabled pushes and commits nothing", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-off-"));
  const transcriptPath = join(root, "transcript.jsonl");
  try {
    await writeTurn(transcriptPath);
    const { result, requests } = await withMockOpenViking(
      (_req, res) => writeJson(res, { status: "ok", result: {} }),
      async (baseUrl, requests) => ({
        result: await runHookScript(SCRIPT, {
          input: precompactInput(root, transcriptPath),
          env: { ...hookEnv(root, baseUrl), OPENVIKING_AUTO_CAPTURE: "0" },
        }),
        requests,
      }),
    );

    expectExit(result);
    assert.deepEqual(JSON.parse(result.stdout), { decision: "approve" });
    assert.deepEqual(requests.filter((r) => r.method === "POST"), []);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

test("a catch-up that outlasts the wait finishes in the background; the hook still commits", async () => {
  const restore = scrubOpenVikingEnv();
  const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-slow-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const events = [];
  let batchDone;
  const batchFinished = new Promise((resolve) => { batchDone = resolve; });
  try {
    await writeTurn(transcriptPath);
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        events.push("batch-received");
        await new Promise((resolve) => setTimeout(resolve, 1500));
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
        events.push("batch-answered");
        batchDone();
      } else if (req.method === "POST" && url.pathname.endsWith("/commit")) {
        await readRequestBody(req);
        events.push("commit");
        writeJson(res, { status: "ok", result: {} });
      } else {
        writeJson(res, { status: "ok", result: {} });
      }
    }, async (baseUrl) => {
      const result = await runHookScript(SCRIPT, {
        input: precompactInput(root, transcriptPath),
        env: { ...hookEnv(root, baseUrl), OPENVIKING_CC_PRECOMPACT_CATCH_UP_WAIT_MS: "300" },
      });
      expectExit(result);
      assert.deepEqual(JSON.parse(result.stdout), { decision: "approve" });
      // The hook has exited and committed while the push is still unanswered.
      assert.ok(events.includes("commit"));
      assert.ok(!events.includes("batch-answered"));

      await batchFinished;
      // The worker was not killed: it saves the cursor once the batch lands.
      const statePath = join(root, "openviking-cc-capture-state", "compacting.json");
      let state = null;
      for (let i = 0; i < 100 && state?.capturedTurnCount !== 2; i++) {
        await new Promise((resolve) => setTimeout(resolve, 50));
        state = JSON.parse(await readFile(statePath, "utf-8").catch(() => "null"));
      }
      assert.equal(state?.capturedTurnCount, 2);
    });
    assert.deepEqual(events.filter((e) => e !== "batch-received"), ["commit", "batch-answered"]);
  } finally {
    restore();
    await rm(root, { recursive: true, force: true });
  }
});

for (const slow of ["/health", "/commit"]) {
  test(`a server slow on ${slow} cannot hold PreCompact past its timeout; the commit is queued`, async () => {
    const restore = scrubOpenVikingEnv();
    const root = await mkdtemp(join(tmpdir(), "ov-cc-precompact-slowserver-"));
    const transcriptPath = join(root, "transcript.jsonl");
    try {
      await writeTurn(transcriptPath);
      const { result, elapsed } = await withMockOpenViking(async (req, res) => {
        const url = new URL(req.url, "http://127.0.0.1");
        if (req.method === "POST") await readRequestBody(req);
        if (url.pathname.endsWith(slow)) await new Promise((resolve) => setTimeout(resolve, 6000));
        writeJson(res, { status: "ok", result: {} });
      }, async (baseUrl) => {
        const started = Date.now();
        const run = await runHookScript(SCRIPT, {
          input: precompactInput(root, transcriptPath),
          env: {
            ...hookEnv(root, baseUrl),
            OPENVIKING_CC_PRECOMPACT_CATCH_UP_WAIT_MS: "200",
            OPENVIKING_CAPTURE_TIMEOUT_MS: "1000",
          },
        });
        return { result: run, elapsed: Date.now() - started };
      });

      expectExit(result);
      assert.deepEqual(JSON.parse(result.stdout), { decision: "approve" });
      // 200ms wait + one 4s request, well short of the 6s the server takes.
      assert.ok(elapsed < 5500, `took ${elapsed}ms`);
      const pending = await readdir(join(root, "pending")).catch(() => []);
      const queued = await Promise.all(pending.map((name) => readFile(join(root, "pending", name), "utf-8")));
      assert.ok(queued.some((entry) => entry.includes("commitSession") && entry.includes("cc-compacting")), queued.join("\n"));
    } finally {
      restore();
      await rm(root, { recursive: true, force: true });
    }
  });
}
