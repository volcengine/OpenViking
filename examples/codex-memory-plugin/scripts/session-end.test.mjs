import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, stat, utimes, writeFile } from "node:fs/promises";
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

function runSessionEnd(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_") || key === "OV_HOOK_WORKER") delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "session-end.mjs")], {
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
        reject(new Error(`session-end exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function workerEnv(baseUrl, stateDir, extra = {}) {
  return {
    OV_HOOK_WORKER: "1",
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

async function writeTranscript(path, count) {
  const lines = [];
  for (let i = 0; i < count; i += 1) {
    lines.push(turn(i % 2 === 0 ? "user" : "assistant", `turn-${i}`));
  }
  await writeFile(path, lines.join("\n"));
}

async function writeState(stateDir, id, patch = {}) {
  const now = Date.now();
  await mkdir(stateDir, { recursive: true });
  await writeFile(join(stateDir, `${id}.json`), JSON.stringify({
    codexSessionId: id,
    ovSessionId: `cx-${id}`,
    capturedTurnCount: 0,
    createdAt: now - 1000,
    lastUpdatedAt: now,
    ...patch,
  }));
}

function readState(stateDir, id) {
  return readFile(join(stateDir, `${id}.json`), "utf-8").then(JSON.parse);
}

async function exists(path) {
  try { await stat(path); return true; } catch { return false; }
}

function mockHandler(calls, { commitStatus = 200 } = {}) {
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
      if (commitStatus !== 200) {
        res.writeHead(commitStatus, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "error", error: { code: "INTERNAL", message: "commit failed", trace_id: "trace-end-error" } }));
        return;
      }
      writeJson(res, { status: "ok", result: { archived: true, task_id: "task-end", trace_id: "trace-end" } });
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: "not found" }));
  };
}

function sentMessages(calls) {
  return calls
    .filter((c) => c.path.endsWith("/messages/batch") || c.path.endsWith("/messages"))
    .flatMap((c) => c.body?.messages ?? (c.body ? [c.body] : []));
}

test("session-end catches up the missing turns then commits", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s1", { capturedTurnCount: 2 });
    await writeTranscript(transcriptPath, 4);
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s1", transcript_path: transcriptPath, hook_event_name: "SessionEnd" },
        workerEnv(baseUrl, stateDir),
      );
    });

    const messages = sentMessages(calls);
    assert.equal(messages.length, 2);
    assert.equal(messages[0].parts?.[0]?.text ?? messages[0].content, "turn-2");
    assert.equal(messages[1].parts?.[0]?.text ?? messages[1].content, "turn-3");
    assert.ok(calls.some((c) => c.method === "POST" && c.path === "/api/v1/sessions/cx-s1/commit"));
    assert.deepEqual(calls.find((c) => c.path.endsWith("/commit")).body, {});

    const state = await readState(stateDir, "s1");
    assert.equal(state.ovSessionId, null);
    assert.equal(state.capturedTurnCount, 4);
    assert.equal(await endedMarkerExists(stateDir, "s1"), false);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a second session-end on an unchanged transcript neither sends nor commits", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-idem-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s2", { capturedTurnCount: 4, ovSessionId: null });
    await writeTranscript(transcriptPath, 4);
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s2", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir),
      );
    });

    assert.equal(sentMessages(calls).length, 0);
    assert.equal(calls.some((c) => c.path.endsWith("/commit")), false);
    const state = await readState(stateDir, "s2");
    assert.equal(state.capturedTurnCount, 4);
    assert.equal(state.ovSessionId, null);
    assert.equal(await endedMarkerExists(stateDir, "s2"), false);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a failed commit keeps the live session and the end marker", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-fail-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s3", { capturedTurnCount: 0 });
    await writeTranscript(transcriptPath, 2);
    await withMockOpenViking(mockHandler(calls, { commitStatus: 500 }), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s3", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir),
      );
    });

    const state = await readState(stateDir, "s3");
    assert.equal(state.ovSessionId, "cx-s3");
    assert.equal(state.capturedTurnCount, 2);
    assert.equal(await endedMarkerExists(stateDir, "s3"), true);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("an unreachable server leaves the cursor and the end marker alone", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-down-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  try {
    await writeState(stateDir, "s4", { capturedTurnCount: 2 });
    await writeTranscript(transcriptPath, 6);
    const closedPort = await withMockOpenViking(() => {}, async (baseUrl) => baseUrl);
    await runSessionEnd(
      { session_id: "s4", transcript_path: transcriptPath },
      workerEnv(closedPort, stateDir, { OPENVIKING_CAPTURE_TIMEOUT_MS: "1500" }),
    );

    const state = await readState(stateDir, "s4");
    assert.equal(state.capturedTurnCount, 2);
    assert.equal(state.ovSessionId, "cx-s4");
    assert.equal(await endedMarkerExists(stateDir, "s4"), true);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a missing transcript never resets the cursor", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-notranscript-"));
  const calls = [];
  try {
    await writeState(stateDir, "s5", { capturedTurnCount: 8, ovSessionId: null });
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s5", transcript_path: join(stateDir, "gone.jsonl") },
        workerEnv(baseUrl, stateDir),
      );
    });

    assert.equal(sentMessages(calls).length, 0);
    const state = await readState(stateDir, "s5");
    assert.equal(state.capturedTurnCount, 8);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("an unreadable transcript never commits the live session", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-unreadable-"));
  const calls = [];
  try {
    await writeState(stateDir, "s13", { capturedTurnCount: 3 });
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s13", transcript_path: join(stateDir, "gone.jsonl") },
        workerEnv(baseUrl, stateDir),
      );
    });

    assert.equal(sentMessages(calls).length, 0);
    assert.equal(calls.some((c) => c.path.endsWith("/commit")), false);
    const state = await readState(stateDir, "s13");
    assert.equal(state.ovSessionId, "cx-s13");
    assert.equal(state.capturedTurnCount, 3);
    assert.equal(await endedMarkerExists(stateDir, "s13"), true);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a shrunk transcript resumes at the last human turn", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-shrink-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s6", { capturedTurnCount: 8 });
    // 6 turns, last user turn at index 2.
    await writeFile(transcriptPath, [
      turn("user", "old-a"),
      turn("assistant", "old-b"),
      turn("user", "current request"),
      turn("assistant", "current reply"),
      turn("assistant", "more"),
      turn("assistant", "tail"),
    ].join("\n"));

    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s6", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir),
      );
    });

    const messages = sentMessages(calls);
    assert.equal(messages.length, 4);
    assert.equal(messages[0].parts?.[0]?.text ?? messages[0].content, "current request");
    const state = await readState(stateDir, "s6");
    assert.equal(state.capturedTurnCount, 6);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a fresh lock blocks the worker; a stale one does not", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-lock-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s7", { capturedTurnCount: 0 });
    await writeTranscript(transcriptPath, 2);
    const lockDir = join(stateDir, "s7.lock");
    await mkdir(lockDir, { recursive: true });

    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s7", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir, { OPENVIKING_CODEX_LOCK_WAIT_MS: "300" }),
      );

      assert.equal(calls.length, 0, "a held lock must stop every HTTP call");
      assert.equal((await readState(stateDir, "s7")).capturedTurnCount, 0);

      // Backdate the lock past the stale window so the next run takes it over.
      const old = new Date(Date.now() - 10 * 60_000);
      await utimes(lockDir, old, old);
      await runSessionEnd(
        { session_id: "s7", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir, { OPENVIKING_CODEX_LOCK_WAIT_MS: "300" }),
      );
    });

    assert.equal(sentMessages(calls).length, 2);
    const state = await readState(stateDir, "s7");
    assert.equal(state.ovSessionId, null);
    assert.equal(state.capturedTurnCount, 2);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a concurrent Stop worker and session-end worker never double-send a turn", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-race-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s8", { capturedTurnCount: 0 });
    await writeTranscript(transcriptPath, 6);

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname.endsWith("/messages/batch")) {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      return mockHandler(calls)(req, res);
    }, async (baseUrl) => {
      const env = workerEnv(baseUrl, stateDir, { OPENVIKING_WRITE_PATH_ASYNC: "0" });
      const stop = new Promise((resolve, reject) => {
        const cleanEnv = { ...process.env };
        for (const key of Object.keys(cleanEnv)) {
          if (key.startsWith("OPENVIKING_") || key === "OV_HOOK_WORKER") delete cleanEnv[key];
        }
        const child = spawn(process.execPath, [join(SCRIPT_DIR, "auto-capture.mjs")], {
          env: { ...cleanEnv, ...env },
          stdio: ["pipe", "ignore", "ignore"],
        });
        child.on("error", reject);
        child.on("close", resolve);
        child.stdin.end(JSON.stringify({ session_id: "s8", transcript_path: transcriptPath }));
      });
      const end = runSessionEnd({ session_id: "s8", transcript_path: transcriptPath }, env);
      await Promise.all([stop, end]);
    });

    assert.equal(sentMessages(calls).length, 6, "each transcript turn must be sent exactly once");
    const state = await readState(stateDir, "s8");
    assert.equal(state.capturedTurnCount, 6);
    assert.equal(state.ovSessionId, null);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("session-end is inert when auto-capture is disabled", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-off-"));
  const calls = [];
  try {
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      const { stdout } = await runSessionEnd(
        { session_id: "s9", transcript_path: null },
        workerEnv(baseUrl, stateDir, { OPENVIKING_AUTO_CAPTURE: "0" }),
      );
      assert.deepEqual(JSON.parse(stdout.trim()), {});
    });
    assert.equal(calls.length, 0);
    assert.equal(await endedMarkerExists(stateDir, "s9"), false);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("the parent hook returns immediately and leaves the end marker behind", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-parent-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s10", { capturedTurnCount: 0 });
    await writeTranscript(transcriptPath, 2);
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      const env = workerEnv(baseUrl, stateDir);
      delete env.OV_HOOK_WORKER;
      const started = Date.now();
      const { stdout } = await runSessionEnd(
        { session_id: "s10", transcript_path: transcriptPath },
        env,
      );
      const elapsed = Date.now() - started;
      assert.deepEqual(JSON.parse(stdout.trim()), {});
      assert.ok(elapsed < 1000, `parent hook took ${elapsed}ms; Codex budgets 1s`);
      assert.equal(await endedMarkerExists(stateDir, "s10"), true);
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("session-end without a session_id writes nothing", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-noid-"));
  const calls = [];
  try {
    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      const { stdout } = await runSessionEnd(
        { transcript_path: null, hook_event_name: "SessionEnd" },
        workerEnv(baseUrl, stateDir),
      );
      assert.deepEqual(JSON.parse(stdout.trim()), {});
    });
    assert.equal(calls.length, 0);
    assert.deepEqual((await readdir(stateDir)).filter((n) => n !== "recall-compressor-profile.json"), []);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a partial catch-up keeps the live session and the end marker instead of committing", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-partial-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s11", { capturedTurnCount: 0 });
    await writeTranscript(transcriptPath, 2);

    // Batch is unavailable, so the sender falls back to serial; the second
    // message then fails, leaving the tail turn unsent.
    let serial = 0;
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      calls.push({ method: req.method, path: url.pathname, body: null });
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "error", error: "no batch endpoint" }));
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages")) {
        calls[calls.length - 1].body = await readRequestBody(req);
        serial += 1;
        if (serial === 1) {
          writeJson(res, { status: "ok", result: { ok: true } });
          return;
        }
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "error", error: { message: "boom" } }));
        return;
      }
      writeJson(res, { status: "ok", result: {} });
    }, async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s11", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir),
      );
    });

    assert.equal(sentMessages(calls).length, 2, "both serial attempts are made");
    assert.equal(calls.some((c) => c.path.endsWith("/commit")), false, "an incomplete append must not commit");
    const state = await readState(stateDir, "s11");
    assert.equal(state.ovSessionId, "cx-s11");
    assert.equal(state.capturedTurnCount, 1, "the cursor advances only past what landed");
    assert.equal(await endedMarkerExists(stateDir, "s11"), true);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a worker whose end token no longer matches the marker does nothing", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-end-superseded-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  try {
    await writeState(stateDir, "s12", { capturedTurnCount: 0 });
    await writeTranscript(transcriptPath, 2);
    const markerAt = Date.now();
    await writeEndedMarker(stateDir, "s12", markerAt);

    await withMockOpenViking(mockHandler(calls), async (baseUrl) => {
      await runSessionEnd(
        { session_id: "s12", transcript_path: transcriptPath },
        workerEnv(baseUrl, stateDir, {
          OPENVIKING_SESSION_END_TOKEN: String(markerAt - 5_000),
        }),
      );
    });

    assert.equal(calls.length, 0, "a superseded worker makes no HTTP calls at all");
    const state = await readState(stateDir, "s12");
    assert.equal(state.ovSessionId, "cx-s12");
    assert.equal(state.capturedTurnCount, 0);
    assert.deepEqual(
      await endedMarkerStamps(stateDir, "s12"),
      [markerAt],
      "the newer marker survives",
    );
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
