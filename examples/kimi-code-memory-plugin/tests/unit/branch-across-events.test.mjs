/**
 * Branch geometry and fork boundaries across separate capture passes.
 *
 * The projector's own tests feed it one list of records. These drive
 * `captureSession` repeatedly against a growing file, which is the only way to
 * catch a projection that cannot survive its own window.
 */

import assert from "node:assert/strict";
import { appendFile, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { captureSession, ovSessionIdFor } from "../../scripts/capture-session.mjs";
import { loadConfig } from "../../scripts/config.mjs";
import { makeAgentFetchJSON } from "../../scripts/shared/agent-hook-runtime.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import {
  contentPart,
  contextClear,
  forked,
  metadata,
  simpleTurn,
  stepBegin,
  stepEnd,
  switched,
  turnEnded,
  turnPrompt,
  wireText,
} from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_branch";

async function runCapture({ home, server, state, mode = "normal" }) {
  const cfg = {
    ...loadConfig(home, { env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home } }),
  };
  const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
  return await captureSession({
    cfg,
    input: {},
    cwd: home,
    log: () => {},
    logError: () => {},
    deadline: Date.now() + 60000,
    fetchJSON,
    sessionId: SESSION,
    state,
    mode,
    now: Date.now(),
  });
}

function texts(server, ovId) {
  return server.messages(ovId)
    .filter((message) => message.message_kind === "user_query")
    .map((message) => message.parts[0].text);
}

test("a branch switch in one event and its new turns in the next both project", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");

      // Event 1: a turn, then the switch that abandons it.
      await writeFile(wirePath, wireText([
        metadata(),
        ...simpleTurn(0, { prompt: "abandoned question", reply: "abandoned answer" }),
        switched({ branch: "undo-1", baseLine: 5 }),
      ]), "utf8");
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.state.agents.main.activeBranch, "undo-1");
      assert.equal(first.state.agents.main.branchEdges.length, 1);

      // Event 2: the new turn arrives with no switch in this window at all.
      await appendFile(wirePath, wireText(simpleTurn(1, {
        prompt: "replacement question",
        reply: "replacement answer",
      })), "utf8");
      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.status, "ok");
      assert.equal(second.summary.delivered, 2);
      // The abandoned turn was never delivered, and the replacement was.
      assert.deepEqual(texts(server, ovId), ["replacement question"]);
      assert.equal(second.state.agents.main.activeBranch, "undo-1");
    });
  } finally {
    await server.close();
  }
});

test("two switches across three events keep the chain intact", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");

      await writeFile(wirePath, wireText([
        metadata(),
        ...simpleTurn(0, { prompt: "main question", reply: "main answer" }),
      ]), "utf8");
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.deepEqual(texts(server, ovId), ["main question"]);

      // Event 2: switch to b and run a turn there.
      await appendFile(wirePath, wireText([
        switched({ branch: "b", baseLine: 6 }),
        ...simpleTurn(1, { prompt: "b question", reply: "b answer" }),
      ]), "utf8");
      const second = await runCapture({ home, server, state: first.state });
      assert.deepEqual(texts(server, ovId), ["main question", "b question"]);

      // Event 3: switch to c whose base is main at line 6, so b is abandoned.
      await appendFile(wirePath, wireText([
        switched({ branch: "c", baseBranch: "main", baseLine: 6 }),
        ...simpleTurn(2, { prompt: "c question", reply: "c answer" }),
      ]), "utf8");
      const third = await runCapture({ home, server, state: second.state });
      assert.equal(third.summary.status, "ok");
      assert.equal(third.state.agents.main.activeBranch, "c");
      assert.equal(third.state.agents.main.branchEdges.length, 2);
      // `b` was delivered before the switch abandoned it, and the design is
      // explicit that a delivered turn cannot be taken back; `c` is added.
      assert.deepEqual(texts(server, ovId), ["main question", "b question", "c question"]);
    });
  } finally {
    await server.close();
  }
});

test("context.clear drops the open turn and keeps the session identity", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");

      await writeFile(wirePath, wireText([
        metadata(),
        turnPrompt({ turnId: 0, text: "half a question" }),
        stepBegin({ turnId: 0, step: 1 }),
        contentPart({ turnId: 0, step: 1, part: { type: "text", text: "half an answer" } }),
        stepEnd({ turnId: 0, step: 1 }),
      ]), "utf8");
      const open = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(open.summary.delivered, 0);
      const ovBefore = open.state.agents.main.ovSessionId;

      await appendFile(wirePath, wireText([contextClear()]), "utf8");
      const cleared = await runCapture({ home, server, state: open.state });
      assert.equal(cleared.summary.delivered, 0, "a cleared open turn is not delivered");
      // The OpenViking session identity does not change on a clear.
      assert.equal(cleared.state.agents.main.ovSessionId, ovBefore);
      assert.equal(texts(server, ovId).length, 0);

      // A later turn in the same session is captured normally.
      await appendFile(wirePath, wireText(simpleTurn(1, { prompt: "after clear", reply: "answer" })), "utf8");
      const after = await runCapture({ home, server, state: cleared.state });
      assert.deepEqual(texts(server, ovId), ["after clear"]);
      assert.equal(after.state.agents.main.ovSessionId, ovBefore);
    });
  } finally {
    await server.close();
  }
});

test("a replaced file does not re-deliver what the stable ids already cover", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const ovId = ovSessionIdFor(SESSION, "main");

      await writeFile(wirePath, wireText([metadata(), ...simpleTurn(0, { prompt: "the original question", reply: "the original answer" })]), "utf8");
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.deepEqual(texts(server, ovId), ["the original question"]);

      // Rewrite the file: same first turn, one new turn, different length.
      await writeFile(wirePath, wireText([
        metadata(),
        ...simpleTurn(0, { prompt: "the original question", reply: "the original answer" }),
        ...simpleTurn(1, { prompt: "the follow-up question", reply: "the follow-up answer" }),
      ]), "utf8");
      const second = await runCapture({ home, server, state: first.state });
      assert.equal(second.summary.status, "ok");
      // The re-read overlap is suppressed by the stable ids; only q1 is new.
      assert.deepEqual(texts(server, ovId), ["the original question", "the follow-up question"]);
      assert.equal(second.state.agents.main.resets, 1);
    });
  } finally {
    await server.close();
  }
});

test("a fork with its marker captures only the turns after it", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: SESSION,
        state: { forkedFrom: "session_parent" },
        wireTextFor: () => wireText([
          metadata(),
          ...simpleTurn(0, { prompt: "inherited question", reply: "inherited answer" }),
          forked(),
          ...simpleTurn(1, { prompt: "new question", reply: "new answer" }),
        ]),
      });
      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(result.summary.status, "ok");
      assert.deepEqual(texts(server, ovSessionIdFor(SESSION, "main")), ["new question"]);
      assert.equal(result.state.forkedFrom, "session_parent");
      assert.equal(result.state.parentSessionId, "session_parent");
    });
  } finally {
    await server.close();
  }
});

test("a fork whose marker is missing starts at the file end and says so", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        state: { forkedFrom: "session_parent" },
        // A turn-sliced copy with no `forked` line: the inherited history cannot
        // be told apart from the fork's own, so nothing may be imported.
        wireTextFor: () => wireText([
          metadata(),
          ...simpleTurn(0, { prompt: "inherited question", reply: "inherited answer" }),
        ]),
      });
      const first = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(first.summary.status, "fork_without_marker");
      assert.equal(first.summary.delivered, 0, "inherited history must not be imported");
      assert.equal(first.state.agents.main.forkDiagnostic.reason, "forked_marker_missing");
      assert.equal(texts(server, ovSessionIdFor(SESSION, "main")).length, 0);

      // A new turn after the fork is captured normally.
      await appendFile(join(sessionDir, "agents", "main", "wire.jsonl"), wireText(simpleTurn(1, {
        prompt: "fork's own question",
        reply: "fork's own answer",
      })), "utf8");
      const second = await runCapture({ home, server, state: first.state });
      assert.deepEqual(texts(server, ovSessionIdFor(SESSION, "main")), ["fork's own question"]);
    });
  } finally {
    await server.close();
  }
});

test("the recorded workspace directory and agent labels are kept for diagnosis", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        agents: { main: "", worker: "" },
      });
      await appendFile(join(sessionDir, "agents", "main", "wire.jsonl"), wireText([metadata(), ...simpleTurn(0)]), "utf8");
      await appendFile(join(sessionDir, "agents", "worker", "wire.jsonl"), wireText([metadata(), ...simpleTurn(0)]), "utf8");
      await writeFile(join(sessionDir, "state.json"), JSON.stringify({
        id: SESSION,
        version: 2,
        cwd: "/work/project",
        environmentId: "local",
        agents: {
          main: { type: "main", homedir: join(sessionDir, "agents", "main") },
          worker: {
            type: "sub",
            homedir: join(sessionDir, "agents", "worker"),
            labels: { parentAgentId: "main", profileName: "explorer" },
          },
        },
      }), "utf8");

      const result = await runCapture({ home, server, state: { agents: {}, startSource: "startup" } });
      assert.equal(result.state.workspaceCwd, "/work/project");
      assert.equal(result.state.agents.worker.parentAgentId, "main");
      assert.equal(result.state.agents.worker.profileName, "explorer");
      assert.equal(result.state.agents.main.parentAgentId, undefined);
    });
  } finally {
    await server.close();
  }
});
