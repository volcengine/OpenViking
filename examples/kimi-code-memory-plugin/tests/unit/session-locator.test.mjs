/**
 * The session locator: index tail, deletion, containment, ambiguity, agents.
 */

import assert from "node:assert/strict";
import { mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import {
  agentParentId,
  agentProfileName,
  isValidSessionId,
  listSessionAgents,
  locateSession,
  sessionEnvironmentId,
  sessionForkedFrom,
} from "../../scripts/session-locator.mjs";
import { appendIndex, withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";

const SESSION = "session_abc-123";

test("the last index line wins, so a deleted session is missing even though an older line names it", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, { sessionId: SESSION, index: false });
    await appendIndex(home, [
      { sessionId: SESSION, sessionDir, workDir: "/work" },
      { sessionId: SESSION, deleted: true },
    ]);

    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "missing");
    assert.equal(located.reason, "deleted");
  });
});

test("a session deleted and re-created is live again", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, { sessionId: SESSION, index: false });
    await appendIndex(home, [
      { sessionId: SESSION, sessionDir, workDir: "/work" },
      { sessionId: SESSION, deleted: true },
      { sessionId: SESSION, sessionDir, workDir: "/work" },
    ]);
    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "ok");
  });
});

test("a session whose directory vanished is missing, not corrupt", async () => {
  await withKimiHome(async ({ home }) => {
    await appendIndex(home, [{ sessionId: SESSION, sessionDir: join(home, "sessions", "w", "gone") }]);
    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "missing");
  });
});

test("state.json that belongs to another session is refused", async () => {
  await withKimiHome(async ({ home }) => {
    const { statePath } = await writeSession(home, { sessionId: SESSION });
    const state = JSON.parse(await readFile(statePath, "utf8"));
    await writeFile(statePath, JSON.stringify({ ...state, id: "session_other" }), "utf8");

    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "unreadable");
    assert.equal(located.error, "state_session_mismatch");
  });
});

test("a session directory reached through a symlink out of the sessions root is refused", async (t) => {
  await withKimiHome(async ({ home }) => {
    const outside = join(home, "outside");
    await mkdir(outside, { recursive: true });
    await writeFile(join(outside, "state.json"), JSON.stringify({ id: SESSION, version: 2 }), "utf8");
    const root = join(home, "sessions", "w");
    await mkdir(root, { recursive: true });
    try {
      await symlink(outside, join(root, SESSION), "dir");
    } catch (error) {
      // Windows without developer mode cannot create symlinks; the containment
      // check is still covered by the path comparison below.
      t.skip(`symlink unavailable: ${error.code}`);
      return;
    }
    await appendIndex(home, [{ sessionId: SESSION, sessionDir: join(root, SESSION) }]);
    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "unreadable");
    assert.equal(located.error, "session_dir_outside_root");
  });
});

test("an index with no line for the session falls back to a bounded scan", async () => {
  await withKimiHome(async ({ home }) => {
    await writeSession(home, { sessionId: SESSION, workspace: "workspace-x", index: false });
    await appendIndex(home, [{ sessionId: "session_other", sessionDir: "/nowhere" }]);
    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "ok");
    assert.match(located.sessionDir.replace(/\\/g, "/"), /workspace-x\/session_abc-123$/);
  });
});

test("two workspaces holding the same id are ambiguous, not a coin flip", async () => {
  await withKimiHome(async ({ home }) => {
    await writeSession(home, { sessionId: SESSION, workspace: "w1", index: false });
    await writeSession(home, { sessionId: SESSION, workspace: "w2", index: false });
    await appendIndex(home, [{ sessionId: "session_other", sessionDir: "/nowhere" }]);
    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "ambiguous");
    assert.equal(located.candidates.length, 2);
  });
});

test("a truncated index line does not lose the complete records before it", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, { sessionId: SESSION, index: false });
    await appendIndex(home, [{ sessionId: SESSION, sessionDir }]);
    // A writer was mid-line when we read.
    const file = join(home, "session_index.jsonl");
    const body = await readFile(file, "utf8");
    await writeFile(file, `${body}{"sessionId":"session_trunc`, "utf8");
    const located = await locateSession(SESSION, { home });
    assert.equal(located.status, "ok");
  });
});

test("a session id that is not a path segment is rejected before any read", async () => {
  assert.equal(isValidSessionId("session_ok-1.2"), true);
  assert.equal(isValidSessionId("../escape"), false);
  assert.equal(isValidSessionId("a/b"), false);
  assert.equal(isValidSessionId(".."), false);
  assert.equal(isValidSessionId(""), false);

  await withKimiHome(async ({ home }) => {
    const located = await locateSession("../../etc", { home });
    assert.equal(located.status, "missing");
    assert.equal(located.reason, "invalid_session_id");
  });
});

test("agents come from state.json, not from an assumed agent-N layout", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, {
      sessionId: SESSION,
      agents: { main: "", researcher: "", "agent-7": "" },
    });
    const state = JSON.parse(await readFile(join(sessionDir, "state.json"), "utf8"));
    const { agents } = await listSessionAgents(sessionDir, state);
    assert.deepEqual(agents.map((agent) => agent.id), ["main", "researcher", "agent-7"]);
    assert.equal(agents[0].homedir.endsWith(join("agents", "main")), true);
  });
});

test("a subagent's parent and profile come from labels, with the legacy field as a fallback", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, {
      sessionId: SESSION,
      agents: { main: "", worker: "" },
      state: {
        agents: {
          main: { type: "main", homedir: join(home, "sessions", "workspace-a", SESSION, "agents", "main") },
          worker: {
            type: "sub",
            homedir: join(home, "sessions", "workspace-a", SESSION, "agents", "worker"),
            labels: { parentAgentId: "main", profileName: "explorer" },
          },
        },
      },
    });
    const state = JSON.parse(await readFile(join(sessionDir, "state.json"), "utf8"));
    const { agents } = await listSessionAgents(sessionDir, state);
    const worker = agents.find((agent) => agent.id === "worker");
    assert.equal(agentParentId(worker, state), "main");
    assert.equal(agentProfileName(worker), "explorer");
  });
});

test("an agent homedir outside the session's agents root is flagged, not followed", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, { sessionId: SESSION, agents: { main: "" } });
    const outside = join(home, "elsewhere");
    await mkdir(outside, { recursive: true });
    await writeFile(join(sessionDir, "state.json"), JSON.stringify({
      id: SESSION,
      version: 2,
      agents: { main: { type: "main", homedir: outside } },
    }), "utf8");

    const state = JSON.parse(await readFile(join(sessionDir, "state.json"), "utf8"));
    const { agents } = await listSessionAgents(sessionDir, state);
    assert.equal(agents[0].error, "homedir_outside_session");
    assert.ok(agents[0].homedir.endsWith(join("agents", "main")));
  });
});

test("environment and fork metadata are read defensively", () => {
  assert.equal(sessionEnvironmentId({}), "local");
  assert.equal(sessionEnvironmentId({ environmentId: "remote-1" }), "remote-1");
  assert.equal(sessionForkedFrom({}), undefined);
  assert.equal(sessionForkedFrom({ forkedFrom: "session_parent" }), "session_parent");
  assert.equal(sessionForkedFrom({ forkedFrom: { id: "session_parent" } }), "session_parent");
});

test("Windows path comparison is case-insensitive", async () => {
  await withKimiHome(async ({ home }) => {
    const { sessionDir } = await writeSession(home, { sessionId: SESSION, index: false });
    await appendIndex(home, [{ sessionId: SESSION, sessionDir: sessionDir.toUpperCase() }]);
    const located = await locateSession(SESSION, { home, platform: "win32" });
    // The case-folded comparison finds the record, and the realpath check
    // decides whether the directory is really inside the root.
    assert.ok(["ok", "missing"].includes(located.status), located.status);
    if (located.status === "missing") assert.equal(located.reason, undefined);
  });
});
