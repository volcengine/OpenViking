import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function writeJson(res, statusCode, value) {
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      try {
        resolve(raw ? JSON.parse(raw) : null);
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

async function withMockOpenViking(handler, fn) {
  const server = http.createServer((req, res) => {
    handler(req, res).catch((err) => {
      writeJson(res, 500, { status: "error", error: String(err?.stack || err) });
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

function runAutoRecall(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "auto-recall.mjs")], {
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
        reject(new Error(`auto-recall exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function hookEnv(root, baseUrl, peerScope = "all", actorPeerId = "peer-123") {
  return {
    HOME: root,
    TMPDIR: root,
    OPENVIKING_AUTO_RECALL: "1",
    OPENVIKING_CONFIG_FILE: join(root, "missing-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(root, "missing-ovcli.conf"),
    OPENVIKING_MEMORY_ENABLED: "1",
    ...(actorPeerId ? { OPENVIKING_PEER_ID: actorPeerId } : {}),
    OPENVIKING_RECALL_COMPRESS: "off",
    OPENVIKING_RECALL_PEER_SCOPE: peerScope,
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    OPENVIKING_WORKSPACE_PEER: "0",
  };
}

async function runFallbackRecall(peerScope, actorPeerId = "peer-123") {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-recall-peer-"));
  const calls = [];
  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      const body = req.method === "POST" ? await readRequestBody(req) : null;
      calls.push({ path: url.pathname, body });

      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, 200, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        writeJson(res, 400, {
          status: "error",
          error: { message: "Extra inputs: mode" },
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/recall") {
        writeJson(res, 404, { status: "error", error: { code: "NOT_FOUND" } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/system/status") {
        writeJson(res, 200, { status: "ok", result: { user: "default" } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/fs/ls") {
        writeJson(res, 200, { status: "ok", result: [] });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/find") {
        writeJson(res, 200, {
          status: "ok",
          result: { memories: [], skills: [] },
        });
        return;
      }
      writeJson(res, 404, { status: "error", error: { code: "NOT_FOUND" } });
    }, async (baseUrl) => {
      await runAutoRecall(
        { session_id: `recall-${peerScope}`, cwd: root, prompt: "find peer memory" },
        hookEnv(root, baseUrl, peerScope, actorPeerId),
      );
    });
    return calls;
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test("raw fallback searches qualified actor peer memories and skills", async () => {
  const calls = await runFallbackRecall("all");
  const searchCalls = calls.filter((call) => call.path.startsWith("/api/v1/search/"));

  assert.deepEqual(
    searchCalls.slice(0, 2).map((call) => call.path),
    ["/api/v1/search/search", "/api/v1/search/recall"],
  );
  assert.deepEqual(
    searchCalls.filter((call) => call.path === "/api/v1/search/find")
      .map((call) => call.body.target_uri)
      .sort(),
    [
      "viking://user/default/memories",
      "viking://user/default/peers/peer-123/memories",
      "viking://user/default/peers/peer-123/skills",
      "viking://user/default/skills",
    ],
  );
});

test("raw actor-scoped fallback searches only qualified actor peer trees", async () => {
  const calls = await runFallbackRecall("actor");
  const searchCalls = calls.filter((call) => call.path.startsWith("/api/v1/search/"));

  assert.deepEqual(
    searchCalls.slice(0, 2).map((call) => call.path),
    ["/api/v1/search/search", "/api/v1/search/recall"],
  );
  assert.deepEqual(
    searchCalls.filter((call) => call.path === "/api/v1/search/find")
      .map((call) => call.body.target_uri)
      .sort(),
    [
      "viking://user/default/peers/peer-123/memories",
      "viking://user/default/peers/peer-123/skills",
    ],
  );
});

test("raw actor-scoped fallback preserves qualified user trees without actorPeerId", async () => {
  const calls = await runFallbackRecall("actor", "");
  const searchCalls = calls.filter((call) => call.path.startsWith("/api/v1/search/"));

  assert.deepEqual(
    searchCalls.slice(0, 2).map((call) => call.path),
    ["/api/v1/search/search", "/api/v1/search/recall"],
  );
  assert.deepEqual(
    searchCalls.filter((call) => call.path === "/api/v1/search/find")
      .map((call) => call.body.target_uri)
      .sort(),
    [
      "viking://user/default/memories",
      "viking://user/default/skills",
    ],
  );
});
