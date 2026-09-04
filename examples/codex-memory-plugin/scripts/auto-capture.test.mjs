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

test("auto-capture commits when pending tokens cross threshold", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-state-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const debugLogPath = join(stateDir, "debug.log");
  const calls = [];

  try {
    await writeFile(
      transcriptPath,
      [
        JSON.stringify({
          payload: {
            message: {
              role: "user",
              content: "remember that I prefer compact commits",
            },
          },
        }),
        JSON.stringify({
          payload: {
            message: {
              role: "assistant",
              content: "noted for future sessions",
            },
          },
        }),
        JSON.stringify({
          payload: {
            type: "function_call",
            id: "call-1",
            name: "shell",
            arguments: "{\"cmd\":\"pwd\"}",
          },
        }),
        JSON.stringify({
          payload: {
            type: "function_call_output",
            call_id: "call-1",
            output: "project root",
          },
        }),
      ].join("\n"),
    );

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      calls.push({ method: req.method, path: url.pathname, body: null });
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        calls[calls.length - 1].body = await readRequestBody(req);
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cx-codex_commit") {
        writeJson(res, {
          status: "ok",
          result: { pending_tokens: 2500, commit_count: 2, total_message_count: 8 },
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/sessions/cx-codex_commit/commit") {
        calls[calls.length - 1].body = await readRequestBody(req);
        writeJson(res, {
          status: "ok",
          result: { archived: true, task_id: "task-1", trace_id: "trace-codex-commit" },
        });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoCapture(
        { session_id: "codex:commit", transcript_path: transcriptPath },
        {
          OPENVIKING_AUTO_CAPTURE: "1",
          OPENVIKING_CAPTURE_ASSISTANT_TURNS: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_DEBUG: "1",
          OPENVIKING_DEBUG_LOG: debugLogPath,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_COMMIT_TOKEN_THRESHOLD: "1000",
          OPENVIKING_COMMIT_KEEP_RECENT_COUNT: "7",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_WRITE_PATH_ASYNC: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      assert.ok(output && typeof output === "object");
      assert.match(output.systemMessage, /trace_id=trace-codex-commit/);
    });

    const commitCall = calls.find((call) => call.path.endsWith("/commit"));
    const debugLog = await readFile(debugLogPath, "utf-8").catch(() => "");
    assert.ok(commitCall, `expected threshold commit call; calls=${JSON.stringify(calls)} debug=${debugLog}`);
    assert.deepEqual(commitCall.body, { keep_recent_count: 7 });
    assert.match(debugLog, /"trace_id":"trace-codex-commit"/);

    const batchCall = calls.find((call) => call.path.endsWith("/messages/batch"));
    assert.ok(batchCall, `expected batch add-message call; calls=${JSON.stringify(calls)}`);
    const messageBodies = calls
      .filter((call) => call.path.endsWith("/messages") || call.path.endsWith("/messages/batch"))
      .flatMap((call) => call.body?.messages ?? [call.body]);
    const toolCallBody = messageBodies.find((body) =>
      body.parts?.some((part) => part.type === "tool" && part.tool_status === "running")
    );
    const toolResultBody = messageBodies.find((body) =>
      body.parts?.some((part) => part.type === "tool" && part.tool_status === "completed")
    );
    assert.deepEqual(toolCallBody.parts[0], {
      type: "tool",
      tool_id: "call-1",
      tool_name: "shell",
      tool_status: "running",
      tool_input: { cmd: "pwd" },
    });
    assert.deepEqual(toolResultBody.parts[0], {
      type: "tool",
      tool_id: "call-1",
      tool_name: "shell",
      tool_status: "completed",
      tool_output: "project root",
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-capture sends every new turn when one response exceeds the old limit", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-complete-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const calls = [];
  const transcript = [
    {
      payload: {
        message: {
          role: "user",
          content: "inspect every tool result",
        },
      },
    },
    {
      payload: {
        message: {
          role: "assistant",
          content: "I will inspect the complete trace.",
        },
      },
    },
  ];
  for (let index = 0; index < 10; index += 1) {
    transcript.push(
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call",
          id: `ctc-item-${index}`,
          call_id: `custom-call-${index}`,
          status: "completed",
          name: "exec",
          input: `command-${index}`,
        },
      },
      {
        type: "response_item",
        payload: {
          type: "custom_tool_call_output",
          id: `ctco-item-${index}`,
          call_id: `custom-call-${index}`,
          output: `result-${index}`,
        },
      },
    );
  }

  try {
    await writeFile(
      transcriptPath,
      transcript.map((entry) => JSON.stringify(entry)).join("\n"),
    );

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        calls.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cx-complete_trace") {
        writeJson(res, {
          status: "ok",
          result: { pending_tokens: 100, commit_count: 0, total_message_count: 22 },
        });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      await runAutoCapture(
        { session_id: "complete:trace", transcript_path: transcriptPath },
        {
          OPENVIKING_AUTO_CAPTURE: "1",
          OPENVIKING_CAPTURE_ASSISTANT_TURNS: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_WRITE_PATH_ASYNC: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );
    });

    assert.equal(
      calls.flatMap((body) => body.messages || []).length,
      22,
      "every normalized text/tool turn must be sent",
    );
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-capture logs a commit error trace_id", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-error-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const debugLogPath = join(stateDir, "debug.log");

  try {
    await writeFile(
      transcriptPath,
      JSON.stringify({
        payload: {
          message: {
            role: "user",
            content: "remember this failed commit trace",
          },
        },
      }),
    );

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        await readRequestBody(req);
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cx-codex_error") {
        writeJson(res, {
          status: "ok",
          result: { pending_tokens: 2500, commit_count: 0, total_message_count: 1 },
        });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/commit")) {
        await readRequestBody(req);
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          status: "error",
          error: {
            code: "INTERNAL",
            message: "commit failed",
            trace_id: "trace-codex-error",
          },
        }));
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      await runAutoCapture(
        { session_id: "codex:error", transcript_path: transcriptPath },
        {
          OPENVIKING_AUTO_CAPTURE: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_DEBUG: "1",
          OPENVIKING_DEBUG_LOG: debugLogPath,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_COMMIT_TOKEN_THRESHOLD: "1000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_WRITE_PATH_ASYNC: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );
    });

    const debugLog = await readFile(debugLogPath, "utf-8");
    assert.match(debugLog, /"trace_id":"trace-codex-error"/);
    assert.match(debugLog, /"error":"commit failed"/);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-capture skips compacted history after transcript shrink", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-compact-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const batches = [];
  const now = Date.now();

  try {
    await writeFile(join(stateDir, "compaction.json"), JSON.stringify({
      codexSessionId: "compaction",
      ovSessionId: "cx-compaction",
      capturedTurnCount: 8,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));
    await writeFile(
      transcriptPath,
      [
        { payload: { message: { role: "user", content: "compacted historical summary" } } },
        { payload: { message: { role: "assistant", content: "prior assistant tail" } } },
        { payload: { message: { role: "user", content: "current user request" } } },
        { payload: { type: "function_call", id: "call-1", name: "shell", arguments: "{}" } },
        { payload: { type: "function_call_output", call_id: "call-1", output: "tool result" } },
        { payload: { message: { role: "assistant", content: "current assistant response" } } },
      ].map((entry) => JSON.stringify(entry)).join("\n"),
    );

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        batches.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cx-compaction") {
        writeJson(res, {
          status: "ok",
          result: { pending_tokens: 0, commit_count: 0, total_message_count: 4 },
        });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      await runAutoCapture(
        { session_id: "compaction", transcript_path: transcriptPath },
        {
          OPENVIKING_AUTO_CAPTURE: "1",
          OPENVIKING_CAPTURE_ASSISTANT_TURNS: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_WRITE_PATH_ASYNC: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );
    });

    const messages = batches.flatMap((batch) => batch.messages || []);
    assert.equal(messages.length, 4);
    assert.equal(messages[0].parts[0].text, "current user request");
    assert.equal(
      messages.some((message) =>
        message.parts?.some((part) => part.text === "compacted historical summary")
      ),
      false,
    );
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-capture clears a stale session-end marker and never resets the cursor", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-ended-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const batches = [];
  const now = Date.now();

  try {
    await writeFile(join(stateDir, "resumed.json"), JSON.stringify({
      codexSessionId: "resumed",
      ovSessionId: "cx-resumed",
      capturedTurnCount: 1,
      createdAt: now - 1000,
      lastUpdatedAt: now,
    }));
    await writeEndedMarker(stateDir, "resumed", now);
    await writeFile(
      transcriptPath,
      [
        { payload: { message: { role: "user", content: "first request" } } },
        { payload: { message: { role: "user", content: "second request" } } },
      ].map((entry) => JSON.stringify(entry)).join("\n"),
    );

    const env = (baseUrl) => ({
      OPENVIKING_AUTO_CAPTURE: "1",
      OPENVIKING_CODEX_STATE_DIR: stateDir,
      OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
      OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
      OPENVIKING_CREDENTIAL_SOURCE: "env",
      OPENVIKING_MIN_QUERY_LENGTH: "1",
      OPENVIKING_WRITE_PATH_ASYNC: "0",
      OPENVIKING_TIMEOUT_MS: "5000",
      OPENVIKING_URL: baseUrl,
    });

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        batches.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cx-resumed") {
        writeJson(res, { status: "ok", result: { pending_tokens: 0, commit_count: 0, total_message_count: 2 } });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      await runAutoCapture({ session_id: "resumed", transcript_path: transcriptPath }, env(baseUrl));

      const marker = await endedMarkerExists(stateDir, "resumed");
      assert.equal(marker, false, "a Stop proves the thread is alive again");
      assert.equal(
        JSON.parse(await readFile(join(stateDir, "resumed.json"), "utf-8")).capturedTurnCount,
        2,
      );

      // An unreadable transcript must not look like a shrink.
      await runAutoCapture(
        { session_id: "resumed", transcript_path: join(stateDir, "gone.jsonl") },
        env(baseUrl),
      );
      assert.equal(
        JSON.parse(await readFile(join(stateDir, "resumed.json"), "utf-8")).capturedTurnCount,
        2,
      );
    });

    assert.equal(batches.flatMap((batch) => batch.messages || []).length, 1);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("a Stop clears only end markers older than the hook run", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-marker-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const startedAt = Date.now();

  try {
    await writeFile(transcriptPath, JSON.stringify({
      payload: { message: { role: "user", content: "hello" } },
    }));

    const env = (baseUrl, extra) => ({
      OPENVIKING_AUTO_CAPTURE: "1",
      OPENVIKING_CODEX_STATE_DIR: stateDir,
      OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
      OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
      OPENVIKING_CREDENTIAL_SOURCE: "env",
      OPENVIKING_MIN_QUERY_LENGTH: "1",
      OPENVIKING_WRITE_PATH_ASYNC: "0",
      OPENVIKING_TIMEOUT_MS: "5000",
      OPENVIKING_URL: baseUrl,
      OPENVIKING_HOOK_STARTED_AT: String(startedAt),
      ...extra,
    });

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        await readRequestBody(req);
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname.startsWith("/api/v1/sessions/")) {
        writeJson(res, { status: "ok", result: { pending_tokens: 0 } });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      // A marker written after this hook started belongs to a later exit.
      await writeEndedMarker(stateDir, "newer", startedAt + 10_000);
      await runAutoCapture({ session_id: "newer", transcript_path: transcriptPath }, env(baseUrl));
      assert.deepEqual(
        await endedMarkerStamps(stateDir, "newer"),
        [startedAt + 10_000],
        "a fresher marker survives a late Stop worker",
      );

      await writeEndedMarker(stateDir, "older", startedAt - 10_000);
      await runAutoCapture({ session_id: "older", transcript_path: transcriptPath }, env(baseUrl));
      assert.equal(
        await endedMarkerExists(stateDir, "older"),
        false,
        "an older marker is cleared: the thread is alive again",
      );
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("the workspace that decides capture is the payload's, not the hook process's", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-capture-workspace-"));
  const transcriptPath = join(stateDir, "transcript.jsonl");
  const workspaceDir = join(stateDir, "workspace");
  const plainDir = join(stateDir, "plain");
  const calls = [];

  try {
    // The `.git` is what makes the directory a workspace root; the hook itself
    // runs from this test's directory, which has no such file.
    await mkdir(join(workspaceDir, ".openviking"), { recursive: true });
    await mkdir(join(workspaceDir, ".git"), { recursive: true });
    await mkdir(join(plainDir, ".git"), { recursive: true });
    await writeFile(
      join(workspaceDir, ".openviking", "config.json"),
      JSON.stringify({ version: 1, capture: { enabled: false } }),
    );
    await writeFile(transcriptPath, JSON.stringify({
      payload: { message: { role: "user", content: "remember this turn" } },
    }));

    const env = (baseUrl) => ({
      OPENVIKING_CODEX_STATE_DIR: stateDir,
      OPENVIKING_HOME: join(stateDir, "home"),
      OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
      OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
      OPENVIKING_CREDENTIAL_SOURCE: "env",
      OPENVIKING_WRITE_PATH_ASYNC: "0",
      OPENVIKING_TIMEOUT_MS: "5000",
      OPENVIKING_URL: baseUrl,
    });

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      calls.push(url.pathname);
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        await readRequestBody(req);
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "GET" && url.pathname.startsWith("/api/v1/sessions/")) {
        writeJson(res, { status: "ok", result: { pending_tokens: 0 } });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const off = await runAutoCapture(
        { session_id: "ws-off", transcript_path: transcriptPath, cwd: workspaceDir },
        env(baseUrl),
      );
      assert.deepEqual(JSON.parse(off.stdout.trim()), {});
      assert.deepEqual(calls, [], "the workspace file turned capture off for this directory");

      await runAutoCapture(
        { session_id: "ws-on", transcript_path: transcriptPath, cwd: plainDir },
        env(baseUrl),
      );
      assert.ok(
        calls.some((path) => path.endsWith("/messages/batch")),
        `expected the same env to capture outside that workspace; calls=${JSON.stringify(calls)}`,
      );
    });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
