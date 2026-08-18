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

test("buildZcodeTurns prefers rollout and recovers missed turns even with pendingPrompt", () => {
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "missed q" }] }, response: { text: "missed a" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "current q" }] }, response: { text: "current a" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = buildZcodeTurns(
    {
      session_id: sessionId,
      responseText: "stdin current a",
    },
    {
      pendingPrompt: { prompt: "current q" },
    },
  );
  process.env.HOME = originalHome;

  assert.deepEqual(
    turns.map(({ role, content, turnId }) => ({ role, content, turnId })),
    [
      { role: "user", content: "missed q", turnId: "turn-001" },
      { role: "assistant", content: "missed a", turnId: "turn-001" },
      { role: "user", content: "current q", turnId: "turn-002" },
      { role: "assistant", content: "current a", turnId: "turn-002" },
    ],
  );
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
  writeFileSync(join(rolloutDir, `model-io-${sessionId}.jsonl`), lines + "\n");
  return { fakeHome, sessionId };
}

test("extractUnseenRolloutTurns returns ALL entries when no lastKnownTurnId", () => {
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "first q" }] }, response: { text: "first a" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "second q" }] }, response: { text: "second a" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = extractUnseenRolloutTurns(
    join(fakeHome, ".zcode", "cli", "rollout", `model-io-${sessionId}.jsonl`),
    null,
  );
  process.env.HOME = originalHome;
  // No lastKnownTurnId → returns ALL entries (first-time capture should not lose prior turns)
  assert.equal(turns.length, 4); // user+assistant × 2 entries
  assert.equal(turns[0].content, "first q");
  assert.equal(turns[0].turnId, "turn-001");
  assert.equal(turns[1].content, "first a");
  assert.equal(turns[1].turnId, "turn-001");
  assert.equal(turns[2].content, "second q");
  assert.equal(turns[2].turnId, "turn-002");
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
    join(fakeHome, ".zcode", "cli", "rollout", `model-io-${sessionId}.jsonl`),
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

// ---------------------------------------------------------------------------
// Lifecycle tests: duplicate Stop, missed Stop, repeated content on distinct turns
// ---------------------------------------------------------------------------

test("lifecycle: missed Stop recovers all unseen turns on next fire", () => {
  // Simulate: Stop fires after turn-001, then hook fails, then Stop fires again
  // after turn-003. With lastTurnId=turn-001, should get turn-002 AND turn-003.
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "q1" }] }, response: { text: "a1" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "q2" }] }, response: { text: "a2" } },
    { turnId: "turn-003", request: { messages: [{ role: "user", content: "q3" }] }, response: { text: "a3" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = buildZcodeTurns(
    { session_id: sessionId, responseText: "ignored" },
    { lastTurnId: "turn-001" },
  );
  process.env.HOME = originalHome;
  assert.equal(turns.length, 4); // turn-002 (user+assistant) + turn-003 (user+assistant)
  assert.equal(turns[0].turnId, "turn-002");
  assert.equal(turns[2].turnId, "turn-003");
});

test("lifecycle: user and assistant from same rollout entry both captured", () => {
  // This is the regression test for the dedup bug where user and assistant
  // shared the same turnId and the assistant was silently dropped.
  const { fakeHome, sessionId } = createFakeRolloutHome([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "user msg" }] }, response: { text: "assistant msg" } },
  ]);
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turns = buildZcodeTurns({
    session_id: sessionId,
    responseText: "ignored", // triggers rollout fallback (no user content in stdin)
  });
  process.env.HOME = originalHome;
  assert.equal(turns.length, 2);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "user msg");
  assert.equal(turns[1].role, "assistant");
  assert.equal(turns[1].content, "assistant msg");
  assert.equal(turns[0].turnId, turns[1].turnId); // same turnId, different role
});

// ---------------------------------------------------------------------------
// Concurrent session isolation: two sessions in the same workspace must not
// cross-read each other's rollout data
// ---------------------------------------------------------------------------

test("isolation: two sessions read their own rollout files, not each other's", () => {
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-iso-"));
  const rolloutDir = join(fakeHome, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });

  const sessA = "sess-isolation-AAA";
  const sessB = "sess-isolation-BBB";

  // Each session has its own rollout file with distinct sentinel content
  writeFileSync(
    join(rolloutDir, `model-io-${sessA}.jsonl`),
    JSON.stringify({ turnId: "turn-A1", request: { messages: [{ role: "user", content: "amber-sentinel-A" }] }, response: { text: "response-A" } }) + "\n",
  );
  writeFileSync(
    join(rolloutDir, `model-io-${sessB}.jsonl`),
    JSON.stringify({ turnId: "turn-B1", request: { messages: [{ role: "user", content: "sapphire-sentinel-B" }] }, response: { text: "response-B" } }) + "\n",
  );

  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;

  // Session A reads its own rollout
  const turnsA = buildZcodeTurns({ session_id: sessA, responseText: "ignored" });
  // Session B reads its own rollout
  const turnsB = buildZcodeTurns({ session_id: sessB, responseText: "ignored" });

  process.env.HOME = originalHome;

  // Session A must NOT contain session B's content
  const aContents = turnsA.map((t) => t.content).join(" ");
  assert.ok(aContents.includes("amber-sentinel-A"), "session A should see its own content");
  assert.ok(!aContents.includes("sapphire-sentinel-B"), "session A must NOT see session B's content");

  // Session B must NOT contain session A's content
  const bContents = turnsB.map((t) => t.content).join(" ");
  assert.ok(bContents.includes("sapphire-sentinel-B"), "session B should see its own content");
  assert.ok(!bContents.includes("amber-sentinel-A"), "session B must NOT see session A's content");

  // Turn IDs must not cross
  assert.equal(turnsA[0].turnId, "turn-A1");
  assert.equal(turnsB[0].turnId, "turn-B1");
});

test("isolation: independent lastTurnId state per session", () => {
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-iso-state-"));
  const rolloutDir = join(fakeHome, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });

  const sessA = "sess-state-AAA";
  const sessB = "sess-state-BBB";

  writeFileSync(
    join(rolloutDir, `model-io-${sessA}.jsonl`),
    [
      JSON.stringify({ turnId: "A-001", request: { messages: [{ role: "user", content: "A1" }] }, response: { text: "a1" } }),
      JSON.stringify({ turnId: "A-002", request: { messages: [{ role: "user", content: "A2" }] }, response: { text: "a2" } }),
    ].join("\n") + "\n",
  );
  writeFileSync(
    join(rolloutDir, `model-io-${sessB}.jsonl`),
    [
      JSON.stringify({ turnId: "B-001", request: { messages: [{ role: "user", content: "B1" }] }, response: { text: "b1" } }),
      JSON.stringify({ turnId: "B-002", request: { messages: [{ role: "user", content: "B2" }] }, response: { text: "b2" } }),
    ].join("\n") + "\n",
  );

  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;

  // Session A processed turn A-001 first; now reads A-002 only
  const turnsA = buildZcodeTurns(
    { session_id: sessA, responseText: "ignored" },
    { lastTurnId: "A-001" },
  );
  // Session B is fresh — reads ALL its entries
  const turnsB = buildZcodeTurns(
    { session_id: sessB, responseText: "ignored" },
    {}, // no lastTurnId — first capture
  );

  process.env.HOME = originalHome;

  // Session A: only A-002 (2 turns: user+assistant)
  assert.equal(turnsA.length, 2);
  assert.equal(turnsA[0].turnId, "A-002");

  // Session B: both entries (4 turns: user+assistant × 2)
  assert.equal(turnsB.length, 4);
  assert.equal(turnsB[0].turnId, "B-001");
  assert.equal(turnsB[2].turnId, "B-002");
});
