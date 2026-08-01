import assert from "node:assert/strict";
import test from "node:test";
import { buildZcodeTurns, cleanZcodeText } from "./zcode-turns.mjs";

test("cleanZcodeText strips openviking-context blocks", () => {
  const input = "hello <openviking-context source=\"test\">secret</openviking-context> world";
  assert.equal(cleanZcodeText(input), "hello  world");
});

test("cleanZcodeText strips relevant-memories blocks", () => {
  const input = "text <relevant-memories>old</relevant-memories> here";
  assert.equal(cleanZcodeText(input), "text  here");
});

test("cleanZcodeText strips system-reminder blocks", () => {
  const input = "msg <system-reminder>warn</system-reminder> end";
  assert.equal(cleanZcodeText(input), "msg  end");
});

test("cleanZcodeText returns empty string for undefined/null", () => {
  assert.equal(cleanZcodeText(undefined), "");
  assert.equal(cleanZcodeText(null), "");
});

test("cleanZcodeText trims whitespace", () => {
  assert.equal(cleanZcodeText("  hello  "), "hello");
});

test("buildZcodeTurns extracts user + assistant from prompt/last_assistant_message", () => {
  const turns = buildZcodeTurns({
    prompt: "what is my name?",
    last_assistant_message: "Your name is Alice.",
  });
  assert.equal(turns.length, 2);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "what is my name?");
  assert.equal(turns[1].role, "assistant");
  assert.equal(turns[1].content, "Your name is Alice.");
});

test("buildZcodeTurns returns only user turn when no assistant content", () => {
  const turns = buildZcodeTurns({ prompt: "hello" });
  assert.equal(turns.length, 1);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "hello");
});

test("buildZcodeTurns returns only assistant turn when no prompt", () => {
  const turns = buildZcodeTurns({ last_assistant_message: "hi there" });
  assert.equal(turns.length, 1);
  assert.equal(turns[0].role, "assistant");
  assert.equal(turns[0].content, "hi there");
});

test("buildZcodeTurns returns empty array when both empty", () => {
  const turns = buildZcodeTurns({});
  assert.equal(turns.length, 0);
});

test("buildZcodeTurns falls back to state.pendingPrompt", () => {
  const turns = buildZcodeTurns(
    {},
    { pendingPrompt: { prompt: "cached question" } },
  );
  assert.equal(turns.length, 1);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "cached question");
});

test("buildZcodeTurns strips injected blocks from content", () => {
  const turns = buildZcodeTurns({
    prompt: "real question <openviking-context>injected</openviking-context>",
    last_assistant_message: "answer <relevant-memories>old</relevant-memories>",
  });
  assert.equal(turns[0].content, "real question");
  assert.equal(turns[1].content, "answer");
});

test("buildZcodeTurns probes alternative field names", () => {
  const turns = buildZcodeTurns({
    user_prompt: "alt prompt",
    assistantMessage: "alt response",
  });
  assert.equal(turns.length, 2);
  assert.equal(turns[0].content, "alt prompt");
  assert.equal(turns[1].content, "alt response");
});
