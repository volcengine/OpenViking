import assert from "node:assert/strict";
import { createServer } from "node:http";
import { appendFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import { antigravityTranscriptPath, resolveAntigravityTurnStep } from "../hosts/antigravity.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const hookEntry = join(pluginRoot, "scripts", "hook.mjs");

// Step shapes as written by agy 1.5.x to .system_generated/logs/transcript_full.jsonl.
const step = (index, fields) => `${JSON.stringify({ step_index: index, status: "DONE", ...fields })}\n`;
const firstTurn = step(0, {
  source: "USER_EXPLICIT",
  type: "USER_INPUT",
  content: "<USER_REQUEST>\nWhat is the retry budget?\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nlocal time\n</ADDITIONAL_METADATA>",
})
  + step(1, { source: "SYSTEM_SDK", type: "SYSTEM_MESSAGE", content: "<openviking-context>old</openviking-context>" })
  + step(2, { source: "MODEL", type: "PLANNER_RESPONSE", tool_calls: [{ name: "run_command" }] })
  + step(3, { source: "MODEL", type: "GENERIC", content: "Command output: 3" })
  + step(4, { source: "MODEL", type: "PLANNER_RESPONSE", content: "The retry budget is three attempts." });

async function runHook(event, input, env) {
  const run = expectExit(await runHookScript(hookEntry, { argv: [event, "antigravity"], input, env }));
  return JSON.parse(run.stdout.trim() || "{}");
}

test("Antigravity hooks.json uses named hooks with the dispatcher's event names", () => {
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "antigravity", "hooks.json"), "utf8"));
  const handlers = hooks["openviking-memory"];
  assert.deepEqual(Object.keys(handlers), ["SessionStart", "PreInvocation", "Stop"]);
  assert.match(handlers.PreInvocation[0].command, /hook\.mjs pre-invocation __OPENVIKING_CLIENT_ID__$/);
});

test("Antigravity recalls once per turn and captures only new transcript text", async () => {
  const messages = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "antigravity memory", entries: [], stats: {} } }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else {
        if (request.url?.endsWith("/commit")) commits.push(request.url);
        response.end(JSON.stringify({ result: { ok: true } }));
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-antigravity-hook-"));
  const transcriptPath = join(root, "transcript_full.jsonl");
  writeFileSync(transcriptPath, firstTurn);
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_WRITE_PATH_ASYNC: "0",
  };
  try {
    const base = { conversationId: "conv-1", cwd: "/workspace", transcriptPath };
    const call = (invocationNum, initialNumSteps) => ({
      ...base, invocationNum, initialNumSteps, lastUserInput: "What is the retry budget?",
    });
    const recalled = await Promise.all([
      runHook("pre-invocation", call(0, 1), env),
      runHook("pre-invocation", call(0, 1), env),
      runHook("pre-invocation", call(1, 3), env),
    ]);
    const injected = recalled.filter((output) => /antigravity memory/.test(
      output.injectSteps?.[0]?.systemMessage?.systemMessage || "",
    ));
    assert.equal(injected.length, 1, "only the first model call of a turn recalls");
    assert.ok(recalled.every((output) => output.injectSteps || Object.keys(output).length === 0));

    // Even before any MODEL step runs (e.g. only SessionStart SYSTEM_SDK is
    // on disk and initialNumSteps moved), the turn key stays on step 0.
    assert.deepEqual(await runHook("pre-invocation", call(0, 2), env), {});

    // A background task, subagent message, or Stop continuation starts a new
    // invocation (`invocationNum` 0) with a higher `initialNumSteps` while
    // `lastUserInput` still holds the earlier prompt. Because the earlier
    // `systemMessage` stays in the trajectory, wakeups must not inject it again.
    assert.deepEqual(await runHook("pre-invocation", call(0, 5), env), {});
    appendFileSync(transcriptPath, step(5, {
      source: "SYSTEM",
      type: "SYSTEM_MESSAGE",
      content: "[Message] sender=conv-1/task-1 Task completed",
    }));
    assert.deepEqual(await runHook("pre-invocation", call(0, 6), env), {});

    await runHook("stop", base, env);
    assert.deepEqual(messages.map((message) => [message.role, message.parts?.[0]?.text ?? message.content]), [
      ["user", "What is the retry budget?"],
      ["assistant", "The retry budget is three attempts."],
    ]);
    assert.equal(commits.length, 1);
    assert.ok(commits[0].includes("ag-conv-1"));

    // A second Stop sends only steps written since the first one. Replies that
    // quote the host's request envelope keep their text.
    const quoted = "The transcript wraps your prompt like this:\n<USER_REQUEST>your prompt</USER_REQUEST>\nKeep metadata separate.";
    const template = "An empty template:\n```\n<USER_REQUEST></USER_REQUEST>\n```";
    appendFileSync(transcriptPath, step(6, { source: "USER_EXPLICIT", type: "USER_INPUT", content: "<USER_REQUEST>\nHow is my prompt wrapped?\n</USER_REQUEST>" }));
    const secondRecall = await runHook("pre-invocation", {
      ...base, invocationNum: 0, initialNumSteps: 7, lastUserInput: "How is my prompt wrapped?",
    }, env);
    assert.match(secondRecall.injectSteps?.[0]?.systemMessage?.systemMessage || "", /antigravity memory/);

    // Backgrounded commands (`GENERIC` with `RUNNING`) and failed/invalid tool
    // steps stay non-DONE permanently in Antigravity transcripts, and a
    // canceled/errored reply will never become DONE; neither may freeze the
    // capture cursor before later finished replies. An in-flight reply at the
    // tail (`RUNNING` / `GENERATING`) still waits for the next Stop.
    appendFileSync(transcriptPath, step(7, { source: "MODEL", type: "GENERIC", content: "Tool is running as a background task", status: "RUNNING" })
      + step(8, { source: "MODEL", type: "GENERIC", content: "Encountered error", status: "ERROR" })
      + step(9, { source: "MODEL", type: "GENERIC", content: "Invalid tool call", status: "INVALID" })
      + step(10, { source: "MODEL", type: "PLANNER_RESPONSE", content: "Aborted draft.", status: "CANCELED" })
      + step(11, { source: "MODEL", type: "PLANNER_RESPONSE", content: quoted })
      + step(12, { source: "MODEL", type: "PLANNER_RESPONSE", content: template })
      + step(13, { source: "MODEL", type: "PLANNER_RESPONSE", content: "Thirty seconds.", status: "RUNNING" }));
    assert.deepEqual(await runHook("pre-invocation", {
      ...base, invocationNum: 0, initialNumSteps: 13, lastUserInput: "How is my prompt wrapped?",
    }, env), {});
    await runHook("stop", base, env);
    assert.deepEqual(messages.slice(2).map((message) => message.parts?.[0]?.text ?? message.content), [
      "How is my prompt wrapped?",
      quoted,
      template,
    ]);
    writeFileSync(
      transcriptPath,
      readFileSync(transcriptPath, "utf8").replace('{"step_index":13,"status":"RUNNING"', '{"step_index":13,"status":"DONE"'),
    );
    await runHook("stop", base, env);
    assert.deepEqual(messages.slice(5).map((message) => message.parts?.[0]?.text ?? message.content), [
      "Thirty seconds.",
    ]);

    // A queued user turn that the model already answered without an initial
    // recall must not recall late on a background wakeup, while a genuine
    // repeated prompt in a new USER_INPUT step still gets its turn injection.
    const latePath = join(root, "late_transcript.jsonl");
    writeFileSync(latePath, step(0, { source: "USER_EXPLICIT", type: "USER_INPUT", content: "<USER_REQUEST>\nContinue\n</USER_REQUEST>" })
      + step(1, { source: "MODEL", type: "PLANNER_RESPONSE", content: "Done." }));
    const lateBase = { conversationId: "conv-2", cwd: "/workspace", transcriptPath: latePath };
    assert.deepEqual(await runHook("pre-invocation", {
      ...lateBase, invocationNum: 0, initialNumSteps: 2, lastUserInput: "Continue",
    }, env), {});
    appendFileSync(latePath, step(2, { source: "USER_EXPLICIT", type: "USER_INPUT", content: "<USER_REQUEST>\nContinue\n</USER_REQUEST>" }));
    const firstContinue = await runHook("pre-invocation", {
      ...lateBase, invocationNum: 0, initialNumSteps: 3, lastUserInput: "Continue",
    }, env);
    assert.match(firstContinue.injectSteps?.[0]?.systemMessage?.systemMessage || "", /antigravity memory/);
    appendFileSync(latePath, step(3, { source: "MODEL", type: "PLANNER_RESPONSE", content: "Done again." })
      + step(4, { source: "USER_EXPLICIT", type: "USER_INPUT", content: "<USER_REQUEST>\nContinue\n</USER_REQUEST>" }));
    const secondContinue = await runHook("pre-invocation", {
      ...lateBase, invocationNum: 0, initialNumSteps: 5, lastUserInput: "Continue",
    }, env);
    assert.match(secondContinue.injectSteps?.[0]?.systemMessage?.systemMessage || "", /antigravity memory/);

    // Missing transcript falls back to initialNumSteps without failing recall.
    const noTranscript = await runHook("pre-invocation", {
      conversationId: "conv-3",
      cwd: "/workspace",
      transcriptPath: join(root, "missing.jsonl"),
      invocationNum: 0,
      initialNumSteps: 1,
      lastUserInput: "Fallback prompt",
    }, env);
    assert.match(noTranscript.injectSteps?.[0]?.systemMessage?.systemMessage || "", /antigravity memory/);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("Antigravity treats an input the transcript has not recorded yet as a new turn", () => {
  // The previous turn is answered; the new turn's USER_INPUT step is not on disk.
  assert.equal(resolveAntigravityTurnStep(firstTurn, 5, "What is the retry budget?"), null);
  assert.equal(resolveAntigravityTurnStep(firstTurn, 5, "  What is the\nretry budget?  "), null);
  assert.equal(resolveAntigravityTurnStep(firstTurn, 5, "And the timeout?"), undefined);
  assert.equal(resolveAntigravityTurnStep(firstTurn, 5), null);

  // transcript.jsonl keeps only the head and tail of a long record, which can
  // not be compared with the input, so it stays a wakeup.
  const truncated = step(0, {
    source: "USER_EXPLICIT",
    type: "USER_INPUT",
    content: "<USER_REQUEST>\nWhat is the …",
    truncated_fields: ["content"],
  }) + step(1, { source: "MODEL", type: "PLANNER_RESPONSE", content: "Three." });
  assert.equal(resolveAntigravityTurnStep(truncated, 2, "What is the retry budget?"), null);

  // Only the SessionStart steps are on disk before the first turn is written.
  const startOnly = step(0, { source: "SYSTEM_SDK", type: "SYSTEM_MESSAGE", content: "<openviking-context>start</openviking-context>" });
  assert.equal(resolveAntigravityTurnStep(startOnly, 1, "First prompt"), undefined);
});

test("Antigravity reads transcript_full.jsonl in place of the truncated transcript", () => {
  const root = mkdtempSync(join(tmpdir(), "openviking-antigravity-transcript-"));
  try {
    const short = join(root, "transcript.jsonl");
    writeFileSync(short, "");
    assert.equal(antigravityTranscriptPath(short), short);
    writeFileSync(join(root, "transcript_full.jsonl"), "");
    assert.equal(antigravityTranscriptPath(short), join(root, "transcript_full.jsonl"));
    const other = join(root, "late_transcript.jsonl");
    assert.equal(antigravityTranscriptPath(other), other);
    assert.equal(antigravityTranscriptPath(undefined), "");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
