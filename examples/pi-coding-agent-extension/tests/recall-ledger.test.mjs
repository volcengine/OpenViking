import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { RecallManager } from "../recall.ts";
import { RecallLedger, ledgerKey } from "../shared/recall-ledger.mjs";

function config(overrides = {}) {
  return {
    minQueryLength: 3,
    recallLimit: 6,
    recallMaxContentChars: 500,
    recallTokenBudget: 2000,
    scoreThreshold: 0.35,
    peerId: "",
    ...overrides,
  };
}

function clientReturning(rendered) {
  return { fetchJSON: async () => ({ ok: true, result: { rendered } }) };
}

/** One provider request: run recall for the newest prompt, then inject into a
 * fresh deep copy of the history — exactly what pi's context hook sees. */
async function turn(recall, block, history) {
  recall.client = clientReturning(block);
  const last = history[history.length - 1].content;
  const prompt = typeof last === "string"
    ? last
    : last.filter((b) => b.type === "text").map((b) => b.text).join("");
  recall.queueSearch(prompt);
  await recall.searchPending();
  const view = structuredClone(history);
  return recall.injectRecall(view);
}

test("historical user messages get their original block back, so the request prefix is stable across turns (#4137)", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const recall = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  recall.openLedger("session-1");

  // Turn 1: [u1] with recall block b1
  const sent1 = await turn(recall, "- b1 memory", [
    { role: "user", content: "u1 prompt" },
  ]);
  assert.match(sent1[0].content, /b1 memory/);

  recall.invalidate(); // agent_end

  // Turn 2: history rebuilt from storage WITHOUT b1; new block b2 for u2.
  const sent2 = await turn(recall, "- b2 memory", [
    { role: "user", content: "u1 prompt" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "u2 prompt" },
  ]);

  // The bytes for u1 this turn must equal the bytes sent last turn.
  assert.equal(sent2[0].content, sent1[0].content, "u1 prefix must be byte-identical to turn 1");
  assert.match(sent2[2].content, /b2 memory/);

  recall.invalidate();

  // Turn 3 after a process restart: a brand-new manager + ledger from disk.
  const restarted = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  restarted.openLedger("session-1");
  const sent3 = await turn(restarted, "- b3 memory", [
    { role: "user", content: "u1 prompt" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "u2 prompt" },
    { role: "assistant", content: "a2" },
    { role: "user", content: "u3 prompt" },
  ]);
  assert.equal(sent3[0].content, sent1[0].content, "u1 survives restart");
  assert.equal(sent3[2].content, sent2[2].content, "u2 survives restart");
  assert.match(sent3[4].content, /b3 memory/);
});

test("repeated identical prompts keep distinct blocks via the ordinal in the key", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const recall = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  recall.openLedger("session-2");

  const sent1 = await turn(recall, "- first continue block", [
    { role: "user", content: "continue" },
  ]);
  recall.invalidate();
  const sent2 = await turn(recall, "- second continue block", [
    { role: "user", content: "continue" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "continue" },
  ]);

  assert.equal(sent2[0].content, sent1[0].content);
  assert.match(sent2[2].content, /second continue block/);
  assert.doesNotMatch(sent2[2].content, /first continue block/);
});

test("a short prompt records nothing and history it appears in stays un-injected", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const recall = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  recall.openLedger("session-3");

  const sent1 = await turn(recall, "- unused", [{ role: "user", content: "x" }]);
  assert.equal(sent1[0].content, "x");
  recall.invalidate();

  const sent2 = await turn(recall, "- b2", [
    { role: "user", content: "x" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "real second prompt" },
  ]);
  assert.equal(sent2[0].content, "x", "no phantom block for a message that never had one");
  assert.match(sent2[2].content, /b2/);
});

test("rewritten history (stale ordinals) misses cleanly instead of injecting into the wrong message", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const recall = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  recall.openLedger("session-4");

  await turn(recall, "- b1", [{ role: "user", content: "u1 prompt" }]);
  recall.invalidate();

  // Compaction dropped u1; a different message now sits at ordinal 0.
  const sent = await turn(recall, "- b2", [
    { role: "user", content: "totally different message" },
    { role: "assistant", content: "a" },
    { role: "user", content: "u2 prompt" },
  ]);
  assert.equal(sent[0].content, "totally different message");
});

test("array-content user messages round-trip through the ledger", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const recall = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  recall.openLedger("session-5");

  const sent1 = await turn(recall, "- b1", [
    { role: "user", content: [{ type: "text", text: "u1 prompt" }] },
  ]);
  assert.match(sent1[0].content[0].text, /b1/);
  recall.invalidate();

  const sent2 = await turn(recall, "- b2", [
    { role: "user", content: [{ type: "text", text: "u1 prompt" }] },
    { role: "assistant", content: "a1" },
    { role: "user", content: [{ type: "text", text: "u2 prompt" }] },
  ]);
  assert.equal(sent2[0].content[0].text, sent1[0].content[0].text);
  assert.match(sent2[2].content[0].text, /b2/);
});

test("a corrupt ledger file degrades to pre-ledger behaviour", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const ledgerA = new RecallLedger(dir);
  ledgerA.open("session-6");
  ledgerA.record(ledgerKey(0, "u1 prompt"), "- b1");
  ledgerA.flush();

  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  assert.equal(files.length, 1);
  writeFileSync(join(dir, files[0]), "{not json");

  const recall = new RecallManager(clientReturning(""), config(), () => null, new RecallLedger(dir));
  recall.openLedger("session-6");
  const sent = await turn(recall, "- b2", [
    { role: "user", content: "u1 prompt" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "u2 prompt" },
  ]);
  assert.equal(sent[0].content, "u1 prompt");
  assert.match(sent[2].content, /b2/);
});

test("ledger file is written atomically with owner-only permissions", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-ledger-"));
  const ledger = new RecallLedger(dir);
  ledger.open("session-7");
  ledger.record(ledgerKey(0, "u1"), "- b1");
  ledger.flush();

  const files = readdirSync(dir);
  assert.deepEqual(files, ["session-7.json"]);
  const data = JSON.parse(readFileSync(join(dir, "session-7.json"), "utf-8"));
  assert.equal(data.version, 1);
  assert.equal(data.entries.length, 1);
});
