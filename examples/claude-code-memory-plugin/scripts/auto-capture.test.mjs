import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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

function runAutoCapture(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "auto-capture.mjs")], {
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
        reject(new Error(`auto-capture exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function hookEnv(root, baseUrl) {
  return {
    HOME: root,
    TMPDIR: root,
    OPENVIKING_AUTO_CAPTURE: "1",
    OPENVIKING_CAPTURE_ASSISTANT_TURNS: "1",
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_WRITE_PATH_ASYNC: "0",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
  };
}

async function writeTranscript(path) {
  await writeFile(
    path,
    [
      JSON.stringify({ role: "user", content: "remember the capture regression" }),
      JSON.stringify({ role: "assistant", content: "I will retain that context" }),
    ].join("\n"),
  );
}

test("failed non-retryable capture keeps the cursor for a later retry", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-retry-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const sessionId = "capture-retry";
  let rejectWrites = true;
  const batches = [];

  try {
    await writeTranscript(transcriptPath);
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, 200, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        if (rejectWrites) {
          writeJson(res, 404, {
            status: "error",
            error: { code: "NOT_FOUND", message: "Resource not found" },
          });
          return;
        }
        batches.push(body);
        writeJson(res, 200, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages")) {
        await readRequestBody(req);
        writeJson(res, 404, {
          status: "error",
          error: { code: "NOT_FOUND", message: "Resource not found" },
        });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cc-capture-retry") {
        writeJson(res, 200, {
          status: "ok",
          result: { message_count: 2, pending_tokens: 10, commit_count: 0 },
        });
        return;
      }
      writeJson(res, 404, { status: "error", error: { code: "NOT_FOUND" } });
    }, async (baseUrl) => {
      const input = { session_id: sessionId, transcript_path: transcriptPath, cwd: root };
      await runAutoCapture(input, hookEnv(root, baseUrl));
      rejectWrites = false;
      await runAutoCapture(input, hookEnv(root, baseUrl));
    });

    assert.equal(batches.length, 1);
    assert.equal(batches[0].messages.length, 2);
    const state = JSON.parse(
      await readFile(join(root, "openviking-cc-capture-state", `${sessionId}.json`), "utf-8"),
    );
    assert.equal(state.capturedTurnCount, 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("legacy advanced cursor rewinds when the server session is empty", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-rewind-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const sessionId = "legacy-empty-session";
  const stateDir = join(root, "openviking-cc-capture-state");
  let captured = false;
  const batches = [];

  try {
    await writeTranscript(transcriptPath);
    await mkdir(stateDir, { recursive: true });
    await writeFile(
      join(stateDir, `${sessionId}.json`),
      JSON.stringify({ capturedTurnCount: 2 }),
    );
    await mkdir(join(root, ".openviking", "state"), { recursive: true });
    await writeFile(
      join(root, ".openviking", "state", "last-capture.json"),
      JSON.stringify({
        turns_captured: 0,
        turns_queued: 0,
        turns_failed: 2,
        ov_session_id: "cc-legacy-empty-session",
        cc_session_id: sessionId,
      }),
    );

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, 200, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cc-legacy-empty-session") {
        writeJson(res, 200, {
          status: "ok",
          result: {
            message_count: captured ? 2 : 0,
            total_message_count: null,
            commit_count: 0,
            pending_tokens: captured ? 10 : 0,
          },
        });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        batches.push(body);
        captured = true;
        writeJson(res, 200, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      writeJson(res, 404, { status: "error", error: { code: "NOT_FOUND" } });
    }, async (baseUrl) => {
      await runAutoCapture(
        { session_id: sessionId, transcript_path: transcriptPath, cwd: root },
        hookEnv(root, baseUrl),
      );
    });

    assert.equal(batches.length, 1);
    assert.equal(batches[0].messages.length, 2);
    const state = JSON.parse(
      await readFile(join(stateDir, `${sessionId}.json`), "utf-8"),
    );
    assert.equal(state.capturedTurnCount, 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

async function captureToolResult(root, transcriptPath, sessionId, extraEnv = {}) {
  const batches = [];
  await withMockOpenViking(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (req.method === "GET" && url.pathname === "/health") {
      writeJson(res, 200, { status: "ok", result: { healthy: true } });
      return;
    }
    if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
      const body = await readRequestBody(req);
      batches.push(body);
      writeJson(res, 200, { status: "ok", result: { added: body.messages.length } });
      return;
    }
    if (req.method === "GET" && url.pathname === `/api/v1/sessions/cc-${sessionId}`) {
      writeJson(res, 200, {
        status: "ok",
        result: { message_count: 2, pending_tokens: 10, commit_count: 0 },
      });
      return;
    }
    writeJson(res, 404, { status: "error", error: { code: "NOT_FOUND" } });
  }, async (baseUrl) => {
    await runAutoCapture(
      { session_id: sessionId, transcript_path: transcriptPath, cwd: root },
      { ...hookEnv(root, baseUrl), ...extraEnv },
    );
  });
  return batches
    .flatMap((batch) => batch.messages)
    .flatMap((message) => message.parts || [])
    .filter((part) => part.type === "tool");
}

async function writeToolTranscript(path, output) {
  await writeFile(
    path,
    [
      JSON.stringify({ role: "user", content: "read the large fixture please" }),
      JSON.stringify({
        role: "assistant",
        content: [{ type: "tool_use", id: "toolu_1", name: "Read", input: { file_path: "/big.txt" } }],
      }),
      JSON.stringify({
        role: "user",
        content: [{
          type: "tool_result",
          tool_use_id: "toolu_1",
          content: [{ type: "text", text: output }],
        }],
      }),
    ].join("\n"),
  );
}

test("tool output is reported verbatim so the server can externalize it", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-toolout-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const output = "x".repeat(50_000);

  try {
    await writeToolTranscript(transcriptPath, output);
    const toolParts = await captureToolResult(root, transcriptPath, "capture-toolout");
    const result = toolParts.find((part) => part.tool_status === "completed");
    assert.equal(result.tool_name, "Read");
    assert.equal(result.tool_output, output);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("captureToolMaxChars still caps tool output when an operator lowers it", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-toolcap-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const output = "y".repeat(5_000);

  try {
    await writeToolTranscript(transcriptPath, output);
    const toolParts = await captureToolResult(root, transcriptPath, "capture-toolcap", {
      OPENVIKING_CAPTURE_TOOL_MAX_CHARS: "1000",
    });
    const result = toolParts.find((part) => part.tool_status === "completed");
    assert.ok(result.tool_output.startsWith("y".repeat(1000)));
    assert.match(result.tool_output, /\[truncated, 4000 more chars\]$/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
