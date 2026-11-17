/**
 * The explicit history import, run the way a user runs it.
 *
 * Three promises matter here: a dry run touches nothing, a fresh session is
 * replayed from its first record and committed, and a second run does not
 * replay what the first one already delivered (OpenViking has no server-side
 * dedup). A non-local session is skipped before any request.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { ovSessionIdFor } from "../../scripts/capture-session.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import { forked, metadata, simpleTurn, wireText } from "../fixtures/wire-fixtures.mjs";

const BACKFILL = fileURLToPath(new URL("../../scripts/backfill.mjs", import.meta.url));

function backfillEnv(home, server) {
  return {
    ...process.env,
    ...fakeOvEnv(server),
    KIMI_CODE_HOME: home,
    OPENVIKING_HOOK_STATE_DIR: join(home, "hook-state"),
    OPENVIKING_PENDING_DIR: join(home, "pending"),
    HOME: home,
    USERPROFILE: home,
    OPENVIKING_CLI_CONFIG_FILE: join(home, ".openviking", "ovcli.conf"),
    OPENVIKING_CONFIG_FILE: join(home, ".openviking", "ov.conf"),
  };
}

async function runBackfill(args, { home, server, env = {} }) {
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [BACKFILL, ...args], {
      env: { ...backfillEnv(home, server), ...env },
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

function twoTurns() {
  return wireText([
    metadata(),
    ...simpleTurn(0, { prompt: "first question", reply: "first answer" }),
    ...simpleTurn(1, { prompt: "second question", reply: "second answer" }),
  ]);
}

function writes(server) {
  return server.requests.filter((request) => request.method === "POST");
}

test("--dry-run lists the session without a request or a state write", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: "session_backfill", wireTextFor: twoTurns });
      const result = await runBackfill(["--dry-run"], { home, server });
      assert.equal(result.code, 0, result.stderr);
      assert.match(result.stdout, /session_backfill\s+import/);
      assert.equal(server.requests.length, 0, "a dry run must not talk to the server");
      await assert.rejects(
        readFile(join(home, "hook-state", "kimicode", "session_backfill.json"), "utf8"),
        "a dry run must not write state",
      );
    });
  } finally {
    await server.close();
  }
});

test("a fresh session is imported from its first turn and committed", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: "session_backfill", wireTextFor: twoTurns });
      const ovSessionId = ovSessionIdFor("session_backfill", "main");

      const first = await runBackfill([], { home, server });
      assert.equal(first.code, 0, first.stderr);
      assert.deepEqual(
        server.messages(ovSessionId).map((message) => message.parts[0].text),
        ["first question", "first answer", "second question", "second answer"],
      );
      assert.ok(
        server.sessions.get(`${ovSessionId}::commits`)?.length > 0,
        "the forced boundary must commit the imported turns",
      );

      const state = JSON.parse(
        await readFile(join(home, "hook-state", "kimicode", "session_backfill.json"), "utf8"),
      );
      assert.equal(state.nativeSessionId, "session_backfill");
      assert.ok(state.agents.main.ackOffset > 0, "the cursor must advance past delivered turns");
      assert.ok(state.agents.main.scanOffset > 0, "the scan cursor must persist the imported range");

      const writesBefore = writes(server).length;
      const second = await runBackfill([], { home, server });
      assert.equal(second.code, 0, second.stderr);
      assert.match(second.stdout, /session_backfill — catch up/);
      assert.equal(writes(server).length, writesBefore, "a second run must not re-send the transcript");
    });
  } finally {
    await server.close();
  }
});

test("a non-local environment is skipped before any request", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: "session_remote",
        state: { environmentId: "remote-7" },
        wireTextFor: twoTurns,
      });
      const result = await runBackfill([], { home, server });
      assert.equal(result.code, 0, result.stderr);
      assert.match(result.stdout, /session_remote — skip \(non_local_environment\)/);
      assert.equal(server.requests.length, 0);
      await assert.rejects(
        readFile(join(home, "hook-state", "kimicode", "session_remote.json"), "utf8"),
        "a skipped session must not get state",
      );
    });
  } finally {
    await server.close();
  }
});

test("a bypassed session is skipped even without plugin state", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: "session_bypass", wireTextFor: twoTurns });
      const result = await runBackfill([], {
        home,
        server,
        env: { OPENVIKING_BYPASS_SESSION: "true" },
      });
      assert.equal(result.code, 0, result.stderr);
      assert.match(result.stdout, /session_bypass — skip \(bypassed\)/);
      assert.equal(server.requests.length, 0);
      await assert.rejects(
        readFile(join(home, "hook-state", "kimicode", "session_bypass.json"), "utf8"),
        "a bypassed session must not get state",
      );
    });
  } finally {
    await server.close();
  }
});

test("an unreachable server fails before any history reaches the queue", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: "session_offline", wireTextFor: twoTurns });
      const result = await runBackfill([], {
        home,
        server,
        env: { OPENVIKING_URL: "http://127.0.0.1:9" },
      });
      assert.equal(result.code, 2, result.stdout);
      assert.match(result.stderr, /server unreachable/);
      assert.equal(server.requests.length, 0);
      await assert.rejects(
        readFile(join(home, "hook-state", "kimicode", "session_offline.json"), "utf8"),
        "a failed preflight must not create state or queue messages",
      );
    });
  } finally {
    await server.close();
  }
});

test("backfill keeps a fork's inherited prefix out", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, {
        sessionId: "session_fork",
        state: { forkedFrom: "session_parent" },
        wireTextFor: () => wireText([
          metadata(),
          ...simpleTurn(0, { prompt: "inherited question", reply: "inherited answer" }),
          forked({ agentId: "main", time: 1760000009000 }),
          ...simpleTurn(1, { prompt: "fork's own question", reply: "fork's own answer" }),
        ]),
      });
      const result = await runBackfill([], { home, server });
      assert.equal(result.code, 0, result.stderr);
      assert.deepEqual(
        server.messages(ovSessionIdFor("session_fork", "main")).map((message) => message.parts[0].text),
        ["fork's own question", "fork's own answer"],
      );
    });
  } finally {
    await server.close();
  }
});
