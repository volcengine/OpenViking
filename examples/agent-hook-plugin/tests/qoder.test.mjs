/**
 * Adapter tests for the Qoder CLI host: the guard envelope, the prompt reader,
 * the injection envelopes, the commit policy, and the capture path driven
 * through the real hook entry against a mock OpenViking (hosts/qoder/DESIGN.md).
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
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
import { qoder, shouldCommitQoderCapture } from "../hosts/qoder.mjs";

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

test("a write to a skill URI is redirected to add_skill", () => {
  const out = qoder.guard(event("Write", {
    file_path: "viking://user/caozhiyong/skills/foo/SKILL.md",
    content: "x",
  }));
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /add_skill/);
});

test("a plain local path is left alone", () => {
  assert.deepEqual(qoder.guard(event("Read", { file_path: "/tmp/x.md" })), {});
});

test("a local write whose body merely mentions viking:// is left alone", () => {
  // `content` is text, not a location: this is the false positive the shared
  // guard's content-key set exists to avoid.
  assert.deepEqual(
    qoder.guard(event("Write", { file_path: "/tmp/x.md", content: "见 viking://user/default/foo" })),
    {},
  );
});

test("a grep for the literal text viking:// is left alone", () => {
  assert.deepEqual(qoder.guard(event("Grep", { pattern: "viking://", path: "/tmp" })), {});
});

test("a shell command carrying viking:// is never denied", () => {
  const out = qoder.guard(event("Bash", { command: "cat viking://user/x" }));
  assert.ok(!out.hookSpecificOutput || !out.hookSpecificOutput.permissionDecision);
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

// ---------------------------------------------------------------------------
// Commit policy
// ---------------------------------------------------------------------------

test("the commit policy commits at session end and on compact, else by turn count", () => {
  assert.equal(shouldCommitQoderCapture("session-end", {}, 0, 1), true);
  assert.equal(shouldCommitQoderCapture("session-end", {}, 0, 0), false);
  // Turns a Stop already sent are still uncommitted at session end.
  assert.equal(shouldCommitQoderCapture("session-end", {}, 3, 0), true);
  assert.equal(shouldCommitQoderCapture("pre-compact", {}, 3, 0), true);
  assert.equal(shouldCommitQoderCapture("stop", { commitTurnThreshold: 8 }, 3, 0), false);
  assert.equal(shouldCommitQoderCapture("pre-compact", {}, 0, 1), true);
  assert.equal(shouldCommitQoderCapture("pre-compact", { autoCommitOnCompact: false }, 0, 1), false);
  assert.equal(shouldCommitQoderCapture("stop", { commitTurnThreshold: 2 }, 1, 1), true);
  assert.equal(shouldCommitQoderCapture("stop", { commitTurnThreshold: 8 }, 1, 1), false);
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

const FIXTURES = join(ROOT, "tests", "fixtures", "qoder");
const transcriptFixture = () => readFileSync(join(FIXTURES, "transcript-tooluse.jsonl"), "utf8");

/** A throwaway transcript plus the env that keeps every state file inside it. */
async function sandbox() {
  const files = await writeCredentialFiles("ov-qd-capture-", {
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

test("a healthy server receives one batch with the human turn and both assistant responses", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  const seen = { batches: [], commit: 0 };
  try {
    await withMockOpenViking(healthyMock(seen), async (url) => {
      const files = await writeCredentialFiles("ov-qd-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const r = await runHookScript(HOOK, {
          argv: ["stop", "qoder"],
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
    assert.equal(messages.filter((m) => m.role === "user").length, 1);
    assert.equal(messages.filter((m) => m.role === "assistant").length, 2);

    const all = JSON.stringify(messages);
    assert.ok(!all.includes("PROBE_CTX_SessionStart: the secret word"), "injected hook context must not be captured");
    assert.ok(all.includes("Read the file a.txt"), "the human ask must be captured");
    assert.ok(all.includes("[tool: Read]"), "the tool call is inlined into the turn text");
    assert.ok(!all.includes("1\\thello"), "tool result bytes stay out of the captured text");
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
      const files = await writeCredentialFiles("ov-qd-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const env = { ...files.env, ...box.env };
        const input = stopPayload(box.transcript, box.files.dir);
        expectExit(await runHookScript(HOOK, { argv: ["stop", "qoder"], input, cwd: box.files.dir, env }), 0);
        expectExit(await runHookScript(HOOK, { argv: ["stop", "qoder"], input, cwd: box.files.dir, env }), 0);
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
      const files = await writeCredentialFiles("ov-qd-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const env = { ...files.env, ...box.env };
        const input = stopPayload(box.transcript, box.files.dir);
        const run = async (event) =>
          expectExit(await runHookScript(HOOK, { argv: [event, "qoder"], input, cwd: box.files.dir, env }), 0);
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
      argv: ["stop", "qoder"],
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
        argv: ["stop", "qoder"],
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

test("the auxiliary SessionEnd for an unknown session with no transcript is a silent no-op", async () => {
  const restoreEnv = scrubOpenVikingEnv();
  const box = await sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const files = await writeCredentialFiles("ov-qd-capture-url-", {
        ovcli: { url, api_key: "k" },
        ov: {},
      });
      try {
        const r = await runHookScript(HOOK, {
          argv: ["session-end", "qoder"],
          input: {
            hook_event_name: "SessionEnd",
            session_id: "aux-1",
            transcript_path: join(box.files.dir, "aux-1.jsonl"),
            cwd: box.files.dir,
            reason: "other",
          },
          cwd: box.files.dir,
          env: { ...files.env, ...box.env },
        });
        expectExit(r, 0);
        assert.equal(r.stdout.trim(), "");
      } finally {
        await rmSync(files.dir, { recursive: true, force: true });
      }
    });
    assert.equal(hits, 0, "no session is created or committed for it");
    assert.equal(existsSync(join(box.env.OPENVIKING_HOOK_STATE_DIR, "qoder", "aux-1.json")), false);
  } finally {
    await rmSync(box.files.dir, { recursive: true, force: true });
    restoreEnv();
  }
});
