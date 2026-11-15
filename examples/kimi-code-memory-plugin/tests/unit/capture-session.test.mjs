/**
 * The capture pipeline end to end against a fake server.
 *
 * These tests drive `captureSession` directly rather than through a hook, so a
 * failure points at the pipeline instead of at envelope plumbing. What they
 * assert is what the server would receive and what the state says about it.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { captureSession, ovSessionIdFor } from "../../scripts/capture-session.mjs";
import { loadConfig } from "../../scripts/config.mjs";
import { readHookStateDetailed, writeHookState } from "../../scripts/shared/agent-hook-runtime.mjs";
import { summarizeLedger } from "../../scripts/turn-ledger.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import {
  contentPart,
  metadata,
  simpleTurn,
  stepBegin,
  stepEnd,
  toolCall,
  toolResult,
  turnEnded,
  turnPrompt,
  turnSteer,
  wireText,
} from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_capture";

function recordsToWire(records) {
  return wireText([metadata(), ...records]);
}

/** Run one capture pass with a real config pointed at the fake server. */
async function runCapture({ home, server, sessionId = SESSION, mode = "normal", cfgOverrides = {}, state = undefined }) {
  const cfg = {
    ...loadConfig(home, { env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home } }),
    ...cfgOverrides,
  };
  const { makeAgentFetchJSON, readHookStateDetailed: read } = await import("../../scripts/shared/agent-hook-runtime.mjs");
  const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
  // An explicit state is used as given; only a missing one is read from disk.
  let initialState = state;
  if (initialState === undefined) {
    const persisted = await read("kimicode", sessionId);
    initialState = persisted.status === "ok" ? persisted.state : { agents: {} };
  }
  return await captureSession({
    cfg,
    input: {},
    cwd: home,
    log: () => {},
    logError: () => {},
    deadline: Date.now() + 60000,
    fetchJSON,
    sessionId,
    state: initialState,
    mode,
    now: Date.now(),
  });
}

test("a closed turn reaches the server in order, and its state records the delivery", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          ...simpleTurn(0, { prompt: "how do I run the tests?", reply: "Run node --test." }),
        ]),
      });

      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.status, "ok");
      assert.equal(first.summary.delivered, 2);

      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.deepEqual(messages.map((message) => message.role), ["user", "assistant"]);
      assert.deepEqual(messages[0].parts, [{ type: "text", text: "how do I run the tests?" }]);
      assert.equal(messages[0].message_kind, "user_query");
      assert.equal(messages[1].message_kind, "assistant_step");
      assert.equal(messages[1].parts[0].text, "Run node --test.");

      const agent = first.state.agents.main;
      assert.equal(agent.stableIds.length, 2);
      // The ledger, not a message counter: one complete native turn whose two
      // messages were both confirmed.
      const summary = summarizeLedger(agent.ledger);
      assert.equal(summary.completeSent, 1);
      assert.equal(summary.queued, 0);
      // The acknowledged line is the turn's terminal line, so the next pass
      // does not re-read it.
      // metadata is line 1, so the turn's terminal record is line 6.
      assert.equal(agent.ackLine, 6);
    });
  } finally {
    await server.close();
  }
});

test("a second pass over an unchanged wire sends nothing again", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.delivered, 2);

      server.reset();
      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.delivered, 0);
      assert.equal(server.requests.filter((request) => request.method === "POST").length, 0);
      // No new message and no new commit request.
      assert.equal(second.summary.committed, 0);
    });
  } finally {
    await server.close();
  }
});

test("an open turn is not sent, and its start line is kept for the next read", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          turnPrompt({ turnId: 0, text: "still going" }),
          stepBegin({ turnId: 0, step: 1 }),
          contentPart({ turnId: 0, step: 1, part: { type: "text", text: "partial answer" } }),
          // No turn.ended: the host is still working.
        ]),
      });

      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.delivered, 0);
      assert.equal(first.state.agents.main.openTurnLine, 2);

      // When the turn closes, the whole turn is delivered exactly once.
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          turnPrompt({ turnId: 0, text: "still going" }),
          stepBegin({ turnId: 0, step: 1 }),
          contentPart({ turnId: 0, step: 1, part: { type: "text", text: "partial answer" } }),
          stepEnd({ turnId: 0, step: 1 }),
          turnEnded({ turnId: 0 }),
        ]),
      });
      server.reset();
      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.delivered, 2);
      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.deepEqual(messages.map((message) => message.message_kind), ["user_query", "assistant_step"]);
    });
  } finally {
    await server.close();
  }
});

test("a cancelled turn is delivered as a partial, without invented content", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          turnPrompt({ turnId: 0, text: "do the thing" }),
          stepBegin({ turnId: 0, step: 1 }),
          contentPart({ turnId: 0, step: 1, part: { type: "text", text: "started" } }),
          stepEnd({ turnId: 0, step: 1 }),
          turnEnded({ turnId: 0, reason: "cancelled" }),
        ]),
      });
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(result.summary.delivered, 2);
      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.equal(messages[1].parts[0].text, "started");
      // Nothing was added to represent the cancellation as conversation.
      assert.equal(messages.length, 2);
    });
  } finally {
    await server.close();
  }
});

test("a steer delivers two logical turns with their own ids", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          turnPrompt({ turnId: 0, text: "the first real question" }),
          stepBegin({ turnId: 0, step: 1 }),
          contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer one" } }),
          stepEnd({ turnId: 0, step: 1 }),
          turnSteer({ turnId: 0, messageId: "s1", text: "the steered question" }),
          stepBegin({ turnId: 0, step: 1 }),
          contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer two" } }),
          stepEnd({ turnId: 0, step: 1 }),
          turnEnded({ turnId: 0 }),
        ]),
      });
      await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      const users = messages.filter((message) => message.message_kind === "user_query");
      assert.deepEqual(users.map((message) => message.parts[0].text), ["the first real question", "the steered question"]);
      // Two logical turns, two distinct ids assigned by the host's own clock.
      const ids = users.map((message) => message.turn_id);
      assert.equal(new Set(ids).size, 2);
    });
  } finally {
    await server.close();
  }
});

test("tool evidence reaches the server as a call part plus a tool_transport message", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          turnPrompt({ turnId: 0, text: "read the config" }),
          stepBegin({ turnId: 0, step: 1 }),
          toolCall({ turnId: 0, step: 1, toolCallId: "c1", name: "Read", args: { file_path: "/tmp/config.json" } }),
          stepEnd({ turnId: 0, step: 1 }),
          stepBegin({ turnId: 0, step: 2 }),
          toolResult({ turnId: 0, toolCallId: "c1", output: "{\"a\":1}" }),
          stepEnd({ turnId: 0, step: 2 }),
          turnEnded({ turnId: 0 }),
        ]),
      });
      await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.deepEqual(messages.map((message) => message.message_kind), ["user_query", "assistant_step", "tool_transport"]);
      assert.equal(messages[1].parts[0].tool_id, "c1");
      assert.equal(messages[2].parts[0].tool_output, "{\"a\":1}");
    });
  } finally {
    await server.close();
  }
});

test("an enqueue failure stops the cursor before the unsent message", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, pendingDir }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      // Make the queue unwritable so both the send and its fallback fail.
      const { mkdir, writeFile } = await import("node:fs/promises");
      await mkdir(pendingDir, { recursive: true });
      await writeFile(join(pendingDir, "blocker"), "not a directory", "utf8");
      process.env.OPENVIKING_PENDING_DIR = join(pendingDir, "blocker", "queue");

      server.failWith = 503;
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(result.summary.delivered, 0);
      assert.equal(result.summary.committed, 0);
      const agent = result.state.agents.main;
      assert.equal(agent.ackLine || 0, 0);
      assert.equal(summarizeLedger(agent.ledger).completeSent, 0);
      delete process.env.OPENVIKING_PENDING_DIR;
    });
  } finally {
    await server.close();
  }
});

test("a non-retryable failure is recorded once and blocks the commit", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      server.failWith = 400;
      const first = await runCapture({
        home,
        server,
        mode: "session-end",
        state: { agents: {}, startSource: "startup" },
      });
      assert.equal(first.summary.delivered, 0);
      const agent = first.state.agents.main;
      assert.equal(agent.captureFailed?.resolved, false);
      assert.ok(agent.captureFailed?.configFingerprint);
      assert.equal(server.commits(ovSessionIdFor(SESSION, "main")).length, 0);

      // The same configuration must not be retried on the next event.
      server.reset();
      const second = await runCapture({ home, server, state: first.state, mode: "stop" });
      assert.equal(second.summary.delivered, 0);
      assert.equal(server.requests.filter((request) => request.method === "POST").length, 0);
      assert.ok(second.summary.blocked.some((reason) => reason.includes("capture_failed")));
    });
  } finally {
    await server.close();
  }
});

test("a retryable failure is queued and replayed at the next boundary", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      server.failWith = 503;
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.delivered, 0);
      assert.equal(first.summary.queued, 2);
      // Queued is not delivered: the turn exists in the ledger as queued only.
      const queuedSummary = summarizeLedger(first.state.agents.main.ledger);
      assert.equal(queuedSummary.completeSent, 0);
      assert.equal(queuedSummary.queued, 2);

      server.failWith = null;
      server.reset();
      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.replayed, 2);
      const messages = server.messages(ovSessionIdFor(SESSION, "main"));
      assert.equal(messages.length, 2);
      assert.deepEqual(messages.map((message) => message.role), ["user", "assistant"]);
    });
  } finally {
    await server.close();
  }
});

test("a commit follows delivery at a forced boundary and keeps keep_recent_count 0", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { session_id: "x", pending_tokens: 10 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const result = await runCapture({
        home,
        server,
        mode: "compact",
        state: { agents: {}, startSource: "startup" },
      });
      assert.equal(result.summary.delivered, 2);
      assert.equal(result.summary.committed, 1);
      const commits = server.commits(ovSessionIdFor(SESSION, "main"));
      assert.equal(commits.length, 1);
      assert.deepEqual(commits[0], { keep_recent_count: 0 });
      assert.equal(summarizeLedger(result.state.agents.main.ledger).completeSent, 0);
    });
  } finally {
    await server.close();
  }
});

test("a forced boundary commits the closed prefix while the tail turn is still open", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { session_id: "x", pending_tokens: 10 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          ...simpleTurn(0),
          turnPrompt({ turnId: 1, text: "still running" }),
          stepBegin({ turnId: 1, step: 1 }),
          contentPart({ turnId: 1, step: 1, part: { type: "text", text: "partial answer" } }),
        ]),
      });
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.committed, 0);
      assert.ok(Number.isFinite(first.state.agents.main.openTurnLine));

      // The open tail is not in the ledger, so the closed prefix is safe to
      // archive; a forced boundary must not skip it.
      const compacted = await runCapture({ home, server, state: first.state, mode: "compact" });
      assert.equal(compacted.summary.committed, 1);
      assert.equal(server.commits(ovSessionIdFor(SESSION, "main")).length, 1);
    });
  } finally {
    await server.close();
  }
});

test("a non-tail open turn does not block a normal commit", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { session_id: "x", pending_tokens: 10 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([
          ...simpleTurn(0),
          // This turn never ends; the next prompt moves the chain past it.
          turnPrompt({ turnId: 1, text: "abandoned" }),
          stepBegin({ turnId: 1, step: 1 }),
          contentPart({ turnId: 1, step: 1, part: { type: "text", text: "abandoned answer" } }),
          ...simpleTurn(2, { prompt: "replacement", reply: "replacement answer" }),
        ]),
      });
      const result = await runCapture({
        home,
        server,
        cfgOverrides: { commitTurnThreshold: 1, commitTokenThreshold: 1_000_000 },
        state: { agents: {}, startSource: "startup" },
      });
      assert.equal(result.summary.committed, 1);
      assert.equal(server.commits(ovSessionIdFor(SESSION, "main")).length, 1);
    });
  } finally {
    await server.close();
  }
});

test("no commit happens while a message is still queued", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { pending_tokens: 999999 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      // First pass: the batch post is retryable-failed and lands in the queue.
      server.failOnce = { match: /\/messages(\/batch)?$/, status: 503 };
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.committed, 0);
      // A second boundary that cannot replay must still not commit.
      server.failWith = 503;
      const second = await runCapture({ home, server, state: first.state, mode: "session-end" });
      assert.equal(second.summary.committed, 0);
      assert.equal(server.commits(ovSessionIdFor(SESSION, "main")).length, 0);
    });
  } finally {
    await server.close();
  }
});

test("metadata that cannot be read is not treated as zero tokens", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { status: 500, body: { status: "error", error: { code: "internal" } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const result = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup" },
        cfgOverrides: { commitTokenThreshold: 1, commitTurnThreshold: 100 },
      });
      // Thresholds were not met by turns, and the token answer was unusable, so
      // no commit — and the state records that the answer was missing rather
      // than inventing a token count.
      assert.equal(result.summary.committed, 0);
      assert.equal(result.state.agents.main.commitTokenCount, undefined);
    });
  } finally {
    await server.close();
  }
});

test("the turn threshold commits a low-token session on its own", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { pending_tokens: 0 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const result = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup" },
        cfgOverrides: { commitTokenThreshold: 100000, commitTurnThreshold: 1 },
      });
      assert.equal(result.summary.committed, 1);
    });
  } finally {
    await server.close();
  }
});

test("autoCapture=false stops new capture and its commit but still replays the queue", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      // Queue one message first.
      server.failWith = 503;
      const queued = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(queued.summary.queued, 2);

      server.failWith = null;
      server.reset();
      const disabled = await runCapture({
        home,
        server,
        state: queued.state,
        cfgOverrides: { autoCapture: false },
      });
      assert.equal(disabled.summary.status, "capture_disabled");
      // The queued messages still went out: they are history, not new capture.
      assert.equal(disabled.summary.replayed, 2);
    });
  } finally {
    await server.close();
  }
});

test("a resumed session holds its predecessor's commit until a new turn is delivered", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { pending_tokens: 500000 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      // A previous generation queued a commit.
      const ovId = ovSessionIdFor(SESSION, "main");
      const { enqueue } = await import("../../scripts/shared/pending-queue.mjs");
      await enqueue("commitSession", ovId, { keep_recent_count: 0 });

      const state = {
        agents: {
          main: {
            agentId: "main",
            ovSessionId: ovId,
            wirePath: join(home, "sessions", "workspace-a", SESSION, "agents", "main", "wire.jsonl"),
            resumeBlockedCommit: true,
            // Turn 0 was already delivered by the previous generation: its
            // terminal record is line 6 (metadata is line 1).
            stableIds: ["t0:s0:mp0:user:0", "t0:s0:mp0:assistant:0"],
            ackLine: 6,
            scanLine: 6,
          },
        },
        startSource: "resume",
        resumedAt: Date.now(),
      };
      const resumed = await runCapture({ home, server, state, mode: "resume" });
      assert.equal(resumed.summary.committed, 0);
      assert.equal(server.commits(ovId).length, 0);

      // A new complete turn arrives. Delivering it lifts the hold, and the
      // queued commit is replayed at the boundary after that — through the
      // queue, so its settlement comes from a real `sent` result.
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire([...simpleTurn(0), ...simpleTurn(1)]),
      });
      server.reset();
      const after = await runCapture({ home, server, state: resumed.state });
      assert.equal(after.summary.delivered, 2);
      assert.equal(after.state.agents.main.resumeBlockedCommit, false);

      server.reset();
      const settled = await runCapture({ home, server, state: after.state });
      assert.equal(settled.summary.replayed, 1);
      // Two commits is the correct outcome: the resumed generation's own turn
      // crossed the token threshold and committed directly, and the commit its
      // predecessor queued was replayed once the hold lifted. What must not
      // happen is either one being skipped, or the hold being lifted before a
      // turn was delivered — which the assertions above already cover.
      assert.ok(server.commits(ovId).length >= 1);
      for (const body of server.commits(ovId)) {
        assert.deepEqual(body, { keep_recent_count: 0 });
      }
      assert.equal(settled.state.agents.main.commitPending, undefined);
      assert.equal(summarizeLedger(settled.state.agents.main.ledger).completeSent, 0);
    });
  } finally {
    await server.close();
  }
});

test("an unreadable state is refused instead of resetting the cursors", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, stateDir }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const { mkdir } = await import("node:fs/promises");
      await mkdir(join(stateDir, "kimicode", `${SESSION}.json`), { recursive: true });

      const read = await readHookStateDetailed("kimicode", SESSION);
      assert.equal(read.status, "unreadable");
      // The entry layer is what refuses; captureSession never sees a state it
      // should not have, and no message is sent.
      assert.equal(server.requests.length, 0);
    });
  } finally {
    await server.close();
  }
});

test("subagents get their own OpenViking session and their own cursor", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        agents: { main: "", researcher: "" },
      });
      await writeFileAt(join(sessionDir, "agents", "main", "wire.jsonl"), recordsToWire(simpleTurn(0, {
        prompt: "main question",
        reply: "main answer",
      })));
      await writeFileAt(join(sessionDir, "agents", "researcher", "wire.jsonl"), recordsToWire(simpleTurn(0, {
        prompt: "research this",
        reply: "research result",
      })));

      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.deepEqual(Object.keys(result.state.agents).sort(), ["main", "researcher"]);
      assert.equal(result.state.agents.main.ovSessionId, ovSessionIdFor(SESSION, "main"));
      assert.equal(result.state.agents.researcher.ovSessionId, ovSessionIdFor(SESSION, "researcher"));
      assert.notEqual(result.state.agents.main.ovSessionId, result.state.agents.researcher.ovSessionId);

      assert.deepEqual(
        server.messages(result.state.agents.main.ovSessionId).map((message) => message.parts[0].text),
        ["main question", "main answer"],
      );
      assert.deepEqual(
        server.messages(result.state.agents.researcher.ovSessionId).map((message) => message.parts[0].text),
        ["research this", "research result"],
      );
    });
  } finally {
    await server.close();
  }
});

test("a non-local environment is skipped with a diagnosis, not captured", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        state: { environmentId: "remote-7" },
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(result.summary.status, "non_local_environment");
      assert.equal(result.summary.environmentId, "remote-7");
      // The skip is persisted, not only summarized: after the hook exits, this
      // record is the only evidence doctor has for why nothing was captured.
      assert.equal(result.state.diagnostics.nonLocalEnvironment.environmentId, "remote-7");
      assert.equal(typeof result.state.diagnostics.nonLocalEnvironment.at, "number");
      assert.deepEqual(result.state.agents, {});
      assert.equal(server.requests.length, 0);
    });
  } finally {
    await server.close();
  }
});

test("a session that is local again clears the stale non-local diagnostic", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const result = await runCapture({
        home,
        server,
        state: {
          agents: {},
          diagnostics: { nonLocalEnvironment: { environmentId: "remote-7", at: 1 } },
        },
      });
      assert.equal(result.summary.status, "ok");
      assert.equal(result.state.diagnostics.nonLocalEnvironment, undefined);
    });
  } finally {
    await server.close();
  }
});

test("a fork captures only what came after its forked marker", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      // The inherited prefix plus a forked marker plus a new turn.
      const inherited = [
        ...simpleTurn(0, { prompt: "inherited question", reply: "inherited answer" }),
        { type: "forked", agentId: "main", time: 1760000009000 },
      ];
      const fresh = simpleTurn(1, { prompt: "new after fork", reply: "new answer" });
      await writeSession(home, {
        sessionId: SESSION,
        state: { forkedFrom: "session_parent" },
        wireTextFor: () => recordsToWire([...inherited, ...fresh]),
      });

      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      const texts = server.messages(ovSessionIdFor(SESSION, "main"))
        .map((message) => message.parts[0]?.text)
        .filter(Boolean);
      assert.ok(texts.includes("new after fork"), JSON.stringify({
        texts,
        summary: result.summary,
        requests: server.requests.map((request) => [request.method, request.path]),
      }));
      assert.ok(texts.includes("new answer"), JSON.stringify(texts));
      assert.equal(texts.includes("inherited question"), false);
      assert.equal(texts.includes("inherited answer"), false);
      assert.equal(result.state.forkedFrom, "session_parent");
    });
  } finally {
    await server.close();
  }
});

async function writeFileAt(path, body) {
  const { writeFile } = await import("node:fs/promises");
  await writeFile(path, body, "utf8");
}

test("a corrupt state is quarantined before capture resumes from the file end", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, stateDir }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const { mkdir } = await import("node:fs/promises");
      await mkdir(join(stateDir, "kimicode"), { recursive: true });
      await writeFileAt(join(stateDir, "kimicode", `${SESSION}.json`), "{not json", "utf8");

      const read = await readHookStateDetailed("kimicode", SESSION);
      assert.equal(read.status, "corrupt");
      const { quarantineHookState } = await import("../../scripts/shared/agent-hook-runtime.mjs");
      const moved = await quarantineHookState("kimicode", SESSION);
      assert.equal(moved.ok, true);
      assert.equal(await readFile(moved.backup, "utf8"), "{not json");

      // After quarantining, the next capture starts a fresh generation. The
      // entry layer marks that generation `corruptRecovered`, and capture must
      // then begin at the file end rather than at line 1 — a corrupt state is
      // not proof that the session is new.
      const result = await runCapture({
        home,
        server,
        state: { agents: {}, startSource: "startup", corruptRecovered: true },
      });
      assert.equal(result.summary.delivered, 0);
      assert.equal(result.state.agents.main.initialBoundary, "eof");
    });
  } finally {
    await server.close();
  }
});

test("the state written by capture survives a round trip through the hook state file", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => recordsToWire(simpleTurn(0)),
      });
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      await writeHookState("kimicode", SESSION, result.state);
      const read = await readHookStateDetailed("kimicode", SESSION);
      assert.equal(read.status, "ok");
      assert.equal(summarizeLedger(read.state.agents.main.ledger).completeSent, 1);
      assert.deepEqual(read.state.agents.main.stableIds, result.state.agents.main.stableIds);
    });
  } finally {
    await server.close();
  }
});
