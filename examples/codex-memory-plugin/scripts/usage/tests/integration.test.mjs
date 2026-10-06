import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { readTurn } from "../state.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPORT = join(HERE, "..", "report.mjs");
const TRACK = join(HERE, "..", "track-lookup.mjs");
const DOC = "viking://resources/team/release-checklist.md";

function runScript(script, input, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(`script exited ${code}: ${stderr}`));
      else resolve(JSON.parse(stdout.trim()));
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("transcript recall and lookup observer produce a stop summary", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-usage-test-"));
  const oldStateDir = process.env.OPENVIKING_CODEX_STATE_DIR;
  process.env.OPENVIKING_CODEX_STATE_DIR = stateDir;
  try {
    const sessionId = "session:one";
    const turnId = "turn:one";
    const transcript = join(stateDir, "rollout.jsonl");
    await writeFile(transcript, [
      { type: "event_msg", payload: { type: "task_started", turn_id: turnId } },
      { type: "response_item", payload: { type: "message", role: "developer", content: [
        { type: "input_text", text: `<openviking-context><memory uri="${DOC}" score="0.72" /></openviking-context>` },
      ] } },
    ].map(JSON.stringify).join("\n"));
    await runScript(TRACK, {
      session_id: sessionId,
      turn_id: turnId,
      hook_event_name: "PostToolUse",
      tool_name: "mcp__plugin_openviking-memory_openviking__read",
      tool_use_id: "tool:one",
      tool_input: { uris: [DOC] },
      tool_response: { content: [{ type: "text", text: `Read ${DOC}` }] },
    }, { OPENVIKING_CODEX_STATE_DIR: stateDir });
    const turn = await readTurn(sessionId, turnId);
    assert.equal(turn.lookups[0].opened[0], DOC);

    const output = await runScript(REPORT, {
      session_id: sessionId,
      turn_id: turnId,
      hook_event_name: "Stop",
      transcript_path: transcript,
    }, { OPENVIKING_CODEX_STATE_DIR: stateDir });
    assert.equal(output.systemMessage, "OV · 1 source · 1 team doc · 1 read in full");
    assert.equal((await readTurn(sessionId, turnId)).recalled[0].uri, DOC);
    const fallback = await runScript(REPORT, {
      session_id: sessionId, turn_id: turnId, hook_event_name: "Stop",
      transcript_path: join(stateDir, "missing.jsonl"),
    }, { OPENVIKING_CODEX_STATE_DIR: stateDir });
    assert.equal(fallback.systemMessage, "OV · 1 source · 1 team doc · 1 read in full");
    assert.deepEqual((await readTurn(sessionId, turnId)).recalled, []);
  } finally {
    if (oldStateDir === undefined) delete process.env.OPENVIKING_CODEX_STATE_DIR;
    else process.env.OPENVIKING_CODEX_STATE_DIR = oldStateDir;
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("disabled reporting does not create metadata and storage failures stay nonblocking", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-usage-isolation-"));
  try {
    const env = { OPENVIKING_CODEX_STATE_DIR: join(dir, "state"), OPENVIKING_USAGE_VIEW: "off" };
    const input = { session_id: "scratch-session", turn_id: "one", tool_use_id: "lookup", tool_name: "mcp__openviking__find", tool_input: { query: "release" }, tool_response: DOC };
    assert.deepEqual(await runScript(TRACK, input, env), {});
    assert.deepEqual(await runScript(REPORT, input, env), {});
    const { access } = await import("node:fs/promises");
    await assert.rejects(access(env.OPENVIKING_CODEX_STATE_DIR), { code: "ENOENT" });
    const blocked = join(dir, "not-a-directory");
    await writeFile(blocked, "occupied");
    assert.deepEqual(await runScript(TRACK, input, { OPENVIKING_CODEX_STATE_DIR: blocked }), {});
    assert.deepEqual(await runScript(REPORT, input, { OPENVIKING_CODEX_STATE_DIR: blocked }), {});
    assert.deepEqual(await runScript(REPORT, { session_id: "scratch", turn_id: "empty" }, { OPENVIKING_CODEX_STATE_DIR: join(dir, "empty") }), {});
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
