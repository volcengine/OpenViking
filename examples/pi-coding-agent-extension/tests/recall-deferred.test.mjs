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
  assert.equal(calls[0].path, "/api/v1/search/recall");

  const messages = [{ role: "user", content: "current prompt" }];
  const injected = recall.injectRecall(messages);
  assert.match(injected[0].content, /current prompt memory/);
  assert.match(injected[0].content, /current prompt$/);

  await recall.searchPending();
  assert.equal(calls.length, 1, "later context iterations must reuse the cached recall");
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
