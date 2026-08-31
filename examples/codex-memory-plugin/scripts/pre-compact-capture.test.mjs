import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

async function endedMarkerStamps(dir, id) {
  const prefix = `${id}.ended.`;
  const files = await readdir(dir).catch(() => []);
  return files
    .filter((name) => name.startsWith(prefix))
    .map((name) => Number(name.slice(prefix.length)))
    .filter((ts) => Number.isFinite(ts));
}

async function endedMarkerExists(dir, id) {
  return (await endedMarkerStamps(dir, id)).length > 0;
}

function writeEndedMarker(dir, id, ts) {
  return writeFile(join(dir, `${id}.ended.${ts}`), String(ts));
}


function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      try { resolve(raw ? JSON.parse(raw) : null); } catch (err) { reject(err); }
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
    Promise.resolve(handler(req, res)).catch((err) => {
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

function runPreCompact(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "pre-compact-capture.mjs")], {
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
        reject(new Error(`pre-compact-capture exited ${code}: ${stderr}`));
        return;
      }
      resolve({ output: JSON.parse(stdout.trim()), stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function baseEnv(baseUrl, stateDir, extra = {}) {
  return {
    OPENVIKING_URL: baseUrl,
    OPENVIKING_AUTO_CAPTURE: "1",
    OPENVIKING_CAPTURE_ASSISTANT_TURNS: "1",
    OPENVIKING_CODEX_STATE_DIR: stateDir,
    OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_MIN_QUERY_LENGTH: "1",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_CAPTURE_TIMEOUT_MS: "5000",
    ...extra,
  };
}

function turn(role, content) {
  return JSON.stringify({ payload: { message: { role, content } } });
}

async function exists(path) {
  try { await stat(path); return true; } catch { return false; }
}

function mockHandler(calls) {
  return async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const call = { method: req.method, path: url.pathname, body: null };
    calls.push(call);
    if (req.method === "GET" && url.pathname === "/health") {
      writeJson(res, { status: "ok", result: { ok: true } });
      return;
    }
    if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
      call.body = await readRequestBody(req);
      writeJson(res, { status: "ok", result: { ok: true } });
      return;
    }
    if (req.method === "POST" && url.pathname.endsWith("/commit")) {
      call.body = await readRequestBody(req);
      writeJson(res, { status: "ok", result: { archived: true, task_id: "task-pc", trace_id: "trace-pc" } });
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: "not found" }));
  };
}

test("pre-compact catches up, commits and keeps the cursor", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-pre-compact-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    const now = Date.now();
    await writeFile(join(stateDir, "pc1.json"), JSON.stringify({
      codexSessionId: "pc1",
      ovSessionId: "cx-pc1",
      capturedTurnCount: 2,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));
    await writeEndedMarker(stateDir, "pc1", now);
    await writeFile(transcriptPath, [
      turn("user", "turn-0"),
      turn("assistant", "turn-1"),
      turn("user", "turn-2"),
      turn("assistant", "turn-3"),
    ].join("\n"));

    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      const { output } = await runPreCompact(
        { session_id: "pc1", transcript_path: transcriptPath, trigger: "auto" },
        baseEnv(baseUrl, stateDir),
      );
      assert.match(output.systemMessage, /cx-pc1 is committed \(trace_id=trace-pc\)/);
    });

    const messages = calls
      .filter((c) => c.path.endsWith("/messages/batch"))
      .flatMap((c) => c.body?.messages ?? []);
    assert.equal(messages.length, 2);
    assert.ok(calls.some((c) => c.path === "/api/v1/sessions/cx-pc1/commit"));

    const state = JSON.parse(await readFile(join(stateDir, "pc1.json"), "utf-8"));
    assert.equal(state.ovSessionId, null);
    assert.equal(state.capturedTurnCount, 4);
    assert.equal(await endedMarkerExists(stateDir, "pc1"), false, "compaction proves the thread is alive");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("pre-compact leaves state untouched when the session lock is held", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-pre-compact-lock-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    const now = Date.now();
    await writeFile(join(stateDir, "pc2.json"), JSON.stringify({
      codexSessionId: "pc2",
      ovSessionId: "cx-pc2",
      capturedTurnCount: 1,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));
    await writeFile(transcriptPath, [turn("user", "turn-0"), turn("assistant", "turn-1")].join("\n"));
    await mkdir(join(stateDir, "pc2.lock"), { recursive: true });

    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      const { output } = await runPreCompact(
        { session_id: "pc2", transcript_path: transcriptPath, trigger: "manual" },
        baseEnv(baseUrl, stateDir, { OPENVIKING_CODEX_LOCK_WAIT_MS: "300" }),
      );
      assert.deepEqual(output, {});
    });

    assert.equal(calls.length, 0, "a held lock must stop every HTTP call");
    const state = JSON.parse(await readFile(join(stateDir, "pc2.json"), "utf-8"));
    assert.equal(state.ovSessionId, "cx-pc2");
    assert.equal(state.capturedTurnCount, 1);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("pre-compact does not commit when the catch-up append fails entirely", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-pre-compact-fail-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  const handler = async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    calls.push({ method: req.method, path: url.pathname });
    if (req.method === "GET" && url.pathname === "/health") {
      writeJson(res, { status: "ok", result: { ok: true } });
      return;
    }
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: { message: "boom" } }));
  };
  try {
    const now = Date.now();
    await writeFile(join(stateDir, "pc3.json"), JSON.stringify({
      codexSessionId: "pc3",
      ovSessionId: "cx-pc3",
      capturedTurnCount: 2,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));
    await writeFile(transcriptPath, [
      turn("user", "turn-0"),
      turn("assistant", "turn-1"),
      turn("user", "turn-2"),
      turn("assistant", "turn-3"),
    ].join("\n"));

    await withMockOpenViking(handler, async (baseUrl) => {
      const { output } = await runPreCompact(
        { session_id: "pc3", transcript_path: transcriptPath, trigger: "manual" },
        baseEnv(baseUrl, stateDir),
      );
      assert.match(output.systemMessage, /catch-up append incomplete for cx-pc3/);
    });

    assert.ok(!calls.some((c) => c.path.endsWith("/commit")), "must not commit with turns still unsent");
    const state = JSON.parse(await readFile(join(stateDir, "pc3.json"), "utf-8"));
    assert.equal(state.ovSessionId, "cx-pc3");
    assert.equal(state.capturedTurnCount, 2);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
