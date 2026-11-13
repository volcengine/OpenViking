import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { catchUpTurns } from "./ov-session.mjs";
import { CAPTURE_FORMAT_VERSION } from "./session-state.mjs";

const user = (text) => ({ payload: { message: { role: "user", content: text } } });
const assistant = (text) => ({ payload: { message: { role: "assistant", content: text } } });
const write = (path, entries) => writeFile(path, entries.map((e) => JSON.stringify(e)).join("\n"));

async function withStateDir(run) {
  const dir = await mkdtemp(join(tmpdir(), "ov-session-cursor-"));
  const previous = process.env.OPENVIKING_CODEX_STATE_DIR;
  process.env.OPENVIKING_CODEX_STATE_DIR = dir;
  try {
    await run(dir);
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_CODEX_STATE_DIR;
    else process.env.OPENVIKING_CODEX_STATE_DIR = previous;
    await rm(dir, { recursive: true, force: true });
  }
}

function harness(state) {
  const sent = [];
  async function fetchJSONRes(_path, init = {}) {
    const body = init.body ? JSON.parse(init.body) : {};
    for (const message of body.messages || [body]) sent.push(message.parts?.[0]?.text ?? message.content);
    return { ok: true, result: {} };
  }
  const capture = (transcriptPath) =>
    catchUpTurns({ state, transcriptPath, fetchJSONRes, cfg: { captureAssistantTurns: true } });
  return { sent, capture };
}

const newState = (fields) => ({ ovSessionId: "cx-test", captureFormatVersion: CAPTURE_FORMAT_VERSION, ...fields });

test("alternating rollout files for one session never replay sent turns", () => withStateDir(async (dir) => {
  // The cumulative thread rollout and a separate compaction-window rollout
  // both reach hooks under the same Codex session id.
  const thread = join(dir, "thread.jsonl");
  const windowFile = join(dir, "window.jsonl");
  const threadTurns = Array.from({ length: 12 }, (_, i) => (i % 2 ? assistant : user)(`thread ${i}`));
  const windowTurns = [user("<environment_context>window</environment_context>"), assistant("window 1"), assistant("window 2")];
  await write(thread, threadTurns);
  await write(windowFile, windowTurns);
  const { sent, capture } = harness(newState({ codexSessionId: "alternating", capturedTurnCount: 0 }));

  for (const path of [thread, windowFile, thread, windowFile]) await capture(path);
  await write(thread, [...threadTurns, user("thread 12")]);
  await write(windowFile, [...windowTurns, assistant("window 3")]);
  await capture(windowFile);
  await capture(thread);

  assert.equal(new Set(sent).size, sent.length, `replayed turns: ${JSON.stringify(sent)}`);
  assert.equal(sent.length, 12 + 3 + 1 + 1);
}));

test("a shrinking rollout at the same path still resumes at the latest human turn", () => withStateDir(async (dir) => {
  const path = join(dir, "rollout.jsonl");
  await write(path, [user("compacted summary"), assistant("prior tail"), user("current request"), assistant("current response")]);
  const { sent, capture } = harness(newState({ codexSessionId: "shrink", transcriptPath: path, capturedTurnCount: 8 }));
  await capture(path);
  assert.deepEqual(sent, ["current request", "current response"]);
}));

test("a session meeting a new rollout file resumes at its latest human turn", () => withStateDir(async (dir) => {
  const known = join(dir, "known.jsonl");
  const unseen = join(dir, "unseen.jsonl");
  await write(known, [user("known 0"), assistant("known 1")]);
  await write(unseen, [user("history 0"), assistant("history 1"), user("latest request"), assistant("latest response")]);
  // State written before per-path cursors existed: one cursor, no map.
  const state = newState({ codexSessionId: "legacy", transcriptPath: known, capturedTurnCount: 2 });
  const { sent, capture } = harness(state);
  await capture(unseen);
  await capture(known);
  assert.deepEqual(sent, ["latest request", "latest response"]);
  assert.equal(state.transcriptCursors[unseen], 4);
}));
