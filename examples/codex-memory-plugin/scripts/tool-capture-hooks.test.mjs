import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  expectExit, readRequestBody, runHookScript, scrubOpenVikingEnv,
  withMockOpenViking, writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const restoreEnv = scrubOpenVikingEnv();
test.after(restoreEnv);
const scripts = fileURLToPath(new URL("./", import.meta.url));
const marker = "HOOK_PRIVATE_TOOL_MARKER";

function prose(role, text) {
  return { type: "response_item", payload: { type: "message", role, content: [{ type: "text", text }] } };
}

function tools(id) {
  return [
    { type: "response_item", payload: { type: "custom_tool_call", call_id: id, name: "exec", input: marker } },
    { type: "response_item", payload: { type: "custom_tool_call_output", call_id: id, output: marker } },
  ];
}

async function writeRollout(path, entries) {
  await writeFile(path, entries.map((entry) => JSON.stringify(entry)).join("\n"));
}

function workerEnv(dir, baseUrl) {
  return {
    OPENVIKING_HOME: join(dir, "home"), OPENVIKING_CODEX_STATE_DIR: dir,
    OPENVIKING_CONFIG_FILE: join(dir, "absent-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(dir, "absent-ovcli.conf"),
    OPENVIKING_CREDENTIAL_SOURCE: "env", OPENVIKING_URL: baseUrl,
    OPENVIKING_AUTO_CAPTURE: "true", OPENVIKING_CAPTURE_ASSISTANT_TURNS: "true",
    OPENVIKING_CAPTURE_TOOL_TRAFFIC: "false", OPENVIKING_WRITE_PATH_ASYNC: "false",
    OPENVIKING_COMMIT_TOKEN_THRESHOLD: "1000", OPENVIKING_CAPTURE_TIMEOUT_MS: "1000",
    OPENVIKING_TIMEOUT_MS: "1000", OV_HOOK_WORKER: "1",
  };
}

test("Stop, PreCompact and SessionEnd upload prose only with correct commits and resume", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-conversation-hooks-"));
  const path = join(dir, "rollout.jsonl");
  const uploaded = [];
  let commits = 0;
  const entries = [prose("user", "First question"), ...tools("one"), prose("assistant", "First answer")];
  try {
    await withMockOpenViking(async (req, res) => {
      if (req.url === "/health") return writeJson(res, { status: "ok", result: { ok: true } });
      if (req.url.endsWith("/messages/batch")) {
        uploaded.push(...(await readRequestBody(req)).messages);
        return writeJson(res, { status: "ok", result: { ok: true } });
      }
      if (req.url.endsWith("/commit")) {
        commits++;
        return writeJson(res, { status: "ok", result: { archived: true } });
      }
      return writeJson(res, { status: "ok", result: { pending_tokens: 1 } });
    }, async (baseUrl) => {
      const env = workerEnv(dir, baseUrl);
      const input = { session_id: "conversation-hooks", transcript_path: path, cwd: dir };
      const hook = async (name) => expectExit(await runHookScript(join(scripts, name), { input, env, cwd: dir }));
      await writeRollout(path, entries);
      await hook("auto-capture.mjs");
      await hook("auto-capture.mjs");
      assert.equal(uploaded.length, 2, "repeated Stop must not replay text or tools");
      assert.equal(commits, 0);
      entries.push(...tools("two"), prose("assistant", "Before compaction"));
      await writeRollout(path, entries);
      await hook("pre-compact-capture.mjs");
      assert.equal(commits, 1, "excluded tools are not an incomplete append");
      let state = JSON.parse(await readFile(join(dir, "conversation-hooks.json"), "utf-8"));
      assert.equal(state.capturedTurnCount, 7);
      assert.equal(state.ovSessionId, null);
      entries.push(prose("user", "Resumed question"), ...tools("three"), prose("assistant", "Final answer"));
      await writeRollout(path, entries);
      await hook("session-end.mjs");
      assert.equal(commits, 2);
      state = JSON.parse(await readFile(join(dir, "conversation-hooks.json"), "utf-8"));
      assert.equal(state.capturedTurnCount, 11);
      assert.equal(state.ovSessionId, null);
      await hook("session-end.mjs");
      assert.equal(commits, 2, "repeat SessionEnd must not revive a committed session");
    });
    assert.ok(!JSON.stringify(uploaded).includes(marker));
    assert.deepEqual(uploaded.flatMap((message) => message.parts.map((part) => {
      assert.equal(part.type, "text");
      return part.text;
    })), ["First question", "First answer", "Before compaction", "Resumed question", "Final answer"]);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("PreCompact does not commit after an unsuccessful filtered append", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-conversation-failed-hook-"));
  const path = join(dir, "rollout.jsonl");
  let commits = 0;
  let failedBody;
  try {
    await writeRollout(path, [...tools("failed"), prose("user", "Must retry this question")]);
    await withMockOpenViking(async (req, res) => {
      if (req.url === "/health") return writeJson(res, { status: "ok", result: { ok: true } });
      if (req.url.endsWith("/messages/batch")) {
        failedBody = await readRequestBody(req);
        return writeJson(res, { status: "error", error: { message: "synthetic failure" } }, 503);
      }
      if (req.url.endsWith("/commit")) commits++;
      return writeJson(res, { status: "ok", result: {} });
    }, async (baseUrl) => {
      const result = expectExit(await runHookScript(join(scripts, "pre-compact-capture.mjs"), {
        input: { session_id: "failed-hook", transcript_path: path, cwd: dir },
        env: workerEnv(dir, baseUrl), cwd: dir,
      }));
      assert.match(result.stdout, /incomplete/);
      const state = JSON.parse(await readFile(join(dir, "failed-hook.json"), "utf-8"));
      assert.equal(state.capturedTurnCount, 0);
      assert.equal(state.ovSessionId, "cx-failed-hook");
    });
    assert.equal(commits, 0);
    assert.equal(failedBody.messages.length, 1);
    assert.ok(!JSON.stringify(failedBody).includes(marker));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
