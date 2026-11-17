/**
 * End to end, from a session's wire to the server and back.
 *
 * This is the scenario a user lives through: start a session, ask a question,
 * let the agent work, end the turn, compact, end the session, resume it. The
 * assertions are about what the server received and what the next prompt got
 * injected — not about internal counters.
 *
 * The whole run happens against a temporary `KIMI_CODE_HOME`, a temporary
 * pending directory and a fake server on a dynamic port, so nothing here can
 * touch a developer's real sessions.
 */

import assert from "node:assert/strict";
import { appendFile, readFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { hookPayload, runHook, withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import {
  contentPart,
  metadata,
  simpleTurn,
  stepBegin,
  stepEnd,
  toolCall,
  toolResult,
  turnEnded,
  turnPrompt,
  wireText,
} from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_e2e";

function envFor(home, server, extra = {}) {
  return {
    ...fakeOvEnv(server),
    KIMI_CODE_HOME: home,
    OPENVIKING_HOOK_STATE_DIR: join(home, "hook-state"),
    OPENVIKING_PENDING_DIR: join(home, "pending"),
    ...extra,
  };
}

/** A server that answers health, recall and session metadata. */
function serverHandler() {
  return (request) => {
    if (request.path === "/health") return { body: { status: "ok" } };
    if (request.path === "/api/v1/search/search") {
      return {
        body: {
          status: "ok",
          result: {
            rendered: "Memory: the deploy script lives in scripts/deploy.sh.",
            entries: [{
              uri: "viking://user/default/memories/deploy.md",
              category: "memory",
              text: "The deploy script lives in scripts/deploy.sh.",
              score: 0.88,
            }],
            stats: { used_tokens: 14, tier_counts: { memory: 1 }, rewrite: "off" },
          },
        },
      };
    }
    if (request.method === "GET" && /\/api\/v1\/sessions\/[^/]+$/.test(request.path || "")) {
      return { body: { status: "ok", result: { session_id: "x", pending_tokens: 30 } } };
    }
    if (request.path === "/api/v1/skills" || request.path === "/api/v1/profile") {
      return { body: { status: "ok", result: {} } };
    }
    return undefined;
  };
}

test("a full session: capture, recall, commit, resume, and the injection the user sees", async () => {
  const server = await startFakeOv(serverHandler());
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText([metadata()]),
      });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const env = envFor(home, server);

      // 1. The session starts. Nothing to capture yet, nothing to print.
      const started = await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });
      assert.equal(started.code, 0, started.stderr);
      assert.equal(started.stdout, "");

      // 2. The user asks a question. Capture runs first (nothing closed yet),
      //    then recall injects one block as plain text.
      const prompt1 = hookPayload("UserPromptSubmit", {
        sessionId: SESSION,
        prompt: [{ type: "text", text: "where does the deploy script live?" }],
        is_steer: false,
      });
      const injected = await runHook("user-prompt.mjs", prompt1, { env });
      assert.equal(injected.code, 0, injected.stderr);
      assert.match(injected.stdout, /scripts\/deploy\.sh/);
      assert.equal(injected.stdout.trimStart().startsWith("{"), false);

      // 3. The agent answers with a tool call. The turn closes, Stop delivers.
      await appendFile(wirePath, wireText([
        turnPrompt({ turnId: 0, text: "where does the deploy script live?" }),
        stepBegin({ turnId: 0, step: 1 }),
        toolCall({ turnId: 0, step: 1, toolCallId: "c1", name: "Glob", args: { pattern: "**/deploy.sh" } }),
        stepEnd({ turnId: 0, step: 1 }),
        stepBegin({ turnId: 0, step: 2 }),
        toolResult({ turnId: 0, toolCallId: "c1", output: "scripts/deploy.sh" }),
        contentPart({ turnId: 0, step: 2, part: { type: "text", text: "It lives at scripts/deploy.sh." } }),
        stepEnd({ turnId: 0, step: 2 }),
        turnEnded({ turnId: 0 }),
      ]), "utf8");

      const stopped = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), { env });
      assert.equal(stopped.code, 0, stopped.stderr);
      assert.equal(stopped.stdout, "");

      const ovSessionId = `kc-${SESSION}`;
      const captured = server.messages(ovSessionId);
      assert.deepEqual(
        captured.map((message) => message.message_kind),
        ["user_query", "assistant_step", "tool_transport", "assistant_step"],
      );
      assert.equal(captured[1].parts[0].tool_id, "c1");
      assert.equal(captured[2].parts[0].tool_output, "scripts/deploy.sh");

      // 4. Compaction forces a commit even though the token threshold is not met.
      const compacted = await runHook("pre-compact.mjs", hookPayload("PreCompact", {
        sessionId: SESSION,
        trigger: "auto",
        token_count: 120000,
      }), { env });
      assert.equal(compacted.code, 0, compacted.stderr);
      const commits = server.commits(ovSessionId);
      assert.equal(commits.length, 1);
      assert.deepEqual(commits[0], { keep_recent_count: 0 });

      // 5. The session ends cleanly: marker written, nothing left unsettled.
      const ended = await runHook("session-end.mjs", hookPayload("SessionEnd", {
        sessionId: SESSION,
        reason: "exit",
      }), { env });
      assert.equal(ended.code, 0, ended.stderr);
      const state = JSON.parse(await readFile(join(home, "hook-state", "kimicode", `${SESSION}.json`), "utf8"));
      assert.equal(state.endedMarker, undefined, "a fully settled session clears its marker");
      assert.equal(state.endedResult.status, "completed");

      // 6. The user resumes. Recall still works, and no new commit is invented.
      const before = server.commits(ovSessionId).length;
      const resumed = await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "resume",
      }), { env });
      assert.equal(resumed.code, 0, resumed.stderr);
      assert.equal(server.commits(ovSessionId).length, before);

      const prompt2 = await runHook("user-prompt.mjs", hookPayload("UserPromptSubmit", {
        sessionId: SESSION,
        prompt: [{ type: "text", text: "and how do I run the tests?" }],
      }), { env });
      assert.equal(prompt2.code, 0, prompt2.stderr);
      assert.match(prompt2.stdout, /deploy\.sh/, "recall must work again after a resume");
    });
  } finally {
    await server.close();
  }
});

test("an offline session queues its work and delivers it when the server returns", async () => {
  const server = await startFakeOv(serverHandler());
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        wireTextFor: () => wireText([metadata()]),
      });
      const env = envFor(home, server);

      await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });

      await appendFile(join(sessionDir, "agents", "main", "wire.jsonl"), wireText(simpleTurn(0, {
        prompt: "an offline question",
        reply: "an offline answer",
      })), "utf8");

      server.failWith = 503;
      const offline = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), { env });
      assert.equal(offline.code, 0);
      assert.equal(offline.stdout, "");
      assert.equal(server.messages(`kc-${SESSION}`).length, 0);

      // The server comes back. A new session start drains the queue.
      server.failWith = null;
      server.reset();
      const recovered = await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });
      assert.equal(recovered.code, 0, recovered.stderr);

      const delivered = server.messages(`kc-${SESSION}`);
      assert.deepEqual(delivered.map((message) => message.parts[0].text), [
        "an offline question",
        "an offline answer",
      ]);
    });
  } finally {
    await server.close();
  }
});

test("two prompts in a row each get their own answer", async () => {
  const server = await startFakeOv(serverHandler());
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const env = envFor(home, server);
      await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });

      const answers = [];
      for (const text of ["first question about deploy", "second question about tests"]) {
        const result = await runHook("user-prompt.mjs", hookPayload("UserPromptSubmit", {
          sessionId: SESSION,
          prompt: [{ type: "text", text }],
        }), { env });
        assert.equal(result.code, 0, result.stderr);
        answers.push(result.stdout.trim());
      }
      assert.ok(answers[0].length > 0);
      assert.ok(answers[1].length > 0, "the second prompt must not be swallowed as a duplicate");
    });
  } finally {
    await server.close();
  }
});

test("a subagent's activity reaches its own OpenViking session", async () => {
  const server = await startFakeOv(serverHandler());
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, {
        sessionId: SESSION,
        agents: { main: "", researcher: "" },
      });
      await appendFile(join(sessionDir, "agents", "main", "wire.jsonl"), wireText([
        metadata(),
        ...simpleTurn(0, { prompt: "delegate this", reply: "delegating" }),
      ]), "utf8");
      await appendFile(join(sessionDir, "agents", "researcher", "wire.jsonl"), wireText([
        metadata(),
        turnPrompt({ turnId: 0, text: "research prompt", origin: { kind: "task" } }),
        stepBegin({ turnId: 0, step: 1 }),
        contentPart({ turnId: 0, step: 1, part: { type: "text", text: "research findings" } }),
        stepEnd({ turnId: 0, step: 1 }),
        turnEnded({ turnId: 0 }),
      ]), "utf8");

      const env = envFor(home, server);
      await runHook("session-start.mjs", hookPayload("SessionStart", {
        sessionId: SESSION,
        source: "startup",
      }), { env });
      const stopped = await runHook("stop.mjs", hookPayload("Stop", { sessionId: SESSION }), { env });
      assert.equal(stopped.code, 0, stopped.stderr);

      const main = server.messages(`kc-${SESSION}`);
      const sub = server.messages(`kc-${SESSION}__subagent-researcher`);
      assert.deepEqual(main.map((message) => message.parts[0].text), ["delegate this", "delegating"]);
      // The subagent's task prompt is not a user question; its work is kept.
      assert.equal(sub.some((message) => message.message_kind === "user_query"), false);
      assert.deepEqual(sub.map((message) => message.parts[0].text), ["research findings"]);
    });
  } finally {
    await server.close();
  }
});
