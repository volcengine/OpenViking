import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function writeJson(res, value) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

async function withMockOpenViking(fn) {
  const commitCalls = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (req.method === "GET" && url.pathname === "/health") {
      writeJson(res, { status: "ok", result: { ok: true } });
      return;
    }
    if (req.method === "POST" && url.pathname.endsWith("/commit")) {
      commitCalls.push({
        path: url.pathname,
        actorPeerId: req.headers["x-openviking-actor-peer"],
      });
      writeJson(res, {
        status: "ok",
        result: { archived: true, task_id: "task-test", status: "done" },
      });
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: "not found" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn({
      baseUrl: `http://127.0.0.1:${port}`,
      commitCalls,
    });
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function runSessionStart(input, env, cwd) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "session-start-commit.mjs")], {
      cwd,
      env: { ...cleanEnv, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`session-start-commit exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function baseEnv(stateDir, baseUrl, extra = {}) {
  return {
    OPENVIKING_CODEX_STATE_DIR: stateDir,
    OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP: "0",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    ...extra,
  };
}

async function writeState(stateDir, state) {
  await writeFile(join(stateDir, `${state.codexSessionId}.json`), JSON.stringify(state));
}

test("SessionStart commits a recently active session with that session's actor peer", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-peer-"));
  try {
    await writeState(stateDir, {
      codexSessionId: "old-session",
      ovSessionId: "cx-old-session",
      actorPeerId: "peer-from-old-session",
      workspacePeerId: "legacy-workspace-peer",
      capturedTurnCount: 2,
      createdAt: Date.now(),
      lastUpdatedAt: Date.now(),
    });

    await withMockOpenViking(async ({ baseUrl, commitCalls }) => {
      await runSessionStart(
        { session_id: "new-session", source: "startup" },
        baseEnv(stateDir, baseUrl, { OPENVIKING_PEER_ID: "peer-from-new-session" }),
        stateDir,
      );

      assert.deepEqual(commitCalls, [{
        path: "/api/v1/sessions/cx-old-session/commit",
        actorPeerId: "peer-from-old-session",
      }]);
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("SessionStart falls back to workspacePeerId for pre-actorPeerId state", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-workspace-peer-"));
  try {
    await writeState(stateDir, {
      codexSessionId: "legacy-session",
      ovSessionId: "cx-legacy-session",
      workspacePeerId: "peer-from-legacy-state",
      capturedTurnCount: 1,
      createdAt: Date.now(),
      lastUpdatedAt: Date.now(),
    });

    await withMockOpenViking(async ({ baseUrl, commitCalls }) => {
      await runSessionStart(
        { session_id: "new-session", source: "startup" },
        baseEnv(stateDir, baseUrl, { OPENVIKING_PEER_ID: "peer-from-new-session" }),
        stateDir,
      );

      assert.equal(commitCalls[0]?.actorPeerId, "peer-from-legacy-state");
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("SessionStart preserves legacy state whose actor peer is unknown", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-unknown-peer-"));
  try {
    await writeState(stateDir, {
      codexSessionId: "unknown-peer-session",
      ovSessionId: "cx-unknown-peer-session",
      capturedTurnCount: 1,
      createdAt: Date.now(),
      lastUpdatedAt: Date.now(),
    });

    await withMockOpenViking(async ({ baseUrl, commitCalls }) => {
      await runSessionStart(
        { session_id: "new-session", source: "startup" },
        baseEnv(stateDir, baseUrl, { OPENVIKING_PEER_ID: "peer-from-new-session" }),
        stateDir,
      );

      assert.deepEqual(commitCalls, []);
      const preserved = JSON.parse(
        await readFile(join(stateDir, "unknown-peer-session.json"), "utf-8"),
      );
      assert.equal(preserved.ovSessionId, "cx-unknown-peer-session");
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("SessionStart commits without actor header when state records peer isolation as disabled", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-global-peer-"));
  try {
    await writeState(stateDir, {
      codexSessionId: "global-session",
      ovSessionId: "cx-global-session",
      actorPeerId: "",
      workspacePeerId: "",
      capturedTurnCount: 1,
      createdAt: Date.now(),
      lastUpdatedAt: Date.now(),
    });

    await withMockOpenViking(async ({ baseUrl, commitCalls }) => {
      await runSessionStart(
        { session_id: "new-session", source: "startup" },
        baseEnv(stateDir, baseUrl, { OPENVIKING_PEER_ID: "peer-from-new-session" }),
        stateDir,
      );

      assert.deepEqual(commitCalls, [{
        path: "/api/v1/sessions/cx-global-session/commit",
        actorPeerId: undefined,
      }]);
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("SessionStart idle sweep uses the stale session's actor peer", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-idle-peer-"));
  try {
    const staleAt = Date.now() - 3_600_000;
    await writeState(stateDir, {
      codexSessionId: "stale-session",
      ovSessionId: "cx-stale-session",
      actorPeerId: "peer-from-stale-session",
      workspacePeerId: "legacy-stale-peer",
      capturedTurnCount: 1,
      createdAt: staleAt,
      lastUpdatedAt: staleAt,
    });

    await withMockOpenViking(async ({ baseUrl, commitCalls }) => {
      await runSessionStart(
        { session_id: "new-session", source: "startup" },
        baseEnv(stateDir, baseUrl, { OPENVIKING_PEER_ID: "peer-from-new-session" }),
        stateDir,
      );

      assert.deepEqual(commitCalls, [{
        path: "/api/v1/sessions/cx-stale-session/commit",
        actorPeerId: "peer-from-stale-session",
      }]);
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("SessionStart persists the effective actor peer on the new session state", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-persist-peer-"));
  try {
    await runSessionStart(
      { session_id: "new-session", source: "unknown" },
      baseEnv(stateDir, "http://127.0.0.1:1", { OPENVIKING_PEER_ID: "configured-peer" }),
      stateDir,
    );

    const state = JSON.parse(await readFile(join(stateDir, "new-session.json"), "utf-8"));
    assert.equal(state.actorPeerId, "configured-peer");
    assert.equal(state.workspacePeerId, "");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("SessionStart preserves a session's persisted actor peer across resume", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-preserve-peer-"));
  try {
    await writeState(stateDir, {
      codexSessionId: "resumed-session",
      ovSessionId: "cx-resumed-session",
      actorPeerId: "original-session-peer",
      workspacePeerId: "original-session-peer",
      capturedTurnCount: 3,
      createdAt: Date.now(),
      lastUpdatedAt: Date.now(),
    });

    await runSessionStart(
      { session_id: "resumed-session", source: "unknown" },
      baseEnv(stateDir, "http://127.0.0.1:1", { OPENVIKING_PEER_ID: "changed-config-peer" }),
      stateDir,
    );

    const state = JSON.parse(await readFile(join(stateDir, "resumed-session.json"), "utf-8"));
    assert.equal(state.actorPeerId, "original-session-peer");
    assert.equal(state.workspacePeerId, "original-session-peer");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
