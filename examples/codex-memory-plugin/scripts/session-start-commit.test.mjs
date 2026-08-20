import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
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
    OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP: "0",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_CAPTURE_TIMEOUT_MS: "5000",
  };
}

function profileHandler(requests, { archiveOverview = "" } = {}) {
  return async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    requests.push({
      method: req.method,
      path: url.pathname,
      uri: url.searchParams.get("uri"),
      actorPeerId: req.headers["x-openviking-actor-peer"] || "",
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
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
