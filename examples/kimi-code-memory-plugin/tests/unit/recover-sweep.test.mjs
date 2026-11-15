/**
 * Failure recovery and the ended sweep.
 *
 * These are the paths a user only reaches after something went wrong, so they
 * are the ones that must not quietly lose data or quietly claim success. The
 * tests drive `recover.mjs` as a command, because that is how a maintainer will.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  acceptLoss,
  emptySessionState,
  evaluateCommitGuard,
  retryCommit,
} from "../../scripts/session-state.mjs";
import { addTurn } from "../../scripts/turn-ledger.mjs";
import { captureSession } from "../../scripts/capture-session.mjs";
import { runStartupSweep } from "../../scripts/sweep.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, withPendingDirFor, writeSession } from "../fixtures/kimi-home.mjs";
import { metadata, simpleTurn, stepBegin, contentPart, turnPrompt, wireText } from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_recover";
const RECOVER = fileURLToPath(new URL("../../scripts/recover.mjs", import.meta.url));

function stateWith(agent) {
  return {
    ...emptySessionState(),
    nativeSessionId: SESSION,
    agents: { main: { agentId: "main", ovSessionId: "kc-x", ...agent } },
  };
}

async function runRecover(args, { home }) {
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [RECOVER, ...args], {
      cwd: home,
      env: {
        ...process.env,
        KIMI_CODE_HOME: home,
        OPENVIKING_HOOK_STATE_DIR: join(home, "hook-state"),
        OPENVIKING_PENDING_DIR: join(home, "pending"),
        ...fakeOvEnv({ url: "http://127.0.0.1:9" }),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    child.stdout.on("data", (chunk) => out.push(chunk));
    child.stderr.on("data", (chunk) => err.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => resolve({
      code,
      stdout: Buffer.concat(out).toString("utf8"),
      stderr: Buffer.concat(err).toString("utf8"),
    }));
  });
}

async function writeStateFile(home, state) {
  const dir = join(home, "hook-state", "kimicode");
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, `${SESSION}.json`), JSON.stringify(state), "utf8");
  return join(dir, `${SESSION}.json`);
}

test("retry-commit refuses while a real queued message exists", async () => {
  await withPendingDirFor(async () => {
    const state = stateWith({
      captureFailed: { kind: "commit_failed", resolved: false },
      pendingLost: [{ type: "commitSession", accepted: false, dedupKey: "k" }],
    });
    // The fact comes from the queue, not from a field in the state.
    const refused = retryCommit(state, "main", { reason: "operator", pendingMessages: 1 });
    assert.equal(refused.ok, false);
    assert.match(refused.error, /still in the queue/);

    const allowed = retryCommit(state, "main", { reason: "operator", pendingMessages: 0 });
    assert.equal(allowed.ok, true);
  });
});

test("retry-commit refuses while another commit for the session is queued", () => {
  const state = stateWith({
    captureFailed: { kind: "commit_failed", resolved: false },
    pendingLost: [{ type: "commitSession", accepted: false, dedupKey: "k" }],
  });
  const result = retryCommit(state, "main", { reason: "operator", otherCommits: 1 });
  assert.equal(result.ok, false);
  assert.match(result.error, /still queued/);
});

test("retry-commit refuses a capture failure it cannot fix", () => {
  const state = stateWith({
    captureFailed: { kind: "non_retryable_delivery", resolved: false },
    pendingLost: [{ type: "commitSession", accepted: false }],
  });
  const result = retryCommit(state, "main", { reason: "operator" });
  assert.equal(result.ok, false);
  assert.match(result.error, /accept-loss/);
});

test("retry-commit clears only a lost commit and warns about duplicates", () => {
  const state = stateWith({
    captureFailed: { kind: "commit_failed", resolved: false, errorKind: "timeout" },
    pendingLost: [{ type: "commitSession", accepted: false, dedupKey: "k" }],
  });
  const result = retryCommit(state, "main", { reason: "retry after timeout" });
  assert.equal(result.ok, true);
  assert.match(result.warning, /archived twice/);
  const agent = result.state.agents.main;
  assert.equal(agent.captureFailed.resolved, true);
  assert.deepEqual(agent.pendingLost, []);
  assert.equal(result.state.recoveries.length, 1);
  assert.equal(result.state.recoveries[0].reason, "retry after timeout");
});

test("retry-commit refuses when a commit is already pending", () => {
  const state = stateWith({
    commitPending: { ovSessionId: "kc-x", dedupKey: "k" },
    pendingLost: [{ type: "commitSession", accepted: false }],
  });
  const result = retryCommit(state, "main");
  assert.equal(result.ok, false);
  assert.match(result.error, /pending commit/);
});

test("accept-loss advances the cursor, writes a tombstone and keeps the gap visible", () => {
  const state = stateWith({
    ackLine: 10,
    captureFailed: { kind: "non_retryable_delivery", resolved: false, wireEnd: 14, errorKind: "http_400" },
    pendingLost: [{ type: "commitSession", accepted: false, wireEnd: 12, dedupKey: "k1" }],
  });
  const result = acceptLoss(state, ["main"], { reason: "server rejected the payload" });
  assert.equal(result.ok, true);
  const agent = result.state.agents.main;
  assert.equal(agent.ackLine, 14);
  assert.equal(agent.hasCaptureGaps, true);
  assert.equal(agent.captureFailed.accepted, true);
  assert.equal(agent.pendingLost[0].accepted, true);
  assert.equal(agent.tombstone.reason, "server rejected the payload");
  assert.equal(result.state.endedResult.status, "partial_loss");
  // The gap stays visible for the doctor, but it no longer blocks a commit.
  assert.deepEqual(
    evaluateCommitGuard(agent, { cfg: {}, force: true }).reasons,
    ["nothing_delivered"],
  );
});

test("accept-loss refuses to call a still-queued message lost", () => {
  const state = stateWith({
    captureFailed: { kind: "non_retryable_delivery", resolved: false, wireEnd: 14 },
  });
  const result = acceptLoss(state, ["main"], {
    reason: "operator",
    // The queue still holds this agent's message: its outcome is unknown.
    pendingMessagesByAgent: { main: 2 },
  });
  assert.equal(result.ok, true, "the capture failure itself may still be accepted");
  // The queued messages are untouched, because they are not lost.
  assert.deepEqual(result.state.agents.main.pendingLost || [], []);
  assert.equal(result.state.agents.main.tombstone.stillQueued, 2);
});

test("accept-loss keeps the ended marker while another agent is still unsettled", () => {
  const state = {
    ...emptySessionState(),
    endedMarker: { at: Date.now(), reason: "exit" },
    agents: {
      main: { agentId: "main", ovSessionId: "kc-main", pendingLost: [{ type: "commitSession", accepted: false, dedupKey: "m1" }] },
      worker: {
        agentId: "worker",
        ovSessionId: "kc-worker",
        // This agent still has a commit waiting for the queue: the session is
        // not finished, so the marker must survive.
        commitPending: { ovSessionId: "kc-worker", dedupKey: "w-commit" },
      },
    },
  };
  const result = acceptLoss(state, ["main"], { reason: "partial" });
  assert.equal(result.ok, true);
  assert.equal(result.ended, false);
  assert.ok(result.state.endedMarker, "the marker must stay until the other agent settles");
});

test("the command requires --yes for accept-loss and reports usage otherwise", async () => {
  await withKimiHome(async ({ home }) => {
    const noYes = await runRecover(["accept-loss", "--session", SESSION, "--agent", "main"], { home });
    assert.equal(noYes.code, 1);
    assert.match(noYes.stderr, /--yes/);

    const usage = await runRecover([], { home });
    assert.equal(usage.code, 2);
    assert.match(usage.stdout, /retry-commit/);
    assert.match(usage.stdout, /accept-loss/);
  });
});

test("the command refuses an ambiguous agent selection", async () => {
  await withKimiHome(async ({ home }) => {
    await writeStateFile(home, {
      ...emptySessionState(),
      agents: {
        main: { agentId: "main", ovSessionId: "kc-main", pendingLost: [{ type: "commitSession", accepted: false, dedupKey: "m1" }] },
        worker: { agentId: "worker", ovSessionId: "kc-worker", pendingLost: [{ type: "addMessage", accepted: false }] },
      },
    });
    const result = await runRecover(["accept-loss", "--session", SESSION, "--yes"], { home });
    assert.equal(result.code, 1);
    assert.match(result.stderr, /pass --agent|--all-agents/);
  });
});

test("the command accepts a single agent without --agent", async () => {
  await withKimiHome(async ({ home }) => {
    await writeStateFile(home, {
      ...emptySessionState(),
      agents: {
        main: {
          agentId: "main",
          ovSessionId: "kc-main",
          captureFailed: { kind: "non_retryable_delivery", resolved: false, wireEnd: 7 },
        },
      },
    });
    const result = await runRecover(["accept-loss", "--session", SESSION, "--yes", "--reason", "known loss"], { home });
    assert.equal(result.code, 0, result.stderr);
    const summary = JSON.parse(result.stdout);
    assert.deepEqual(summary.agents, ["main"]);

    const saved = JSON.parse(await readFile(join(home, "hook-state", "kimicode", `${SESSION}.json`), "utf8"));
    assert.equal(saved.agents.main.captureFailed.accepted, true);
    assert.equal(saved.agents.main.tombstone.reason, "known loss");
  });
});

test("the command refuses a session it has no state for", async () => {
  await withKimiHome(async ({ home }) => {
    const result = await runRecover(["retry-commit", "--session", SESSION, "--agent", "main"], { home });
    assert.equal(result.code, 1);
    assert.match(result.stderr, /no plugin state/);
  });
});

test("the sweep leaves a growing file alone and closes a quiet ended one", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      // A session whose marker says it ended, with an open turn in its wire.
      const records = [
        metadata(),
        turnPrompt({ turnId: 0, text: "started before the crash" }),
        stepBegin({ turnId: 0, step: 1 }),
        contentPart({ turnId: 0, step: 1, part: { type: "text", text: "partial work" } }),
      ];
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText(records),
      });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");

      const ovSessionId = `kc-${SESSION}`;
      const state = {
        ...emptySessionState(),
        nativeSessionId: SESSION,
        // The sweep resolves the session's configuration and peer from the
        // directory it actually ran in, never from the project that opened
        // first.
        workspaceCwd: home,
        endedMarker: { at: Date.now() - 1000, reason: "exit" },
        agents: {
          main: {
            agentId: "main",
            ovSessionId,
            wirePath,
            scanOffset: 0,
            scanLine: 0,
            ackOffset: 0,
            ackLine: 0,
            stableIds: [],
          },
        },
      };
      await writeStateFile(home, state);

      const cfg = {
        ...(await import("../../scripts/config.mjs")).loadConfig(home, {
          env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home },
        }),
      };
      const { makeAgentFetchJSON } = await import("../../scripts/shared/agent-hook-runtime.mjs");
      const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });

      const quiet = await runStartupSweep({
        cfg,
        cwd: home,
        log: () => {},
        logError: () => {},
        deadline: Date.now() + 60000,
        fetchJSON,
        excludeSessionId: "another",
      });
      assert.equal(quiet.swept, 1);
      assert.equal(quiet.failed, 0);

      const messages = server.messages(ovSessionId);
      assert.deepEqual(messages.map((message) => message.message_kind), ["user_query", "assistant_step"]);
      // No invented terminal record and no tool result.
      assert.equal(messages.length, 2);
      // The recovery partial is a terminal turn, so the ended session's commit
      // goes out in the same sweep rather than waiting for a start that may
      // never come.
      assert.deepEqual(server.commits(ovSessionId), [{ keep_recent_count: 0 }]);
    });
  } finally {
    await server.close();
  }
});

test("the sweep ignores a state without an ended marker", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const records = [
        metadata(),
        turnPrompt({ turnId: 0, text: "still running" }),
        stepBegin({ turnId: 0, step: 1 }),
        contentPart({ turnId: 0, step: 1, part: { type: "text", text: "work" } }),
      ];
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText(records),
      });
      await writeStateFile(home, {
        ...emptySessionState(),
        nativeSessionId: SESSION,
        agents: {
          main: {
            agentId: "main",
            ovSessionId: "kc-nomarker",
            wirePath: join(sessionDir, "agents", "main", "wire.jsonl"),
            stableIds: [],
          },
        },
      });

      const cfg = (await import("../../scripts/config.mjs")).loadConfig(home, {
        env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home },
      });
      const { makeAgentFetchJSON } = await import("../../scripts/shared/agent-hook-runtime.mjs");
      const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });

      const result = await runStartupSweep({
        cfg,
        cwd: home,
        log: () => {},
        logError: () => {},
        deadline: Date.now() + 60000,
        fetchJSON,
      });
      assert.equal(result.candidates, 0);
      assert.equal(result.swept, 0);
      assert.equal(server.requests.length, 0);
    });
  } finally {
    await server.close();
  }
});

test("the sweep skips a session that was resumed after its marker", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const markerAt = Date.now() - 5000;
      await writeStateFile(home, {
        ...emptySessionState(),
        nativeSessionId: SESSION,
        endedMarker: { at: markerAt, reason: "exit" },
        resumedAt: markerAt + 1000,
        agents: { main: { agentId: "main", ovSessionId: "kc-resumed", wirePath: "", stableIds: [] } },
      });

      const cfg = (await import("../../scripts/config.mjs")).loadConfig(home, {
        env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home },
      });
      const { makeAgentFetchJSON } = await import("../../scripts/shared/agent-hook-runtime.mjs");
      const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });

      const result = await runStartupSweep({
        cfg,
        cwd: home,
        log: () => {},
        logError: () => {},
        deadline: Date.now() + 60000,
        fetchJSON,
      });
      assert.equal(result.candidates, 0, "a resumed session is not an ended one");
      assert.equal(server.requests.length, 0);
    });
  } finally {
    await server.close();
  }
});

test("the sweep never touches the session that just started", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeStateFile(home, {
        ...emptySessionState(),
        nativeSessionId: SESSION,
        endedMarker: { at: Date.now() - 1000, reason: "exit" },
        agents: { main: { agentId: "main", ovSessionId: "kc-self", stableIds: [] } },
      });
      const cfg = (await import("../../scripts/config.mjs")).loadConfig(home, {
        env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home },
      });
      const { makeAgentFetchJSON } = await import("../../scripts/shared/agent-hook-runtime.mjs");
      const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });

      const result = await runStartupSweep({
        cfg,
        cwd: home,
        log: () => {},
        logError: () => {},
        deadline: Date.now() + 60000,
        fetchJSON,
        excludeSessionId: SESSION,
      });
      assert.equal(result.scanned, 0);
    });
  } finally {
    await server.close();
  }
});

test("an accepted loss lets the session finish as partial_loss and still commit what it has", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\/[^/]+$/.test(request.path || "")) {
      return { body: { status: "ok", result: { pending_tokens: 999999 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]),
      });
      const ovId = `kc-${SESSION}`;
      // One turn was delivered; a later one was dropped by the queue.
      let ledger = addTurn(undefined, {
        turnId: "0", kind: "complete", wireStart: 2, wireEnd: 6,
        messages: [{ id: "t0:user", status: "sent" }, { id: "t0:assistant", status: "sent" }],
        messageCount: 2,
      });
      await writeStateFile(home, {
        ...emptySessionState(),
        nativeSessionId: SESSION,
        workspaceCwd: home,
        endedMarker: { at: Date.now(), reason: "exit" },
        agents: {
          main: {
            agentId: "main",
            ovSessionId: ovId,
            wirePath: join(sessionDir, "agents", "main", "wire.jsonl"),
            scanLine: 6,
            ackLine: 6,
            ledger,
            pendingLost: [{
              type: "addMessage", dedupKey: "gone", wireStart: 7, wireEnd: 11,
              stableIds: ["t1:user"], accepted: false,
            }],
            hasCaptureGaps: true,
          },
        },
      });

      // Before acceptance, the loss blocks the commit.
      const cfg = (await import("../../scripts/config.mjs")).loadConfig(home, {
        env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home },
      });
      const { makeAgentFetchJSON } = await import("../../scripts/shared/agent-hook-runtime.mjs");
      const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
      const blocked = await captureSession({
        cfg, input: {}, cwd: home, log: () => {}, logError: () => {},
        deadline: Date.now() + 60000, fetchJSON, sessionId: SESSION,
        state: JSON.parse(await readFile(join(home, "hook-state", "kimicode", `${SESSION}.json`), "utf8")),
        mode: "session-end", now: Date.now(),
      });
      assert.equal(blocked.summary.committed, 0);
      assert.ok(blocked.summary.blocked.some((reason) => reason.includes("pending_lost")));

      // Accepting it records a tombstone and clears the block.
      const accepted = await runRecover(
        ["accept-loss", "--session", SESSION, "--agent", "main", "--yes", "--reason", "queue dropped it"],
        { home },
      );
      assert.equal(accepted.code, 0, accepted.stderr);
      const saved = JSON.parse(await readFile(join(home, "hook-state", "kimicode", `${SESSION}.json`), "utf8"));
      assert.equal(saved.endedResult.status, "partial_loss");
      assert.equal(saved.agents.main.hasCaptureGaps, true, "the gap stays visible for diagnosis");

      // The session can now commit what it actually has.
      server.reset();
      const settled = await captureSession({
        cfg, input: {}, cwd: home, log: () => {}, logError: () => {},
        deadline: Date.now() + 60000, fetchJSON, sessionId: SESSION,
        state: saved, mode: "session-end", now: Date.now(),
      });
      assert.equal(settled.summary.committed, 1, "an accepted loss does not prohibit the commit forever");
      assert.deepEqual(server.commits(ovId), [{ keep_recent_count: 0 }]);
    });
  } finally {
    await server.close();
  }
});

test("accept-loss leaves the cursors consistent, so the next read is not shifted", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]),
      });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const { readWireRecords } = await import("../../scripts/wire-reader.mjs");

      // A state whose line cursor was advanced past the file by an accepted loss.
      const state = stateWith({
        wirePath,
        ackLine: 6,
        ackOffset: 900,
        openTurnLine: 2,
        pendingTurnOffset: 100,
        pendingLost: [{ type: "addMessage", accepted: false, wireStart: 2, wireEnd: 6 }],
        captureFailed: { kind: "non_retryable_delivery", resolved: false, wireEnd: 6 },
      });
      const result = acceptLoss(state, ["main"], { reason: "accepted" });
      const agent = result.state.agents.main;
      assert.equal(agent.ackLine, 6);
      assert.equal(agent.ackOffset, 0, "the byte cursor must not describe an earlier line");
      assert.equal(agent.openTurnLine, undefined, "an open turn inside the lost range can never complete");

      // Reading from that state must start at line 1 and number correctly.
      const reader = await readWireRecords(wirePath, {
        wirePath, scanOffset: agent.ackOffset, line: 0,
      });
      assert.equal(reader.status, "ok");
      assert.deepEqual(reader.records.map((entry) => entry.line), [1, 2, 3, 4, 5, 6]);
    });
  } finally {
    await server.close();
  }
});
