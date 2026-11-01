/**
 * Adapter tests for the CodeBuddy host: the guard envelope, the prompt gate,
 * the injection envelopes, the commit policy, and the capture path driven
 * through the real hook entry against a mock OpenViking.
 *
 * The cases that matter are the ones the host contract (hosts/codebuddy/
 * DESIGN.md) established: `is_internal_continuation` turns must not search,
 * `PreToolUse` has no advisory channel so the guard is deny-only, and the
 * write path may detach because the host does not kill a hook's descendants.
 */

import assert from "node:assert/strict";
import { readdirSync, rmSync, writeFileSync } from "node:fs";
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
import { codebuddy, shouldCommitCodebuddyCapture } from "../hosts/codebuddy.mjs";

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
// URI guard — deny-only, through the shared evaluator
// ---------------------------------------------------------------------------

test("a file tool pointed at a viking:// URI is denied with a usable reason", () => {
  const out = codebuddy.guard(event("Read", { file_path: "viking://user/caozhiyong/memories/x.md" }));
  assert.equal(out.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  const reason = out.hookSpecificOutput.permissionDecisionReason;
  assert.match(reason, /MCP read/);
  assert.match(reason, /read\(uris=/);
});

test("a write to a skill URI is redirected to add_skill", () => {
  const out = codebuddy.guard(event("Write", {
    file_path: "viking://user/caozhiyong/skills/foo/SKILL.md",
    content: "x",
  }));
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /add_skill/);
});

test("a plain local path is left alone", () => {
  assert.deepEqual(codebuddy.guard(event("Read", { file_path: "/tmp/x.md" })), {});
});

test("a local write whose body merely mentions viking:// is left alone", () => {
  // `content` is text, not a location: this is the false positive the shared
  // guard's content-key set exists to avoid.
  assert.deepEqual(
    codebuddy.guard(event("Write", { file_path: "/tmp/x.md", content: "见 viking://user/default/foo" })),
    {},
  );
});

test("a grep for the literal text viking:// is left alone", () => {
  assert.deepEqual(codebuddy.guard(event("Grep", { pattern: "viking://", path: "/tmp" })), {});
});

test("a shell command carrying viking:// is never denied", () => {
  // The shared evaluator answers shell tools with a notice, and CodeBuddy's
  // PreToolUse has no channel for one — what must hold is that the command
  // still runs, which a notice envelope does (no permissionDecision).
  const out = codebuddy.guard(event("Bash", { command: "cat viking://user/x" }));
  assert.ok(!out.hookSpecificOutput || !out.hookSpecificOutput.permissionDecision);
});

test("an empty or malformed payload produces no opinion and exits cleanly", async () => {
  assert.deepEqual(codebuddy.guard({}), {});
  const r = await runHookScript(URI_GUARD, { argv: ["codebuddy"], input: {} });
  expectExit(r, 0);
  assert.equal(r.stdout.trim(), "");
});

// ---------------------------------------------------------------------------
// Prompt gate and envelopes
// ---------------------------------------------------------------------------

test("a host-generated continuation is not a searchable prompt", () => {
  assert.equal(codebuddy.prompt({ prompt: "继续", is_internal_continuation: true }), "");
  assert.equal(codebuddy.prompt({ prompt: "继续" }), "继续");
});

test("the prompt is read from a string or an array of content parts", () => {
  assert.equal(codebuddy.prompt({ prompt: "  trimmed  " }), "trimmed");
  assert.equal(codebuddy.prompt({ prompt: [{ text: "a" }, { text: "b" }] }), "a\nb");
  assert.equal(codebuddy.prompt({}), "");
});

test("both injection events answer the Claude Code envelope, write-path events stay silent", () => {
  const start = codebuddy.envelope("session-start", "BLOCK");
  assert.deepEqual(start, {
    decision: "approve",
    hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: "BLOCK" },
  });
  const prompt = codebuddy.envelope("user-prompt-submit", "BLOCK");
  assert.deepEqual(prompt, {
    decision: "approve",
    hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "BLOCK" },
  });
  for (const event of ["stop", "pre-compact", "session-end"]) {
    assert.equal(codebuddy.envelope(event, "BLOCK"), null);
    assert.equal(codebuddy.envelope(event, ""), null);
  }
  // An empty envelope is verified safe on this host, so a stage with nothing
  // to say answers nothing at all.
  assert.equal(codebuddy.envelope("session-start", ""), null);
});

// ---------------------------------------------------------------------------
// Commit policy
// ---------------------------------------------------------------------------

test("the commit policy commits at session end and on compact, else by turn count", () => {
  assert.equal(shouldCommitCodebuddyCapture("session-end", {}, 0, 1), true);
  assert.equal(shouldCommitCodebuddyCapture("session-end", {}, 0, 0), false);
  // Turns a Stop already sent are still uncommitted at session end.
  assert.equal(shouldCommitCodebuddyCapture("session-end", {}, 3, 0), true);
  assert.equal(shouldCommitCodebuddyCapture("pre-compact", {}, 3, 0), true);
  assert.equal(shouldCommitCodebuddyCapture("stop", { commitTurnThreshold: 8 }, 3, 0), false);
  assert.equal(shouldCommitCodebuddyCapture("pre-compact", {}, 0, 1), true);
  assert.equal(shouldCommitCodebuddyCapture("pre-compact", { autoCommitOnCompact: false }, 0, 1), false);
  assert.equal(shouldCommitCodebuddyCapture("stop", { commitTurnThreshold: 2 }, 1, 1), true);
  assert.equal(shouldCommitCodebuddyCapture("stop", { commitTurnThreshold: 8 }, 1, 1), false);
});

test("plugin.codebuddy.* overrides apply and plugin.workbuddy.* does not", async () => {
  // `workbuddy` is the server-side ingest adapter's name for this same
  // transcript format; borrowing it as a harness key would silently drop the
  // user's overrides. Only `codebuddy` resolves (DESIGN.md §10).
  const files = await writeCredentialFiles("ov-cb-harness-key-", {
    ovcli: { plugin: { workbuddy: { recallLimit: 3 }, codebuddy: { recallLimit: 7 } } },
    ov: {},
  });
  try {
    const cfg = loadAgentHookConfig("codebuddy", files.dir, { env: files.env });
    assert.equal(cfg.recallLimit, 7);
  } finally {
    await rmSync(files.dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Capture, driven through the hook entry
// ---------------------------------------------------------------------------

const transcriptFixture = () => [
  // A real ask wrapped in host markup.
  JSON.stringify({ type: "message", role: "user", content: [{ type: "input_text", text: "<system-reminder>n</system-reminder>\n<user_query>请记住：我偏好用 tabs 缩进</user_query>" }] }),
  JSON.stringify({ type: "message", role: "assistant", content: [{ type: "output_text", text: "好的，已记录。" }] }),
  JSON.stringify({ type: "function_call", name: "Bash", arguments: '{"command":"ls"}', callId: "c1" }),
  JSON.stringify({ type: "function_call_result", name: "Bash", callId: "c1", status: "success", output: "a.txt" }),
  // Host-authored turn: no human text, must not be captured.
  JSON.stringify({ type: "message", role: "user", content: [{ type: "input_text", text: "<task-notification>x</task-notification>" }], providerData: { isMeta: true } }),
  JSON.stringify({ type: "message", role: "user", content: [{ type: "input_text", text: "那 A 方案里的并发问题呢" }] }),
].join("\n") + "\n";

/** A throwaway transcript plus the env that keeps every state file inside it. */
async function sandbox() {
  const files = await writeCredentialFiles("ov-cb-capture-", {
    ovcli: { url: "http://127.0.0.1:1", api_key: "k" },
    ov: {},
  });
  const transcript = join(files.dir, "session.jsonl");
  writeFileSync(transcript, transcriptFixture());
  return {
    files,
    transcript,
    env: {
      OPENVIKING_MEMORY_ENABLED: "1",
      // Keep the write inline so the test observes it; the host default is to
      // detach, which would move the work into a process the test cannot await.
      OPENVIKING_WRITE_PATH_ASYNC: "0",
      OPENVIKING_HOOK_STATE_DIR: join(files.dir, "hook-state"),
      OPENVIKING_PENDING_DIR: join(files.dir, "pending"),
      OPENVIKING_STATE_DIR: join(files.dir, "state"),
    },
  };
}

const stopPayload = (transcript, dir, sessionId = "sess-1") => ({
  hook_event_name: "Stop",
  session_id: sessionId,
  transcript_path: transcript,
  cwd: dir,
});

/** A mock that records batch sends and answers the commit route. */
function healthyMock(seen) {
  return async (req, res) => {
    const { pathname } = new URL(req.url, "http://127.0.0.1");
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

test("a healthy server receives one batch with the human turns and the tool-carrying assistant turn", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  const seen = { batches: [], commit: 0 };
  try {
    await withMockOpenViking(healthyMock(seen), async (url) => {
      const files = await writeCredentialFiles("ov-cb-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const r = await runHookScript(HOOK, {
          argv: ["stop", "codebuddy"],
          input: stopPayload(box.transcript, box.files.dir),
          cwd: box.files.dir,
          env: { ...files.env, ...box.env },
        });
        expectExit(r, 0);
      } finally {
        await rmSync(files.dir, { recursive: true, force: true });
      }
    });

    assert.equal(seen.batches.length, 1);
    const messages = seen.batches[0].messages;
    assert.equal(messages.filter((m) => m.role === "user").length, 2);
    assert.equal(messages.filter((m) => m.role === "assistant").length, 1);

    const all = JSON.stringify(messages);
    assert.ok(!all.includes("task-notification"), "the host-authored turn must not be captured");
    assert.ok(!all.includes("system-reminder"), "host markup must not leak into captured text");
    assert.ok(!all.includes("<user_query>"), "the wrapper must be stripped, not captured verbatim");
    assert.ok(all.includes("那 A 方案里的并发问题呢"), "the second human ask must be captured");
    assert.ok(all.includes("我偏好用 tabs"), "the first human ask must be captured");
    // The tool call survives as inline text; the result bytes do not.
    assert.ok(all.includes("[tool: Bash]"), "the tool call is inlined into the turn text");
    assert.ok(!all.includes("a.txt"), "tool result bytes stay out of the captured text");
    // Every message names the turn it came from, which is what the
    // dispatcher's acknowledgement set dedups on.
    for (const m of messages) assert.ok(m.turn_id, "each message carries its turn id");
  } finally {
    await rmSync(box.files.dir, { recursive: true, force: true });
    restoreEnv();
  }
});

test("a second Stop on the same session sends nothing — the cursor is idempotent", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  const seen = { batches: [], commit: 0 };
  try {
    await withMockOpenViking(healthyMock(seen), async (url) => {
      const files = await writeCredentialFiles("ov-cb-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const env = { ...files.env, ...box.env };
        const input = stopPayload(box.transcript, box.files.dir);
        expectExit(await runHookScript(HOOK, { argv: ["stop", "codebuddy"], input, cwd: box.files.dir, env }), 0);
        expectExit(await runHookScript(HOOK, { argv: ["stop", "codebuddy"], input, cwd: box.files.dir, env }), 0);
      } finally {
        await rmSync(files.dir, { recursive: true, force: true });
      }
    });
    assert.equal(seen.batches.length, 1, "re-delivery must not re-send the same turns");
  } finally {
    await rmSync(box.files.dir, { recursive: true, force: true });
    restoreEnv();
  }
});

test("SessionEnd commits turns a Stop already sent below the commit threshold", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  const seen = { batches: [], commit: 0 };
  try {
    await withMockOpenViking(healthyMock(seen), async (url) => {
      const files = await writeCredentialFiles("ov-cb-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const env = { ...files.env, ...box.env };
        const input = stopPayload(box.transcript, box.files.dir);
        const run = async (event) =>
          expectExit(await runHookScript(HOOK, { argv: [event, "codebuddy"], input, cwd: box.files.dir, env }), 0);
        await run("stop");
        assert.equal(seen.batches.length, 1);
        assert.equal(seen.commit, 0, "Stop stays below the commit threshold");
        await run("session-end");
        assert.equal(seen.batches.length, 1, "SessionEnd has nothing new to send");
        assert.equal(seen.commit, 1, "SessionEnd commits what Stop already sent");
        await run("session-end");
        assert.equal(seen.commit, 1, "nothing is left to commit");
      } finally {
        await rmSync(files.dir, { recursive: true, force: true });
      }
    });
  } finally {
    await rmSync(box.files.dir, { recursive: true, force: true });
    restoreEnv();
  }
});

test("a downed server queues the turns durably instead of losing them", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  try {
    // Nothing listens on port 9.
    const r = await runHookScript(HOOK, {
      argv: ["stop", "codebuddy"],
      input: stopPayload(box.transcript, box.files.dir),
      cwd: box.files.dir,
      env: { ...box.files.env, OPENVIKING_URL: "http://127.0.0.1:9", ...box.env },
    });
    expectExit(r, 0);
    const queued = readdirSync(box.env.OPENVIKING_PENDING_DIR).filter((f) => f.endsWith(".json"));
    assert.ok(queued.length >= 1, "the offline queue must hold the turns");
  } finally {
    await rmSync(box.files.dir, { recursive: true, force: true });
    restoreEnv();
  }
});

test("a disabled plugin exits cleanly and touches the network not at all", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const r = await runHookScript(HOOK, {
        argv: ["stop", "codebuddy"],
        input: stopPayload(box.transcript, box.files.dir),
        cwd: box.files.dir,
        env: { ...box.files.env, OPENVIKING_URL: url, OPENVIKING_MEMORY_ENABLED: "0" },
      });
      expectExit(r, 0);
    });
    assert.equal(hits, 0);
  } finally {
    await rmSync(box.files.dir, { recursive: true, force: true });
    restoreEnv();
  }
});
