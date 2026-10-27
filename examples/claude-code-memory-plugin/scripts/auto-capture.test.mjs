import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { readRequestBody, withMockOpenViking, withPendingDir, writeJson } from "../../memory-plugin-shared/testing/support.mjs";
import { extractCaptureTurns, mapLegacyCaptureCursor } from "./cc-transcript.mjs";
import { enqueue, listPending } from "./lib/pending-queue.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function runHook(script, input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, script)], {
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
        reject(new Error(`${script} exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function runAutoCapture(input, env) {
  return runHook("auto-capture.mjs", input, env);
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
        writeJson(res, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        if (rejectWrites) {
          writeJson(res, {
            status: "error",
            error: { code: "NOT_FOUND", message: "Resource not found" },
          }, 404);
          return;
        }
        batches.push(body);
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages")) {
        await readRequestBody(req);
        writeJson(res, {
          status: "error",
          error: { code: "NOT_FOUND", message: "Resource not found" },
        }, 404);
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cc-capture-retry") {
        writeJson(res, {
          status: "ok",
          result: { message_count: 2, pending_tokens: 10, commit_count: 0 },
        });
        return;
      }
      writeJson(res, { status: "error", error: { code: "NOT_FOUND" } }, 404);
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

test("Stop replays queued writes before sending the new turns", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-replay-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const requests = [];

  try {
    await writeTranscript(transcriptPath);
    await withPendingDir(async (pendingDir) => {
      for (let i = 0; i < 20; i++) {
        await enqueue("addMessage", "cc-old-session", {
          role: "assistant",
          parts: [{ type: "text", text: `old ${i}` }],
        }, { createdAt: 1000 + i });
      }

      await withMockOpenViking(async (req, res) => {
        const url = new URL(req.url, "http://127.0.0.1");
        if (req.method === "GET" && url.pathname === "/health") {
          writeJson(res, { status: "ok", result: { healthy: true } });
          return;
        }
        if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
          const body = await readRequestBody(req);
          requests.push({ path: url.pathname, count: body.messages.length });
          writeJson(res, { status: "ok", result: { added: body.messages.length } });
          return;
        }
        if (req.method === "POST" && url.pathname.endsWith("/messages")) {
          const body = await readRequestBody(req);
          requests.push({ path: url.pathname, text: body.parts[0].text });
          writeJson(res, { status: "ok", result: {} });
          return;
        }
        writeJson(res, { status: "ok", result: {} });
      }, async (baseUrl) => {
        await runAutoCapture(
          { session_id: "capture-replay", transcript_path: transcriptPath, cwd: root },
          { ...hookEnv(root, baseUrl), OPENVIKING_PENDING_DIR: pendingDir },
        );
      });

      assert.deepEqual(await listPending(), []);
    });

    assert.deepEqual(requests, [
      ...Array.from({ length: 20 }, (_, i) => ({
        path: "/api/v1/sessions/cc-old-session/messages",
        text: `old ${i}`,
      })),
      { path: "/api/v1/sessions/cc-capture-replay/messages/batch", count: 2 },
    ]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("threshold commit archives every captured message", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-commit-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const commits = [];

  try {
    await writeTranscript(transcriptPath);
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cc-capture-commit") {
        writeJson(res, {
          status: "ok",
          result: { message_count: 2, pending_tokens: 5000, commit_count: 0 },
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/sessions/cc-capture-commit/commit") {
        commits.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { status: "accepted" } });
        return;
      }
      writeJson(res, { status: "error", error: { code: "NOT_FOUND" } }, 404);
    }, async (baseUrl) => {
      await runAutoCapture(
        { session_id: "capture-commit", transcript_path: transcriptPath, cwd: root },
        {
          ...hookEnv(root, baseUrl),
          OPENVIKING_COMMIT_TOKEN_THRESHOLD: "1000",
          OPENVIKING_COMMIT_KEEP_RECENT_COUNT: "10",
        },
      );
    });

    assert.deepEqual(commits, [{ keep_recent_count: 0 }]);
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
        writeJson(res, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/sessions/cc-legacy-empty-session") {
        writeJson(res, {
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
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      writeJson(res, { status: "error", error: { code: "NOT_FOUND" } }, 404);
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
      writeJson(res, { status: "ok", result: { healthy: true } });
      return;
    }
    if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
      const body = await readRequestBody(req);
      batches.push(body);
      writeJson(res, { status: "ok", result: { added: body.messages.length } });
      return;
    }
    if (req.method === "GET" && url.pathname === `/api/v1/sessions/cc-${sessionId}`) {
      writeJson(res, {
        status: "ok",
        result: { message_count: 2, pending_tokens: 10, commit_count: 0 },
      });
      return;
    }
    writeJson(res, { status: "error", error: { code: "NOT_FOUND" } }, 404);
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

test("the workspace that decides capture is the payload's, not the hook process's", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-workspace-"));
  const home = join(root, "home");
  const workspaceDir = join(root, "workspace");
  const plainDir = join(root, "plain");
  const transcriptPath = join(root, "transcript.jsonl");
  const paths = [];

  try {
    // The `.git` is what makes the directory a workspace root; the hook itself
    // runs from this test's directory, which has no such file.
    await mkdir(join(workspaceDir, ".openviking"), { recursive: true });
    await mkdir(join(workspaceDir, ".git"), { recursive: true });
    await mkdir(join(plainDir, ".git"), { recursive: true });
    await mkdir(home, { recursive: true });
    await writeFile(
      join(workspaceDir, ".openviking", "config.json"),
      JSON.stringify({ version: 1, capture: { enabled: false } }),
    );
    await writeTranscript(transcriptPath);

    const env = (baseUrl) => {
      const base = hookEnv(root, baseUrl);
      delete base.OPENVIKING_AUTO_CAPTURE;
      return { ...base, HOME: home, OPENVIKING_HOME: join(home, ".openviking") };
    };

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      paths.push(url.pathname);
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      if (req.method === "GET" && url.pathname.startsWith("/api/v1/sessions/")) {
        writeJson(res, {
          status: "ok",
          result: { message_count: 0, pending_tokens: 10, commit_count: 0 },
        });
        return;
      }
      writeJson(res, { status: "error", error: { code: "NOT_FOUND" } }, 404);
    }, async (baseUrl) => {
      await runAutoCapture(
        { session_id: "ws-off", transcript_path: transcriptPath, cwd: workspaceDir },
        env(baseUrl),
      );
      assert.deepEqual(paths, [], "the workspace file turned capture off for this directory");

      await runAutoCapture(
        { session_id: "ws-on", transcript_path: transcriptPath, cwd: plainDir },
        env(baseUrl),
      );
      assert.ok(
        paths.some((path) => path.endsWith("/messages/batch")),
        `expected the same env to capture outside that workspace; paths=${JSON.stringify(paths)}`,
      );
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("capture filters rewrite and drop turns at the send site", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-capture-filters-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const sessionId = "capture-filters";
  const batches = [];

  try {
    await writeFile(
      transcriptPath,
      [
        JSON.stringify({ role: "user", content: "the token is sk_LIVE_ABCDEF, remember it" }),
        JSON.stringify({ role: "user", content: "scratch: ignore this throwaway note" }),
        JSON.stringify({ role: "assistant", content: "noted, I will retain that context" }),
      ].join("\n"),
    );
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { healthy: true } });
        return;
      }
      if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        const body = await readRequestBody(req);
        batches.push(body);
        writeJson(res, { status: "ok", result: { added: body.messages.length } });
        return;
      }
      writeJson(res, { status: "ok", result: {} });
    }, async (baseUrl) => {
      await runAutoCapture({ session_id: sessionId, transcript_path: transcriptPath, cwd: root }, {
        ...hookEnv(root, baseUrl),
        OPENVIKING_CAPTURE_FILTERS: "s/sk_[A-Za-z0-9_]+/[redacted]/g,user:d/^scratch:/",
      });
    });

    assert.equal(batches.length, 1);
    const texts = batches[0].messages.flatMap(
      (message) => message.parts.filter((p) => p.type === "text").map((p) => p.text),
    );
    assert.deepEqual(texts, [
      "the token is [redacted], remember it",
      "noted, I will retain that context",
    ]);
    const state = JSON.parse(
      await readFile(join(root, "openviking-cc-capture-state", `${sessionId}.json`), "utf-8"),
    );
    // The cursor counts extracted turns, so the dropped one still advances it.
    assert.equal(state.capturedTurnCount, 3);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("subagent capture sanitizes injected text before filtering and sending", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-subagent-filters-"));
  const transcriptPath = join(root, "subagent.jsonl");
  const batches = [];
  try {
    await writeFile(transcriptPath, [
      JSON.stringify({ role: "user", content: "<system-reminder>approved</system-reminder>ignore this" }),
      JSON.stringify({ role: "user", content: "<system-reminder>secret</system-reminder>approved SECRET123" }),
    ].join("\n"));
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { healthy: true } });
      } else if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        batches.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { added: batches.at(-1).messages.length } });
      } else {
        writeJson(res, { status: "ok", result: {} });
      }
    }, async (baseUrl) => {
      await runHook("subagent-stop.mjs", {
        session_id: "parent-capture-filters",
        agent_id: "child-1",
        agent_transcript_path: transcriptPath,
        cwd: root,
      }, {
        ...hookEnv(root, baseUrl),
        OPENVIKING_CAPTURE_FILTERS: "user:k/approved/,user:d/secret/,user:s/SECRET123/[redacted]/g",
      });
    });
    assert.equal(batches.length, 1);
    assert.deepEqual(batches[0].messages.map((message) => message.parts), [
      [{ type: "text", text: "approved [redacted]" }],
    ]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("api-request-blob lines are not captured as conversation turns", () => {
  const question = { role: "user", content: [{ type: "text", text: "what does the cursor track?" }] };
  const answer = { role: "assistant", content: [{ type: "text", text: "the number of captured turns" }] };
  const blob = (message) => ({ type: "api-request-blob", sessionId: "s1", hash: "h", message });
  const turns = extractCaptureTurns([
    { type: "user", sessionId: "s1", message: question },
    blob({ role: "system", content: "You are Claude Code." }),
    blob(question),
    { type: "assistant", sessionId: "s1", message: answer },
    blob(answer),
  ]);

  assert.deepEqual(turns.map((turn) => [turn.role, turn.text]), [
    ["user", "what does the cursor track?"],
    ["assistant", "the number of captured turns"],
  ]);
});

test("a subagent stopping again pushes and commits only its new turns", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-subagent-cursor-"));
  const transcriptPath = join(root, "subagent.jsonl");
  const batches = [];
  let commits = 0;
  const lines = [
    JSON.stringify({ role: "user", content: "first task for the subagent" }),
    JSON.stringify({ type: "attachment", attachment: { type: "queued_command", commandMode: "prompt", source_uuid: "sub-queue", prompt: "queued subagent input" } }),
    JSON.stringify({ role: "assistant", content: "first task done" }),
  ];
  try {
    await writeFile(transcriptPath, lines.join("\n"));
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { healthy: true } });
      } else if (req.method === "POST" && url.pathname.endsWith("/messages/batch")) {
        batches.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { added: batches.at(-1).messages.length } });
      } else if (req.method === "POST" && url.pathname.endsWith("/commit")) {
        await readRequestBody(req);
        commits++;
        writeJson(res, { status: "ok", result: {} });
      } else {
        writeJson(res, { status: "ok", result: {} });
      }
    }, async (baseUrl) => {
      const env = hookEnv(root, baseUrl);
      const input = {
        session_id: "parent-cursor",
        agent_id: "child-cursor",
        agent_transcript_path: transcriptPath,
        cwd: root,
      };
      await runHook("subagent-start.mjs", input, env);
      await runHook("subagent-stop.mjs", input, env);
      // Woken again: the transcript grows and the start hook may run again.
      await writeFile(transcriptPath, [
        ...lines,
        JSON.stringify({ role: "user", content: "follow-up from a background task" }),
      ].join("\n"));
      await runHook("subagent-start.mjs", input, env);
      await runHook("subagent-stop.mjs", input, env);
      // Stopping with nothing new must not commit an empty archive.
      await runHook("subagent-stop.mjs", input, env);
    });
    const texts = (batch) => batch.messages.flatMap(
      (message) => message.parts.filter((p) => p.type === "text").map((p) => p.text),
    );
    assert.equal(batches.length, 2);
    assert.deepEqual(texts(batches[0]), ["first task for the subagent", "queued subagent input", "first task done"]);
    assert.deepEqual(texts(batches[1]), ["follow-up from a background task"]);
    assert.equal(commits, 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("queued inputs keep their own source order and identity without API snapshots", () => {
  const queued = (id, prompt, extra = {}) => ({ type: "attachment", attachment: {
    type: "queued_command", commandMode: "prompt", source_uuid: id, prompt, ...extra,
  } });
  const rows = [
    { type: "user", uuid: "first", message: { role: "user", content: "first" } },
    queued("q1", "same", { humanTurn: true, origin: { kind: "human" } }),
    queued("q1", "same"),
    { type: "api-request-blob", message: { role: "user", content: "snapshot" } },
    { type: "user", uuid: "q1", message: { role: "user", content: "same" } },
    queued("first", "first"),
    queued("q2", "same"),
    queued("agent", "not a human prompt", { humanTurn: false, origin: { kind: "agent" } }),
    queued("agent-only", "not human", { origin: { kind: "agent" } }),
    { type: "attachment", attachment: { type: "other", prompt: "not a command" } },
    queued("command", "not prompt mode", { commandMode: "shell" }),
  ];
  const turns = extractCaptureTurns(rows);
  assert.deepEqual(turns.map(turn => [turn.role, turn.text]), [
    ["user", "first"], ["user", "same"], ["user", "same"],
  ]);
  // A canonical row appended later must not move an already captured queued turn.
  assert.deepEqual(extractCaptureTurns(rows.slice(0, 3)), turns.slice(0, 2));
});

test("queued capture migrates an old boundary, filters input, and resumes without replay", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-queued-cursor-"));
  const transcriptPath = join(root, "transcript.jsonl");
  const sessionId = "queued-cursor";
  const batches = [];
  const queued = (id, text) => ({ type: "attachment", attachment: {
    type: "queued_command", commandMode: "prompt", source_uuid: id, prompt: text,
  } });
  const text = "correction token sk_SECRET";
  const rows = [
    { role: "user", content: "old question" }, queued("old", "old omitted input"),
    { role: "assistant", content: "old answer" },
    queued("new", text), { role: "assistant", content: "new answer" },
  ];
  try {
    await mkdir(join(root, "openviking-cc-capture-state"));
    const statePath = join(root, "openviking-cc-capture-state", `${sessionId}.json`);
    await writeFile(statePath, JSON.stringify({ capturedTurnCount: 2 }));
    await writeFile(transcriptPath, rows.map(row => JSON.stringify(row)).join("\n"));
    await withMockOpenViking(async (req, res) => {
      if (req.method === "POST" && req.url.endsWith("/messages/batch")) {
        batches.push(await readRequestBody(req));
        writeJson(res, { status: "ok", result: { added: batches.at(-1).messages.length } });
      } else {
        writeJson(res, { status: "ok", result: { healthy: true, message_count: 10 } });
      }
    }, async baseUrl => {
      const env = { ...hookEnv(root, baseUrl), OPENVIKING_CAPTURE_FILTERS: "s/sk_[A-Z]+/[redacted]/g" };
      const input = { session_id: sessionId, transcript_path: transcriptPath, cwd: root };
      await runAutoCapture(input, env);
      await runAutoCapture(input, env);
      rows.push({ type: "user", uuid: "new", message: { role: "user", content: text } });
      rows.push({ type: "user", uuid: "different-event", message: { role: "user", content: text } });
      await writeFile(transcriptPath, rows.map(row => JSON.stringify(row)).join("\n"));
      await runAutoCapture(input, env);
      await runAutoCapture(input, env);
    });
    const texts = batch => batch.messages.map(message => message.parts.map(part => part.text).join(""));
    assert.deepEqual(batches.map(texts), [
      ["correction token [redacted]", "new answer"], ["correction token [redacted]"],
    ]);
    const state = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(state.capturedTurnCount, 6);
    assert.equal(state.captureCursorVersion, 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("legacy cursor mapping preserves every ordinary-turn boundary and rejects truncation", () => {
  const rows = [
    { role: "user", content: "first" },
    { type: "attachment", attachment: { type: "queued_command", commandMode: "prompt", prompt: "queued" } },
    { role: "assistant", content: "answer" },
    { role: "user", content: "next" },
  ];
  for (const [before, after] of [[0, 0], [1, 1], [2, 3], [3, 4]]) {
    assert.equal(mapLegacyCaptureCursor(rows, {}, before), after);
  }
  for (const invalid of [-1, 1.5, 4]) {
    assert.throws(() => mapLegacyCaptureCursor(rows, {}, invalid));
  }
});
