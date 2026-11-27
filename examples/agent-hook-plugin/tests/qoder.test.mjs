import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  expectExit,
  readRequestBody,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeCredentialFiles,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";
import { loadAgentHookConfig } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { qoder } from "../hosts/qoder.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const HOOK = join(ROOT, "scripts", "hook.mjs");
const URI_GUARD = join(ROOT, "scripts", "uri-guard.mjs");

const event = (toolName, toolInput) => ({
  hook_event_name: "PreToolUse",
  session_id: "s",
  cwd: "/tmp",
  tool_name: toolName,
  tool_input: toolInput,
});

// ---------------------------------------------------------------------------
// URI guard, through the shared evaluator
// ---------------------------------------------------------------------------

test("a file tool pointed at a viking:// URI is denied with a usable reason", () => {
  const out = qoder.guard(event("Read", { file_path: "viking://user/caozhiyong/memories/x.md" }));
  assert.equal(out.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  const reason = out.hookSpecificOutput.permissionDecisionReason;
  assert.match(reason, /MCP read/);
  assert.match(reason, /mcp_call\(toolName="mcp__openviking__read", arguments=\{uri="viking:\/\/user\/caozhiyong\/memories\/x\.md"\}\)/);
});

test("a shell command carrying viking:// runs with a notice and no decision", () => {
  const out = qoder.guard(event("Bash", { command: "cat viking://user/x" }));
  assert.deepEqual(Object.keys(out.hookSpecificOutput), ["hookEventName", "additionalContext"]);
  assert.match(out.hookSpecificOutput.additionalContext, /mcp_call\(toolName="mcp__openviking__read"/);
  assert.deepEqual(qoder.guard(event("Bash", { command: "ls /tmp" })), {});
  const matcher = JSON.parse(readFileSync(join(ROOT, "hosts", "qoder", "hooks.json"), "utf8")).hooks.PreToolUse[0].matcher;
  assert.ok(matcher.split("|").includes("Bash"), matcher);
});

test("an empty or malformed payload produces no opinion and exits cleanly", async () => {
  assert.deepEqual(qoder.guard({}), {});
  const r = await runHookScript(URI_GUARD, { argv: ["qoder"], input: {} });
  expectExit(r, 0);
  assert.equal(r.stdout.trim(), "");
});

// ---------------------------------------------------------------------------
// Prompt gate and envelopes
// ---------------------------------------------------------------------------

test("the prompt is the payload's string, trimmed", () => {
  assert.equal(qoder.prompt({ prompt: "  trimmed  " }), "trimmed");
  assert.equal(qoder.prompt({ prompt: [{ text: "a" }] }), "");
  assert.equal(qoder.prompt({}), "");
});

test("both injection events answer hookSpecificOutput without a decision, write-path events stay silent", () => {
  const start = qoder.envelope("session-start", "BLOCK");
  assert.deepEqual(start, {
    hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: "BLOCK" },
  });
  const prompt = qoder.envelope("user-prompt-submit", "BLOCK");
  assert.deepEqual(prompt, {
    hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "BLOCK" },
  });
  for (const event of ["stop", "pre-compact", "session-end"]) {
    assert.equal(qoder.envelope(event, "BLOCK"), null);
    assert.equal(qoder.envelope(event, ""), null);
  }
  assert.equal(qoder.envelope("session-start", ""), null);
});

test("plugin.qoder.* overrides apply", async () => {
  const files = await writeCredentialFiles("ov-qd-harness-key-", {
    ovcli: { plugin: { qoder: { recallLimit: 7 } } },
    ov: {},
  });
  try {
    const cfg = loadAgentHookConfig("qoder", files.dir, { env: files.env });
    assert.equal(cfg.recallLimit, 7);
  } finally {
    await rmSync(files.dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Capture, driven through the hook entry
// ---------------------------------------------------------------------------

const qoderFixture = (name) => readFileSync(join(ROOT, "tests", "fixtures", "qoder", `${name}.jsonl`), "utf8");
const qoderTranscript = () => qoderFixture("transcript-tooluse");

const codebuddyTranscript = () => [
  // A real ask wrapped in host markup.
  JSON.stringify({ type: "message", role: "user", content: [{ type: "input_text", text: "<system-reminder>n</system-reminder>\n<user_query>请记住：我偏好用 tabs 缩进</user_query>" }] }),
  JSON.stringify({ type: "message", role: "assistant", content: [{ type: "output_text", text: "好的，已记录。" }] }),
  JSON.stringify({ type: "function_call", name: "Bash", arguments: '{"command":"ls"}', callId: "c1" }),
  JSON.stringify({ type: "function_call_result", name: "Bash", callId: "c1", status: "completed", output: { type: "text", text: "a.txt" } }),
  // Host-authored turn: no human text, must not be captured.
  JSON.stringify({ type: "message", role: "user", content: [{ type: "input_text", text: "<task-notification>x</task-notification>" }], providerData: { isMeta: true } }),
  JSON.stringify({ type: "message", role: "user", content: [{ type: "input_text", text: "那 A 方案里的并发问题呢" }] }),
].join("\n") + "\n";

async function withCapture(t, handler, runCapture, transcript = qoderTranscript()) {
  const restoreEnv = scrubOpenVikingEnv();
  t.after(restoreEnv);
  const files = await writeCredentialFiles("ov-incremental-capture-", {
    ovcli: { url: "http://127.0.0.1:9", api_key: "k" },
    ov: {},
  });
  t.after(() => rmSync(files.dir, { recursive: true, force: true }));
  const transcriptPath = join(files.dir, "session.jsonl");
  writeFileSync(transcriptPath, transcript);
  const state = join(files.dir, "hook-state");
  const pending = join(files.dir, "pending");
  const input = { session_id: "sess-1", transcript_path: transcriptPath, cwd: files.dir };

  await withMockOpenViking(handler, (url) => runCapture({
    dir: files.dir,
    state,
    pending,
    input,
    run: (event, host = "qoder", options = {}) => runHookScript(HOOK, {
      argv: [event, host],
      cwd: files.dir,
      input: options.input ?? input,
      env: {
        ...files.env,
        OPENVIKING_URL: options.url ?? url,
        OPENVIKING_MEMORY_ENABLED: options.disabled ? "0" : "1",
        OPENVIKING_WRITE_PATH_ASYNC: "0",
        OPENVIKING_HOOK_STATE_DIR: state,
        OPENVIKING_PENDING_DIR: pending,
        OPENVIKING_STATE_DIR: join(files.dir, "state"),
      },
    }),
  }));
}

/** A mock that records batch sends, reports `seen.pendingTokens` and answers the commit route. */
function healthyMock(seen) {
  return async (req, res) => {
    const { pathname } = new URL(req.url, "http://127.0.0.1");
    if (req.method === "GET" && /^\/api\/v1\/sessions\/[^/]+$/.test(pathname)) {
      return writeJson(res, { status: "ok", result: { pending_tokens: seen.pendingTokens || 0 } });
    }
    if (pathname.endsWith("/messages/batch")) {
      seen.batches.push(await readRequestBody(req));
      return writeJson(res, { status: "ok", data: { added: 1 } });
    }
    if (pathname.endsWith("/commit")) {
      seen.commit++;
      return writeJson(res, { status: "ok", trace_id: "t" });
    }
    return writeJson(res, { status: "error", error: `unexpected ${pathname}` }, 404);
  };
}

test("Qoder and CodeBuddy each capture their transcript format and commit it once", async (t) => {
  for (const [host, transcript, roles, expected, excluded] of [
    [
      "qoder", qoderTranscript(), ["user", "assistant", "assistant"],
      ["Read the file a.txt", '"tool_name":"Read","tool_status":"completed"', "1\\thello"],
      ["PROBE_CTX_SessionStart: the secret word", "[tool:"],
    ],
    [
      "codebuddy", codebuddyTranscript(), ["user", "assistant", "user"],
      ["我偏好用 tabs", "那 A 方案里的并发问题呢", '"tool_name":"Bash","tool_status":"completed"', "a.txt"],
      ["system-reminder", "<user_query>", "task-notification", "[tool:"],
    ],
  ]) {
    await t.test(host, async (subtest) => {
      const seen = { batches: [], commit: 0 };
      await withCapture(subtest, healthyMock(seen), async ({ run }) => {
        expectExit(await run("stop", host), 0);
        assert.equal(seen.batches.length, 1);
        const { messages } = seen.batches[0];
        assert.deepEqual(messages.map((message) => message.role), roles);
        assert.ok(messages.every((message) => message.turn_id));
        const sent = JSON.stringify(messages);
        for (const text of expected) assert.ok(sent.includes(text), text);
        for (const text of excluded) assert.ok(!sent.includes(text), text);

        expectExit(await run("stop", host), 0);
        assert.equal(seen.batches.length, 1);
        assert.equal(seen.commit, 0);
        expectExit(await run("session-end", host), 0);
        assert.equal(seen.batches.length, 1);
        assert.equal(seen.commit, 1);
        expectExit(await run("session-end", host), 0);
        assert.equal(seen.commit, 1);
      }, transcript);
    });
  }
});

test("a Stop commits once pending tokens reach commitTokenThreshold, a session end commits the rest", async (t) => {
  const threshold = 100_000; // the default commitTokenThreshold
  const lines = qoderTranscript().split("\n");
  const seen = { batches: [], commit: 0, pendingTokens: 0 };
  await withCapture(t, healthyMock(seen), async ({ run, input }) => {
    // Each Stop finds one new turn: the ask, the tool-using response, the answer.
    const stops = [[12, threshold - 1, 0], [17, threshold, 1], [lines.length, 0, 1]];
    for (const [index, [end, pendingTokens, commits]] of stops.entries()) {
      writeFileSync(input.transcript_path, lines.slice(0, end).join("\n"));
      seen.pendingTokens = pendingTokens;
      expectExit(await run("stop"), 0);
      assert.deepEqual([seen.batches.length, seen.commit], [index + 1, commits]);
    }
    expectExit(await run("session-end"), 0);
    assert.deepEqual([seen.batches.length, seen.commit], [3, 2]);
  }, "");
});

test("Qoder SessionEnd waits for an in-flight Stop holding the session lock", async (t) => {
  const seen = { batches: [], commit: 0 };
  await withCapture(t, healthyMock(seen), async ({ run, state }) => {
    expectExit(await run("stop"), 0);
    const lock = join(state, "qoder", "sess-1.json.lock");
    mkdirSync(lock);
    const ending = run("session-end");
    try {
      await new Promise((resolve) => setTimeout(resolve, 6000));
      assert.equal(seen.commit, 0);
    } finally {
      rmSync(lock, { recursive: true, force: true });
      expectExit(await ending, 0);
    }
    assert.equal(seen.commit, 1);
  });
});

test("a disabled hook makes no requests and an outage leaves turns on disk for retry", async (t) => {
  const seen = { batches: [], commit: 0 };
  await withCapture(t, healthyMock(seen), async ({ run, pending }) => {
    const disabled = await run("stop", "qoder", { disabled: true });
    expectExit(disabled, 0);
    assert.deepEqual(seen, { batches: [], commit: 0 });
    assert.equal(existsSync(pending), false);

    const offline = await run("stop", "qoder", { url: "http://127.0.0.1:9" });
    expectExit(offline, 0);
    assert.ok(readdirSync(pending).some((file) => file.endsWith(".json")));
  });
});

test("the auxiliary Qoder SessionEnd for an unknown transcript is a silent no-op", async (t) => {
  const seen = { batches: [], commit: 0 };
  await withCapture(t, healthyMock(seen), async ({ run, dir, state }) => {
    const result = await run("session-end", "qoder", {
      input: {
        hook_event_name: "SessionEnd",
        session_id: "aux-1",
        transcript_path: join(dir, "aux-1.jsonl"),
        cwd: dir,
        reason: "other",
      },
    });
    expectExit(result, 0);
    assert.equal(result.stdout.trim(), "");
    assert.deepEqual(seen, { batches: [], commit: 0 });
    assert.equal(existsSync(join(state, "qoder", "aux-1.json")), false);
  });
});
