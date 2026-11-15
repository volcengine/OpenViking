/**
 * The delivery ledger: what may be committed, and when.
 *
 * Every test here fails against the first implementation, which counted
 * messages and treated `queued` as delivered.
 */

import assert from "node:assert/strict";
import { appendFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { captureSession, ovSessionIdFor } from "../../scripts/capture-session.mjs";
import { loadConfig } from "../../scripts/config.mjs";
import { makeAgentFetchJSON, readHookStateDetailed } from "../../scripts/shared/agent-hook-runtime.mjs";
import { listPending } from "../../scripts/shared/pending-queue.mjs";
import { summarizeLedger } from "../../scripts/turn-ledger.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import {
  contentPart,
  metadata,
  simpleTurn,
  stepBegin,
  stepEnd,
  turnEnded,
  turnPrompt,
  wireText,
} from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_ledger";

function wireOf(records) {
  return wireText([metadata(), ...records]);
}

async function runCapture({ home, server, state, mode = "normal", cfgOverrides = {}, deadlineMs = 60000 }) {
  const cfg = {
    ...loadConfig(home, { env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home } }),
    ...cfgOverrides,
  };
  const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
  return await captureSession({
    cfg,
    input: {},
    cwd: home,
    log: () => {},
    logError: () => {},
    deadline: Date.now() + deadlineMs,
    fetchJSON,
    sessionId: SESSION,
    state,
    mode,
    now: Date.now(),
  });
}

function metadataHandler(pendingTokens) {
  return (request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\/[^/]+$/.test(request.path || "")) {
      const result = pendingTokens === undefined ? {} : { pending_tokens: pendingTokens };
      return { body: { status: "ok", result } };
    }
    return undefined;
  };
}

test("an exhausted budget records nothing, and the next pass delivers everything", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });

      // A deadline already gone: the stage must not send, and must not pretend
      // the messages were handed over.
      const first = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup" },
        deadlineMs: -1,
      });
      assert.equal(first.summary.delivered, 0);
      const afterFirst = first.state.agents.main;
      assert.equal(afterFirst.stableIds.length, 0, "an unsent message must not be recorded as handed over");
      assert.equal(summarizeLedger(afterFirst.ledger).completeSent, 0);
      assert.equal(server.requests.filter((request) => request.method === "POST").length, 0);

      // The next pass, with budget, delivers the whole turn.
      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.delivered, 2);
      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.deepEqual(messages.map((message) => message.parts[0].text), [
        "question 0",
        "answer 0",
      ]);
    });
  } finally {
    await server.close();
  }
});

test("queued messages are recorded as queued, never as delivered", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });
      server.failWith = 503;

      const queued = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(queued.summary.queued, 2);
      const summary = summarizeLedger(queued.state.agents.main.ledger);
      assert.equal(summary.completeSent, 0, "a queued message is not a delivered turn");
      assert.equal(summary.queued, 2);

      // The queue now holds exactly those two, and each carries the key the
      // ledger recorded, so a replay can settle the right message.
      const pending = await listPending();
      assert.equal(pending.length, 2);
      const recorded = queued.state.agents.main.ledger.turns[0].messages;
      assert.deepEqual(
        recorded.map((message) => message.dedupKey).sort(),
        pending.map((item) => item.entry.dedupKey).sort(),
      );

      // When the server returns, the replay settles them by that key.
      server.failWith = null;
      const recovered = await runCapture({ home, server, state: queued.state });
      assert.equal(recovered.summary.replayed, 2);
      const after = summarizeLedger(recovered.state.agents.main.ledger);
      assert.equal(after.completeSent, 1, "the turn is complete only once both messages are sent");
      assert.equal(after.queued, 0);
    });
  } finally {
    await server.close();
  }
});

test("a turn split across two events counts once, when its last message lands", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf([]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const env = { ...fakeOvEnv(server), KIMI_CODE_HOME: home };
      const cfg = { ...loadConfig(home, { env }), ...{} };
      assert.ok(cfg);

      await writeFile(wirePath, wireOf([turnPrompt({ turnId: 0, text: "a question" })]), "utf8");
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.delivered, 0, "an open turn is not delivered");

      const { appendFile } = await import("node:fs/promises");
      await appendFile(wirePath, wireText([
        stepBegin({ turnId: 0, step: 1 }),
        contentPart({ turnId: 0, step: 1, part: { type: "text", text: "the answer" } }),
        stepEnd({ turnId: 0, step: 1 }),
        turnEnded({ turnId: 0 }),
      ]), "utf8");

      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.delivered, 2);
      assert.equal(summarizeLedger(second.state.agents.main.ledger).completeSent, 1);
    });
  } finally {
    await server.close();
  }
});

test("a normal boundary never commits a terminal partial", async () => {
  const server = await startFakeOv(metadataHandler(999999));
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireOf([
          turnPrompt({ turnId: 0, text: "do the thing" }),
          stepBegin({ turnId: 0, step: 1 }),
          contentPart({ turnId: 0, step: 1, part: { type: "text", text: "started" } }),
          stepEnd({ turnId: 0, step: 1 }),
          turnEnded({ turnId: 0, reason: "cancelled" }),
        ]),
      });

      const stopped = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup" },
        mode: "normal",
        cfgOverrides: { commitTokenThreshold: 1, commitTurnThreshold: 1 },
      });
      assert.equal(stopped.summary.delivered, 2);
      assert.equal(stopped.summary.committed, 0, "a cancelled turn is not a normal commit boundary");
      assert.ok(stopped.summary.blocked.includes("main:nothing_delivered"));

      // The forced boundary may commit it.
      const forced = await runCapture({
        home,
        server,
        state: stopped.state,
        mode: "session-end",
      });
      assert.equal(forced.summary.committed, 1);
      assert.deepEqual(server.commits(ovSessionIdFor(SESSION, "main")), [{ keep_recent_count: 0 }]);
    });
  } finally {
    await server.close();
  }
});

test("metadata that cannot be read stops the commit even at a forced boundary", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\/[^/]+$/.test(request.path || "")) {
      return { status: 500, body: { status: "error", error: { code: "internal" } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });
      const result = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup" },
        mode: "compact",
      });
      assert.equal(result.summary.delivered, 2);
      assert.equal(result.summary.committed, 0, "a null metadata answer is not a licence to commit");
      assert.ok(result.summary.blocked.includes("main:metadata_unavailable"));
      assert.equal(server.commits(ovSessionIdFor(SESSION, "main")).length, 0);
      // The turn stays in the ledger, so the next boundary can still commit it.
      assert.equal(summarizeLedger(result.state.agents.main.ledger).completeSent, 1);
    });
  } finally {
    await server.close();
  }
});

test("a server that omits pending_tokens falls back to the turn threshold", async () => {
  const server = await startFakeOv(metadataHandler(undefined));
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });
      const result = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup" },
        cfgOverrides: { commitTokenThreshold: 100000, commitTurnThreshold: 1 },
      });
      assert.equal(result.summary.committed, 1, "a missing field is 0 tokens, not unavailable metadata");
    });
  } finally {
    await server.close();
  }
});

test("a non-retryable failure is not retried until the configuration changes", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });
      server.failWith = 400;

      const failed = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(failed.summary.delivered, 0);
      const fingerprint = failed.state.agents.main.captureFailed.configFingerprint;
      assert.ok(fingerprint);

      // Same configuration: no request at all.
      server.reset();
      server.failWith = 400;
      const same = await runCapture({ home, server, state: failed.state });
      assert.equal(same.summary.delivered, 0);
      assert.equal(server.requests.filter((request) => request.method === "POST").length, 0);
      assert.ok(same.summary.blocked.some((reason) => reason.includes("capture_failed")));

      // The endpoint changes, which is exactly the kind of change that can fix
      // a 400 at the connection layer: the failure lifts and the range is sent.
      server.failWith = null;
      const changed = await runCapture({
        home,
        server,
        state: failed.state,
        cfgOverrides: { baseUrl: `${server.url}/other` },
      });
      assert.equal(changed.summary.delivered, 2);
      // The record is gone once the retry succeeded, which is what lets the
      // commit guard stop reporting `capture_failed`.
      assert.equal(changed.state.agents.main.captureFailed, undefined);
    });
  } finally {
    await server.close();
  }
});

test("the retry after a configuration change does not skip the messages it failed on", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });
      server.failWith = 400;
      const failed = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      const startLine = failed.state.agents.main.captureFailed.wireStart;

      server.failWith = null;
      server.reset();
      const retried = await runCapture({
        home,
        server,
        state: failed.state,
        cfgOverrides: { baseUrl: `${server.url}/other` },
      });
      const delivered = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.deepEqual(delivered.map((message) => message.parts[0].text), ["question 0", "answer 0"]);
      assert.ok(startLine <= 6, "the failure range must start before the turn it failed on");
      assert.equal(retried.summary.delivered, 2);
    });
  } finally {
    await server.close();
  }
});

test("a failed commit does not stop new capture, and does not commit again", async () => {
  const failing = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\/[^/]+$/.test(request.path || "")) {
      return { body: { status: "ok", result: { pending_tokens: 999999 } } };
    }
    if (/\/commit$/.test(request.path || "")) {
      return { status: 400, body: { status: "error", error: { code: "bad_commit" } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireOf(simpleTurn(0)) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");

      // Deliver turn 0, then ask for a commit that the server rejects.
      const rejected = await runCapture({
        home,
        server: failing,
        state: { agents: {}, startSource: "startup" },
        mode: "compact",
      });
      assert.equal(rejected.summary.delivered, 2);
      assert.equal(rejected.summary.committed, 0);
      assert.equal(rejected.state.agents.main.captureFailed?.kind, "commit_failed");

      // A new turn arrives. Capture must still read and deliver it.
      await appendFile(wirePath, wireText(simpleTurn(1, { prompt: "a new question after a bad commit", reply: "a new answer" })), "utf8");
      const after = await runCapture({ home, server: failing, state: rejected.state });
      assert.notEqual(after.summary.status, "capture_blocked", "a commit failure must not stop capture");
      assert.equal(after.summary.delivered, 2, "the new turn still reaches the server");
      // But it must not commit on its own: the same configuration that failed
      // is still in force, so the guard keeps refusing.
      assert.equal(after.summary.committed, 0);
      assert.ok(after.summary.blocked.some((reason) => reason.includes("capture_failed")),
        `expected the commit to stay blocked, got ${JSON.stringify(after.summary.blocked)}`);
    });
  } finally {
    await failing.close();
  }
});
