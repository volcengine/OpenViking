import test from "node:test";
import assert from "node:assert/strict";
import {
  CONTEXT_BLOCK_MARKER,
  countUserTurns,
  estimatePayloadTokens,
  estimateTokens,
  fingerprintMessage,
  flattenContent,
  isUserTurnStart,
  truncateToTokens,
} from "../lib/text-budget.mjs";

function msg(role, content) {
  return { role, content };
}

const user = (text) => msg("user", text);
const assistant = (text) => msg("assistant", text);

test("flattenContent handles strings and text arrays", () => {
  assert.equal(flattenContent(user("hello")), "hello");
  assert.equal(
    flattenContent(user([{ type: "text", text: "a" }, { type: "image" }, { type: "text", text: "b" }])),
    "ab",
  );
  assert.equal(flattenContent(user(null)), "");
  assert.equal(flattenContent(null), "");
});

test("fingerprintMessage includes role length and 200-char prefix", () => {
  const fp = fingerprintMessage(user("x".repeat(250)));
  assert.equal(fp, `user:250:${"x".repeat(200)}`);
  assert.notEqual(fingerprintMessage(user("same")), fingerprintMessage(assistant("same")));
});

test("user turn helpers ignore messages this extension injected", () => {
  const messages = [
    user("first"),
    assistant("answer"),
    user(`${CONTEXT_BLOCK_MARKER} source="context-window">…`),
    user("second"),
  ];
  assert.equal(isUserTurnStart(messages[0]), true);
  assert.equal(isUserTurnStart(messages[1]), false);
  assert.equal(isUserTurnStart(messages[2]), false);
  assert.equal(countUserTurns(messages), 2);
});

test("estimateTokens and truncateToTokens handle CJK conservatively", () => {
  assert.equal(estimateTokens(""), 0);
  assert.equal(estimateTokens("a".repeat(100)), 25);
  assert.equal(estimateTokens("界".repeat(10)), 15);
  assert.equal(estimateTokens("界界" + "a".repeat(8)), 5);
  assert.equal(truncateToTokens("hello", 100), "hello");
  assert.equal(truncateToTokens("hello", 0), "");
  const truncated = truncateToTokens("界".repeat(9000), 3000);
  assert.ok(estimateTokens(truncated) <= 3000);
  assert.ok(truncated.length < 2500);
});

test("truncateToTokens returns the longest prefix inside the budget", () => {
  const text = "a".repeat(400);
  const cut = truncateToTokens(text, 10);
  assert.equal(cut.length, 40);
  assert.equal(estimateTokens(cut), 10);
  assert.equal(estimateTokens(text.slice(0, cut.length + 1)), 11);
});

test("estimatePayloadTokens counts content and structured parts", () => {
  assert.equal(estimatePayloadTokens({ content: "a".repeat(40) }), 10);
  assert.equal(estimatePayloadTokens(null), 0);
  const withParts = estimatePayloadTokens({
    parts: [
      { type: "text", text: "a".repeat(40) },
      { type: "tool", tool_name: "read", tool_input: { path: "file" }, tool_status: "running" },
    ],
  });
  assert.ok(withParts > 10);
});
