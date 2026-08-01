import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

test("buildZcodeTurns extracts user + assistant from prompt/responseText", () => {
  const turns = buildZcodeTurns({
    prompt: "what is my name?",
    responseText: "Your name is Alice.",
  });
  assert.equal(turns.length, 2);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "what is my name?");
  assert.equal(turns[1].role, "assistant");
  assert.equal(turns[1].content, "Your name is Alice.");
});

test("buildZcodeTurns probes responsePreview field", () => {
  const turns = buildZcodeTurns({
    prompt: "hello",
    responsePreview: "hi there",
  });
  assert.equal(turns.length, 2);
  assert.equal(turns[1].content, "hi there");
});

test("buildZcodeTurns returns only assistant turn when no prompt", () => {
  const turns = buildZcodeTurns({ responseText: "hi there" });
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
    { responseText: "answer" },
    { pendingPrompt: { prompt: "cached question" } },
  );
  assert.equal(turns.length, 2);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "cached question");
});

test("buildZcodeTurns strips injected blocks from content", () => {
  const turns = buildZcodeTurns({
    prompt: "real question <openviking-context>injected</openviking-context>",
    responseText: "answer <relevant-memories>old</relevant-memories>",
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

// ---------------------------------------------------------------------------
// Rollout fallback tests — when stdin lacks user content, read from rollout
// ---------------------------------------------------------------------------

test("buildZcodeTurns falls back to rollout when user content missing", () => {
  // Create a temp rollout file
  const tmpDir = mkdtempSync(join(tmpdir(), "zcode-rollout-test-"));
  const sessionId = "test-sess-rollout";
  const rolloutPath = join(tmpDir, `model-io-sess-${sessionId}.jsonl`);
  const rolloutEntry = {
    sessionId,
    turnId: "turn-001",
    type: "model_io",
    request: {
      messages: [
        { role: "user", content: "rollout user question" },
        { role: "assistant", content: "rollout prev answer" },
        { role: "user", content: "rollout latest question" },
      ],
    },
    response: { text: "rollout assistant response" },
  };
  writeFileSync(rolloutPath, JSON.stringify(rolloutEntry) + "\n");

  // Create the expected directory structure under a fake HOME
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-home-"));
  mkdirSync(join(fakeHome, ".zcode", "cli", "rollout"), { recursive: true });
  writeFileSync(
    join(fakeHome, ".zcode", "cli", "rollout", `model-io-sess-${sessionId}.jsonl`),
    JSON.stringify(rolloutEntry) + "\n",
  );

  const originalHomedir = process.env.HOME;
  process.env.HOME = fakeHome;

  const turns = buildZcodeTurns({
    session_id: sessionId,
    responseText: "stdin assistant only",
  });

  process.env.HOME = originalHomedir;

  // Should prefer rollout turns (has both user + assistant)
  assert.ok(turns.length >= 2, `expected >= 2 turns, got ${turns.length}`);
  assert.equal(turns[0].role, "user");
  assert.ok(turns[0].content.includes("rollout"), `expected rollout content, got "${turns[0].content}"`);
});

test("buildZcodeTurns handles missing rollout file gracefully", () => {
  const turns = buildZcodeTurns({
    session_id: "nonexistent-session",
    responseText: "assistant only",
  });
  // Falls back to just the assistant turn from stdin
  assert.equal(turns.length, 1);
  assert.equal(turns[0].role, "assistant");
  assert.equal(turns[0].content, "assistant only");
});
