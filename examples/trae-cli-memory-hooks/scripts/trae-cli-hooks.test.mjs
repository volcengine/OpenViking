import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildTraeCliTurns } from "./trae-cli-turns.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

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

test("TRAE CLI turn parsing supports every documented prompt and response alias", () => {
  const promptFields = ["prompt", "user_prompt", "userPrompt", "message", "text"];
  const responseFields = [
    "last_assistant_message",
    "lastAssistantMessage",
    "assistant_message",
    "assistantMessage",
    "response",
    "output",
    "text_content",
  ];

  for (const field of promptFields) {
    assert.deepEqual(buildTraeCliTurns({ [field]: "user question", last_assistant_message: "assistant answer" }), [
      { role: "user", content: "user question" },
      { role: "assistant", content: "assistant answer" },
    ], `prompt field ${field}`);
  }
  for (const field of responseFields) {
    assert.deepEqual(buildTraeCliTurns({ prompt: "user question", [field]: "assistant answer" }), [
      { role: "user", content: "user question" },
      { role: "assistant", content: "assistant answer" },
    ], `response field ${field}`);
  }
});

test("TRAE CLI lifecycle hooks recall and capture message/output payloads", async () => {
  const messages = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "TRAE CLI memory", entries: [], stats: {} } }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else {
        response.end(JSON.stringify({ result: { ok: true } }));
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const home = mkdtempSync(join(tmpdir(), "openviking-trae-cli-hooks-"));
  const env = {
    HOME: home,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(home, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    const base = { session_id: "same-session", cwd: "/workspace" };
    const recalled = await runHook("auto-recall.mjs", {
      ...base,
      message: "remember this TRAE CLI question",
      generation_id: "prompt-1",
    }, env);
    assert.match(recalled.hookSpecificOutput?.additionalContext || "", /TRAE CLI memory/);

    await runHook("auto-capture.mjs", { ...base, output: "TRAE CLI answer" }, env);
    assert.deepEqual(messages.map((message) => message.content), [
      "remember this TRAE CLI question",
      "TRAE CLI answer",
    ]);
  } finally {
    await new Promise((resolveClose) => server.close(resolveClose));
    rmSync(home, { recursive: true, force: true });
  }
});
