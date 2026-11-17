/**
 * Hook contract: the six entries as the host actually runs them.
 *
 * Each test spawns the real script with a real payload on stdin and asserts the
 * bytes the host would read: exit code, stdout (empty or exactly one answer),
 * and that nothing threw. This is where "the hook must not block the user" is
 * either true or not.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { hookPayload, runHook, withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import { metadata, simpleTurn, wireText } from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_hooks";


/**
 * A server whose context face renders a block.
 *
 * The shared recall core asks `/api/v1/search/search` for a server-assembled
 * context and falls back to older endpoints; answering the modern face with a
 * rendered block is what a real 1.5 server does.
 */
function recallServer() {
  return (request) => {
    if (request.path === "/health") return { body: { status: "ok" } };
    if (request.path === "/api/v1/search/search") {
      return {
        body: {
          status: "ok",
          result: {
            rendered: "Relevant memory: the reader consumes complete JSONL lines.",
            entries: [{
              uri: "viking://user/default/memories/reader.md",
              category: "memory",
              text: "The reader consumes complete JSONL lines.",
              score: 0.9,
            }],
            stats: { used_tokens: 12, tier_counts: { memory: 1 }, rewrite: "off" },
          },
        },
      };
    }
    return undefined;
  };
}

function wireBody(records = simpleTurn(0)) {
  return wireText([metadata(), ...records]);
}

/** The env every hook needs to reach the fake server and the temp home. */
function hookEnv(home, server) {
  return {
    ...fakeOvEnv(server),
    KIMI_CODE_HOME: home,
    OPENVIKING_HOOK_STATE_DIR: join(home, "hook-state"),
    OPENVIKING_PENDING_DIR: join(home, "pending"),
  };
}

test("SessionStart prints nothing and replays the queue", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      // A real queued message, so the replay has something to do. Without it
      // this test passed even when the whole replay path was broken.
      const { enqueue } = await import("../../scripts/shared/pending-queue.mjs");
      const { ovSessionIdFor } = await import("../../scripts/capture-session.mjs");
      await enqueue("addMessage", ovSessionIdFor(SESSION, "main"), {
        role: "user",
        parts: [{ type: "text", text: "a message from an offline session" }],
        message_kind: "user_query",
        turn_id: "0",
      });

      const result = await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env: hookEnv(home, server) });

      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "", "SessionStart output is discarded by the host");
      const posts = server.requests.filter((request) => request.method === "POST");
      assert.ok(posts.length >= 1, "the queued message should have been replayed");
      const delivered = server.messages(ovSessionIdFor(SESSION, "main"))
        .map((message) => message.parts[0].text);
      assert.ok(
        delivered.includes("a message from an offline session"),
        `the replayed message must arrive first, got ${JSON.stringify(delivered)}`,
      );
      assert.equal(delivered[0], "a message from an offline session", "and before anything captured now");
    });
  } finally {
    await server.close();
  }
});

test("SessionStart skips a fork and writes no state for it", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const result = await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "fork",
      }), { env: hookEnv(home, server) });
      assert.equal(result.code, 0);
      assert.equal(result.stdout, "");
      assert.equal(server.requests.length, 0);
    });
  } finally {
    await server.close();
  }
});

test("Stop prints nothing, sends the closed turn, and never asks the agent to continue", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const env = hookEnv(home, server);
      // A brand-new session: SessionStart(startup) is what establishes that
      // the whole wire is new, so capture may start at its first line.
      await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });

      // The user's next turn is appended; Stop is its boundary.
      const { appendFile } = await import("node:fs/promises");
      await appendFile(join(sessionDir, "agents", "main", "wire.jsonl"), wireText(simpleTurn(1, {
        prompt: "a follow-up question",
        reply: "a follow-up answer",
      })), "utf8");

      const result = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), { env });

      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "", "a Stop hook that prints is a continuation request");
      // A refusal would be `{"decision":"block"}`; nothing of the sort appears.
      assert.equal(/decision|block|continue/i.test(result.stdout), false);
      const posted = server.requests.filter((request) => request.method === "POST");
      assert.ok(posted.length >= 1, "the closed turn should have been delivered");
    });
  } finally {
    await server.close();
  }
});

test("Stop on a session this plugin has never seen starts at the file end", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const result = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), {
        env: hookEnv(home, server),
      });
      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "");
      // A plugin installed mid-session must not import the history that
      // predates it: no message is sent for the existing turn.
      assert.equal(server.requests.filter((request) => request.method === "POST").length, 0);
    });
  } finally {
    await server.close();
  }
});

test("PreCompact prints nothing and commits a short session", async () => {
  const server = await startFakeOv((request) => {
    if (request.method === "GET" && /\/api\/v1\/sessions\//.test(request.path)) {
      return { body: { status: "ok", result: { pending_tokens: 5 } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const env = hookEnv(home, server);
      // SessionStart(startup) is what marks this wire as new; without it the
      // plugin correctly treats the session as pre-existing history.
      await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });

      const result = await runHook("pre-compact.mjs", hookPayload("PreCompact", {
        sessionId: SESSION,
        trigger: "auto",
        token_count: 120000,
      }), { env });

      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "");
      const commits = server.requests.filter((request) => /\/commit$/.test(request.path || ""));
      assert.equal(commits.length, 1);
      assert.deepEqual(commits[0].body, { keep_recent_count: 0 });
    });
  } finally {
    await server.close();
  }
});

test("SessionEnd prints nothing, writes a marker, and does not invent state for an unknown session", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, stateDir }) => {
      // No prior state: the session was never captured, so no sweep target.
      const result = await runHook("session-end.mjs", hookPayload("SessionEnd", {
        sessionId: SESSION,
        reason: "exit",
      }), { env: hookEnv(home, server) });
      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "");
      await assert.rejects(readFile(join(stateDir, "kimicode", `${SESSION}.json`), "utf8"));
    });
  } finally {
    await server.close();
  }
});

test("SessionEnd marks a session it has state for, before doing anything else", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, stateDir }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      // Capture once so the session has state.
      await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env: hookEnv(home, server) });
      const before = JSON.parse(await readFile(join(stateDir, "kimicode", `${SESSION}.json`), "utf8"));
      assert.ok(before.agents?.main, "the stop should have created agent state");

      server.failWith = 503;
      const result = await runHook("session-end.mjs", hookPayload("SessionEnd", {
        sessionId: SESSION,
        reason: "exit",
      }), { env: hookEnv(home, server) });
      assert.equal(result.code, 0);
      assert.equal(result.stdout, "");

      const after = JSON.parse(await readFile(join(stateDir, "kimicode", `${SESSION}.json`), "utf8"));
      // The marker survives a failed commit: that is what lets the sweep retry.
      assert.ok(after.endedMarker?.at, JSON.stringify(after.endedMarker));
      assert.equal(after.endedMarker.reason, "exit");
    });
  } finally {
    await server.close();
  }
});

test("UserPromptSubmit prints exactly one injection, and nothing when recall is off", async () => {
  const server = await startFakeOv(recallServer());
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });

      const on = await runHook("user-prompt.mjs", hookPayload("UserPromptSubmit", {
        sessionId: SESSION,
        prompt: [{ type: "text", text: "how do I configure the reader?" }],
        is_steer: false,
      }), { env: hookEnv(home, server) });
      assert.equal(on.code, 0, on.stderr);
      assert.ok(on.stdout.trim().length > 0, "the prompt should have received something");
      // Plain text, not JSON: the host wraps it in its own hook_result part.
      assert.equal(on.stdout.trimStart().startsWith("{"), false);

      server.reset();
      const off = await runHook("user-prompt.mjs", hookPayload("UserPromptSubmit", {
        sessionId: SESSION,
        prompt: [{ type: "text", text: "another question" }],
      }), {
        env: { ...hookEnv(home, server), OPENVIKING_AUTO_RECALL: "0", OPENVIKING_NO_AUTO_INJECT: "1" },
      });
      assert.equal(off.code, 0);
      assert.equal(off.stdout, "", "with both paths off there is nothing to say");
    });
  } finally {
    await server.close();
  }
});

test("UserPromptSubmit still answers when the server is down", async () => {
  const server = await startFakeOv(() => ({ status: 500, body: { status: "error", error: { code: "boom" } } }));
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const result = await runHook("user-prompt.mjs", hookPayload("UserPromptSubmit", {
        sessionId: SESSION,
        prompt: [{ type: "text", text: "does this still work?" }],
      }), { env: hookEnv(home, server) });
      assert.equal(result.code, 0, result.stderr);
      // Fail-open: no error text is injected into the user's prompt.
      assert.equal(/error|failed|exception/i.test(result.stdout), false);
    });
  } finally {
    await server.close();
  }
});

test("UserPromptSubmit treats two identical prompts as two events", async () => {
  const server = await startFakeOv(recallServer());
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const payload = hookPayload("UserPromptSubmit", {
        sessionId: SESSION,
        prompt: [{ type: "text", text: "the same question twice" }],
      });
      const env = hookEnv(home, server);
      const first = await runHook("user-prompt.mjs", payload, { env });
      const second = await runHook("user-prompt.mjs", payload, { env });
      assert.equal(first.code, 0);
      assert.equal(second.code, 0);
      // Both events answered. The second may reuse the cached block, but it is
      // still an answer to a real prompt and must not be swallowed.
      assert.ok(first.stdout.trim().length > 0);
      assert.ok(second.stdout.trim().length > 0);
    });
  } finally {
    await server.close();
  }
});

test("UserPromptSubmit ignores a payload with no session id", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const result = await runHook("user-prompt.mjs", { hook_event_name: "UserPromptSubmit", prompt: [] }, {
        env: hookEnv(home, server),
      });
      assert.equal(result.code, 0);
      assert.equal(result.stdout, "");
    });
  } finally {
    await server.close();
  }
});

test("UserPromptSubmit survives malformed stdin without a non-zero exit", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const result = await runHook("user-prompt.mjs", "{ this is not json", {
        env: hookEnv(home, server),
      });
      assert.equal(result.code, 0);
      assert.equal(result.stdout, "");
    });
  } finally {
    await server.close();
  }
});

test("PreToolUse denies a viking:// path on a file tool and stays silent otherwise", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const env = hookEnv(home, server);

      const denied = await runHook("uri-guard.mjs", hookPayload("PreToolUse", {
        sessionId: SESSION,
        tool_name: "Read",
        tool_input: { file_path: "viking://resources/notes.md" },
        tool_call_id: "call-1",
      }), { env });
      assert.equal(denied.code, 0, denied.stderr);
      const parsed = JSON.parse(denied.stdout);
      assert.equal(parsed.hookSpecificOutput.permissionDecision, "deny");
      assert.match(parsed.hookSpecificOutput.permissionDecisionReason, /viking:\/\/resources\/notes\.md/);

      const media = await runHook("uri-guard.mjs", hookPayload("PreToolUse", {
        sessionId: SESSION,
        tool_name: "ReadMediaFile",
        tool_input: { path: "viking://resources/diagram.png" },
      }), { env });
      assert.equal(media.code, 0);
      assert.equal(JSON.parse(media.stdout).hookSpecificOutput.permissionDecision, "deny");

      const local = await runHook("uri-guard.mjs", hookPayload("PreToolUse", {
        sessionId: SESSION,
        tool_name: "Read",
        tool_input: { file_path: "/tmp/notes.md" },
      }), { env });
      assert.equal(local.code, 0);
      assert.equal(local.stdout, "");

      const bash = await runHook("uri-guard.mjs", hookPayload("PreToolUse", {
        sessionId: SESSION,
        tool_name: "Bash",
        tool_input: { command: "ov read viking://resources/notes.md" },
      }), { env });
      assert.equal(bash.code, 0);
      assert.equal(bash.stdout, "", "a shell command is not blocked, and its notice would be invisible");
    });
  } finally {
    await server.close();
  }
});

test("every entry exits 0 when the payload is empty", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const env = hookEnv(home, server);
      for (const script of ["session-start.mjs", "user-prompt.mjs", "stop.mjs", "pre-compact.mjs", "session-end.mjs", "uri-guard.mjs"]) {
        const result = await runHook(script, {}, { env });
        assert.equal(result.code, 0, `${script}: ${result.stderr}`);
        assert.equal(result.stdout, "", `${script} printed on an empty payload`);
      }
    });
  } finally {
    await server.close();
  }
});

test("a corrupt state file is quarantined and the hook still answers", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, stateDir }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const { mkdir, readdir, writeFile } = await import("node:fs/promises");
      await mkdir(join(stateDir, "kimicode"), { recursive: true });
      await writeFile(join(stateDir, "kimicode", `${SESSION}.json`), "{broken", "utf8");

      const result = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), {
        env: hookEnv(home, server),
      });
      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "");

      const files = await readdir(join(stateDir, "kimicode"));
      assert.ok(files.some((name) => name.includes(".corrupt-")), files.join(", "));
    });
  } finally {
    await server.close();
  }
});

test("an unreadable state file stops capture without losing the file", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home, stateDir }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const { mkdir } = await import("node:fs/promises");
      // A directory where the state file should be: every read fails with
      // something other than ENOENT, which is the "unreadable" case.
      await mkdir(join(stateDir, "kimicode", `${SESSION}.json`), { recursive: true });

      const result = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), {
        env: hookEnv(home, server),
      });
      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "");
      // Nothing was sent: the cursors could not be trusted.
      assert.equal(server.requests.filter((request) => request.method === "POST").length, 0);
    });
  } finally {
    await server.close();
  }
});

test("a disabled plugin makes every entry a silent no-op", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const env = { ...hookEnv(home, server), OPENVIKING_MEMORY_ENABLED: "0" };
      for (const [script, event] of [
        ["session-start.mjs", "SessionStart"],
        ["user-prompt.mjs", "UserPromptSubmit"],
        ["stop.mjs", "Stop"],
        ["pre-compact.mjs", "PreCompact"],
        ["session-end.mjs", "SessionEnd"],
        ["uri-guard.mjs", "PreToolUse"],
      ]) {
        const payload = hookPayload(event, {
          sessionId: SESSION,
          prompt: [{ type: "text", text: "hello" }],
          tool_name: "Read",
          tool_input: { file_path: "viking://resources/a.md" },
        });
        const result = await runHook(script, payload, { env });
        assert.equal(result.code, 0, script);
        assert.equal(result.stdout, "", `${script} answered while disabled`);
      }
      assert.equal(server.requests.length, 0);
    });
  } finally {
    await server.close();
  }
});

test("an unwritable state directory still ends with the event's empty answer", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireBody() });
      const { mkdir, writeFile } = await import("node:fs/promises");
      // A file where the state directory should be: every write under it fails
      // with ENOTDIR, which is the class of failure the outer guard exists for.
      await mkdir(join(home, "hook-state"), { recursive: true });
      await writeFile(join(home, "hook-state", "kimicode"), "not a directory", "utf8");

      for (const [script, event] of [
        ["session-start.mjs", "SessionStart"],
        ["stop.mjs", "Stop"],
        ["pre-compact.mjs", "PreCompact"],
        ["session-end.mjs", "SessionEnd"],
        ["user-prompt.mjs", "UserPromptSubmit"],
      ]) {
        const result = await runHook(script, hookPayload(event, {
          sessionId: SESSION,
          source: "startup",
          prompt: [{ type: "text", text: "does this still work?" }],
        }), { env: hookEnv(home, server) });
        assert.equal(result.code, 0, `${script}: ${result.stderr}`);
        // No event may turn its own failure into a refusal or a continuation.
        assert.equal(/decision|block/i.test(result.stdout), false, script);
      }
    });
  } finally {
    await server.close();
  }
});
