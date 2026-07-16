import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const AUTO_RECALL = join(SCRIPT_DIR, "..", "auto-recall.mjs");

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf-8");
        resolve(raw ? JSON.parse(raw) : null);
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res, value) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

async function withMockOpenViking(handler, fn) {
  const server = http.createServer((req, res) => {
    handler(req, res).catch((err) => {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: String(err?.stack || err) }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    return await fn(`http://127.0.0.1:${server.address().port}`);
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
    const child = spawn(process.execPath, [AUTO_RECALL], {
      env: { ...cleanEnv, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(`auto-recall exited ${code}: ${stderr}`));
      else resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("Claude auto-recall injects the bounded archive fallback after empty recall", async () => {
  const ovHome = await mkdtemp(join(tmpdir(), "ov-cc-archive-fallback-"));
  const requests = [];
  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/recall") {
        requests.push({ path: url.pathname, body: await readRequestBody(req) });
        writeJson(res, { status: "ok", result: { entries: [], rendered: "" } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/grep") {
        const body = await readRequestBody(req);
        requests.push({ path: url.pathname, body });
        writeJson(res, {
          status: "ok",
          result: {
            matches: [{
              uri: "viking://user/alice/sessions/cc-old/history/archive_002/.overview.md",
              line: 11,
              content: "release-42 used make deploy-canary",
            }],
          },
        });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall({
        prompt: "What command did we use previously for release-42?",
        session_id: "cc-archive",
        cwd: process.cwd(),
      }, {
        OPENVIKING_MEMORY_ENABLED: "1",
        OPENVIKING_AUTO_RECALL: "1",
        OPENVIKING_CONFIG_FILE: join(ovHome, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(ovHome, "missing-ovcli.conf"),
        OPENVIKING_HOME: ovHome,
        OPENVIKING_MIN_QUERY_LENGTH: "1",
        OPENVIKING_TIMEOUT_MS: "5000",
        OPENVIKING_URL: baseUrl,
        OPENVIKING_USER: "alice",
      });

      const output = JSON.parse(result.stdout.trim());
      assert.equal(output.decision, "approve");
      assert.match(output.hookSpecificOutput.additionalContext, /read-only-fallback/);
      assert.match(output.hookSpecificOutput.additionalContext, /make deploy-canary/);
      assert.match(output.hookSpecificOutput.additionalContext, /archive_002\/\.overview\.md#L11/);
    });

    assert.deepEqual(requests.map((request) => request.path), [
      "/api/v1/search/recall",
      "/api/v1/search/grep",
    ]);
    assert.deepEqual(requests[1].body, {
      uri: "viking://user/alice/sessions",
      pattern: "release-42|release",
      case_insensitive: true,
      node_limit: 12,
      level_limit: 10,
    });
  } finally {
    await rm(ovHome, { recursive: true, force: true });
  }
});
