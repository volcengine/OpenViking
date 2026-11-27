import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import fs from "node:fs/promises";
import { createServer } from "node:http";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { stableHash } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import { cursor } from "../hosts/cursor.mjs";
import { evaluateHostUriGuard } from "../scripts/uri-guard.mjs";

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hookEntry = resolve(pluginRoot, "scripts", "hook.mjs");

async function runHook(event, input, env) {
  const run = expectExit(await runHookScript(hookEntry, { argv: [event, "cursor"], input, env }));
  return JSON.parse(run.stdout.trim() || "{}");
}

test("Cursor command-installed integration contains Hook, Rule, Skill, and MCP entrypoints", () => {
  for (const file of [
    "plugin.json",
    "hosts/cursor/hooks.json",
    "hosts/cursor/.mcp.json",
    "hosts/cursor/openviking.integration.json",
    "hosts/cursor.mjs",
    "hosts/cursor-transcript.mjs",
    "scripts/hook.mjs",
    "scripts/uri-guard.mjs",
    "servers/mcp-proxy.mjs",
    "hosts/cursor/rules/openviking-memory.mdc",
    "hosts/cursor/skills/openviking-memory/SKILL.md",
    "hosts/cursor/skills/openviking-skills/SKILL.md",
    "hosts/cursor/skills/ov-experience-memory/SKILL.md",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  const plugin = JSON.parse(readFileSync(join(pluginRoot, "plugin.json"), "utf8"));
  const integration = JSON.parse(readFileSync(join(pluginRoot, "hosts", "cursor", "openviking.integration.json"), "utf8"));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "cursor", "hooks.json"), "utf8"));
  assert.equal(plugin.version, integration.version);
  assert.deepEqual(Object.keys(hooks.hooks), [
    "sessionStart",
    "beforeSubmitPrompt",
    "beforeReadFile",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "afterMCPExecution",
    "stop",
    "preCompact",
    "sessionEnd",
  ]);
});

test("Cursor URI guard redirects virtual paths to OpenViking MCP tools", () => {
  const readDecision = evaluateHostUriGuard("cursor", {
    file_path: "viking://resources/project/file.md",
  });
  assert.deepEqual(Object.keys(readDecision).sort(), ["permission", "user_message"]);
  assert.equal(readDecision.permission, "deny");
  assert.match(readDecision.user_message, /OpenViking MCP read/);

  assert.deepEqual(evaluateHostUriGuard("cursor", { file_path: "/tmp/file.md" }), {});
});

test("Cursor URI guard lets a shell command routed by an older install run", () => {
  assert.deepEqual(evaluateHostUriGuard("cursor", {
    command: "cat viking://resources/project/file.md",
    cwd: "/workspace",
  }), {});
});

test("Cursor captures a busy session without replaying text or rereading uploaded tool bodies", async () => {
  const root = mkdtempSync(join(tmpdir(), "openviking-cursor-history-"));
  const transcript = join(root, "transcript.jsonl");
  const messages = [];
  let commits = 0;
  let toolReadBytes = 0;
  const originalReadFile = fs.readFile;
  const originalOpen = fs.open;
  fs.readFile = async (path, ...args) => {
    const data = await originalReadFile(path, ...args);
    if (String(path).endsWith(".tools.jsonl")) toolReadBytes += Buffer.byteLength(data);
    return data;
  };
  fs.open = async (path, ...args) => {
    const file = await originalOpen(path, ...args);
    if (String(path).endsWith(".tools.jsonl")) {
      const read = file.read.bind(file);
      file.read = async (...args) => {
        const result = await read(...args);
        toolReadBytes += result.bytesRead;
        return result;
      };
    }
    return file;
  };
  syncBuiltinESMExports();
  const ctx = {
    cfg: { enabled: true, autoCapture: true, commitTurnThreshold: 8 },
    sessionId: "cu-history",
    stateFile: join(root, "state.json"),
    peerId: "",
    log: () => {},
    fetchJSON: async (path, request) => {
      if (path.endsWith("/messages/batch")) messages.push(...JSON.parse(request.body).messages);
      if (path.endsWith("/commit")) commits++;
      return { ok: true };
    },
  };
  const tool = {
    transcript_path: transcript,
    mcp_server_name: "openviking",
    tool_name: "read",
    tool_input: JSON.stringify({ uris: "viking://~/memories/experiences/example.md" }),
    result_json: JSON.stringify({ content: [{ type: "text", text: `${"x".repeat(19996)}部署经验` }], isError: false }),
  };
  let state = {};
  const lines = [];
  try {
    for (let round = 0; round < 60; round++) {
      lines.push(
        JSON.stringify({ role: "user", content: `Question in round ${round}` }),
        JSON.stringify({ role: "assistant", content: `Answer in round ${round}` }),
      );
      writeFileSync(transcript, lines.join("\n"));
      for (let call = 0; call < 15; call++) {
        ctx.input = { ...tool, tool_use_id: `${round}-${call}` };
        await cursor.capture(ctx, state, "afterMCPExecution");
      }
      ctx.input = { transcript_path: transcript };
      state = await cursor.capture(ctx, state, "stop");
    }
    assert.equal(messages.filter((message) => message.parts[0].type === "text").length, 120);
    assert.equal(messages.filter((message) => message.parts[0].type === "tool").length, 900);
    assert.equal(commits, 15);
    const readBeforeEmptyStop = toolReadBytes;
    assert.equal(toolReadBytes, statSync(`${ctx.stateFile}.tools.jsonl`).size,
      "each tool-result byte should be read once across all rounds");
    state = await cursor.capture(ctx, state, "stop");
    assert.equal(toolReadBytes, readBeforeEmptyStop, "empty Stop must not read tool-result bodies");

    // An old completion can be delivered again after its result was accepted.
    ctx.input = { ...tool, tool_use_id: "0-0" };
    await cursor.capture(ctx, state, "afterMCPExecution");
    ctx.input = { transcript_path: transcript };
    state = await cursor.capture(ctx, state, "stop");
    await cursor.capture(ctx, state, "stop");
    assert.equal(messages.length, 1020, "empty Stop must not resend accepted text or tools");
    assert.equal(commits, 15, "duplicate capture must not advance the commit threshold");
    assert.equal(toolReadBytes, statSync(`${ctx.stateFile}.tools.jsonl`).size,
      "a repeated completion only reads the newly appended record");
  } finally {
    fs.readFile = originalReadFile;
    fs.open = originalOpen;
    syncBuiltinESMExports();
    rmSync(root, { recursive: true, force: true });
  }
});

test("Cursor sends each turn of a long transcript once, also after an upgrade from content hashes", async () => {
  const root = mkdtempSync(join(tmpdir(), "openviking-cursor-long-"));
  const transcript = join(root, "transcript.jsonl");
  const sent = [];
  const ctx = {
    cfg: { enabled: true, autoCapture: true, commitTurnThreshold: 1_000_000 },
    sessionId: "cu-long",
    stateFile: join(root, "state.json"),
    input: { transcript_path: transcript },
    peerId: "",
    log: () => {},
    fetchJSON: async (path, request) => {
      if (path.endsWith("/messages/batch")) sent.push(...JSON.parse(request.body).messages.map((m) => m.parts[0].text));
      return { ok: true };
    },
  };
  const turns = [];
  const write = (count) => {
    for (let i = 0; i < count; i++) {
      const n = turns.length;
      turns.push({ role: n % 2 ? "assistant" : "user", content: `turn ${n}` });
    }
    writeFileSync(transcript, turns.map((turn) => JSON.stringify(turn)).join("\n"));
  };
  try {
    write(1200);
    let state = await cursor.capture(ctx, {}, "stop");
    write(2);
    state = await cursor.capture(ctx, state, "stop");
    await cursor.capture(ctx, state, "stop");
    assert.deepEqual(sent, turns.map((turn) => turn.content));

    // An earlier version kept only the hashes of the last 1000 captured turns.
    const capturedHashes = turns.map((turn, index) => stableHash(index, turn.role, turn.content)).slice(-1000);
    sent.length = 0;
    write(1);
    state = await cursor.capture(ctx, { capturedHashes }, "stop");
    assert.deepEqual(sent, ["turn 1202"]);
    assert.equal(state.capturedHashes, undefined);
    await cursor.capture(ctx, state, "stop");
    assert.deepEqual(sent, ["turn 1202"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("Cursor captures text and every completed tool together before commit", async () => {
  const messages = [];
  const actorPeers = [];
  const archives = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        actorPeers.push(request.headers["x-openviking-actor-peer"]);
        response.end(JSON.stringify({
          result: { rendered: "remembered context", entries: [], stats: {} },
        }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else if (request.url?.endsWith("/commit")) {
        archives.push([...messages]);
        response.end(JSON.stringify({ result: { ok: true } }));
      } else {
        response.statusCode = 404;
        response.end(JSON.stringify({ status: "error" }));
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-cursor-hook-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_PENDING_DIR: join(root, "pending"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    // The peer comes from the repository the workspace root sits in; outside
    // one the plugin deliberately sends no peer at all.
    const workspace = join(root, "cursor-project");
    mkdirSync(join(workspace, ".git"), { recursive: true });
    writeFileSync(join(workspace, ".git", "config"), '[remote "origin"]\n\turl = git@github.com:acme/cursor-project.git\n');
    const base = { conversation_id: "cursor-test", workspace_roots: [workspace] };
    const injections = await Promise.all([
      runHook("beforeSubmitPrompt", { ...base, prompt: "what did we decide?", generation_id: "prompt-1" }, env),
      runHook("beforeSubmitPrompt", { ...base, prompt: "what did we decide?", generation_id: "prompt-1" }, env),
    ]);
    assert.equal(injections.filter((item) => /remembered context/.test(item.additional_context || "")).length, 1);
    assert.deepEqual(actorPeers, ["github.com-acme-cursor-project"]);

    const transcript = join(root, "cursor-test.jsonl");
    writeFileSync(transcript, [
      JSON.stringify({ role: "user", message: { content: [{ type: "text", text: "question" }] } }),
      JSON.stringify({ role: "assistant", message: { content: [{ type: "text", text: "answer" }] } }),
    ].join("\n"));
    const experienceUri = "viking://~/memories/experiences/example.md";
    const read = {
      ...base,
      transcript_path: transcript,
      tool_use_id: "read-1",
      mcp_server_name: "user-openviking",
      tool_name: "read",
      tool_input: JSON.stringify({ uris: experienceUri }),
      result_json: JSON.stringify({ content: [{ type: "text", text: "experience body" }], isError: false }),
    };
    await Promise.all([
      runHook("afterMCPExecution", read, env),
      runHook("afterMCPExecution", read, env),
    ]);
    // postToolUse repeats every MCP call as `MCP:<tool>`, without its server.
    await runHook("postToolUse", {
      ...base, transcript_path: transcript, tool_use_id: "read-1", tool_name: "MCP:read",
      tool_input: JSON.parse(read.tool_input), tool_output: read.result_json,
    }, env);
    const shell = {
      ...base, transcript_path: transcript, tool_use_id: "shell-1", tool_name: "Shell",
      tool_input: { command: "ls", cwd: workspace }, tool_output: JSON.stringify({ output: "hello.txt\n", exitCode: 0 }),
    };
    await runHook("postToolUse", shell, env);
    await runHook("postToolUseFailure", {
      ...base, transcript_path: transcript, tool_use_id: "shell-2", tool_name: "Shell",
      tool_input: { command: "false", cwd: workspace }, error_message: "Command failed with exit code 1", failure_type: "error",
    }, env);
    await runHook("afterMCPExecution", {
      ...read, tool_use_id: "read-failed",
      result_json: JSON.stringify({ content: [{ type: "text", text: "NOT_FOUND: missing" }], isError: true }),
    }, env);
    await runHook("afterMCPExecution", { ...read, tool_use_id: "other-server", mcp_server_name: "user-other" }, env);
    await runHook("afterMCPExecution", { ...read, tool_use_id: "" }, env);
    await runHook("afterMCPExecution", { ...read, tool_use_id: "no-result", result_json: "{}" }, env);
    await runHook("afterMCPExecution", { ...read, tool_use_id: "disabled" }, { ...env, OPENVIKING_AUTO_CAPTURE: "0" });
    await runHook("preCompact", { ...base, transcript_path: join(root, "missing.jsonl") }, env);
    assert.equal(messages.length, 0, "tool completion only records locally");
    assert.equal(archives.length, 0);
    writeFileSync(transcript, `${readFileSync(transcript, "utf8")}\n${JSON.stringify({
      role: "assistant", content: "Task finished after reading the experience.",
    })}`);
    await Promise.all([
      runHook("stop", { ...base, transcript_path: transcript }, env),
      runHook("stop", { ...base, transcript_path: transcript }, env),
    ]);
    // Session writes ignore the actor-peer header, so the peer rides in the body.
    const peer_id = "github.com-acme-cursor-project";
    const tool = (part) => ({ role: "assistant", peer_id, parts: [{ type: "tool", ...part }] });
    const text = (role, value) => ({ role, peer_id, parts: [{ type: "text", text: value }] });
    assert.deepEqual(messages, [
      text("user", "question"),
      text("assistant", "answer"),
      tool({
        tool_id: "read-1", tool_name: "mcp__openviking__read",
        tool_status: "completed", tool_input: { uris: experienceUri }, tool_output: read.result_json,
      }),
      tool({
        tool_id: "shell-1", tool_name: "Shell",
        tool_status: "completed", tool_input: shell.tool_input, tool_output: shell.tool_output,
      }),
      tool({
        tool_id: "shell-2", tool_name: "Shell",
        tool_status: "error", tool_input: { command: "false", cwd: workspace },
        tool_output: "Command failed with exit code 1",
      }),
      tool({
        tool_id: "read-failed", tool_name: "mcp__openviking__read",
        tool_status: "error", tool_input: { uris: experienceUri },
        tool_output: JSON.stringify({ content: [{ type: "text", text: "NOT_FOUND: missing" }], isError: true }),
      }),
      tool({
        tool_id: "other-server", tool_name: "mcp__other__read",
        tool_status: "completed", tool_input: { uris: experienceUri }, tool_output: read.result_json,
      }),
      text("assistant", "Task finished after reading the experience."),
    ]);
    await runHook("afterMCPExecution", read, env);
    await runHook("sessionEnd", { ...base, transcript_path: transcript }, env);
    assert.equal(messages.length, 8, "a repeated completion after Stop is not captured twice");
    assert.deepEqual(archives.at(-1), messages);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("Cursor queues the unsent tail of a partial capture and the commit behind it", async () => {
  const messages = [];
  const archives = [];
  let offline = true;
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url?.includes("/messages")) {
        if (offline && request.url.endsWith("/messages/batch")) {
          response.statusCode = 404;
          response.end(JSON.stringify({ status: "error" }));
          return;
        }
        if (offline && messages.length >= 3) {
          response.statusCode = 503;
          response.end(JSON.stringify({ status: "error", error: "offline" }));
          return;
        }
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]));
      } else if (request.url?.endsWith("/commit")) {
        archives.push([...messages]);
      }
      response.end(JSON.stringify({ result: { ok: true } }));
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-cursor-replay-"));
  const pendingDir = join(root, "pending");
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_PENDING_DIR: pendingDir,
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    const transcript = join(root, "cursor-offline.jsonl");
    writeFileSync(transcript, [
      JSON.stringify({ role: "user", message: { content: [{ type: "text", text: "offline question" }] } }),
      JSON.stringify({ role: "assistant", message: { content: [{ type: "text", text: "offline answer" }] } }),
    ].join("\n"));
    const input = { conversation_id: "cursor-offline", cwd: "/workspace", transcript_path: transcript };

    const tool = {
      ...input,
      tool_use_id: "offline-read",
      mcp_server_name: "openviking",
      tool_name: "read",
      tool_input: JSON.stringify({ uris: "viking://~/memories/experiences/offline.md" }),
      result_json: JSON.stringify({ content: [{ type: "text", text: "offline experience" }], isError: false }),
    };
    await runHook("afterMCPExecution", tool, env);
    await runHook("afterMCPExecution", tool, env);
    await runHook("afterMCPExecution", { ...tool, tool_use_id: "offline-read-2" }, env);
    await runHook("preCompact", input, env);
    assert.equal(archives.length, 0, "commit must not overtake unreported messages");
    assert.equal(messages.length, 3);
    assert.equal(messages[2].parts[0].tool_id, "offline-read");
    assert.equal(readdirSync(pendingDir).length, 2, "the unsent tool record and the commit are queued");

    // Any later hook replays the queue, not only this conversation's next capture.
    offline = false;
    await runHook("sessionStart", { conversation_id: "cursor-other", cwd: "/workspace" }, env);
    assert.deepEqual(messages, [
      { role: "user", parts: [{ type: "text", text: "offline question" }] },
      { role: "assistant", parts: [{ type: "text", text: "offline answer" }] },
      {
        role: "assistant",
        parts: [{
          type: "tool", tool_id: "offline-read", tool_name: "mcp__openviking__read",
          tool_status: "completed", tool_input: JSON.parse(tool.tool_input), tool_output: tool.result_json,
        }],
      },
      {
        role: "assistant",
        parts: [{
          type: "tool", tool_id: "offline-read-2", tool_name: "mcp__openviking__read",
          tool_status: "completed", tool_input: JSON.parse(tool.tool_input), tool_output: tool.result_json,
        }],
      },
    ]);
    assert.deepEqual(archives, [messages]);
    assert.deepEqual(readdirSync(pendingDir), []);

    await runHook("stop", input, env);
    assert.equal(messages.length, 4, "acknowledged turns are not stored twice");

    // User-only capture drops assistant text and tools; a compaction without
    // autoCommitOnCompact still captures but leaves the commit alone.
    const userOnly = { ...env, OPENVIKING_CAPTURE_ASSISTANT_TURNS: "0", OPENVIKING_AUTO_COMMIT_ON_COMPACT: "0" };
    await runHook("postToolUse", { ...input, tool_use_id: "quiet-shell", tool_name: "Shell", tool_input: { command: "ls" }, tool_output: "{}" }, userOnly);
    writeFileSync(transcript, `${readFileSync(transcript, "utf8")}\n${[
      JSON.stringify({ role: "user", content: "later question" }),
      JSON.stringify({ role: "assistant", content: "later answer" }),
    ].join("\n")}`);
    await runHook("preCompact", input, userOnly);
    assert.deepEqual(messages.slice(4), [{ role: "user", parts: [{ type: "text", text: "later question" }] }]);
    assert.equal(archives.length, 1);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
