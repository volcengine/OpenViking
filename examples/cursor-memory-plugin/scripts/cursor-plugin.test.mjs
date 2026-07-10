import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { parseCursorTranscript } from "./lib/cursor-transcript.mjs";

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(pluginRoot, "..", "..");

function runHook(event, input, env) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(process.execPath, [join(pluginRoot, "scripts", "cursor-hook.mjs"), event], {
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

test("Cursor plugin and both marketplace manifests agree", () => {
  const manifest = JSON.parse(readFileSync(join(pluginRoot, ".cursor-plugin", "plugin.json"), "utf8"));
  assert.equal(manifest.name, "openviking-memory");
  for (const key of ["hooks", "mcpServers", "rules", "skills"]) {
    assert.ok(existsSync(resolve(pluginRoot, manifest[key])), `${key} path must exist`);
  }
  const repoMarketplace = JSON.parse(readFileSync(join(repoRoot, ".cursor-plugin", "marketplace.json"), "utf8"));
  assert.equal(resolve(repoRoot, repoMarketplace.plugins[0].source), pluginRoot);
  const examplesMarketplace = JSON.parse(readFileSync(join(repoRoot, "examples", ".cursor-plugin", "marketplace.json"), "utf8"));
  assert.equal(resolve(repoRoot, "examples", examplesMarketplace.plugins[0].source), pluginRoot);
});

test("Cursor transcript parser keeps only user and assistant text", () => {
  const raw = [
    JSON.stringify({ role: "user", message: { content: [{ type: "text", text: "question" }] } }),
    JSON.stringify({ role: "assistant", message: { content: [{ type: "text", text: "answer [REDACTED]" }, { type: "tool_use", name: "Read" }] } }),
    JSON.stringify({ type: "turn_ended", status: "success" }),
  ].join("\n");
  assert.deepEqual(parseCursorTranscript(raw), [
    { role: "user", content: "question" },
    { role: "assistant", content: "answer" },
  ]);
});

test("Cursor prefetch injects after the first tool and Stop captures transcript deltas", async () => {
  const messages = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "remembered context" } }));
      } else if (request.url?.includes("/messages")) {
        messages.push(JSON.parse(body));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else if (request.url?.endsWith("/commit")) {
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
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    const base = { conversation_id: "cursor-test", cwd: "/workspace" };
    assert.deepEqual(await runHook("beforeSubmitPrompt", { ...base, prompt: "what did we decide?" }, env), { continue: true });
    const injections = await Promise.all([
      runHook("postToolUse", { ...base, tool_name: "Read" }, env),
      runHook("postToolUse", { ...base, tool_name: "Read" }, env),
    ]);
    assert.equal(injections.filter((item) => /remembered context/.test(item.additional_context || "")).length, 1);

    const transcript = join(root, "cursor-test.jsonl");
    writeFileSync(transcript, [
      JSON.stringify({ role: "user", message: { content: [{ type: "text", text: "question" }] } }),
      JSON.stringify({ role: "assistant", message: { content: [{ type: "text", text: "answer" }] } }),
    ].join("\n"));
    await Promise.all([
      runHook("stop", { ...base, transcript_path: transcript }, env),
      runHook("stop", { ...base, transcript_path: transcript }, env),
    ]);
    assert.deepEqual(messages, [
      { role: "user", content: "question" },
      { role: "assistant", content: "answer" },
    ]);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("Cursor replays offline capture on the next SessionStart", async () => {
  const messages = [];
  let offline = true;
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url?.includes("/messages")) {
        if (offline) {
          response.statusCode = 503;
          response.end(JSON.stringify({ status: "error", error: "offline" }));
          return;
        }
        messages.push(JSON.parse(body));
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

    await runHook("stop", input, env);
    assert.equal(readdirSync(pendingDir).filter((name) => name.endsWith(".json")).length, 2);

    offline = false;
    await runHook("sessionStart", input, env);
    assert.deepEqual(messages, [
      { role: "user", content: "offline question" },
      { role: "assistant", content: "offline answer" },
    ]);
    assert.equal(readdirSync(pendingDir).filter((name) => name.endsWith(".json")).length, 0);

    await runHook("stop", input, env);
    assert.equal(messages.length, 2, "captured hashes prevent replayed turns from being stored twice");
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
