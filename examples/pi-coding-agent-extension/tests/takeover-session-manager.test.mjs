import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { TakeoverCore } from "../lib/takeover-core.mjs";

const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * pi's real SessionManager, wherever pi is installed; the version-matrix gate
 * points PI_SESSION_MANAGER at a specific pi. The unit suite's hand-built
 * branches cannot prove that the boundary survives what a live session puts
 * between turns — takeover's own state entries, pi's compaction entries, tree
 * navigation — so these checks drive the real session projection and skip
 * only when no pi is resolvable.
 */
function findSessionManager() {
  if (process.env.PI_SESSION_MANAGER !== undefined) {
    const explicit = process.env.PI_SESSION_MANAGER;
    return explicit && existsSync(explicit) ? explicit : "";
  }
  const candidates = [];
  try {
    candidates.push(createRequire(join(EXTENSION_DIR, "package.json"))
      .resolve("@earendil-works/pi-coding-agent/dist/core/session-manager.js"));
  } catch {
    // not installed locally
  }
  candidates.push(
    "/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/dist/core/session-manager.js",
    "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/core/session-manager.js",
  );
  return candidates.find((path) => path && existsSync(path)) ?? "";
}

const SESSION_MANAGER_PATH = findSessionManager();

async function openSession(t, config = {}) {
  if (!SESSION_MANAGER_PATH) {
    t.skip("pi SessionManager not resolvable");
    return null;
  }
  const { SessionManager } = await import(SESSION_MANAGER_PATH);
  const sm = SessionManager.inMemory("/tmp");
  const logs = [];
  let archives = 0;
  const core = new TakeoverCore({
    config: {
      takeoverEnabled: true,
      takeoverTokenThreshold: 100,
      takeoverKeepRecentTurns: 1,
      takeoverOverviewPollMs: 0,
      takeoverOverviewPollMax: 1,
      ...config,
    },
    io: {
      syncBranch: async () => ({ added: 0, tokens: 0, allDelivered: true, permanentFailures: 0 }),
      flush: async () => true,
      commit: async () => ({
        status: "accepted",
        archived: true,
        archive_uri: `viking://user/u/sessions/pi-s/history/archive_${String(++archives).padStart(3, "0")}`,
      }),
      readArchiveOverview: async () => "OV SUMMARY",
      captureCount: (slice) => slice.filter((entry) =>
        entry?.type === "message" && entry.message?.role !== "system").length,
      // Exactly how takeover.ts persists: a custom entry at the session leaf.
      persistEntry: (type, data) => sm.appendCustomEntry(type, data),
      getWatermark: () => sm.getEntries().length,
      log: (message) => logs.push(message),
    },
  });
  let clock = 1_000;
  const text = (value) => [{ type: "text", text: value }];
  /** One prompt: the user message, the context hook, the reply, then turn_end. */
  async function turn(label) {
    const userId = sm.appendMessage({ role: "user", content: text(label), timestamp: clock++ });
    // pi >= 0.87 hides system messages from the `context` hook.
    const context = sm.buildSessionContext().messages.filter((message) => message.role !== "system");
    const out = core.transformContext(context, sm.getBranch());
    sm.appendMessage({
      role: "assistant", content: text(`ack ${label}`), timestamp: clock++,
      api: "test", provider: "test", model: "test", usage: {}, stopReason: "stop",
    });
    await core.onTurnSynced(150, () => sm.getBranch());
    return { userId, trimmed: out.length < context.length, out };
  }
  return { sm, core, logs, turn };
}

test("takeover state entries between turns do not reset the boundary", async (t) => {
  const session = await openSession(t);
  if (!session) return;
  const trimmed = [];
  for (let i = 1; i <= 5; i++) {
    if ((await session.turn(`turn ${i}`)).trimmed) trimmed.push(i);
  }
  assert.deepEqual(trimmed, [3, 4, 5]);
  assert.ok(session.sm.getBranch().some((entry) => entry.type === "custom"));
  assert.equal(session.logs.some((line) => /boundary reset/.test(line)), false);
});

test("takeover keeps trimming after pi's own compaction", async (t) => {
  const session = await openSession(t);
  if (!session) return;
  const trimmed = [];
  for (let i = 1; i <= 8; i++) {
    const { userId, trimmed: cut } = await session.turn(`turn ${i}`);
    if (cut) trimmed.push(i);
    if (i === 4) {
      assert.ok(await session.core.handleBeforeCompact({ firstKeptEntryId: userId, tokensBefore: 1 }, () => session.sm.getBranch()));
      session.sm.appendCompaction("pi summary", userId, 1);
    }
  }
  // Turn 5 is the first after the compaction absorbed the boundary.
  assert.deepEqual(trimmed, [3, 4, 6, 7, 8]);
});

test("the boundary follows tree navigation away from and back to the covered branch", async (t) => {
  const session = await openSession(t);
  if (!session) return;
  const { sm, core, turn } = session;
  const first = await turn("turn 1");
  await turn("turn 2");
  assert.equal((await turn("turn 3")).trimmed, true);
  const coveredLeaf = sm.getLeafId();

  // Fork from the first prompt: the covered prefix is not on this branch.
  sm.branch(first.userId);
  const forked = sm.buildSessionContext().messages;
  assert.equal(core.transformContext(forked, sm.getBranch()), forked);

  sm.branch(coveredLeaf);
  const back = sm.buildSessionContext().messages;
  assert.ok(core.transformContext(back, sm.getBranch()).length < back.length);
});

test("a branch summary /tree leaves at the boundary survives the cut", async (t) => {
  const session = await openSession(t);
  if (!session) return;
  const { sm, turn } = session;
  await turn("turn 1");
  await turn("turn 2");
  const third = await turn("turn 3");
  assert.equal(third.trimmed, true);
  const boundary = session.core.state.coveredThroughEntryId;
  // turn_end of turn 3 advanced the boundary to just in front of it.
  assert.equal(sm.getBranch().find((entry) => entry.id === third.userId).parentId, boundary);

  // /tree back to "turn 3", the first kept turn, with a summary of what is
  // abandoned: pi hangs it off the boundary entry, in front of the retry.
  sm.branchWithSummary(boundary, "abandoned turn 3");
  const retry = await turn("turn 3, retried");
  assert.equal(retry.trimmed, true);
  assert.deepEqual(retry.out.map((message) => message.role), ["user", "branchSummary", "user"]);
});
