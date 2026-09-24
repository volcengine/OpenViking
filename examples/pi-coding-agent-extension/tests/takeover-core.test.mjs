import test from "node:test";
import assert from "node:assert/strict";
import {
  OVERVIEW_MARKER,
  TAKEOVER_ENTRY_TYPE,
  TakeoverCore,
  buildOverviewMessage,
  commitOutcome,
  countUndeliveredForSession,
  countUserTurns,
  estimatePayloadTokens,
  estimateTokens,
  findBoundaryIndex,
  fingerprintMessage,
  flattenContent,
  isUserTurnStart,
  deriveHistoryUri,
  projectContextEntries,
  truncateToTokens,
} from "../lib/takeover-core.mjs";

// Every message gets its own timestamp, as pi's do: the core maps a user entry
// onto the context's messages by it.
let clock = 0;
let nextId = 0;

function msg(role, content, extra = {}) {
  clock += 1;
  return { role, content, timestamp: clock, ...extra };
}

function user(text) {
  return msg("user", text);
}

function assistant(text) {
  return msg("assistant", text);
}

function system(text, extra = {}) {
  return msg("system", text, extra);
}

/**
 * A pi branch: messages become `message` entries, `STATE` becomes the
 * `ov-takeover` custom entry takeover itself appends between turns. That entry
 * sits in front of every following user turn in a real session, which is what
 * the plain-message branches of earlier tests never showed.
 */
const STATE = Symbol("state");
function branchOf(...items) {
  return items.map((item) => {
    nextId += 1;
    if (item === STATE) return { id: `c${nextId}`, type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {} };
    if (item?.type) return { id: `x${nextId}`, ...item };
    return { id: `m${nextId}`, type: "message", message: item };
  });
}

/** The messages pi's `context` hook sees for a branch. */
function contextOf(branch) {
  return projectContextEntries(branch).flatMap((entry) => {
    if (entry.type === "message") return [entry.message];
    if (entry.type === "compaction") return [{ role: "compactionSummary", summary: entry.summary, timestamp: 0 }];
    if (entry.type === "branch_summary") return [{ role: "branchSummary", summary: entry.summary, timestamp: 0 }];
    if (entry.type === "custom_message") return [{ role: "custom", content: entry.content, timestamp: 0 }];
    return [];
  });
}

function makeCore(overrides = {}) {
  const calls = {
    synced: 0,
    flushed: 0,
    committed: 0,
    persisted: [],
    slept: [],
    logs: [],
    overviewUris: [],
    tools: overrides.tools ?? [],
  };
  let watermark = overrides.watermark ?? 0;
  let dropped = overrides.dropped ?? 0;
  const io = {
    syncBranch: async (branch) => {
      calls.synced++;
      calls.lastSyncBranch = branch;
      return overrides.syncResult ?? { added: 0, tokens: 0, allDelivered: true };
    },
    flush: async () => {
      calls.flushed++;
      return overrides.flushResult ?? true;
    },
    commit: async (opts) => {
      calls.committed++;
      calls.lastCommitOpts = opts;
      // A concrete archive URI lets the poller bind the summary to this commit.
      return overrides.commitResult === undefined
        ? {
            status: "accepted",
            archived: true,
            task_id: "t-1",
            archive_uri: "viking://user/x/sessions/s/history/archive_001",
          }
        : overrides.commitResult;
    },
    readArchiveOverview: async (uri) => {
      calls.overviewUris.push(uri);
      const values = overrides.overviews ?? ["overview ready"];
      const value = values[Math.min(calls.overviewCalls || 0, values.length - 1)];
      calls.overviewCalls = (calls.overviewCalls || 0) + 1;
      return value;
    },
    // What sync's capture path sends: conversational messages only.
    captureCount: (slice) => slice.filter((entry) =>
      entry?.type === "message" && entry.message?.role !== "system").length,
    persistEntry: (type, data) => calls.persisted.push({ type, data }),
    getWatermark: () => watermark,
    droppedCount: () => dropped,
    availableTools: () => calls.tools,
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
  return {
    core,
    calls,
    setWatermark: (n) => { watermark = n; },
    setDropped: (n) => { dropped = n; },
  };
}

function restoreBoundary(core, coveredThroughEntryId, data = {}) {
  core.restore([{
    type: "custom",
    customType: TAKEOVER_ENTRY_TYPE,
    data: { coveredThroughEntryId, coveredUserTurns: 1, overview: "archived first turn", pendingTokens: 0, ...data },
  }]);
}

test("flattenContent handles strings and text arrays", () => {
  assert.equal(flattenContent({ role: "user", content: "hello" }), "hello");
  assert.equal(flattenContent({ role: "user", content: [{ type: "text", text: "a" }, { type: "image" }, { type: "text", text: "b" }] }), "ab");
  assert.equal(flattenContent({ role: "user", content: null }), "");
});

test("fingerprintMessage includes role length and 200-char prefix", () => {
  const fp = fingerprintMessage({ role: "user", content: "x".repeat(250) });
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

test("buildOverviewMessage does not promise an unavailable tool", () => {
  const message = buildOverviewMessage("summary", 1, 1000);
  assert.doesNotMatch(message.content, /openviking_search/);
  assert.doesNotMatch(message.content, /viking_archive_expand/);
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

test("commitOutcome requires this commit's archive URI", () => {
  assert.deepEqual(commitOutcome(null), { accepted: false, reason: "no_result" });
  assert.deepEqual(
    commitOutcome({ status: "skipped", archived: false, archive_uri: null, reason: "no_messages" }),
    { accepted: false, reason: "skipped:no_messages" },
  );
  assert.deepEqual(commitOutcome({ status: "accepted", archived: false, archive_uri: "viking://x" }), {
    accepted: false, reason: "not_archived",
  });
  assert.deepEqual(commitOutcome({ status: "accepted", archived: true }), {
    accepted: false, reason: "no_archive_uri",
  });
  assert.deepEqual(commitOutcome({ status: "failed", archived: true, archive_uri: "viking://bad" }), {
    accepted: false, reason: "status:failed",
  });
  assert.deepEqual(commitOutcome({ archive_uri: "viking://user/u/sessions/s/history/archive_002", task_id: "t" }), {
    accepted: true, reason: "accepted", archiveUri: "viking://user/u/sessions/s/history/archive_002", taskId: "t",
  });
  assert.equal(deriveHistoryUri("viking://user/u/sessions/s/history/archive_002"), "viking://user/u/sessions/s/history");
  assert.equal(deriveHistoryUri("viking://unexpected/archive"), "");
});

test("projectContextEntries follows pi's compaction and context-edit projection", () => {
  const head = branchOf(system("BASE"), user("one"), assistant("a1"), STATE, user("two"), assistant("a2"));
  assert.deepEqual(projectContextEntries(head), head);

  const compaction = { type: "compaction", summary: "pi summary", firstKeptEntryId: head[4].id };
  const [compactionEntry, ...after] = branchOf(compaction, user("three"));
  const compacted = [...head, compactionEntry, ...after];
  assert.deepEqual(
    projectContextEntries(compacted).map((entry) => entry.id),
    [compactionEntry.id, head[4].id, head[5].id, after[0].id],
  );

  const [removal, replacement] = branchOf(
    { type: "context_edit", targetId: head[2].id, replacement: null },
    { type: "context_edit", targetId: head[5].id, replacement: { content: "edited" } },
  );
  const edited = projectContextEntries([...head, removal, replacement]).map((entry) => entry.id);
  assert.equal(edited.includes(head[2].id), false);
  assert.equal(edited.includes(head[5].id), true);
});

test("transformContext drops covered turns and injects overview before recall", () => {
  const { core } = makeCore();
  const branch = branchOf(user("first"), assistant("answer"), STATE, user("second"), assistant("answer 2"));
  restoreBoundary(core, branch[2].id);
  const out = core.transformContext(contextOf(branch), branch);
  assert.equal(out.length, 3);
  assert.equal(out[0].role, "user");
  assert.match(out[0].content, /archived first turn/);
  assert.equal(out[0].timestamp, branch[3].message.timestamp - 1);
  assert.equal(out[1].content, "second");
  assert.equal(core.state.coveredUserTurns, 1);
});

test("transformContext is stable between commits", () => {
  const { core } = makeCore();
  const branch = branchOf(user("first"), assistant("answer"), user("second"));
  restoreBoundary(core, branch[1].id);
  const a = JSON.stringify(core.transformContext(contextOf(branch), branch));
  const b = JSON.stringify(core.transformContext(contextOf(branch), () => branch));
  assert.equal(a, b);
});

test("a covered prefix outside the active context is sent in full and applies again on return", () => {
  const { core, calls } = makeCore();
  const covered = branchOf(user("first"), assistant("answer"), STATE, user("second"));
  restoreBoundary(core, covered[2].id);
  assert.equal(core.transformContext(contextOf(covered), covered).length, 2);

  // /tree to a sibling of the covered answer: the prefix is not in this context.
  const other = [covered[0], ...branchOf(assistant("other answer"), user("second"))];
  const messages = contextOf(other);
  assert.equal(core.transformContext(messages, other), messages);
  assert.equal(core.state.coveredThroughEntryId, covered[2].id);
  assert.ok(calls.logs.some((line) => /not in the active context/.test(line)));

  assert.equal(core.transformContext(contextOf(covered), covered).length, 2);
});

test("transformContext waits for a user turn after the covered prefix", () => {
  const { core } = makeCore();
  const branch = branchOf(user("first"), assistant("answer"));
  restoreBoundary(core, branch[1].id);
  const messages = contextOf(branch);
  assert.equal(core.transformContext(messages, branch), messages);
});

test("transformContext keeps what a run appended after a keepRecentTurns 0 cut", () => {
  const { core } = makeCore();
  // Frozen at the tip after a tool-use turn; the run went on before the next
  // user turn, and no archive covers those messages.
  const branch = branchOf(
    user("first"), assistant("tool call"), msg("toolResult", "output"),
    STATE, assistant("final answer"), user("second"),
  );
  restoreBoundary(core, branch[2].id);
  const out = core.transformContext(contextOf(branch), branch);
  assert.match(out[0].content, /archived first turn/);
  assert.deepEqual(out.slice(1).map((message) => message.content), ["final answer", "second"]);
});

test("a branch summary /tree left at the boundary is kept", () => {
  const { core } = makeCore();
  // /tree back to the first kept turn: pi hangs the summary of the abandoned
  // turns off the boundary entry, and the retried prompt follows it.
  const branch = branchOf(
    user("first"), assistant("answer"),
    { type: "branch_summary", summary: "abandoned second turn", fromId: "m-old" },
    user("second, retried"),
  );
  restoreBoundary(core, branch[1].id);
  const out = core.transformContext(contextOf(branch), branch);
  assert.deepEqual(out.map((message) => message.role), ["user", "branchSummary", "user"]);
  assert.equal(out[1].summary, "abandoned second turn");
});

test("messages that disagree with the branch between the prefix and the kept turn trim nothing", () => {
  const { core } = makeCore();
  const branch = branchOf(user("first"), assistant("answer"), assistant("more"), user("second"));
  restoreBoundary(core, branch[1].id);
  const messages = contextOf(branch);
  // Another extension's context handler replaced a message in that stretch.
  const rewritten = [...messages.slice(0, 2), { role: "custom", content: "injected", timestamp: 0 }, messages[3]];
  assert.equal(core.transformContext(rewritten, branch), rewritten);
});

test("transformContext keeps covered-region system messages in original order", () => {
  const { core } = makeCore();
  const baseSystem = system("BASE PROMPT", { toolsAdded: [{ name: "read" }] });
  const midSystem = system("added a tool", { toolsAdded: [{ name: "openviking_search" }] });
  const branch = branchOf(baseSystem, user("first"), assistant("answer"), midSystem, user("second"), assistant("answer 2"));
  restoreBoundary(core, branch[3].id);
  const out = core.transformContext(contextOf(branch), branch);
  // Both covered system messages survive, in order, ahead of the overview.
  assert.equal(out[0], baseSystem);
  assert.equal(out[1], midSystem);
  assert.equal(out[2].role, "user");
  assert.match(out[2].content, /archived first turn/);
  assert.equal(out[3].content, "second");
  // Nothing is dropped from the retained tail, and no system is duplicated.
  assert.equal(out.filter((m) => m.role === "system").length, 2);
});

test("transformContext leaves a system message inside the retained tail in place", () => {
  const { core } = makeCore();
  const keptSystem = system("tool removed mid-tail", { toolsRemoved: [{ name: "read" }] });
  const branch = branchOf(system("BASE"), user("first"), assistant("answer"), user("second"), keptSystem, assistant("answer 2"));
  restoreBoundary(core, branch[2].id);
  const out = core.transformContext(contextOf(branch), branch);
  // The covered BASE is hoisted before the overview; the tail system stays put.
  assert.equal(out[0].content, "BASE");
  assert.match(out[1].content, /archived/);
  assert.equal(out[2].content, "second");
  assert.equal(out[3], keptSystem);
});

test("transformContext finds the kept turn when pi 0.87 hides system messages from the hook", () => {
  const { core } = makeCore();
  const branch = branchOf(system("BASE"), user("first"), assistant("answer"), system("mid"), user("second"));
  restoreBoundary(core, branch[3].id);
  const withoutSystem = contextOf(branch).filter((message) => message.role !== "system");
  const out = core.transformContext(withoutSystem, branch);
  assert.equal(out.length, 2);
  assert.equal(out[1].content, "second");
});

test("a count-based boundary is adopted as the entry in front of its first kept turn", () => {
  const { core } = makeCore();
  const branch = branchOf(user("first"), assistant("answer"), STATE, user("second"), assistant("answer 2"));
  const messages = contextOf(branch);
  core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {
    coveredUserTurns: 1, overview: "legacy", fingerprint: fingerprintMessage(messages[1]), pendingTokens: 0,
  } }]);
  const out = core.transformContext(messages, branch);
  assert.equal(out.length, 3);
  assert.match(out[0].content, /legacy/);
  assert.equal(core.state.coveredThroughEntryId, branch[2].id);
  assert.equal(core.persistedState().coveredThroughEntryId, branch[2].id);
  assert.equal("fingerprint" in core.persistedState(), false);
});

test("a count-based boundary whose fingerprint no longer matches is dropped", () => {
  const { core } = makeCore();
  const branch = branchOf(user("first"), assistant("new answer"), user("second"));
  core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {
    coveredUserTurns: 1, overview: "legacy", fingerprint: fingerprintMessage(assistant("old answer")), pendingTokens: 0,
  } }]);
  // Until the context hook sees the session, the fingerprint is carried along.
  assert.equal(typeof core.persistedState().fingerprint, "string");
  const messages = contextOf(branch);
  assert.equal(core.transformContext(messages, branch), messages);
  assert.equal(core.state.coveredThroughEntryId, "");
  assert.equal(core.state.coveredUserTurns, 0);
});

test("restore uses the last ov-takeover entry and restores syncedEntryCount", () => {
  const { core } = makeCore();
  core.restore([
    { type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: { coveredThroughEntryId: "old", coveredUserTurns: 1, overview: "old", pendingTokens: 3, syncedEntryCount: 10 } },
    { type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: { coveredThroughEntryId: "new", coveredUserTurns: 2, overview: "new", pendingTokens: 7, syncedEntryCount: 12 } },
  ]);
  assert.equal(core.state.coveredThroughEntryId, "new");
  assert.equal(core.state.coveredUserTurns, 2);
  assert.equal(core.state.overview, "new");
  assert.equal(core.state.pendingTokens, 7);
  assert.equal(core.state.syncedEntryCount, 12);
});

test("onTurnSynced waits for threshold and enough user turns", async () => {
  const { core, calls } = makeCore({ config: { takeoverTokenThreshold: 50, takeoverKeepRecentTurns: 3 } });
  const short = branchOf(user("one"), STATE, user("two"));
  assert.equal(await core.onTurnSynced(60, short), false);
  assert.equal(calls.committed, 0);

  const enough = branchOf(user("one"), STATE, user("two"), STATE, user("three"), STATE, user("four"));
  assert.equal(await core.onTurnSynced(0, enough), true);
  assert.equal(calls.committed, 1);
});

test("commitAndAdvance advances to an entry id and the next context is trimmed", async () => {
  const { core, calls, setWatermark } = makeCore({
    watermark: 4,
    overviews: ["", "fresh overview"],
  });
  const branch = branchOf(user("one"), assistant("a"), STATE, user("two"), assistant("b"), STATE, user("three"));
  setWatermark(7);
  // The summary is not there right after the commit; turn_end does not wait.
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.ok(core.state.pendingArchive);
  assert.deepEqual(calls.slept, []);
  // The next turn reads it once and advances.
  assert.equal(await core.onTurnSynced(0, branch), true);
  assert.equal(core.state.coveredThroughEntryId, branch[5].id);
  assert.equal(core.state.coveredUserTurns, 2);
  assert.equal(core.state.overview, "fresh overview");
  assert.equal(core.state.pendingTokens, 0);
  assert.equal(core.state.syncedEntryCount, 7);
  assert.equal(calls.flushed, 1);
  assert.equal(calls.committed, 1);
  assert.equal(calls.lastCommitOpts.queueOnFailure, false);
  assert.equal(calls.lastCommitOpts.keepRecentCount, 1);
  assert.deepEqual(calls.slept, []);
  // Once for the pending archive, once for the advance.
  assert.equal(calls.persisted.length, 2);
  assert.equal(calls.persisted[1].type, TAKEOVER_ENTRY_TYPE);
  assert.equal(calls.persisted[1].data.coveredThroughEntryId, branch[5].id);
  assert.deepEqual(calls.overviewUris, [
    "viking://user/x/sessions/s/history/archive_001",
    "viking://user/x/sessions/s/history/archive_001",
  ]);

  // The state entry takeover just persisted lands in front of the next turn.
  const next = [...branch, ...branchOf(assistant("c"), STATE, user("four"))];
  const out = core.transformContext(contextOf(next), next);
  assert.match(out[0].content, /fresh overview/);
  assert.deepEqual(out.slice(1).map((message) => message.content), ["three", "c", "four"]);
});

test("keep_recent_count counts captured messages after the covered prefix", async () => {
  const { core, calls } = makeCore({ config: { takeoverKeepRecentTurns: 1 } });
  const branch = branchOf(
    system("base"), user("one"), assistant("one answer"),
    STATE, user("two"), assistant("tool one"), system("tool added"), assistant("tool two"),
  );
  assert.equal(await core.onTurnSynced(120, branch), true);
  assert.equal(calls.lastCommitOpts.keepRecentCount, 3);
  assert.equal(core.state.coveredThroughEntryId, branch[3].id);
});

test("keepRecentTurns zero archives every captured message and cuts at the tip", async () => {
  const { core, calls } = makeCore({ config: { takeoverKeepRecentTurns: 0 } });
  const branch = branchOf(user("one"), assistant("answer"));
  assert.equal(await core.onTurnSynced(120, branch), true);
  assert.equal(calls.lastCommitOpts.keepRecentCount, 0);
  assert.equal(core.state.coveredThroughEntryId, branch[1].id);
  assert.equal(core.state.coveredUserTurns, 1);

  const next = [...branch, ...branchOf(STATE, user("two"))];
  const out = core.transformContext(contextOf(next), next);
  assert.deepEqual(out.slice(1).map((message) => message.content), ["two"]);
});

test("the boundary is counted on the context pi rebuilt after its own compaction", async () => {
  const { core, calls } = makeCore({ config: { takeoverKeepRecentTurns: 1 } });
  const head = branchOf(user("one"), assistant("a1"), STATE, user("two"), assistant("a2"));
  const tail = branchOf(
    { type: "compaction", summary: "pi summary", firstKeptEntryId: head[3].id },
    STATE, user("three"), assistant("a3"), STATE, user("four"),
  );
  const branch = [...head, ...tail];
  assert.equal(await core.onTurnSynced(120, branch), true);
  // Pre-compaction turn one is not part of the active context and not counted.
  assert.equal(core.state.coveredUserTurns, 2);
  assert.equal(core.state.coveredThroughEntryId, tail[4].id);
  assert.equal(calls.lastCommitOpts.keepRecentCount, 1);

  const out = core.transformContext(contextOf(branch), branch);
  assert.deepEqual(out.slice(1).map((message) => message.content), ["four"]);
});

test("commitAndAdvance keeps pending tokens when flush fails", async () => {
  const { core, calls } = makeCore({ flushResult: false });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(core.state.pendingTokens, 120);
  assert.equal(calls.committed, 0);
  assert.equal(core.state.coveredThroughEntryId, "");
  // A postponed attempt is not a state transition worth a session entry.
  assert.equal(calls.persisted.length, 0);
});

test("below-threshold turns do not append takeover state entries", async () => {
  const { core, calls } = makeCore();
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(10, branch), false);
  assert.equal(await core.onTurnSynced(10, branch), false);
  assert.equal(calls.persisted.length, 0);
  assert.equal(core.state.pendingTokens, 20);
});

test("commitAndAdvance persists the same pending archive until its overview is ready", async () => {
  const { core, calls } = makeCore({ overviews: ["", "", ""] });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(core.state.coveredThroughEntryId, "");
  assert.equal(core.state.pendingTokens, 120);
  assert.equal(core.state.pendingArchive.archiveUri, "viking://user/x/sessions/s/history/archive_001");
  assert.equal(core.state.pendingArchive.coveredThroughEntryId, branch[0].id);
  assert.equal(calls.committed, 1);
  assert.equal(calls.persisted.length, 1);
  assert.equal(await core.onTurnSynced(10, branch), false);
  assert.equal(calls.committed, 1);
  assert.equal(core.state.pendingTokens, 130);
});

test("pending archive survives restore and later advances without another commit", async () => {
  const first = makeCore({ overviews: ["", "", ""] });
  const branch = branchOf(user("one"), assistant("a"), user("two"));
  assert.equal(await first.core.onTurnSynced(120, branch), false);
  const saved = first.core.persistedState();

  const resumed = makeCore({ overviews: ["restored overview"] });
  resumed.core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: saved }]);
  assert.equal(await resumed.core.onTurnSynced(7, branch), true);
  assert.equal(resumed.calls.committed, 0);
  assert.equal(resumed.core.state.pendingArchive, null);
  assert.equal(resumed.core.state.coveredThroughEntryId, branch[1].id);
  assert.equal(resumed.core.state.pendingTokens, 7);
});

test("a pending archive without a boundary entry is dropped instead of advanced", async () => {
  const { core, calls } = makeCore();
  core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {
    coveredUserTurns: 0, overview: "", pendingTokens: 120,
    pendingArchive: { archiveUri: "viking://user/x/sessions/s/history/archive_001", coveredUserTurns: 1, fingerprint: "x" },
  } }]);
  assert.equal(await core.onTurnSynced(0, branchOf(user("one"), user("two"))), false);
  assert.equal(core.state.pendingArchive, null);
  assert.equal(core.state.coveredThroughEntryId, "");
  assert.equal(calls.committed, 0);
});

test("old state without archive fields remains compatible", () => {
  const { core } = makeCore();
  core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {
    coveredUserTurns: 1, overview: "legacy", pendingTokens: 4, syncedEntryCount: 2,
  } }]);
  assert.equal(core.state.pendingArchive, null);
  assert.equal(core.state.captureGap, false);
  assert.equal(core.state.archiveUri, "");
  assert.equal(core.state.historyUri, "");
  const branch = branchOf(user("one"), assistant("a"), user("two"));
  const out = core.transformContext(contextOf(branch), branch);
  assert.match(out[0].content, /legacy/);
  assert.doesNotMatch(out[0].content, /openviking_list/);
});

test("capture gap survives restore and blocks takeover plus native compaction", async () => {
  const { core, calls } = makeCore();
  const branch = branchOf(user("one"), user("two"));
  core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {
    coveredUserTurns: 0, overview: "", pendingTokens: 120, captureGap: true,
  } }]);
  assert.equal(await core.commitAndAdvance(branch), false);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "pi-kept" }, branch), undefined);
  assert.equal(calls.committed, 0);
});

test("skipped, not archived and missing URI results never advance", async () => {
  const branch = branchOf(user("one"), user("two"));
  for (const commitResult of [
    { status: "skipped", archived: false, archive_uri: null, reason: "no_messages" },
    { status: "accepted", archived: false, archive_uri: "viking://bad" },
    { status: "accepted", archived: true },
  ]) {
    const { core, calls } = makeCore({ commitResult });
    assert.equal(await core.onTurnSynced(120, branch), false);
    assert.equal(core.state.coveredThroughEntryId, "");
    assert.equal(calls.overviewUris.length, 0);
  }
});

test("archive overview read failures are logged and keep the boundary pending", async () => {
  const { core, calls } = makeCore({
    io: { readArchiveOverview: async () => { throw new Error("storage failed"); } },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.ok(core.state.pendingArchive);
  assert.ok(calls.logs.some((line) => /overview read failed.*storage failed/.test(line)));
});

test("a delayed summary preserves token pressure from messages arriving while waiting", async () => {
  const { core, calls } = makeCore({ overviews: ["", "ready"] });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(await core.onTurnSynced(17, branch), true);
  assert.equal(calls.committed, 1);
  assert.equal(core.state.pendingTokens, 17);
});

test("messages appended while an archive is pending are not included in its frozen boundary", async () => {
  const { core, calls } = makeCore({ overviews: ["", "ready"] });
  const initial = branchOf(user("one"), assistant("a"), STATE, user("two"));
  assert.equal(await core.onTurnSynced(120, initial), false);
  const extended = [...initial, ...branchOf(assistant("b"), STATE, user("three"))];
  assert.equal(await core.onTurnSynced(9, extended), true);
  assert.equal(core.state.coveredThroughEntryId, initial[2].id);
  assert.equal(core.state.coveredUserTurns, 1);
  assert.equal(core.state.pendingTokens, 9);
  assert.equal(calls.committed, 1);
});

test("queued transient capture is drained before commit", async () => {
  const { core, calls } = makeCore({
    syncResult: { added: 1, tokens: 5, allDelivered: false, queued: 1, permanentFailures: 0 },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), true);
  assert.equal(calls.flushed, 1);
  assert.equal(calls.committed, 1);
});

test("the commit syncs the same branch snapshot it froze", async () => {
  const branch = branchOf(user("one"), user("two"));
  let reads = 0;
  const { core, calls } = makeCore();
  // A getter that would return a longer branch on a second read.
  assert.equal(await core.onTurnSynced(120, () => (reads++ === 0 ? branch : [...branch, ...branchOf(user("x"))])), true);
  assert.equal(calls.lastSyncBranch, branch);
});

test("permanent capture failure persists a gap and blocks later takeover", async () => {
  const { core, calls } = makeCore({
    syncResult: { added: 1, tokens: 5, allDelivered: false, queued: 0, permanentFailures: 1 },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(core.state.captureGap, true);
  assert.equal(calls.committed, 0);
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(calls.synced, 1);
});

test("the advance is abandoned when a fork replaces the covered prefix, even with equal content", async () => {
  const initial = branchOf(user("one"), assistant("same"), user("two"));
  let active = initial;
  const { core, calls } = makeCore({
    io: {
      readArchiveOverview: async (uri) => {
        calls.overviewUris.push(uri);
        active = [initial[0], ...branchOf(assistant("same"), user("two"))];
        return "fresh overview";
      },
    },
  });
  assert.equal(await core.onTurnSynced(120, () => active), false);
  assert.equal(core.state.coveredThroughEntryId, "");
  assert.equal(core.state.captureGap, false);
  assert.match(calls.logs.at(-1), /boundary no longer/);
});

test("the advance holds for append-only growth and a fork after the covered prefix", async () => {
  const initial = branchOf(user("one"), assistant("answer one"), user("two"), assistant("answer two"));
  for (const change of [
    () => [...initial, ...branchOf(user("three"))],
    () => [...initial.slice(0, 2), ...branchOf(user("forked"))],
  ]) {
    let active = initial;
    const run = makeCore({
      io: {
        readArchiveOverview: async (uri) => {
          run.calls.overviewUris.push(uri);
          active = change();
          return "fresh overview";
        },
      },
    });
    assert.equal(await run.core.onTurnSynced(120, () => active), true);
    assert.equal(run.core.state.coveredThroughEntryId, initial[1].id);
  }
});

test("two consecutive archives advance one complete user turn at a time", async () => {
  let nextArchive = 0;
  const { core, calls } = makeCore({
    io: {
      commit: async (opts) => {
        calls.committed++;
        calls.lastCommitOpts = opts;
        nextArchive++;
        return { status: "accepted", archived: true, archive_uri: `viking://user/x/sessions/s/history/archive_00${nextArchive}` };
      },
    },
  });
  const first = branchOf(user("one"), assistant("a"), STATE, user("two"));
  assert.equal(await core.onTurnSynced(120, first), true);
  assert.equal(core.state.coveredUserTurns, 1);

  const second = [...first, ...branchOf(assistant("b"), STATE, user("three"))];
  // Nothing new to cover before the next user turn arrives.
  assert.equal(await core.onTurnSynced(120, first), false);
  assert.equal(await core.onTurnSynced(0, second), true);
  assert.equal(core.state.coveredUserTurns, 2);
  assert.equal(core.state.coveredThroughEntryId, second[5].id);
  assert.equal(calls.overviewUris.at(-1), "viking://user/x/sessions/s/history/archive_002");
  assert.equal(calls.committed, 2);
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
  const branch = branchOf(user("one"), user("two"));
  const first = core.commitAndAdvance(branch);
  const second = core.commitAndAdvance(branch);
  assert.equal(await second, false);
  release();
  assert.equal(await first, true);
  assert.equal(calls.committed, 1);
});

test("handleBeforeCompact returns OV summary and resets boundary", async () => {
  const { core } = makeCore({ overviews: ["compact overview"] });
  core.restore([
    { type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: { coveredThroughEntryId: "m1", coveredUserTurns: 2, overview: "old", pendingTokens: 50 } },
  ]);
  const branch = branchOf(user("one"), assistant("a"));
  const result = await core.handleBeforeCompact({ firstKeptEntryId: "entry-3", tokensBefore: 1234 }, branch);
  assert.equal(result.compaction.firstKeptEntryId, "entry-3");
  assert.equal(result.compaction.tokensBefore, 1234);
  assert.equal(result.compaction.details.source, "openviking");
  assert.match(result.compaction.summary, /compact overview/);
  assert.equal(core.state.coveredThroughEntryId, "");
  assert.equal(core.state.coveredUserTurns, 0);
  assert.equal(core.state.pendingTokens, 0);
});

test("handleBeforeCompact fail-opens without firstKeptEntryId or overview", async () => {
  const { core } = makeCore({ overviews: [""] });
  assert.equal(await core.handleBeforeCompact({ tokensBefore: 1 }), undefined);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "x", tokensBefore: 1 }), undefined);
});

test("handleBeforeCompact uses pi's keep position, archives all messages, and honours cancellation", async () => {
  const branch = branchOf(user("one"), assistant("a"), user("two"));
  const normal = makeCore({ overviews: ["native overview"] });
  const result = await normal.core.handleBeforeCompact(
    { firstKeptEntryId: "pi-kept", tokensBefore: 44, signal: new AbortController().signal },
    branch,
  );
  assert.equal(result.compaction.firstKeptEntryId, "pi-kept");
  assert.equal(normal.calls.lastCommitOpts.keepRecentCount, 0);
  assert.equal(normal.calls.lastSyncBranch, branch);

  const cancelled = makeCore();
  const controller = new AbortController();
  controller.abort();
  assert.equal(await cancelled.core.handleBeforeCompact(
    { firstKeptEntryId: "pi-kept", signal: controller.signal }, branch,
  ), undefined);
  assert.equal(cancelled.calls.committed, 0);
});

test("handleBeforeCompact cancels after an in-flight overview read", async () => {
  const controller = new AbortController();
  const { core } = makeCore({
    io: { readArchiveOverview: async () => { controller.abort(); return "too late"; } },
  });
  assert.equal(await core.handleBeforeCompact(
    { firstKeptEntryId: "pi-kept", signal: controller.signal }, branchOf(user("one")),
  ), undefined);
  assert.equal(core.state.overview, "");
  assert.ok(core.state.pendingArchive);
});

test("handleBeforeCompact catches transport failures and returns control to pi", async () => {
  const { core, calls } = makeCore({ io: { commit: async () => { throw new Error("offline"); } } });
  assert.equal(await core.handleBeforeCompact(
    { firstKeptEntryId: "pi-kept" }, branchOf(user("one")),
  ), undefined);
  assert.ok(calls.logs.some((line) => /native compaction archive failed.*offline/.test(line)));
});

test("native compaction does not start another archive while a takeover summary is pending", async () => {
  const branch = branchOf(user("one"), user("two"));
  const { core, calls } = makeCore({ overviews: ["", "", ""] });
  await core.onTurnSynced(120, branch);
  assert.ok(core.state.pendingArchive);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "pi-kept" }, branch), undefined);
  assert.equal(calls.committed, 1);
});

test("a native archive not summarized in time leaves the takeover boundary alone", async () => {
  const branch = branchOf(user("one"), assistant("a"));
  const first = makeCore({ overviews: ["", "", ""] });
  first.core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: {
    coveredThroughEntryId: branch[0].id, coveredUserTurns: 1, overview: "old overview", pendingTokens: 20,
    archiveUri: "viking://user/x/sessions/s/history/archive_000", historyUri: "viking://user/x/sessions/s/history",
  } }]);
  assert.equal(await first.core.handleBeforeCompact({ firstKeptEntryId: "pi-kept" }, branch), undefined);
  assert.equal(first.core.state.pendingArchive.nativeCompaction, true);
  // Pi may still cancel or fail its own compaction, so the boundary stays.
  assert.equal(first.core.state.coveredThroughEntryId, branch[0].id);
  assert.equal(first.core.state.overview, "old overview");

  const resumed = makeCore({ overviews: ["late native overview"] });
  resumed.core.restore([{ type: "custom", customType: TAKEOVER_ENTRY_TYPE, data: first.core.persistedState() }]);
  assert.equal(await resumed.core.onTurnSynced(5, branch), false);
  assert.equal(resumed.calls.committed, 0);
  assert.equal(resumed.core.state.pendingArchive, null);
  assert.equal(resumed.core.state.coveredThroughEntryId, branch[0].id);
  // The recovery hint keeps naming the archive the current overview came from.
  assert.equal(resumed.core.state.archiveUri, "viking://user/x/sessions/s/history/archive_000");
});

test("turn_end never sleeps waiting for a summary", async () => {
  const { core, calls } = makeCore({ overviews: [""] });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(await core.onTurnSynced(5, branch), false);
  assert.equal(await core.onTurnSynced(5, branch), false);
  assert.deepEqual(calls.slept, []);
  assert.equal(calls.overviewUris.length, 3);
  assert.equal(calls.committed, 1);
});

test("a pending archive that is terminal without a summary is dropped once", async () => {
  const asked = [];
  const { core, calls } = makeCore({
    overviews: [""],
    // `.done` without Working Memory: the server has it disabled.
    io: { archiveState: async (uri) => { asked.push(uri); return "completed"; } },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.ok(core.state.pendingArchive);
  assert.equal(await core.onTurnSynced(10, branch), false);
  assert.deepEqual(asked, ["viking://user/x/sessions/s/history/archive_001"]);
  assert.equal(core.state.pendingArchive, null);
  assert.equal(core.state.coveredThroughEntryId, "");
  // Its frozen pressure is spent, so the next archive waits for fresh pressure.
  assert.equal(core.state.pendingTokens, 10);
  assert.equal(await core.onTurnSynced(10, branch), false);
  assert.equal(calls.committed, 1);
  assert.ok(calls.logs.some((line) => /completed without Working Memory/.test(line)));
});

test("a summary that lands just as its archive completes still advances", async () => {
  const { core } = makeCore({
    overviews: ["", "", "late but ready"],
    io: { archiveState: async () => "completed" },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  assert.equal(await core.onTurnSynced(0, branch), true);
  assert.equal(core.state.overview, "late but ready");
});

test("a pending archive is kept while it is pending or cannot be asked", async () => {
  for (const status of ["pending", null]) {
    const { core } = makeCore({ overviews: [""], io: { archiveState: async () => status } });
    const branch = branchOf(user("one"), user("two"));
    await core.onTurnSynced(120, branch);
    await core.onTurnSynced(0, branch);
    assert.ok(core.state.pendingArchive, String(status));
  }
});

test("a pending archive whose boundary left the context is dropped without a read", async () => {
  const initial = branchOf(user("one"), assistant("a"), user("two"));
  const { core, calls } = makeCore({ overviews: [""] });
  assert.equal(await core.onTurnSynced(120, initial), false);
  const reads = calls.overviewUris.length;
  const forked = [initial[0], ...branchOf(assistant("other"), user("two"))];
  assert.equal(await core.onTurnSynced(0, forked), false);
  assert.equal(calls.overviewUris.length, reads);
  assert.equal(core.state.pendingArchive, null);
});

test("resumePending advances before the next prompt", async () => {
  const { core, calls } = makeCore({ overviews: ["", "ready"] });
  const branch = branchOf(user("one"), assistant("a"), user("two"), assistant("b"));
  assert.equal(await core.onTurnSynced(120, branch), false);
  const next = [...branch, ...branchOf(user("three"))];
  assert.equal(await core.resumePending(next), true);
  assert.equal(calls.committed, 1);
  const out = core.transformContext(contextOf(next), next);
  assert.deepEqual(out.slice(1).map((message) => message.content), ["two", "b", "three"]);
  assert.equal(await core.resumePending(next), false);
});

test("the drain and the commit only get what is left of the handler budget", async () => {
  let clock = 1_000_000;
  const flushBudgets = [];
  const { core, calls } = makeCore({
    io: {
      now: () => clock,
      flush: async (budgetMs) => { flushBudgets.push(budgetMs); clock += 4_000; return true; },
    },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch, { deadline: clock + 25_000 }), true);
  assert.deepEqual(flushBudgets, [15_000]);
  assert.equal(calls.lastCommitOpts.timeoutMs, 16_000);
});

test("a commit that would run past the handler deadline is postponed", async () => {
  let clock = 1_000_000;
  const { core, calls } = makeCore({
    io: { now: () => clock, flush: async () => { clock += 18_000; return true; } },
  });
  const branch = branchOf(user("one"), user("two"));
  assert.equal(await core.onTurnSynced(120, branch, { deadline: clock + 25_000 }), false);
  assert.equal(calls.committed, 0);
  assert.equal(core.state.pendingTokens, 120);
  assert.ok(calls.logs.some((line) => /budget spent before commit/.test(line)));
});

test("native compaction polls only until the handler deadline", async () => {
  let clock = 1_000_000;
  const deadline = clock + 25_000;
  const branch = branchOf(user("one"), assistant("a"), user("two"));
  const { core, calls } = makeCore({
    overviews: [""],
    config: { takeoverOverviewPollMs: 2_000, takeoverOverviewPollMax: 15 },
    io: { now: () => clock, sleep: async (ms) => { calls.slept.push(ms); clock += ms; } },
  });
  restoreBoundary(core, branch[1].id);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "pi-kept" }, branch, { deadline }), undefined);
  assert.ok(clock <= deadline);
  assert.ok(calls.slept.length < 14);
  assert.equal(core.state.coveredThroughEntryId, branch[1].id);
  assert.equal(core.state.pendingArchive.nativeCompaction, true);
});

test("recovery hint is outside the overview budget and requires list plus read", () => {
  const archiveUri = "viking://user/x/sessions/s/history/archive_001";
  const branch = branchOf(user("one"), assistant("a"), user("two"));
  const both = makeCore({ tools: ["openviking_list", "openviking_read", "openviking_grep"] });
  restoreBoundary(both.core, branch[1].id, {
    overview: "x".repeat(1000), archiveUri, historyUri: "viking://user/x/sessions/s/history",
  });
  const out = both.core.transformContext(contextOf(branch), branch);
  assert.match(out[0].content, /openviking_list/);
  assert.match(out[0].content, /openviking_read/);
  assert.match(out[0].content, /offset and limit/);
  assert.match(out[0].content, /captured historical messages/);
  assert.ok(out[0].content.length > 1000);

  const missingRead = makeCore({ tools: ["openviking_list"] });
  restoreBoundary(missingRead.core, branch[1].id, {
    overview: "summary", archiveUri, historyUri: "viking://user/x/sessions/s/history",
  });
  const noHint = missingRead.core.transformContext(contextOf(branch), branch);
  assert.doesNotMatch(noHint[0].content, /openviking_list/);
});

test("native compaction summary uses the same recovery hint", async () => {
  const { core } = makeCore({
    tools: ["openviking_list", "openviking_read"],
    overviews: ["compact overview"],
  });
  const result = await core.handleBeforeCompact(
    { firstKeptEntryId: "pi-kept", tokensBefore: 100 }, branchOf(user("one")),
  );
  assert.match(result.compaction.summary, /captured historical messages/);
  assert.match(result.compaction.summary, /openviking_list/);
  assert.match(result.compaction.summary, /openviking_read/);
});

test("disabled takeover is a passthrough", async () => {
  const { core, calls } = makeCore({ config: { takeoverEnabled: false } });
  const branch = branchOf(user("one"), user("two"));
  const messages = contextOf(branch);
  assert.equal(core.transformContext(messages, branch), messages);
  assert.equal(await core.onTurnSynced(999, branch), false);
  assert.equal(await core.commitAndAdvance(branch), false);
  assert.equal(await core.handleBeforeCompact({ firstKeptEntryId: "x" }), undefined);
  assert.equal(calls.committed, 0);
});

test("shutdown persists deduped state once", async () => {
  const { core, calls } = makeCore({ watermark: 9 });
  core.transformContext([user("one")], []);
  await core.shutdown();
  await core.shutdown();
  assert.equal(calls.persisted.length, 1);
  assert.equal(calls.persisted[0].data.syncedEntryCount, 9);
});
