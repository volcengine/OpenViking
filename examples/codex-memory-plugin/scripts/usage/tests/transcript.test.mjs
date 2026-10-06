import assert from "node:assert/strict";
import test from "node:test";
import { recallFromTranscript, readTranscriptRecall } from "../transcript.mjs";

const DOC = "viking://resources/team/checklist.md";
const block = `<openviking-context><memory uri="${DOC}" score="0.72" /></openviking-context>`;
const message = (role, text, turn) => ({ type: "response_item", payload: {
  type: "message", role, content: [{ type: "input_text", text }],
  ...(turn ? { internal_chat_message_metadata_passthrough: { turn_id: turn } } : {}),
} });
const jsonl = (...records) => records.map(JSON.stringify).join("\n");

test("only current-turn developer recall is attributed", () => {
  const text = jsonl(
    { type: "turn_context", payload: { turn_id: "old" } },
    message("developer", block),
    { type: "event_msg", payload: { type: "task_started", turn_id: "new" } },
    message("user", block), message("assistant", block),
    message("developer", block.replace("<openviking-context>", '<openviking-context source="session-start">')),
  );
  assert.deepEqual(recallFromTranscript(text, "new"), []);
  assert.deepEqual(recallFromTranscript(`${text}\n${JSON.stringify(message("developer", block))}`, "new"),
    [{ uri: DOC, score: 0.72 }]);
});

test("message turn metadata works without an earlier boundary", () => {
  assert.deepEqual(recallFromTranscript(jsonl(message("developer", block, "new")), "new"),
    [{ uri: DOC, score: 0.72 }]);
  assert.deepEqual(recallFromTranscript(jsonl(message("developer", block)), "new"), []);
});

test("malformed and missing transcripts fail safely", async () => {
  assert.deepEqual(recallFromTranscript('broken\n{"payload":null}\n', "new"), []);
  assert.deepEqual(await readTranscriptRecall("", "new"), []);
  assert.deepEqual(await readTranscriptRecall("/does-not-exist/rollout.jsonl", "new"), []);
});
