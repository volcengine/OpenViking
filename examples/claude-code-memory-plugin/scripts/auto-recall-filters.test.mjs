import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
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
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      try {
        resolve(raw ? JSON.parse(raw) : null);
      } catch {
        resolve(null);
      }
    });
  });
}

/** Mock server that records every request the hook makes. */
async function withRecordedServer(fn) {
  const seen = [];
  const server = http.createServer((req, res) => {
    readRequestBody(req).then((body) => {
      seen.push({ method: req.method, path: req.url.split("?")[0], body });
      if (req.url.startsWith("/health")) {
        writeJson(res, 200, { status: "ok", result: { healthy: true } });
        return;
      }
      writeJson(res, 200, { status: "ok", result: {} });
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`, seen);
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
      if (code !== 0) reject(new Error(`auto-recall exited ${code}: ${stderr}`));
      else resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function hookEnv(root, baseUrl, extra) {
  return {
    HOME: root,
    TMPDIR: root,
    OPENVIKING_HOME: join(root, ".openviking"),
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_AUTO_RECALL: "1",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    ...extra,
  };
}

async function lastRecall(root) {
  return JSON.parse(await readFile(join(root, ".openviking", "state", "last-recall.json"), "utf-8"));
}

test("a drop rule ends the turn before any request is made", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-recall-filter-drop-"));
  try {
    await withRecordedServer(async (baseUrl, seen) => {
      const { stdout } = await runAutoRecall(
        { session_id: "filter-drop", prompt: "/help me out here", cwd: root },
        hookEnv(root, baseUrl, { OPENVIKING_RECALL_QUERY_FILTERS: "d|^\\s*[/!]|" }),
      );
      assert.deepEqual(JSON.parse(stdout), { decision: "approve" });
      assert.deepEqual(seen, []);
    });
    const state = await lastRecall(root);
    assert.equal(state.reason, "query_filtered");
    assert.equal(state.count, 0);
    assert.equal(state.cc_session_id, "filter-drop");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a substitute rule rewrites the query the server is asked for", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-recall-filter-sub-"));
  try {
    await withRecordedServer(async (baseUrl, seen) => {
      await runAutoRecall(
        { session_id: "filter-sub", prompt: "ultrathink what did we decide about retries", cwd: root },
        hookEnv(root, baseUrl, { OPENVIKING_RECALL_QUERY_FILTERS: "s/^\\s*ultrathink\\s+//i" }),
      );
      const queries = seen.map((call) => call.body?.query).filter(Boolean);
      assert.ok(queries.length > 0, `no query was sent: ${JSON.stringify(seen)}`);
      for (const query of queries) {
        assert.equal(query, "what did we decide about retries");
      }
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("an unparsable rule is skipped and the turn proceeds", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-recall-filter-bad-"));
  try {
    await withRecordedServer(async (baseUrl, seen) => {
      await runAutoRecall(
        { session_id: "filter-bad", prompt: "what did we decide about retries", cwd: root },
        hookEnv(root, baseUrl, { OPENVIKING_RECALL_QUERY_FILTERS: "s/(/x/" }),
      );
      assert.ok(seen.some((call) => call.path === "/health"), "the turn should have proceeded");
    });
    assert.notEqual((await lastRecall(root)).reason, "query_filtered");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
