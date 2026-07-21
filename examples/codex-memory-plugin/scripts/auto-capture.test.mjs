import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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
        writeJson(res, { status: "ok", result: { archived: true, task_id: "task-1" } });
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
    });

    const commitCall = calls.find((call) => call.path.endsWith("/commit"));
    const debugLog = await readFile(debugLogPath, "utf-8").catch(() => "");
    assert.ok(commitCall, `expected threshold commit call; calls=${JSON.stringify(calls)} debug=${debugLog}`);
    assert.deepEqual(commitCall.body, { keep_recent_count: 7 });

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
