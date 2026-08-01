import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { buildZcodeTurns, cleanZcodeText, extractUnseenRolloutTurns } from "./zcode-turns.mjs";

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
// Rollout fallback + turnId tests
// ---------------------------------------------------------------------------

function createFakeRolloutHome(entries) {
  const sessionId = "test-sess-rollout";
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-home-"));
  const rolloutDir = join(fakeHome, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });
  const lines = entries.map((e) => JSON.stringify(e)).join("\n");
  writeFileSync(join(rolloutDir, `model-io-sess-${sessionId}.jsonl`), lines + "\n");
  return { fakeHome, sessionId };
}

test("extractUnseenRolloutTurns returns last entry when no lastKnownTurnId", () => {
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "first q" }] }, response: { text: "first a" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "second q" }] }, response: { text: "second a" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = extractUnseenRolloutTurns(
    join(fakeHome, ".zcode", "cli", "rollout", `model-io-sess-${sessionId}.jsonl`),
    null,
  );
  process.env.HOME = originalHome;
  // No lastKnownTurnId → returns only the last entry
  assert.equal(turns.length, 2); // user + assistant from last entry
  assert.equal(turns[0].content, "second q");
  assert.equal(turns[0].turnId, "turn-002");
});

test("extractUnseenRolloutTurns returns all entries after lastKnownTurnId", () => {
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "q1" }] }, response: { text: "a1" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "q2" }] }, response: { text: "a2" } },
    { turnId: "turn-003", request: { messages: [{ role: "user", content: "q3" }] }, response: { text: "a3" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = extractUnseenRolloutTurns(
    join(fakeHome, ".zcode", "cli", "rollout", `model-io-sess-${sessionId}.jsonl`),
    "turn-001",
  );
  process.env.HOME = originalHome;
  // Should return turn-002 and turn-003 (4 turns: user+assistant × 2)
  assert.equal(turns.length, 4);
  assert.equal(turns[0].content, "q2");
  assert.equal(turns[0].turnId, "turn-002");
  assert.equal(turns[2].content, "q3");
  assert.equal(turns[2].turnId, "turn-003");
});

test("buildZcodeTurns falls back to rollout when user content missing", () => {
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "rollout question" }] }, response: { text: "rollout answer" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = buildZcodeTurns({
    session_id: sessionId,
    responseText: "stdin assistant only",
  });
  process.env.HOME = originalHome;
  assert.ok(turns.length >= 2);
  assert.equal(turns[0].role, "user");
  assert.ok(turns[0].content.includes("rollout"));
  assert.ok(turns[0].turnId, "turn from rollout should carry turnId");
});

test("buildZcodeTurns handles missing rollout file gracefully", () => {
  const turns = buildZcodeTurns({
    session_id: "nonexistent-session",
    responseText: "assistant only",
  });
  assert.equal(turns.length, 1);
  assert.equal(turns[0].role, "assistant");
  assert.equal(turns[0].content, "assistant only");
});

test("buildZcodeTurns respects lastTurnId in state for incremental capture", () => {
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "old q" }] }, response: { text: "old a" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "new q" }] }, response: { text: "new a" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  // Pass state with lastTurnId = turn-001 → should only get turn-002
  const turns = buildZcodeTurns(
    { session_id: sessionId, responseText: "ignored" },
    { lastTurnId: "turn-001" },
  );
  process.env.HOME = originalHome;
  assert.equal(turns.length, 2); // user + assistant from turn-002
  assert.equal(turns[0].content, "new q");
  assert.equal(turns[0].turnId, "turn-002");
});
