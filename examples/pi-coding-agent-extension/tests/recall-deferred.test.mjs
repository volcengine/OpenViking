import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { RecallManager } from "../recall.ts";
import { OVClient } from "../client.ts";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { withMockOpenViking, writeJson } from "../../memory-plugin-shared/testing/support.mjs";

let testHome;
const savedHome = process.env.OPENVIKING_STATE_DIR;
before(async () => {
  testHome = await mkdtemp(join(tmpdir(), "ov-pi-recall-tests-"));
  process.env.OPENVIKING_STATE_DIR = testHome;
});
after(async () => {
  if (savedHome === undefined) delete process.env.OPENVIKING_STATE_DIR;
  else process.env.OPENVIKING_STATE_DIR = savedHome;
  await rm(testHome, { recursive: true, force: true });
});

test("actor recall preserves peers and the server request budget", async (t) => {
  const home = await mkdtemp(join(tmpdir(), "ov-pi-peer-recall-"));
  const previousHome = process.env.OPENVIKING_STATE_DIR;
  process.env.OPENVIKING_STATE_DIR = home;
  const delays = [];
  const realSetTimeout = globalThis.setTimeout;
  t.mock.method(globalThis, "setTimeout", (fn, ms, ...args) => {
    delays.push(ms);
    return realSetTimeout(fn, ms, ...args);
  });
  try {
    await withMockOpenViking((req, res) => {
      const peer = req.headers["x-openviking-actor-peer"];
      writeJson(res, { status: "ok", result: { rendered: `- memory for ${peer}` } });
    }, async (baseUrl, requests) => {
      const cfg = config({ endpoint: baseUrl, peerId: "new-git-peer", legacyPeerId: "old-cwd-peer", recallPeerScope: "actor", timeoutMs: 123456 });
      const recall = new RecallManager(new OVClient(cfg), cfg, () => "pi-session");
      recall.queueSearch("project conventions");
      const block = await recall.searchPending();
      assert.deepEqual(requests.map((r) => r.headers["x-openviking-actor-peer"]), ["new-git-peer", "old-cwd-peer"]);
      assert.match(block, /memory for new-git-peer/);
      assert.match(block, /memory for old-cwd-peer/);
      assert.equal(delays.filter((ms) => ms === 123456).length, 2, "both HTTP requests must retain the helper budget");
    });
  } finally {
    if (previousHome === undefined) delete process.env.OPENVIKING_STATE_DIR;
    else process.env.OPENVIKING_STATE_DIR = previousHome;
    await rm(home, { recursive: true, force: true });
  }
});

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
