import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { withStateTransaction } from "./session-state.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf-8") || "null"));
      } catch (error) {
        reject(error);
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
    handler(req, res).catch((error) => {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: String(error?.stack || error) }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    return await fn(`http://127.0.0.1:${server.address().port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function startPreCompact(input, env) {
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
  const closed = new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
  child.stdin.end(JSON.stringify(input));
  return { child, closed };
}

test("pre-compact persists each accepted batch before attempting the next one", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-pre-compact-crash-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const batches = [];
  let markSecondBatch;
  const secondBatchSeen = new Promise((resolve) => { markSecondBatch = resolve; });

  try {
    const entries = Array.from({ length: 101 }, (_, index) => ({
      payload: {
        message: { role: "user", content: `durable pre-compact turn ${index}` },
      },
    }));
    await writeFile(transcriptPath, entries.map((entry) => JSON.stringify(entry)).join("\n"));

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        batches.push(await readRequestBody(req));
        if (batches.length === 1) {
          writeJson(res, { status: "ok", result: { ok: true } });
        } else {
          // Seeing the next request proves the first batch's awaited onSent
          // callback (including its atomic state save) has completed.
          markSecondBatch();
        }
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const running = startPreCompact(
        { session_id: "precompact-crash", transcript_path: transcriptPath, trigger: "auto" },
        {
          OPENVIKING_AUTO_COMMIT_ON_COMPACT: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_CAPTURE_TIMEOUT_MS: "5000",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );
      await secondBatchSeen;
      running.child.kill("SIGKILL");
      const exit = await running.closed;
      assert.equal(exit.signal, "SIGKILL", exit.stderr);
    });

    assert.equal(batches[0].messages.length, 100);
    assert.equal(batches[1].messages.length, 1);
    const state = JSON.parse(await readFile(join(stateDir, "precompact-crash.json"), "utf-8"));
    assert.equal(state.capturedTurnCount, 100);
    assert.equal(state.revision, 1);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("pre-compact reports lock contention before consuming its whole hook deadline", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-pre-compact-lock-timeout-"));
  const previousStateDir = process.env.OPENVIKING_CODEX_STATE_DIR;
  process.env.OPENVIKING_CODEX_STATE_DIR = stateDir;
  let releaseHolder;
  let markHeld;
  const held = new Promise((resolve) => { markHeld = resolve; });
  const gate = new Promise((resolve) => { releaseHolder = resolve; });
  const holder = withStateTransaction("contended", async () => {
    markHeld();
    await gate;
  });

  try {
    await held;
    const running = startPreCompact(
      { session_id: "contended", transcript_path: join(stateDir, "missing.jsonl") },
      {
        OPENVIKING_AUTO_COMMIT_ON_COMPACT: "1",
        OPENVIKING_CODEX_STATE_DIR: stateDir,
        OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
        OPENVIKING_CREDENTIAL_SOURCE: "env",
        OPENVIKING_PRECOMPACT_STATE_LOCK_TIMEOUT_MS: "30",
      },
    );
    const exit = await running.closed;
    assert.equal(exit.code, 0, exit.stderr);
    const output = JSON.parse(exit.stdout.trim());
    assert.match(output.systemMessage, /another same-session writer is still active/);
  } finally {
    releaseHolder();
    await holder;
    if (previousStateDir === undefined) delete process.env.OPENVIKING_CODEX_STATE_DIR;
    else process.env.OPENVIKING_CODEX_STATE_DIR = previousStateDir;
    await rm(stateDir, { recursive: true, force: true });
  }
});
