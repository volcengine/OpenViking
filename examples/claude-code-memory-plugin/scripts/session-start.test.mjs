import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function runSessionStart(baseUrl, stateDir, extraEnv = {}) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "session-start.mjs")], {
      env: {
        ...cleanEnv,
        HOME: stateDir,
        OPENVIKING_MEMORY_ENABLED: "1",
        OPENVIKING_URL: baseUrl,
        OPENVIKING_CREDENTIAL_SOURCE: "env",
        OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
        OPENVIKING_STATE_DIR: join(stateDir, "shared-state"),
        OPENVIKING_PENDING_DIR: join(stateDir, "pending"),
        OPENVIKING_NO_AUTO_INJECT: "1",
        ...extraEnv,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`session-start exited ${code}: ${stderr}`));
        return;
      }
      resolve(JSON.parse(stdout.trim()));
    });
    child.stdin.end(JSON.stringify({
      session_id: "claude-policy",
      source: "startup",
      cwd: "/tmp/claude-policy",
      hook_event_name: "SessionStart",
    }));
  });
}

test("Claude SessionStart creates the session with an idle policy", async () => {
  const requests = [];
  const server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    requests.push({ method: req.method, url: req.url, body });
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/health") {
      res.end(JSON.stringify({ status: "ok", result: {} }));
      return;
    }
    if (req.url === "/api/v1/sessions") {
      const payload = JSON.parse(body);
      res.end(JSON.stringify({
        status: "ok",
        result: {
          session_id: payload.session_id,
          auto_commit_policy: payload.auto_commit_policy,
          auto_commit_idle_enabled: true,
        },
      }));
      return;
    }
    res.statusCode = 404;
    res.end(JSON.stringify({ status: "error", error: { code: "NOT_FOUND" } }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const stateDir = await mkdtemp(join(tmpdir(), "ov-claude-session-start-"));
  try {
    const { port } = server.address();
    await runSessionStart(`http://127.0.0.1:${port}`, stateDir);
    const policyRequest = requests.find((request) =>
      request.method === "POST" && request.url === "/api/v1/sessions"
    );
    assert.deepEqual(JSON.parse(policyRequest.body), {
      session_id: "cc-claude-policy",
      auto_commit_policy: {
        idle_timeout_seconds: 3600,
        pending_token_threshold: 0,
        message_count_threshold: 0,
      },
    });
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("Claude SessionStart sends no session policy when the knob is off", async () => {
  const requests = [];
  const server = createServer(async (req, res) => {
    requests.push({ method: req.method, url: req.url });
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ status: "ok", result: {} }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const stateDir = await mkdtemp(join(tmpdir(), "ov-claude-session-off-"));
  try {
    const { port } = server.address();
    await runSessionStart(`http://127.0.0.1:${port}`, stateDir, {
      OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS: "off",
    });
    assert.equal(
      requests.some((request) =>
        request.method === "POST" && request.url === "/api/v1/sessions"
      ),
      false,
    );
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(stateDir, { recursive: true, force: true });
  }
});
