/**
 * Ordering, resume generations and loss attribution.
 *
 * Each test names a behaviour the previous implementation got wrong: a replayed
 * backlog message releasing the resume hold, new messages overtaking a queued
 * one, an unconfirmed item being recorded as queued, a queued turn blocking the
 * cursor, and a loss record claiming the whole ledger's range.
 */

import assert from "node:assert/strict";
import { appendFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { captureSession, ovSessionIdFor } from "../../scripts/capture-session.mjs";
import { loadConfig } from "../../scripts/config.mjs";
import { makeAgentFetchJSON, readHookStateDetailed } from "../../scripts/shared/agent-hook-runtime.mjs";
import { enqueue, listPending } from "../../scripts/shared/pending-queue.mjs";
import { addTurn, emptyLedger, messageRange, summarizeLedger } from "../../scripts/turn-ledger.mjs";
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

const SESSION = "session_order";

async function runCapture({ home, server, state, mode = "normal", cfgOverrides = {} }) {
  const cfg = {
    ...loadConfig(home, { env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home } }),
    ...cfgOverrides,
  };
  const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
  return await captureSession({
    cfg, input: {}, cwd: home, log: () => {}, logError: () => {},
    deadline: Date.now() + 60000, fetchJSON, sessionId: SESSION, state, mode, now: Date.now(),
  });
}

function metadataHandler(tokens = 999999) {
  return (request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\/[^/]+$/.test(request.path || "")) {
      return { body: { status: "ok", result: { pending_tokens: tokens } } };
    }
    return undefined;
  };
}

// ---------------------------------------------------------------------------
// 1. Resume generation
// ---------------------------------------------------------------------------

test("a replayed backlog message does not release the resume hold", async () => {
  const server = await startFakeOv(metadataHandler());
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]) });
      const ovId = ovSessionIdFor(SESSION, "main");
      // A message from the previous generation is still queued.
      await enqueue("addMessage", ovId, {
        role: "user", parts: [{ type: "text", text: "old backlog" }], message_kind: "user_query", turn_id: "0",
      });

      const state = {
        agents: {
          main: {
            agentId: "main",
            ovSessionId: ovId,
            resumeBlockedCommit: true,
            resumeBoundaryLine: 7,
            scanLine: 6,
            ackLine: 6,
            ledger: addTurn(undefined, {
              turnId: "0", kind: "complete", wireStart: 2, wireEnd: 6,
              messages: [{ id: "t0:s0:mp0:user:0", status: "queued", dedupKey: "k0" }],
              messageCount: 1,
            }),
          },
        },
        startSource: "resume",
        resumedAt: Date.now(),
      };

      const result = await runCapture({ home, server, state, mode: "resume" });
      assert.equal(result.summary.replayed, 1, "the backlog is replayed");
      assert.equal(
        result.state.agents.main.resumeBlockedCommit,
        true,
        "a previous generation's message must not release the hold",
      );
      assert.equal(result.summary.committed, 0);
      assert.equal(server.commits(ovId).length, 0);
    });
  } finally {
    await server.close();
  }
});

test("a new complete turn from this generation releases the hold, then the commit replays", async () => {
  const server = await startFakeOv(metadataHandler());
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]),
      });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");
      await enqueue("commitSession", ovId, { keep_recent_count: 0 });

      const state = {
        agents: {
          main: {
            agentId: "main",
            ovSessionId: ovId,
            wirePath,
            resumeBlockedCommit: true,
            // This generation starts after the inherited turn.
            resumeBoundaryLine: 7,
            scanLine: 6,
            ackLine: 6,
            scanOffset: (await import("node:fs/promises")).stat(wirePath).then((s) => s.size),
          },
        },
        startSource: "resume",
        resumedAt: Date.now(),
      };
      const scanOffset = await state.agents.main.scanOffset;
      state.agents.main.scanOffset = scanOffset;

      const held = await runCapture({ home, server, state, mode: "resume" });
      assert.equal(held.state.agents.main.resumeBlockedCommit, true);
      assert.equal(server.commits(ovId).length, 0, "the queued commit is held");

      // The user's own new turn arrives and completes.
      await appendFile(wirePath, wireText(simpleTurn(1, { prompt: "fresh question", reply: "fresh answer" })), "utf8");
      const after = await runCapture({ home, server, state: held.state });
      assert.equal(after.summary.delivered, 2);
      assert.equal(after.state.agents.main.resumeBlockedCommit, false, "this generation's complete turn released it");

      // The hold lifted in the same pass that delivered the new turn, so the
      // commit may go out there or at the next boundary. Either way it must
      // happen, carry `keep_recent_count: 0`, and leave the queue empty.
      const settled = await runCapture({ home, server, state: after.state });
      const commits = server.commits(ovId);
      assert.ok(commits.length >= 1, "the held commit was replayed after the release");
      for (const body of commits) assert.deepEqual(body, { keep_recent_count: 0 });
      assert.deepEqual(await listPending(), []);
    });
  } finally {
    await server.close();
  }
});

test("a terminal partial from this generation does not release the hold", async () => {
  const server = await startFakeOv(metadataHandler());
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");
      await appendFile(wirePath, wireText([
        turnPrompt({ turnId: 0, text: "cancelled question" }),
        stepBegin({ turnId: 0, step: 1 }),
        contentPart({ turnId: 0, step: 1, part: { type: "text", text: "partial" } }),
        stepEnd({ turnId: 0, step: 1 }),
        turnEnded({ turnId: 0, reason: "cancelled" }),
      ]), "utf8");

      const state = {
        agents: {
          main: {
            agentId: "main", ovSessionId: ovId, wirePath,
            resumeBlockedCommit: true, resumeBoundaryLine: 2,
            scanLine: 1, ackLine: 1, scanOffset: Buffer.byteLength(JSON.stringify(metadata())) + 1,
          },
        },
        startSource: "resume",
        resumedAt: Date.now(),
      };
      const result = await runCapture({ home, server, state, mode: "session-end" });
      assert.equal(result.summary.delivered, 2);
      assert.equal(
        result.state.agents.main.resumeBlockedCommit,
        true,
        "a cancelled turn is not the new complete turn the hold waits for",
      );
      assert.equal(server.commits(ovId).length, 0);
    });
  } finally {
    await server.close();
  }
});

test("a held generation refuses a commit even with a terminal partial in the ledger", async () => {
  const server = await startFakeOv(metadataHandler());
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const ovId = ovSessionIdFor(SESSION, "main");
      const state = {
        agents: {
          main: {
            agentId: "main", ovSessionId: ovId,
            resumeBlockedCommit: true, resumeBoundaryLine: 100,
            ledger: addTurn(undefined, {
              turnId: "9", kind: "terminal", wireStart: 2, wireEnd: 5,
              messages: [{ id: "a", status: "sent" }], messageCount: 1,
            }),
          },
        },
      };
      const result = await runCapture({ home, server, state, mode: "session-end" });
      assert.equal(result.summary.committed, 0);
      assert.ok(result.summary.blocked.some((reason) => reason.includes("resume_waiting_for_new_turn")));
    });
  } finally {
    await server.close();
  }
});

// ---------------------------------------------------------------------------
// 2. Queue ordering
// ---------------------------------------------------------------------------

test("a queued message stops new sends until it is delivered, then order is kept", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");

      // Turn 0 fails to send and lands in the queue.
      await appendFile(wirePath, wireText(simpleTurn(0, { prompt: "the first queued question", reply: "first answer" })), "utf8");
      server.failWith = 503;
      const queued = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(queued.summary.queued, 2);

      // Turn 1 arrives while turn 0 is still queued: no new POST may happen.
      await appendFile(wirePath, wireText(simpleTurn(1, { prompt: "the newer question", reply: "second answer" })), "utf8");
      server.reset();
      const blocked = await runCapture({ home, server, state: queued.state });
      // The replay may retry the queued messages themselves — that is correct.
      // What must not happen is a *new* message being posted: the batch endpoint
      // is what a new turn goes through, a single message post is the replay.
      const batches = server.requests.filter((request) => request.method === "POST" && /\/messages\/batch$/.test(request.path || ""));
      assert.equal(batches.length, 0, `new messages overtook the queued ones: ${JSON.stringify(batches.map((b) => b.body))}`);
      assert.ok(blocked.summary.blocked.some((reason) => reason.includes("pending_add_messages")));

      // The server returns. The replay keeps the queue's own order: the two
      // queued messages go out before anything newer, one boundary at a time.
      server.failWith = null;
      server.reset();
      // The queue drains first, and only then may the newer turn go — the whole
      // transcript arrives in the order it happened.
      const recovered = await runCapture({ home, server, state: blocked.state });
      assert.equal(recovered.summary.replayed, 2, "the queued turn is replayed as one ordered pair");
      const all = server.messages(ovId).map((message) => message.parts[0].text);
      assert.deepEqual(all, ["the first queued question", "first answer", "the newer question", "second answer"]);
    });
  } finally {
    await server.close();
  }
});

// ---------------------------------------------------------------------------
// 3. Only explicit confirmations reach the ledger
// ---------------------------------------------------------------------------

test("a failed payload is not recorded as queued", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "POST" && /\/messages/.test(request.path || "")) {
      // A non-retryable rejection: the sender reports `failed`, no queue entry.
      return { status: 413, body: { status: "error", error: { message: "too large" } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]) });
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      const ledger = result.state.agents.main.ledger || emptyLedger();
      const messages = ledger.turns.flatMap((turn) => turn.messages);
      assert.equal(messages.length, 0, "a rejected message must not appear as queued");
      assert.equal(summarizeLedger(ledger).queued, 0);
      assert.deepEqual(await listPending(), [], "a non-retryable failure is not queued");
    });
  } finally {
    await server.close();
  }
});

test("a half-delivered turn keeps its real size and is never complete", () => {
  // Two passes: the first delivers the user message, the second the assistant.
  let ledger = addTurn(undefined, {
    turnId: "t1", kind: "complete", wireStart: 2, wireEnd: 6,
    messages: [{ id: "u", status: "sent" }], messageCount: 2,
  });
  assert.equal(summarizeLedger(ledger).completeSent, 0, "one of two is not a complete turn");

  ledger = addTurn(ledger, {
    turnId: "t1", kind: "complete", wireStart: 2, wireEnd: 6,
    messages: [{ id: "a", status: "sent" }], messageCount: 2,
  });
  assert.equal(summarizeLedger(ledger).completeSent, 1, "both delivered makes it complete");
  // Merging kept the earlier message rather than replacing it.
  assert.deepEqual(ledger.turns[0].messages.map((m) => m.id).sort(), ["a", "u"]);
});

test("a partial resend does not lose the messages already confirmed", () => {
  let ledger = addTurn(undefined, {
    turnId: "t2", kind: "complete", wireStart: 2, wireEnd: 8,
    messages: [
      { id: "u", status: "sent" },
      { id: "a1", status: "queued", dedupKey: "k-a1" },
    ],
    messageCount: 3,
  });
  // A later pass only knows about the last message.
  ledger = addTurn(ledger, {
    turnId: "t2", kind: "complete", wireStart: 2, wireEnd: 8,
    messages: [{ id: "a2", status: "sent" }],
    messageCount: 3,
  });
  const turn = ledger.turns[0];
  assert.equal(turn.messageCount, 3);
  assert.deepEqual(turn.messages.map((m) => m.id).sort(), ["a1", "a2", "u"]);
  assert.equal(turn.messages.find((m) => m.id === "a1").dedupKey, "k-a1", "the queue key survives");
  assert.equal(summarizeLedger(ledger).completeSent, 0, "a1 is still queued");
});

test("an overflowed ledger blocks the commit instead of being ignored", async () => {
  const server = await startFakeOv(metadataHandler());
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      let ledger = emptyLedger();
      for (let i = 0; i < 250; i += 1) {
        ledger = addTurn(ledger, {
          turnId: `t${i}`, kind: "complete", wireStart: i * 2, wireEnd: i * 2 + 1,
          messages: [{ id: `m${i}`, status: "sent" }], messageCount: 1,
        });
      }
      assert.equal(ledger.overflow, true);
      const result = await runCapture({
        home, server, mode: "session-end",
        state: { agents: { main: { agentId: "main", ovSessionId: ovSessionIdFor(SESSION, "main"), ledger } } },
      });
      assert.equal(result.summary.committed, 0, "an overflowed ledger must not be committed as if complete");
      assert.ok(result.summary.blocked.some((reason) => reason.includes("ledger_overflow")));
    });
  } finally {
    await server.close();
  }
});

// ---------------------------------------------------------------------------
// 4. Handover cursor
// ---------------------------------------------------------------------------

test("a durably queued turn advances the handover cursor, not only a fully sent one", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]) });
      server.failWith = 503;
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      // The messages are durable in the queue, so the cursor may pass them; the
      // ledger still says they are not delivered.
      assert.equal(result.state.agents.main.ackLine, 6, "a queued turn is handed over");
      assert.equal(summarizeLedger(result.state.agents.main.ledger).queued, 2);
      assert.equal(summarizeLedger(result.state.agents.main.ledger).completeSent, 0);
    });
  } finally {
    await server.close();
  }
});

test("the cursor stops at the first unconfirmed turn and never hides an earlier hole", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "POST" && /\/messages/.test(request.path || "")) {
      return { status: 413, body: { status: "error", error: { message: "too large" } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText([metadata(), ...simpleTurn(0), ...simpleTurn(1)]),
      });
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      // Nothing was confirmed, so the cursor did not move past turn 1's failure.
      assert.equal(result.state.agents.main.ackLine || 0, 0);
      const turns = result.state.agents.main.ledger?.turns || [];
      assert.equal(turns.every((turn) => turn.messages.every((m) => m.status !== "queued")), true);
    });
  } finally {
    await server.close();
  }
});

// ---------------------------------------------------------------------------
// 5. Loss attribution
// ---------------------------------------------------------------------------

test("a loss record names the message's own turn, not the whole ledger", () => {
  const ledger = addTurn(addTurn(undefined, {
    turnId: "t1", kind: "complete", wireStart: 2, wireEnd: 6,
    messages: [{ id: "u1", status: "queued", dedupKey: "k1" }], messageCount: 1,
  }), {
    turnId: "t2", kind: "complete", wireStart: 7, wireEnd: 11,
    messages: [{ id: "u2", status: "queued", dedupKey: "k2" }], messageCount: 1,
  });

  const range = messageRange(ledger, { dedupKey: "k2" });
  assert.equal(range.wireStart, 7);
  assert.equal(range.wireEnd, 11);
  assert.deepEqual(range.stableIds, ["u2"]);
  assert.equal(range.turnId, "t2");
});

test("a commit loss has no wire range at all", () => {
  assert.equal(messageRange(emptyLedger(), { dedupKey: "commit-key" }), undefined);
});
