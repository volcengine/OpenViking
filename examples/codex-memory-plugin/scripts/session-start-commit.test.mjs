import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
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

async function withMockOpenViking(handler, fn) {
  const server = http.createServer((req, res) => {
    Promise.resolve(handler(req, res)).catch((error) => {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: String(error?.stack || error) }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function runSessionStart(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "session-start-commit.mjs")], {
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
      resolve({ output: JSON.parse(stdout.trim()), stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function baseEnv(baseUrl, stateDir) {
  return {
    OPENVIKING_URL: baseUrl,
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
    OPENVIKING_CODEX_STATE_DIR: stateDir,
    OPENVIKING_STATE_DIR: join(stateDir, "shared-state"),
    OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP: "0",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_CAPTURE_TIMEOUT_MS: "5000",
  };
}

function profileHandler(
  requests,
  {
    archiveOverview = "",
    sessionPolicyIdleActive,
  } = {},
) {
  return async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    let body = "";
    for await (const chunk of req) body += chunk;
    requests.push({
      method: req.method,
      path: url.pathname,
      uri: url.searchParams.get("uri"),
      actorPeerId: req.headers["x-openviking-actor-peer"] || "",
      body,
    });

    if (req.method === "GET" && url.pathname === "/health") {
      writeJson(res, { status: "ok", result: { healthy: true } });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/v1/system/status") {
      writeJson(res, { status: "ok", result: { user: "zeus" } });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
      writeJson(res, {
        status: "ok",
        result: "# Zeus\nWorks on OpenViking integrations.\nPrefers concise implementation notes.",
      });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/v1/fs/ls") {
      const uri = url.searchParams.get("uri");
      if (uri === "viking://user") {
        writeJson(res, {
          status: "ok",
          result: [{ name: "zeus", isDir: true }],
        });
        return;
      }
      if (uri?.endsWith("/preferences")) {
        writeJson(res, {
          status: "ok",
          result: [{
            name: "workflow.md",
            rel_path: "zeus/workflow.md",
            abstract: "Prefer focused changes and targeted tests.",
            isDir: false,
          }],
        });
        return;
      }
      if (uri?.endsWith("/entities")) {
        writeJson(res, {
          status: "ok",
          result: [{
            name: "openviking.md",
            rel_path: "software/openviking.md",
            abstract: "OpenViking memory and context platform.",
            isDir: false,
          }],
        });
        return;
      }
    }
    if (req.method === "GET" && url.pathname.endsWith("/context")) {
      writeJson(res, {
        status: "ok",
        result: {
          latest_archive_overview: archiveOverview,
          pre_archive_abstracts: [],
        },
      });
      return;
    }
    if (
      req.method === "POST"
      && url.pathname === "/api/v1/sessions"
      && typeof sessionPolicyIdleActive === "boolean"
    ) {
      const payload = JSON.parse(body || "{}");
      writeJson(res, {
        status: "ok",
        result: {
          session_id: payload.session_id,
          auto_commit_policy: payload.auto_commit_policy,
          auto_commit_idle_enabled: sessionPolicyIdleActive,
        },
      });
      return;
    }
    if (req.method === "POST" && url.pathname.endsWith("/commit")) {
      writeJson(res, {
        status: "ok",
        result: {
          archived: true,
          task_id: "task-profile-test",
          trace_id: "trace-session-start",
        },
      });
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: "not found" }));
  };
}

test("startup injects the shared profile block with workspace peer routing", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-session-start-"));
  const requests = [];
  try {
    await withMockOpenViking(profileHandler(requests), async (baseUrl) => {
      const { output } = await runSessionStart(
        {
          session_id: "startup-profile",
          source: "startup",
          cwd: "/tmp/codex-profile",
          hook_event_name: "SessionStart",
        },
        baseEnv(baseUrl, stateDir),
      );

      assert.equal(output.hookSpecificOutput.hookEventName, "SessionStart");
      assert.match(output.hookSpecificOutput.additionalContext, /source="session-start"/);
      assert.match(output.hookSpecificOutput.additionalContext, /<user-profile uri="viking:\/\/user\/zeus\/memories\/profile\.md">/);
      assert.match(output.hookSpecificOutput.additionalContext, /Works on OpenViking integrations/);
      assert.match(output.hookSpecificOutput.additionalContext, /zeus\/workflow\.md/);
      assert.match(output.hookSpecificOutput.additionalContext, /software\/openviking\.md/);
      assert.equal(output.systemMessage, undefined);
    });

    const profileRequests = requests.filter((request) =>
      request.path === "/api/v1/content/read" || request.path === "/api/v1/fs/ls"
    );
    assert.ok(profileRequests.length >= 4);
    assert.ok(profileRequests.every((request) => request.actorPeerId === "-tmp-codex-profile"));
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("startup preserves the existing commit systemMessage alongside profile context", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-session-commit-"));
  const requests = [];
  try {
    await mkdir(stateDir, { recursive: true });
    const now = Date.now();
    await writeFile(join(stateDir, "old-session.json"), JSON.stringify({
      codexSessionId: "old-session",
      ovSessionId: "cx-old-session",
      capturedTurnCount: 2,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));

    await withMockOpenViking(profileHandler(requests), async (baseUrl) => {
      const { output } = await runSessionStart(
        {
          session_id: "new-session",
          source: "startup",
          cwd: "/tmp/codex-commit",
          hook_event_name: "SessionStart",
        },
        baseEnv(baseUrl, stateDir),
      );

      assert.match(output.hookSpecificOutput.additionalContext, /Works on OpenViking integrations/);
      assert.equal(
        output.systemMessage,
        "OpenViking session cx-old-session is committed (trace_id=trace-session-start)",
      );
    });

    assert.ok(requests.some((request) =>
      request.method === "POST"
      && request.path === "/api/v1/sessions/cx-old-session/commit"
    ));
    const commitRequest = requests.find((request) =>
      request.method === "POST"
      && request.path === "/api/v1/sessions/cx-old-session/commit"
    );
    assert.equal(commitRequest.actorPeerId, "-tmp-codex-commit");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("policy marker is written only when the server confirms idle sweeping", async () => {
  for (const [idleActive, expectedMarker] of [[true, true], [false, undefined]]) {
    const stateDir = await mkdtemp(join(tmpdir(), `ov-codex-policy-${idleActive}-`));
    const requests = [];
    try {
      await withMockOpenViking(
        profileHandler(requests, { sessionPolicyIdleActive: idleActive }),
        async (baseUrl) => {
          await runSessionStart({
            session_id: `policy-${idleActive}`,
            source: "startup",
            cwd: "/tmp/codex-policy",
            hook_event_name: "SessionStart",
          }, baseEnv(baseUrl, stateDir));
        },
      );

      const state = JSON.parse(await readFile(
        join(stateDir, `policy-${idleActive}.json`),
        "utf8",
      ));
      assert.equal(state.serverIdleCommit, expectedMarker);
      assert.equal(
        state.serverIdleTimeoutSeconds,
        expectedMarker ? 3600 : undefined,
      );
      const policyRequest = requests.find((request) =>
        request.method === "POST" && request.path === "/api/v1/sessions"
      );
      assert.deepEqual(JSON.parse(policyRequest.body), {
        session_id: `cx-policy-${idleActive}`,
        auto_commit_policy: {
          idle_timeout_seconds: 3600,
          pending_token_threshold: 0,
          message_count_threshold: 0,
        },
      });
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  }
});

test("idle policy knob off sends no session create request", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-policy-off-"));
  const requests = [];
  try {
    await withMockOpenViking(profileHandler(requests), async (baseUrl) => {
      await runSessionStart({
        session_id: "policy-off",
        source: "startup",
        cwd: "/tmp/codex-policy-off",
        hook_event_name: "SessionStart",
      }, {
        ...baseEnv(baseUrl, stateDir),
        OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS: "off",
      });
    });

    assert.equal(
      requests.some((request) =>
        request.method === "POST" && request.path === "/api/v1/sessions"
      ),
      false,
    );
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("server-covered stale state skips local commit and is eventually garbage-collected", async () => {
  for (const [ageMs, shouldExist] of [
    [2_000_000, true],
    [4_300_000, false],
  ]) {
    const stateDir = await mkdtemp(join(tmpdir(), `ov-codex-covered-${ageMs}-`));
    const requests = [];
    const now = Date.now();
    try {
      await writeFile(join(stateDir, "covered.json"), JSON.stringify({
        codexSessionId: "covered",
        ovSessionId: "cx-covered",
        capturedTurnCount: 2,
        serverIdleCommit: true,
        serverIdleTimeoutSeconds: 3600,
        createdAt: now - ageMs,
        lastUpdatedAt: now - ageMs,
      }));
      await withMockOpenViking(
        profileHandler(requests, { sessionPolicyIdleActive: true }),
        async (baseUrl) => {
          await runSessionStart({
            session_id: `new-covered-${ageMs}`,
            source: "startup",
            cwd: "/tmp/codex-covered",
            hook_event_name: "SessionStart",
          }, baseEnv(baseUrl, stateDir));
        },
      );

      assert.equal(
        requests.some((request) =>
          request.path === "/api/v1/sessions/cx-covered/commit"
        ),
        false,
      );
      const files = await import("node:fs/promises").then((fs) => fs.readdir(stateDir));
      assert.equal(files.includes("covered.json"), shouldExist);
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  }
});

// Regression guard for the gate itself: broadening the covered-skip predicate
// (e.g. `!== false` instead of `=== true`) would silently stop committing
// killed sessions on every stock server, where auto_commit_idle_enabled is
// false. These two cases must keep sweeping locally.
test("local idle sweep still commits stale state the server does not cover", async () => {
  for (const [label, extraState] of [
    ["unmarked", {}],
    ["marked without a usable timeout", { serverIdleCommit: true }],
  ]) {
    const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-uncovered-"));
    const requests = [];
    try {
      const now = Date.now();
      await writeFile(join(stateDir, "stale.json"), JSON.stringify({
        codexSessionId: "stale",
        ovSessionId: "cx-stale",
        capturedTurnCount: 2,
        createdAt: now - 2_000_000,
        lastUpdatedAt: now - 2_000_000,
        ...extraState,
      }));
      await withMockOpenViking(
        profileHandler(requests, { sessionPolicyIdleActive: false }),
        async (baseUrl) => {
          await runSessionStart({
            session_id: "new-uncovered",
            source: "startup",
            cwd: "/tmp/codex-uncovered",
            hook_event_name: "SessionStart",
          }, baseEnv(baseUrl, stateDir));
        },
      );

      assert.equal(
        requests.some((request) =>
          request.method === "POST"
          && request.path === "/api/v1/sessions/cx-stale/commit"
        ),
        true,
        `expected a local sweep commit for ${label} stale state`,
      );
      const files = await readdir(stateDir);
      assert.equal(files.includes("stale.json"), false, `expected ${label} state cleared`);
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  }
});

test("idle-inactive server leaves no covered marker on the current session", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-inactive-marker-"));
  const requests = [];
  try {
    await withMockOpenViking(
      profileHandler(requests, { sessionPolicyIdleActive: false }),
      async (baseUrl) => {
        await runSessionStart({
          session_id: "inactive-marker",
          source: "startup",
          cwd: "/tmp/codex-inactive",
          hook_event_name: "SessionStart",
        }, baseEnv(baseUrl, stateDir));
      },
    );

    const state = JSON.parse(await readFile(join(stateDir, "inactive-marker.json"), "utf-8"));
    assert.equal(state.serverIdleCommit, undefined);
    assert.equal(state.serverIdleTimeoutSeconds, undefined);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("startup without a session id writes no unknown state file", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-unknown-"));
  const requests = [];
  try {
    await withMockOpenViking(
      profileHandler(requests, { sessionPolicyIdleActive: true }),
      async (baseUrl) => {
        await runSessionStart({
          source: "startup",
          cwd: "/tmp/codex-unknown",
          hook_event_name: "SessionStart",
        }, baseEnv(baseUrl, stateDir));
      },
    );

    const files = (await readdir(stateDir)).filter((name) => name.endsWith(".json"));
    assert.equal(files.includes("unknown.json"), false);
    assert.equal(
      requests.some((request) =>
        request.method === "POST"
        && request.path === "/api/v1/sessions"
        && request.body.includes("cx-unknown")
      ),
      false,
    );
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("swept commit uses the stored workspace peer identity", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-stored-peer-"));
  const requests = [];
  try {
    const now = Date.now();
    await writeFile(join(stateDir, "stored-peer.json"), JSON.stringify({
      codexSessionId: "stored-peer",
      ovSessionId: "cx-stored-peer",
      workspacePeerId: "-old-workspace",
      capturedTurnCount: 2,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));
    await withMockOpenViking(profileHandler(requests), async (baseUrl) => {
      await runSessionStart({
        session_id: "new-peer",
        source: "startup",
        cwd: "/tmp/new-workspace",
        hook_event_name: "SessionStart",
      }, baseEnv(baseUrl, stateDir));
    });

    const commitRequest = requests.find((request) =>
      request.path === "/api/v1/sessions/cx-stored-peer/commit"
    );
    assert.equal(commitRequest.actorPeerId, "-old-workspace");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
