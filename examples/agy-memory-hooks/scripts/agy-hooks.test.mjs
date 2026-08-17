import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildAgyTurns, cleanAgyText, latestAgyPrompt } from "./agy-turns.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = join(pluginRoot, "..", "..");

test("AGY integration contains hook, scripts and shared runtime", () => {
  for (const file of [
    "hooks/hooks.json",
    "openviking.integration.json",
    "scripts/agy-hook.mjs",
    "scripts/agy-turns.mjs",
    "scripts/session-start.mjs",
    "scripts/auto-recall.mjs",
    "scripts/auto-capture.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  for (const shared of [
    "examples/memory-plugin-shared/lib/agent-hook-runtime.mjs",
    "examples/memory-plugin-shared/lib/credentials.mjs",
    "examples/memory-plugin-shared/lib/session-model.mjs",
  ]) {
    assert.ok(existsSync(join(repoRoot, shared)), `${shared} must exist in the shared library`);
  }
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hooks", "hooks.json"), "utf8"));
  assert.deepEqual(Array.isArray(hooks.openviking) ? hooks.openviking : Object.keys(hooks.openviking), [
    "PreInvocation",
    "Stop",
  ]);
  const integration = JSON.parse(readFileSync(join(pluginRoot, "openviking.integration.json"), "utf8"));
  assert.deepEqual(integration.clients, ["agy"]);
  assert.ok(integration.capabilities.includes("hooks"));
});

// Shape taken from a real agy 1.1.13 transcript: the model's tool results share
// the `MODEL` source with its prose, IDE edit notices share `USER_EXPLICIT`
// with the user's prompt, and the injected context comes back as `SYSTEM_SDK`.
const transcriptLines = [
  { step_index: 0, source: "USER_EXPLICIT", type: "USER_INPUT", status: "DONE", content: "<USER_REQUEST>\nremember this\n</USER_REQUEST>" },
  { step_index: 1, source: "SYSTEM_SDK", type: "EPHEMERAL_MESSAGE", status: "DONE", content: "<openviking-context>injected</openviking-context>" },
  { step_index: 3, source: "MODEL", type: "VIEW_FILE", status: "DONE", content: "File Path: `file:///repo/notes.md`\nTotal Lines: 2" },
  { step_index: 2, source: "MODEL", type: "PLANNER_RESPONSE", status: "DONE", tool_calls: [{ name: "view_file" }] },
  { step_index: 4, source: "USER_EXPLICIT", type: "CODE_ACTION", status: "DONE", content: "The following changes were made by the USER to: /repo/notes.md" },
  { step_index: 5, source: "MODEL", type: "PLANNER_RESPONSE", status: "DONE", content: "done" },
];

function writeTranscript(root, lines = transcriptLines) {
  const logs = join(root, ".system_generated", "logs");
  mkdirSync(logs, { recursive: true });
  const path = join(logs, "transcript.jsonl");
  writeFileSync(path, lines.map((line) => JSON.stringify(line)).join("\n"), "utf8");
  return path;
}

test("AGY transcript parsing keeps conversation turns and drops tool output", () => {
  assert.equal(cleanAgyText("x<openviking-context>secret</openviking-context>y"), "xy");
  assert.equal(cleanAgyText("<USER_REQUEST>\nhello\n</USER_REQUEST>"), "hello");
  assert.equal(
    cleanAgyText("ship it\n\n<ADDITIONAL_METADATA>\nThe current local time is: 2026-01-01T00:00:00Z.\n</ADDITIONAL_METADATA>"),
    "ship it",
  );
  const root = mkdtempSync(join(tmpdir(), "openviking-agy-turns-"));
  try {
    const path = writeTranscript(root);
    const all = buildAgyTurns({ transcriptPath: path }, {});
    assert.deepEqual(
      all.map((turn) => [turn.role, turn.content]),
      [
        ["user", "remember this"],
        ["assistant", "done"],
      ],
      "tool results, IDE edit notices and injected context must never become turns",
    );
    const again = buildAgyTurns({ transcriptPath: path }, { lastStepKey: 5 });
    assert.deepEqual(
      again.map((turn) => [turn.role, turn.content]),
      [
        ["user", "remember this"],
        ["assistant", "done"],
      ],
      "a scan past the cursor must still surface un-hashed turns (transcripts can be written out of order)",
    );
    assert.equal(latestAgyPrompt({ transcriptPath: path }, {}), "remember this");
    assert.equal(latestAgyPrompt({}, { pendingPrompt: { prompt: "fallback" } }), "fallback");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("AGY turns are ordered by step_index numerically", () => {
  const root = mkdtempSync(join(tmpdir(), "openviking-agy-order-"));
  try {
    // Written out of order and crossing the 10 boundary, where a lexicographic
    // sort of the `s:<n>` keys would place `s:10` before `s:2`.
    const lines = [];
    for (const step of [2, 1, 0, 11, 10, 9, 3, 4, 5, 6, 7, 8]) {
      lines.push({
        step_index: step,
        source: step % 2 === 0 ? "USER_EXPLICIT" : "MODEL",
        type: step % 2 === 0 ? "USER_INPUT" : "PLANNER_RESPONSE",
        content: `msg-${step}`,
      });
    }
    const path = writeTranscript(root, lines);
    assert.deepEqual(
      buildAgyTurns({ transcriptPath: path }, {}).map((turn) => turn.content),
      Array.from({ length: 12 }, (_, step) => `msg-${step}`),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function runHook(entrypoint, input, env) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(process.execPath, [join(pluginRoot, "scripts", entrypoint)], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr || `hook exited ${code}`));
      else resolveRun(JSON.parse(stdout.trim() || "{}"));
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("AGY PreInvocation injects recall and Stop captures transcript turns", async () => {
  const messages = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url?.includes("/search")) {
        response.end(JSON.stringify({ result: { rendered: "agy memory", entries: [], stats: {} } }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]).map((message) => ({ url: request.url, message })));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else if (request.url?.endsWith("/commit")) {
        commits.push(request.url);
        response.end(JSON.stringify({ result: { ok: true } }));
      } else {
        response.end(JSON.stringify({ result: { ok: true } }));
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-agy-hook-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    const transcriptPath = writeTranscript(root);
    const base = { conversationId: "conv-test-1", workspacePaths: ["/workspace"], transcriptPath };

    const recalled = await Promise.all([
      runHook("auto-recall.mjs", { ...base, invocationNum: 1 }, env),
      runHook("auto-recall.mjs", { ...base, invocationNum: 2 }, env),
    ]);
    assert.equal(
      recalled.filter((item) => item.injectSteps?.some((step) => /agy memory/.test(step.ephemeralMessage || ""))).length,
      1,
    );

    await Promise.all([
      runHook("auto-capture.mjs", { ...base, executionNum: 1 }, env),
      runHook("auto-capture.mjs", { ...base, executionNum: 2 }, env),
    ]);
    const sent = messages.filter((item) => item.url.includes("ag-conv-test-1"));
    assert.equal(sent.length, 2, "user + assistant turns captured once");
    assert.equal(commits.length, 1, "completed AGY session committed immediately");
    assert.deepEqual(
      sent.map((item) => item.message),
      [
        { role: "user", content: "remember this" },
        { role: "assistant", content: "done" },
      ],
      "turns are sent in step order and carry no adapter-internal fields",
    );
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("AGY hooks are inert for bypassed workspaces", async () => {
  const root = mkdtempSync(join(tmpdir(), "openviking-agy-bypass-"));
  const transcriptPath = writeTranscript(root);
  const input = {
    conversationId: "conv-private",
    workspacePaths: ["/workspace/private-project/secrets"],
    transcriptPath,
    invocationNum: 1,
  };
  const baseEnv = {
    HOME: root,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    assert.deepEqual(
      await runHook("auto-recall.mjs", input, {
        ...baseEnv,
        OPENVIKING_BYPASS_SESSION_PATTERNS: "**private-project**",
      }),
      {},
      "the environment variable bypasses the workspace",
    );

    // Same bypass, this time declared in the `agy` section of ov.conf.
    const ovConfPath = join(root, "ov.conf");
    writeFileSync(
      ovConfPath,
      JSON.stringify({ agy: { bypassSessionPatterns: ["**private-project**"] } }),
      "utf8",
    );
    assert.deepEqual(
      await runHook("auto-recall.mjs", input, { ...baseEnv, OPENVIKING_CONFIG_FILE: ovConfPath }),
      {},
      "the ov.conf agy section bypasses the workspace",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
