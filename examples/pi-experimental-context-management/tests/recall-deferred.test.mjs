import test from "node:test";
import assert from "node:assert/strict";
import { RecallManager } from "../recall.ts";

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

test("queued recall waits for the context phase and still injects current-query memory", async () => {
  const calls = [];
  const client = {
    fetchJSON: async (path, init) => {
      calls.push({ path, init });
      return {
        ok: true,
        result: { rendered: "- current prompt memory" },
      };
    },
  };
  const recall = new RecallManager(client, config());

  recall.queueSearch("current prompt");
  assert.equal(calls.length, 0, "queueing in before_agent_start must not perform I/O");

  await recall.searchPending();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/api/v1/search/search");
  assert.equal(JSON.parse(calls[0].init.body).mode, "context");

  const messages = [{ role: "user", content: "current prompt" }];
  const injected = recall.injectRecall(messages);
  assert.match(injected[0].content, /current prompt memory/);
  assert.match(injected[0].content, /current prompt$/);

  await recall.searchPending();
  assert.equal(calls.length, 1, "later context iterations must reuse the cached recall");
});

test("recall sends the OV session id so dedup and expansion engage", async () => {
  const bodies = [];
  const client = {
    fetchJSON: async (path, init) => {
      bodies.push(JSON.parse(init.body));
      return { ok: true, result: { rendered: "- memory" } };
    },
  };
  // The sync manager owns the id and is constructed after the recall manager,
  // so it arrives as a getter and is null until a session has been opened.
  let sessionId = null;
  const recall = new RecallManager(client, config({ recallDedupTurns: 5 }), () => sessionId);

  recall.queueSearch("first prompt");
  await recall.searchPending();
  assert.equal(bodies[0].session_id, undefined);

  sessionId = "pi-session-1";
  recall.queueSearch("second prompt");
  await recall.searchPending();
  assert.equal(bodies[1].session_id, "pi-session-1");
  assert.equal(bodies[1].dedup_turns, 5);
});

test("a queued short prompt clears the previous recall block", async () => {
  const client = {
    fetchJSON: async () => ({
      ok: true,
      result: { rendered: "- stale memory" },
    }),
  };
  const recall = new RecallManager(client, config());

  recall.queueSearch("long enough prompt");
  await recall.searchPending();

  recall.queueSearch("x");
  await recall.searchPending();

  const messages = [{ role: "user", content: "x" }];
  assert.deepEqual(recall.injectRecall(messages), messages);
  assert.equal(messages[0].content, "x");
});
