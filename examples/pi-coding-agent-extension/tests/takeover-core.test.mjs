import test from "node:test";
import assert from "node:assert/strict";
import {
  OVERVIEW_MARKER,
  TAKEOVER_ENTRY_TYPE,
  TakeoverCore,
  buildOverviewMessage,
  countUndeliveredForSession,
  countUserTurns,
  estimatePayloadTokens,
  estimateTokens,
  findBoundaryIndex,
  fingerprintMessage,
  flattenContent,
  isUserTurnStart,
  truncateToTokens,
} from "../lib/takeover-core.mjs";

function msg(role, content, timestamp = 0) {
  return { role, content, timestamp };
}

function user(text, timestamp = 0) {
  return msg("user", text, timestamp);
}

function assistant(text, timestamp = 0) {
  return msg("assistant", text, timestamp);
}

function makeCore(overrides = {}) {
  const calls = {
    flushed: 0,
    committed: 0,
    persisted: [],
    slept: [],
    logs: [],
  };
  let watermark = overrides.watermark ?? 0;
  const io = {
    flush: async () => {
      calls.flushed++;
      return overrides.flushResult ?? true;
    },
    commit: async (opts) => {
      calls.committed++;
      calls.lastCommitOpts = opts;
      return overrides.commitResult === undefined
        ? { task_id: "t-1", archive_uri: "viking://archive/1" }
        : overrides.commitResult;
    },
    fetchOverview: async (budget) => {
      calls.lastOverviewBudget = budget;
      const values = overrides.overviews ?? ["overview ready"];
      const value = values[Math.min(calls.overviewCalls || 0, values.length - 1)];
      calls.overviewCalls = (calls.overviewCalls || 0) + 1;
      return value;
    },
    persistEntry: (type, data) => calls.persisted.push({ type, data }),
    getWatermark: () => watermark,
    sleep: async (ms) => calls.slept.push(ms),
    log: (message) => calls.logs.push(message),
  };
  const core = new TakeoverCore({
    config: {
      takeoverEnabled: true,
      takeoverTokenThreshold: 100,
      takeoverKeepRecentTurns: 1,
      takeoverOverviewBudget: 1000,
      takeoverOverviewPollMs: 1,
      takeoverOverviewPollMax: 3,
      ...overrides.config,
    },
    io: { ...io, ...overrides.io },
  });
  return { core, calls, setWatermark: (n) => { watermark = n; } };
}

test("flattenContent handles strings and text arrays", () => {
  assert.equal(flattenContent(user("hello")), "hello");
  assert.equal(flattenContent(user([{ type: "text", text: "a" }, { type: "image" }, { type: "text", text: "b" }])), "ab");
  assert.equal(flattenContent(user(null)), "");
});

test("fingerprintMessage includes role length and 200-char prefix", () => {
  const fp = fingerprintMessage(user("x".repeat(250)));
  assert.equal(fp, `user:250:${"x".repeat(200)}`);
  assert.notEqual(fingerprintMessage(user("same")), fingerprintMessage(assistant("same")));
});

test("user turn helpers ignore injected overview messages", () => {
  const messages = [
    user("first"),
    assistant("answer"),
    user(`${OVERVIEW_MARKER} archived`),
    user("second"),
  ];
  assert.equal(isUserTurnStart(messages[0]), true);
  assert.equal(isUserTurnStart(messages[2]), false);
  assert.equal(countUserTurns(messages), 2);
  assert.equal(findBoundaryIndex(messages, 0), 0);
  assert.equal(findBoundaryIndex(messages, 1), 3);
  assert.equal(findBoundaryIndex(messages, 2), -1);
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

test("estimatePayloadTokens counts content and structured parts", () => {
  assert.equal(estimatePayloadTokens({ content: "a".repeat(40) }), 10);
  const withParts = estimatePayloadTokens({
    parts: [
      { type: "text", text: "a".repeat(40) },
      { type: "tool", tool_name: "read", tool_input: { path: "file" }, tool_status: "running" },
    ],
  });
  assert.ok(withParts > 10);
});

test("buildOverviewMessage is byte-stable for the same inputs", () => {
  const a = buildOverviewMessage("summary", 42, 1000);
  const b = buildOverviewMessage("summary", 42, 1000);
  assert.deepEqual(a, b);
  assert.equal(a.timestamp, 41);
  assert.match(a.content, /\[OpenViking Session Context\]/);
});

test("countUndeliveredForSession only counts addMessage for the same session", () => {
  const pending = [
    { entry: { type: "addMessage", sessionId: "a" } },
    { entry: { type: "commitSession", sessionId: "a" } },
    { entry: { type: "addMessage", sessionId: "b" } },
    { type: "addMessage", sessionId: "a" },
  ];
  assert.equal(countUndeliveredForSession(pending, "a"), 2);
});

test("transformContext drops covered turns and injects overview before recall", () => {
  const { core } = makeCore();
  core.restore([
    {
      type: "custom",
      customType: TAKEOVER_ENTRY_TYPE,
      data: {
        coveredUserTurns: 1,
        overview: "archived first turn",
        pendingTokens: 0,
      },
    },
  ]);
  const messages = [
    user("first", 10),
    assistant("answer", 11),
    user("second", 20),
    assistant("answer 2", 21),
  ];
  const out = core.transformContext(messages);
  assert.equal(out.length, 3);
  assert.equal(out[0].role, "user");
  assert.match(out[0].content, /archived first turn/);
  assert.equal(out[0].timestamp, 19);
  assert.equal(out[1].content, "second");
});

test("transformContext is stable between commits", () => {
  const { core } = makeCore();
  core.restore([
    {
      type: "custom",
      customType: TAKEOVER_ENTRY_TYPE,
      data: { coveredUserTurns: 1, overview: "same overview", pendingTokens: 0 },
    },
  ]);
  const messages = [user("first", 1), assistant("answer", 2), user("second", 3)];
  const a = JSON.stringify(core.transformContext(messages));
  const b = JSON.stringify(core.transformContext(messages));
  assert.equal(a, b);
});

test("transformContext resets boundary on fingerprint mismatch", () => {
  const { core } = makeCore();
  core.restore([
    {
      type: "custom",
      customType: TAKEOVER_ENTRY_TYPE,
      data: {
        coveredUserTurns: 1,
        overview: "overview",
        fingerprint: fingerprintMessage(assistant("old answer")),
        pendingTokens: 0,
      },
    },
  ]);
  const messages = [user("first"), assistant("new answer"), user("second")];
  const out = core.transformContext(messages);
  assert.equal(out, messages);
  assert.equal(core.state.coveredUserTurns, 0);
});

test("restore uses the last ov-takeover entry and restores syncedEntryCount", () => {
  const { core } = makeCore();
  core.restore([
    { type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: { coveredUserTurns: 1, overview: "old", pendingTokens: 3, syncedEntryCount: 10 } },
    { type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: { coveredUserTurns: 2, overview: "new", pendingTokens: 7, syncedEntryCount: 12 } },
  ]);
  assert.equal(core.state.coveredUserTurns, 2);
  assert.equal(core.state.overview, "new");
  assert.equal(core.state.pendingTokens, 7);
  assert.equal(core.state.syncedEntryCount, 12);
});

test("onTurnSynced waits for threshold and enough user turns", async () => {
  const { core, calls } = makeCore({ config: { takeoverTokenThreshold: 50, takeoverKeepRecentTurns: 3 } });
  core.transformContext([user("one"), user("two")]);
  assert.equal(await core.onTurnSynced(60), false);
  assert.equal(calls.committed, 0);

  core.transformContext([user("one"), user("two"), user("three"), user("four")]);
  assert.equal(await core.onTurnSynced(0), true);
  assert.equal(calls.committed, 1);
});

test("commitAndAdvance advances boundary and persists after overview is ready", async () => {
  const { core, calls, setWatermark } = makeCore({
    watermark: 4,
    overviews: ["", { latest_archive_overview: "fresh overview" }],
  });
  core.transformContext([user("one"), assistant("a"), user("two"), assistant("b"), user("three")]);
  setWatermark(5);
  assert.equal(await core.onTurnSynced(120), true);
  assert.equal(core.state.coveredUserTurns, 2);
  assert.equal(core.state.overview, "fresh overview");
  assert.equal(core.state.pendingTokens, 0);
  assert.equal(core.state.syncedEntryCount, 5);
  assert.equal(calls.flushed, 1);
  assert.equal(calls.committed, 1);
  assert.equal(calls.lastCommitOpts.queueOnFailure, false);
  assert.deepEqual(calls.slept, [1]);
  assert.equal(calls.persisted.length, 1);
  assert.equal(calls.persisted[0].type, TAKEOVER_ENTRY_TYPE);
});

test("commitAndAdvance keeps pending tokens when flush fails", async () => {
  const { core, calls } = makeCore({ flushResult: false });
  core.transformContext([user("one"), user("two")]);
  assert.equal(await core.onTurnSynced(120), false);
  assert.equal(core.state.pendingTokens, 120);
  assert.equal(calls.committed, 0);
  assert.equal(calls.persisted.length, 0);
});

test("commitAndAdvance keeps boundary unchanged when overview is not ready", async () => {
  const { core, calls } = makeCore({ overviews: ["", "", ""] });
  core.transformContext([user("one"), user("two")]);
  assert.equal(await core.onTurnSynced(120), false);
  assert.equal(core.state.coveredUserTurns, 0);
  // Token pressure resets so the retry waits for the next threshold crossing
  // instead of re-committing (and spawning a new archive) on every turn.
  assert.equal(core.state.pendingTokens, 0);
  assert.equal(calls.committed, 1);
  assert.equal(calls.persisted.length, 0);
  assert.equal(await core.onTurnSynced(10), false);
  assert.equal(calls.committed, 1);
  assert.equal(core.state.pendingTokens, 10);
});

test("concurrent commitAndAdvance calls are serialized", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { core, calls } = makeCore({
    io: {
      flush: async () => {
        calls.flushed++;
        await gate;
        return true;
      },
    },
  });
  core.transformContext([user("one"), user("two")]);
  const first = core.commitAndAdvance();
  const second = core.commitAndAdvance();
  assert.equal(await second, false);
  release();
  assert.equal(await first, true);
  assert.equal(calls.committed, 1);
});

test("handleBeforeCompact returns OV summary and resets boundary", async () => {
  const { core } = makeCore({ overviews: ["compact overview"] });
  core.restore([
    { type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: { coveredUserTurns: 2, overview: "old", pendingTokens: 50 } },
  ]);
  const result = await core.handleBeforeCompact({ firstKeptEntryId: "entry-3", tokensBefore: 1234 });
  assert.equal(result.compaction.firstKeptEntryId, "entry-3");
  assert.equal(result.compaction.tokensBefore, 1234);
  assert.equal(result.compaction.details.source, "openviking");
  assert.match(result.compaction.summary, /compact overview/);
  assert.equal(core.state.coveredUserTurns, 0);
  assert.equal(core.state.pendingTokens, 0);
});

test("handleBeforeCompact fail-opens without firstKeptEntryId or overview", async () => {
  const { core } = makeCore({ overviews: [""] });
  assert.equal(await core.handleBeforeCompact({ tokensBefore: 1 }), undefined);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "x", tokensBefore: 1 }), undefined);
});

test("disabled takeover is a passthrough", async () => {
  const { core, calls } = makeCore({ config: { takeoverEnabled: false } });
  const messages = [user("one"), user("two")];
  assert.equal(core.transformContext(messages), messages);
  assert.equal(await core.onTurnSynced(999), false);
  assert.equal(await core.commitAndAdvance(), false);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "x" }), undefined);
  assert.equal(calls.committed, 0);
});

test("shutdown persists deduped state once", async () => {
  const { core, calls } = makeCore({ watermark: 9 });
  core.transformContext([user("one")]);
  await core.shutdown();
  await core.shutdown();
  assert.equal(calls.persisted.length, 1);
  assert.equal(calls.persisted[0].data.syncedEntryCount, 9);
});
