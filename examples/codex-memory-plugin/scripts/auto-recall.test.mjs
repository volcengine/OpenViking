import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

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
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function runAutoRecall(input, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "auto-recall.mjs")], {
      env: { ...process.env, ...env },
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

test("auto-recall uses context-aware search with the derived OpenViking session id", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-state-"));
  const requests = [];

  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        const body = await readRequestBody(req);
        requests.push({ path: url.pathname, body });
        if (body.target_uri === "viking://user/memories") {
          writeJson(res, {
            status: "ok",
            result: {
              memories: [{
                uri: "viking://user/zeus/memories/events/context-search.md",
                level: 2,
                score: 0.9,
                category: "events",
                abstract: "context search memory",
              }],
              skills: [],
            },
          });
          return;
        }
        writeJson(res, { status: "ok", result: { memories: [], skills: [] } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
        writeJson(res, { status: "ok", result: "context-aware recalled detail" });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "please use prior context", session_id: "codex:123" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "1",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      assert.match(
        output.hookSpecificOutput.additionalContext,
        /context-aware recalled detail/,
      );
    });

    assert.equal(requests.length, 2);
    assert.deepEqual(
      requests.map((request) => request.body.target_uri).sort(),
      ["viking://user/memories", "viking://user/skills"],
    );
    assert.ok(requests.every((request) => request.body.session_id === "cx-codex_123"));
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
