/**
 * Host shaping on top of the shared capture model (DESIGN.md §6.5):
 * the wire's turn id, the host wrappers that are transport rather than
 * conversation, and the cap that reaches the parts actually shipped.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { shapeKimicodeTurns, stripHostEnvelopes } from "./capture-shape.mjs";

const entry = (text, turnId, role = "user") => ({
  type: "response_item",
  payload: { type: "message", role, content: [{ type: "text", text }], turn_id: turnId },
});

test("the wire's turn id survives shaping", () => {
  const turns = shapeKimicodeTurns([entry("hello", "7"), entry("done", "7", "assistant")], {
    captureAssistantTurns: true,
  });
  assert.equal(turns.length, 2);
  assert.deepEqual(turns.map((turn) => turn.turnId), ["7", "7"]);
});

test("a dropped entry does not shift the turn ids that follow it", () => {
  const turns = shapeKimicodeTurns(
    [
      entry("first", "1"),
      // No recognisable role: the extractor drops it. The ids must come from
      // each turn's own entry, not from its position in the transcript.
      { type: "response_item", payload: { type: "noise" } },
      entry("second", "2"),
    ],
    { captureAssistantTurns: true },
  );
  assert.deepEqual(turns.map((turn) => turn.turnId), ["1", "2"]);
});

test("a turn with no turn id on the wire is shaped without one", () => {
  const turns = shapeKimicodeTurns([entry("hello", null)], { captureAssistantTurns: true });
  assert.equal(turns.length, 1);
  assert.equal("turnId" in turns[0], false);
});

test("host transport wrappers are dropped, a notification body is kept", () => {
  const text = [
    "<hook_result hook_event=\"UserPromptSubmit\">recalled memory</hook_result>",
    "<bash-stdout>a.md</bash-stdout>",
    "<output-file path=\"x.md\" />",
    "<notification kind=\"task\">background task finished</notification>",
    "the actual question",
  ].join("\n");
  const shaped = stripHostEnvelopes(text);
  assert.doesNotMatch(shaped, /hook_result|bash-stdout|output-file/);
  assert.doesNotMatch(shaped, /<notification/);
  assert.match(shaped, /background task finished/);
  assert.match(shaped, /the actual question/);
});

test("captureMaxLength caps the parts that are sent, not only the summary", () => {
  const long = "x".repeat(3000);
  const turns = shapeKimicodeTurns([entry(long, "1")], { captureMaxLength: 200 });
  assert.equal(turns.length, 1);
  const shipped = turns[0].parts?.[0]?.text ?? turns[0].text;
  assert.ok(shipped.length <= 200, `expected <= 200 chars, got ${shipped.length}`);
  assert.match(shipped, /\[truncated\]$/);
});

test("an entry the shared extractor drops does not shift the later turn ids", () => {
  const dropped = { type: "response_item", payload: { type: "message", role: "user", content: [{ type: "text", text: "   " }], turn_id: "1" } };
  const turns = shapeKimicodeTurns([dropped, entry("kept", "2")], {});
  assert.equal(turns.length, 1);
  assert.equal(turns[0].turnId, "2");
});
