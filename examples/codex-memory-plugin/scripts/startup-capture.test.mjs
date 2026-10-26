import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-startup-"));
process.env.OPENVIKING_CODEX_STATE_DIR = stateDir;

const { catchUpTurns } = await import("./ov-session.mjs");
const { CAPTURE_FORMAT_VERSION, loadState, saveState, withSessionLock } = await import("./session-state.mjs");

const cfg = { captureAssistantTurns: true, captureMaxLength: 24000 };
const startup = [
  "<recommended_plugins>\nHere is a list of plugins that are available but not installed.\n\n- Example (example@marketplace)\n</recommended_plugins>",
  "# AGENTS.md instructions\n\n<INSTRUCTIONS>\nAnswer in Chinese.\n</INSTRUCTIONS>",
  "<environment_context>\n  <cwd>/tmp/project</cwd>\n  <shell>zsh</shell>\n  <current_date>2026-09-25</current_date>\n  <timezone>Asia/Singapore</timezone>\n  <filesystem><permission_profile type=\"disabled\" /></filesystem>\n</environment_context>",
];

function message(role, ...texts) {
  return { type: "response_item", payload: {
    type: "message", role,
    content: texts.map((text) => ({ type: role === "user" ? "input_text" : "output_text", text })),
  } };
}

function rollout(...conversation) {
  return [
    { type: "session_meta", payload: { id: "example" } },
    message("user", ...startup),
    { type: "turn_context", payload: { turn_id: "turn-1" } },
    ...conversation,
  ];
}

async function writeRollout(id, entries) {
  const path = join(stateDir, `${id}.jsonl`);
  await writeFile(path, entries.map((entry) => JSON.stringify(entry)).join("\n"));
  return path;
}

async function catchUp(id, transcriptPath, sent, { oldCursor, fail = false } = {}) {
  const outcome = await withSessionLock(id, async () => {
    const state = await loadState(id);
    if (oldCursor !== undefined) {
      state.capturedTurnCount = oldCursor;
      state.captureFormatVersion = 1;
      await saveState(state);
    }
    const result = await catchUpTurns({
      state, transcriptPath, cfg,
      fetchJSONRes: async (_path, init) => {
        const persisted = await readFile(join(stateDir, `${id}.json`), "utf-8").then(JSON.parse, () => null);
        if (oldCursor !== undefined) {
          assert.equal(persisted.captureFormatVersion, CAPTURE_FORMAT_VERSION,
            "migration must be durable before the first send");
        }
        if (fail) return { ok: false, status: 500 };
        sent.push(...JSON.parse(init.body).messages);
        return { ok: true, result: {} };
      },
    });
    return { result, state };
  });
  return outcome.value;
}

test("startup-only rollout sends nothing and never opens an OV session", async () => {
  const id = "startup-only";
  const sent = [];
  const path = await writeRollout(id, rollout());
  const { result, state } = await catchUp(id, path, sent);
  assert.equal(result.added, 0);
  assert.deepEqual(sent, []);
  assert.equal(state.ovSessionId, null);
  assert.equal(state.capturedTurnCount, 0);
});

test("first real prompt is sent and resumed capture does not replay it", async () => {
  const id = "fresh";
  const sent = [];
  const path = await writeRollout(id, rollout(message("user", "First question"), message("assistant", "First answer")));
  const first = await catchUp(id, path, sent);
  assert.equal(first.result.added, 2);
  assert.deepEqual(sent.map((item) => item.parts[0].text), ["First question", "First answer"]);
  const second = await catchUp(id, path, sent);
  assert.equal(second.result.added, 0);
  assert.equal(sent.length, 2);
  await writeRollout(id, rollout(
    message("user", "First question"), message("assistant", "First answer"),
    message("user", "Quoted <environment_context> in a real prompt"),
  ));
  const resumed = await catchUp(id, path, sent);
  assert.equal(resumed.result.added, 1);
  assert.equal(sent.at(-1).parts[0].text, "Quoted <environment_context> in a real prompt");
});

test("legacy cursor is corrected by excluded old positions before sending", async () => {
  const id = "legacy";
  const sent = [];
  const path = await writeRollout(id, rollout(
    message("user", "Already sent"), message("assistant", "Also sent"),
    message("user", "New question"),
  ));
  const { result, state } = await catchUp(id, path, sent, { oldCursor: 3 });
  assert.equal(result.added, 1);
  assert.deepEqual(sent.map((item) => item.parts[0].text), ["New question"]);
  assert.equal(state.capturedTurnCount, 3);
  assert.equal((await catchUp(id, path, sent)).result.added, 0);
});

test("failed send keeps the corrected legacy cursor for retry", async () => {
  const id = "failure";
  const sent = [];
  const path = await writeRollout(id, rollout(message("user", "Retry me")));
  const failed = await catchUp(id, path, sent, { oldCursor: 1, fail: true });
  assert.equal(failed.result.added, 0);
  assert.equal((await loadState(id)).capturedTurnCount, 0);
  assert.equal((await catchUp(id, path, sent)).result.added, 1);
  assert.equal(sent[0].parts[0].text, "Retry me");
});

test("legacy startup-only cursor migrates without an empty batch", async () => {
  const id = "legacy-startup-only";
  const sent = [];
  const path = await writeRollout(id, rollout());
  const { result, state } = await catchUp(id, path, sent, { oldCursor: 1 });
  assert.equal(result.added, 0);
  assert.equal(state.capturedTurnCount, 0);
  assert.deepEqual(sent, []);
});

test("shortened transcript resumes at the latest human turn", async () => {
  const id = "shortened";
  const sent = [];
  const path = await writeRollout(id, rollout(
    message("user", "Current question"), message("assistant", "Current answer"),
  ));
  const { result, state } = await catchUp(id, path, sent, { oldCursor: 9 });
  assert.equal(result.added, 2);
  assert.deepEqual(sent.map((item) => item.parts[0].text), ["Current question", "Current answer"]);
  assert.equal(state.capturedTurnCount, 2);
});

test.after(async () => { await rm(stateDir, { recursive: true, force: true }); });
