import { test } from "node:test";
import assert from "node:assert/strict";

import { cleanZcodeText, buildZcodeTurns } from "./zcode-turns.mjs";

test("cleanZcodeText strips openviking-context blocks", () => {
  const out = cleanZcodeText(
    'hello <openviking-context version="1">secret</openviking-context> world'
  );
  assert.equal(out, "hello  world");
});

test("cleanZcodeText strips relevant-memories blocks", () => {
  const out = cleanZcodeText("a <relevant-memories>x</relevant-memories> b");
  assert.equal(out, "a  b");
});

test("cleanZcodeText is null/undefined safe and trims", () => {
  assert.equal(cleanZcodeText(undefined), "");
  assert.equal(cleanZcodeText(null), "");
  assert.equal(cleanZcodeText("  hi  "), "hi");
});

test("buildZcodeTurns builds user+assistant turns from input", () => {
  const turns = buildZcodeTurns({ prompt: "hello", last_assistant_message: "hi back" });
  assert.deepEqual(
    turns,
    [
      { role: "user", content: "hello" },
      { role: "assistant", content: "hi back" },
    ]
  );
});

test("buildZcodeTurns falls back to state.pendingPrompt", () => {
  const turns = buildZcodeTurns(
    { last_assistant_message: "reply" },
    { pendingPrompt: { prompt: "from-state" } }
  );
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "from-state");
});

test("buildZcodeTurns drops empty turns", () => {
  const turns = buildZcodeTurns({ prompt: "", last_assistant_message: "" });
  assert.deepEqual(turns, []);
  const one = buildZcodeTurns({ prompt: "only-user" });
  assert.equal(one.length, 1);
  assert.equal(one[0].role, "user");
});

test("buildZcodeTurns strips injected context tags from turn content", () => {
  const turns = buildZcodeTurns({
    prompt: "q <openviking-context>x</openviking-context>",
    text_content: "<relevant-memories>y</relevant-memories> a",
  });
  assert.equal(turns[0].content, "q");
  assert.equal(turns[1].content, "a");
});
