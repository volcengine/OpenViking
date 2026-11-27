import assert from "node:assert/strict";
import { createServer } from "node:http";
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildCaptureMessage } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import {
  antigravityTranscriptPath,
  buildAntigravityTurns,
  resolveAntigravityTurnStep,
} from "../hosts/antigravity.mjs";
import { evaluateHostUriGuard } from "../scripts/uri-guard.mjs";

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
  + step(2, { source: "MODEL", type: "PLANNER_RESPONSE", tool_calls: [{ name: "run_command", args: { CommandLine: "grep -c retry app.toml" } }] })
  + step(3, { source: "MODEL", type: "GENERIC", content: "Created At: 2026-01-01T00:00:00Z\nCompleted At: 2026-01-01T00:00:01Z\n".repeat(2) + "3" })
  + step(4, { source: "MODEL", type: "PLANNER_RESPONSE", content: "The retry budget is three attempts." });

async function runHook(event, input, env) {
  const run = expectExit(await runHookScript(hookEntry, { argv: [event, "antigravity"], input, env }));
  return JSON.parse(run.stdout.trim() || "{}");
}

test("Antigravity hooks.json uses named hooks with the dispatcher's event names", () => {
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "antigravity", "hooks.json"), "utf8"));
  const handlers = hooks["openviking-memory"];
  assert.deepEqual(Object.keys(handlers), ["SessionStart", "PreInvocation", "PreToolUse", "Stop"]);
  assert.match(handlers.PreInvocation[0].command, /hook\.mjs pre-invocation __OPENVIKING_CLIENT_ID__$/);
  assert.match(handlers.PreToolUse[0].hooks[0].command, /uri-guard\.mjs __OPENVIKING_CLIENT_ID__$/);
});

test("Antigravity denies a file tool whose location is a viking:// URI", () => {
  const guard = (name, args) => evaluateHostUriGuard("antigravity", { toolCall: { name, args } });
  const uri = "viking://user/default/memories/profile.md";

  const read = guard("read_file", { Path: uri });
  assert.equal(read.decision, "deny");
  assert.match(read.reason, /read\(uri="viking:\/\/user\/default\/memories\/profile\.md"\)/);
  assert.match(guard("write_to_file", { TargetFile: uri, CodeContent: "x" }).reason, /OpenViking MCP write/);
  assert.match(guard("list_dir", { DirectoryPath: "viking://resources/" }).reason, /glob or list/);

  // A body or a command line that mentions a URI is data, and no decision is
  // returned for a call that may proceed.
  assert.deepEqual(guard("write_to_file", { TargetFile: "/tmp/a.md", CodeContent: `see ${uri}` }), {});
  assert.deepEqual(guard("run_command", { CommandLine: `echo ${uri}` }), {});
  assert.deepEqual(guard("read_file", { Path: "/tmp/notes.txt" }), {});
  assert.deepEqual(evaluateHostUriGuard("antigravity", {}), {});
});

test("Antigravity recalls once per turn and captures only new transcript text", async () => {
  const sent = {};
  const commits = [];
  let serialPosts = 0;
  let rejectSerialPost = 0;
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      const session = request.url?.match(/\/sessions\/([^/]+)\/messages/)?.[1];
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "antigravity memory", entries: [], stats: {} } }));
      } else if (session === "ag-conv-4" && request.url.endsWith("/batch")) {
        // No batch route: the client falls back to one request per message.
        response.statusCode = 404;
        response.end(JSON.stringify({ error: { code: "NOT_FOUND" } }));
      } else if (session) {
        if (!request.url.endsWith("/batch") && ++serialPosts === rejectSerialPost) {
          response.statusCode = 400;
          response.end(JSON.stringify({ error: { code: "INVALID_ARGUMENT" } }));
          return;
        }
        const parsed = JSON.parse(body);
        (sent[session] ||= []).push(...(parsed.messages ?? [parsed]).map(({ peer_id, ...message }) => message));
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
  const messages = () => sent["ag-conv-1"] || [];
  const textOf = (message) => message.parts.filter((part) => part.type === "text").map((part) => part.text).join("\n\n");
  const texts = (from) => messages().slice(from).map(textOf);
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
    const runCommand = { type: "tool", tool_id: "ag-2-0", tool_name: "run_command" };
    assert.deepEqual(messages(), [
      { role: "user", turn_id: "0", parts: [{ type: "text", text: "What is the retry budget?" }] },
      {
        role: "assistant",
        turn_id: "2",
        parts: [
          { ...runCommand, tool_status: "running", tool_input: { CommandLine: "grep -c retry app.toml" } },
          { ...runCommand, tool_status: "completed", tool_output: "3" },
        ],
      },
      { role: "assistant", turn_id: "4", parts: [{ type: "text", text: "The retry budget is three attempts." }] },
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

    // Tool steps no planner step called are dropped, and a canceled reply will
    // never become DONE; neither may freeze the capture cursor before later
    // finished replies. An in-flight reply at the tail (`RUNNING` /
    // `GENERATING`) still waits for the next Stop.
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
    assert.deepEqual(texts(3), [
      "How is my prompt wrapped?",
      quoted,
      template,
    ]);
    writeFileSync(
      transcriptPath,
      readFileSync(transcriptPath, "utf8").replace('{"step_index":13,"status":"RUNNING"', '{"step_index":13,"status":"DONE"'),
    );
    await runHook("stop", base, env);
    assert.deepEqual(texts(6), ["Thirty seconds."]);

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

    // With assistant capture off only the user requests go out.
    await runHook("stop", lateBase, { ...env, OPENVIKING_CAPTURE_ASSISTANT_TURNS: "0" });
    assert.deepEqual(sent["ag-conv-2"].map((message) => message.role), ["user", "user", "user"]);

    // After a partial write the next Stop resends only what the server did not take.
    const partialPath = join(root, "partial_transcript.jsonl");
    writeFileSync(partialPath, step(0, { source: "USER_EXPLICIT", type: "USER_INPUT", content: "<USER_REQUEST>\nList the services\n</USER_REQUEST>" })
      + step(1, { source: "MODEL", type: "PLANNER_RESPONSE", tool_calls: [{ name: "list_dir", args: { DirectoryPath: "/srv" } }] })
      + step(2, { source: "MODEL", type: "LIST_DIRECTORY", content: "Created At: T\nCompleted At: T\napi\nworker" })
      + step(3, { source: "MODEL", type: "PLANNER_RESPONSE", content: "There are two services: api and worker." }));
    const partialBase = { conversationId: "conv-4", cwd: "/workspace", transcriptPath: partialPath };
    rejectSerialPost = serialPosts + 2;
    await runHook("stop", partialBase, env);
    await runHook("stop", partialBase, env);
    const summary = (message) => message.parts.map((part) => part.text ?? part.tool_status);
    assert.deepEqual(sent["ag-conv-4"].map(summary), [
      ["List the services"],
      ["running", "completed"],
      ["There are two services: api and worker."],
    ]);

    // State left by a version that kept a step cursor resumes after that step.
    const stateFile = join(env.OPENVIKING_HOOK_STATE_DIR, "antigravity", "conv-5.json");
    mkdirSync(dirname(stateFile), { recursive: true });
    writeFileSync(stateFile, JSON.stringify({ lastStep: 2 }));
    const upgradedBase = { conversationId: "conv-5", cwd: "/workspace", transcriptPath: partialPath };
    await runHook("stop", upgradedBase, env);
    await runHook("stop", upgradedBase, env);
    assert.deepEqual(sent["ag-conv-5"].map(summary), [["There are two services: api and worker."]]);
    const upgraded = JSON.parse(readFileSync(stateFile, "utf8"));
    assert.equal(upgraded.lastTurnId, "3");
    assert.equal(upgraded.lastStep, undefined);

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

test("Antigravity folds each call and the result answering it into the planner step's turn", () => {
  const user = (index, text) => step(index, { source: "USER_EXPLICIT", type: "USER_INPUT", content: `<USER_REQUEST>\n${text}\n</USER_REQUEST>` });
  const planner = (index, calls, content) => step(index, {
    source: "MODEL",
    type: "PLANNER_RESPONSE",
    ...(content ? { content } : {}),
    tool_calls: calls.map((call) => (typeof call === "string" ? { name: call, args: { Path: `/src/${call}` } } : call)),
  });
  const result = (index, status, content) => step(index, { source: "MODEL", type: "GENERIC", status, content });
  const mcpRead = {
    name: "call_mcp_tool",
    args: { ServerName: "openviking", ToolName: "read", Arguments: { uri: "viking://resources/a.md" }, toolSummary: "Read" },
  };
  const jsonl = user(0, "Check the build")
    // Superseded before any result: the call goes out unanswered. Its record
    // lands after the first result of the next planner step, and step 4 is
    // written twice; the order is step_index and the last record counts.
    + result(3, "RUNNING", "Created At: T\nTool is running as a background task with task id t-1")
    + result(4, "PENDING", "")
    + planner(1, ["read_file"])
    + planner(2, ["run_command", mcpRead, "view_file"], "Checking the build.")
    + result(5, "INVALID", "Created At: T\nCompleted At: T\nmissing argument")
    + result(4, "DONE", "Created At: T\nCompleted At: T\nretry budget: 3")
    // More results than calls: none of them can be placed.
    + planner(6, ["search_web"])
    + result(7, "DONE", "first")
    + result(8, "DONE", "second")
    + user(9, "And the tests?")
    // No call is pending after a user step.
    + result(10, "DONE", "stray")
    // A result still being written holds back its planner step.
    + planner(11, ["list_dir"])
    + result(12, "PENDING", "");

  const turns = buildAntigravityTurns(jsonl);
  const blocks = (turn) => turn.payload?.content.map((block) => [block.type, block.id ?? block.tool_use_id ?? block.text]);
  assert.deepEqual(turns.map((turn) => [turn.role, turn.turnId, blocks(turn) ?? turn.content]), [
    ["user", "0", "Check the build"],
    ["assistant", "1", [["tool_use", "ag-1-0"]]],
    ["assistant", "2", [
      ["text", "Checking the build."],
      ["tool_use", "ag-2-0"],
      ["tool_use", "ag-2-1"],
      ["tool_use", "ag-2-2"],
      ["tool_result", "ag-2-1"],
      ["tool_result", "ag-2-2"],
    ]],
    ["assistant", "6", [["tool_use", "ag-6-0"]]],
    ["user", "9", "And the tests?"],
  ]);
  const message = buildCaptureMessage(turns[2].payload, "assistant", {});
  assert.deepEqual(message.parts.slice(1).map((part) => [part.tool_name, part.tool_status, part.tool_input ?? part.tool_output]), [
    ["run_command", "running", { Path: "/src/run_command" }],
    ["mcp__openviking__read", "running", { uri: "viking://resources/a.md" }],
    ["view_file", "running", { Path: "/src/view_file" }],
    ["mcp__openviking__read", "completed", "retry budget: 3"],
    ["view_file", "error", "missing argument"],
  ]);

  // A last line still being written holds back the step it belongs to.
  const partial = user(0, "Check the build") + planner(1, ["read_file"]) + '{"step_index":2,"source":"MO';
  assert.deepEqual(buildAntigravityTurns(partial).map((turn) => turn.turnId), ["0"]);
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
