import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { expectExit, readRequestBody, runHookScript, withMockOpenViking, writeJson } from "../../../memory-plugin-shared/testing/support.mjs";
import { resolveCodexLaunch, trySpawnCodex } from "./codex-launch.mjs";

const HOOK = fileURLToPath(new URL("../../scripts/hook.mjs", import.meta.url));

test("Windows Codex launch bypasses the npm POSIX shim", () => {
  const npmBin = String.raw`C:\Users\test\AppData\Roaming\npm`;
  const npmEntryPoint = String.raw`C:\Users\test\AppData\Roaming\npm\node_modules\@openai\codex\bin\codex.js`;
  const launch = resolveCodexLaunch({
    platform: "win32",
    pathValue: `${npmBin};C:\\Windows\\System32`,
    execPath: String.raw`C:\Program Files\nodejs\node.exe`,
    pathExists: (candidate) => candidate === npmEntryPoint,
  });

  assert.deepEqual(launch, {
    command: String.raw`C:\Program Files\nodejs\node.exe`,
    argsPrefix: [npmEntryPoint],
  });
});

test("Codex launch converts a synchronous spawn failure into a fallback signal", () => {
  const failure = Object.assign(new Error("spawn EPERM"), { code: "EPERM" });
  const result = trySpawnCodex(["exec"], { stdio: "pipe" }, {
    resolveLaunch: () => ({ command: "codex", argsPrefix: [] }),
    spawnImpl: () => { throw failure; },
  });

  assert.equal(result.child, null);
  assert.equal(result.error, failure);
});

function writeStatusJson(res, status, value) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

async function runAutoRecall(input, env) {
  const result = expectExit(await runHookScript(HOOK, {
    argv: ["UserPromptSubmit", "codex"],
    input,
    env: { HOME: env.OPENVIKING_HOME, OPENVIKING_PENDING_DIR: join(env.OPENVIKING_STATE_DIR, "pending"), OPENVIKING_HOOK_STATE_DIR: join(env.OPENVIKING_STATE_DIR, "hooks"), ...env },
    inheritOpenVikingEnv: false,
  }));
  return result;
}

function recallEnv(stateDir, baseUrl, overrides = {}) {
  return {
    OPENVIKING_AUTO_RECALL: "1",
    OPENVIKING_CODEX_STATE_DIR: stateDir,
    OPENVIKING_STATE_DIR: stateDir,
    OPENVIKING_HOME: join(stateDir, "home"),
    OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_RECALL_COMPRESS: "0",
    OPENVIKING_RECALL_TIMEOUT_MS: "10000",
    OPENVIKING_MIN_QUERY_LENGTH: "1",
    OPENVIKING_SCORE_THRESHOLD: "0",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    ...overrides,
  };
}

async function withFakeCodex(output, fn, { exitCode = 0 } = {}) {
  const binDir = await mkdtemp(join(tmpdir(), "ov-fake-codex-"));
  const executable = join(binDir, "codex");
  const npmEntryPoint = join(
    binDir,
    "node_modules",
    "@openai",
    "codex",
    "bin",
    "codex.js",
  );
  const callLog = join(binDir, "calls.log");
  const argsLog = join(binDir, "args.log");
  const promptLog = join(binDir, "prompt.log");
  const fakeCodex = `#!/usr/bin/env node
const fs = require("node:fs");

const outputFlagIndex = process.argv.indexOf("--output-last-message");
const outputPath = outputFlagIndex >= 0 ? process.argv[outputFlagIndex + 1] : "";
const prompt = fs.readFileSync(0, "utf8");
fs.writeFileSync(process.env.FAKE_CODEX_PROMPT_LOG, prompt);
fs.appendFileSync(process.env.FAKE_CODEX_CALL_LOG, "called\\n");
fs.appendFileSync(process.env.FAKE_CODEX_ARGS_LOG, process.argv.slice(2).join("\\n") + "\\n");

const exitCode = Number(process.env.FAKE_CODEX_EXIT_CODE || 0);
if (exitCode !== 0) process.exit(exitCode);
if (!outputPath) process.exit(2);
fs.writeFileSync(outputPath, process.env.FAKE_CODEX_OUTPUT || "");
`;
  await writeFile(executable, fakeCodex);
  await mkdir(dirname(npmEntryPoint), { recursive: true });
  await writeFile(npmEntryPoint, fakeCodex);
  await chmod(executable, 0o755);
  try {
    return await fn({
      callLog,
      argsLog,
      promptLog,
      env: {
        PATH: `${binDir}${delimiter}${process.env.PATH}`,
        FAKE_CODEX_CALL_LOG: callLog,
        FAKE_CODEX_ARGS_LOG: argsLog,
        FAKE_CODEX_PROMPT_LOG: promptLog,
        FAKE_CODEX_EXIT_CODE: String(exitCode),
        FAKE_CODEX_OUTPUT: output,
      },
    });
  } finally {
    await rm(binDir, { recursive: true, force: true });
  }
}

async function runEndpointCompressionCase({
  prompt,
  entry,
  rendered,
  compressorOutput,
  exitCode = 0,
  extraEnv = {},
  stateDir: providedStateDir = "",
  sessionId = "codex:endpoint-compress",
}) {
  const stateDir = providedStateDir || await mkdtemp(join(tmpdir(), "ov-auto-recall-endpoint-compress-"));
  let requestBody = null;
  try {
    return await withFakeCodex(compressorOutput, async ({ callLog, argsLog, promptLog, env }) => {
      const result = await withMockOpenViking(async (req, res) => {
        const url = new URL(req.url, "http://127.0.0.1");
        if (req.method === "GET" && url.pathname === "/health") {
          writeJson(res, { status: "ok", result: { ok: true } });
          return;
        }
        if (req.method === "POST" && url.pathname === "/api/v1/search/recall") {
          requestBody = await readRequestBody(req);
          writeJson(res, {
            status: "ok",
            result: { entries: [entry], rendered, stats: { returned: 1 } },
          });
          return;
        }
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "error", error: "not found" }));
      }, async (baseUrl) => runAutoRecall(
        { prompt, session_id: sessionId },
        recallEnv(stateDir, baseUrl, {
          ...env,
          OPENVIKING_RECALL_COMPRESS: "1",
          // With recallTimeoutMs=10000 the compressor child inherits only
          // max(1000, recallTimeoutMs - 10000) = 1000ms, which a fresh
          // `node` cold start intermittently exceeds (seen in CI as
          // compress_timeout → SIGKILL → empty args log). Give the fake
          // codex subprocess a budget that cannot race with process startup.
          OPENVIKING_RECALL_TIMEOUT_MS: "60000",
          OPENVIKING_RECALL_COMPRESS_TIMEOUT_MS: "30000",
          ...extraEnv,
        }),
      ));
      const compressorCallLog = await readFile(callLog, "utf-8").catch(() => "");
      const compressorArgs = await readFile(argsLog, "utf-8").catch(() => "");
      const compressorPrompt = await readFile(promptLog, "utf-8").catch(() => "");
      return {
        output: JSON.parse(result.stdout.trim()),
        compressorCalls: compressorCallLog.trim().split("\n").filter(Boolean).length,
        compressorArgs: compressorArgs.trim().split("\n").filter(Boolean),
        compressorPrompt,
        requestBody,
      };
    }, { exitCode });
  } finally {
    if (!providedStateDir) await rm(stateDir, { recursive: true, force: true });
  }
}

test("UserPromptSubmit applies the relevance compressor to server recall entries", async () => {
  const result = await runEndpointCompressionCase({
    prompt: "Explain HTTP 429",
    entry: {
      uri: "viking://user/zeus/memories/events/unrelated.md",
      score: 0.42,
      type: "events",
      mode: "summary",
      summary: "Unrelated remembered detail",
    },
    rendered: "<memory_group>Unrelated remembered detail</memory_group>",
    compressorOutput: "NO_RELEVANT_MEMORY",
    extraEnv: { OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS: "0" },
  });

  assert.deepEqual(result.output, {});
  assert.equal(result.compressorCalls, 1);
  assert.equal(result.requestBody.max_chars, 18000);
});

test("UserPromptSubmit skips the compressor for a bounded short recall", async () => {
  const result = await runEndpointCompressionCase({
    prompt: "Which editor do I prefer?",
    entry: {
      uri: "viking://user/zeus/memories/preferences/editor.md",
      score: 0.91,
      type: "preferences",
      mode: "summary",
      summary: "Use Vim",
    },
    rendered: '<memory uri="viking://user/zeus/memories/preferences/editor.md">Use Vim</memory>',
    compressorOutput: "NO_RELEVANT_MEMORY",
  });

  assert.equal(result.compressorCalls, 0);
  assert.match(result.output.hookSpecificOutput.additionalContext, /Use Vim/);
});

test("UserPromptSubmit reuses a cached digest for an identical recall", async () => {
  const uri = "viking://user/zeus/memories/events/retry.md";
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-cache-"));
  const options = {
    prompt: "How should retries work?",
    entry: { uri, score: 0.91, type: "events", mode: "full", summary: "Retry with backoff" },
    rendered: `<memory uri="${uri}">${"Retry with exponential backoff. ".repeat(80)}</memory>`,
    compressorOutput: `- Retry with exponential backoff. source: ${uri}`,
    stateDir,
  };
  try {
    const first = await runEndpointCompressionCase(options);
    // The same prompt again in one session within 500 ms is a duplicate event.
    const second = await runEndpointCompressionCase({ ...options, sessionId: "codex:endpoint-compress-2" });
    assert.equal(first.compressorCalls, 1);
    assert.equal(second.compressorCalls, 0);
    assert.match(second.output.hookSpecificOutput.additionalContext, /exponential backoff/);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("UserPromptSubmit gives the compressor full content from the raw-search fallback", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-raw-compress-"));
  const memories = Array.from({ length: 6 }, (_, index) => ({
    uri: `viking://user/zeus/memories/events/raw-${index}.md`,
    level: 2,
    score: 0.9 - (index * 0.01),
    category: "events",
    abstract: `raw fallback memory ${index}`,
  }));
  const sentinels = memories.map((_, index) => `DETAIL_AFTER_320_${index}`);

  try {
    await withFakeCodex(
      `- kept raw detail source: ${memories[0].uri}`,
      async ({ promptLog, env }) => {
        const result = await withMockOpenViking(async (req, res) => {
          const url = new URL(req.url, "http://127.0.0.1");
          if (req.method === "GET" && url.pathname === "/health") {
            writeJson(res, { status: "ok", result: { ok: true } });
            return;
          }
          if (req.method === "POST" && url.pathname === "/api/v1/search/recall") {
            writeStatusJson(res, 404, { status: "error", error: "not found" });
            return;
          }
          if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
            const body = await readRequestBody(req);
            if (body.mode === "context") {
              writeStatusJson(res, 404, { status: "error", error: "Context search unavailable" });
              return;
            }
            const isMemoryScope = body.target_uri === "viking://user/zeus/memories";
            writeJson(res, {
              status: "ok",
              result: { memories: isMemoryScope ? memories : [], skills: [] },
            });
            return;
          }
          if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
            const index = memories.findIndex((item) => item.uri === url.searchParams.get("uri"));
            writeJson(res, {
              status: "ok",
              result: `${"x".repeat(320)}${sentinels[index]}${"y".repeat(80)}`,
            });
            return;
          }
          writeStatusJson(res, 404, { status: "error", error: "not found" });
        }, async (baseUrl) => runAutoRecall(
          { prompt: "use the raw fallback details", session_id: "codex:raw-compress" },
          recallEnv(stateDir, baseUrl, {
            ...env,
            OPENVIKING_RECALL_COMPRESS: "1",
            OPENVIKING_RECALL_LIMIT: "6",
            OPENVIKING_USER: "zeus",
          }),
        ));

        const compressorPrompt = await readFile(promptLog, "utf8");
        for (const sentinel of sentinels) assert.match(compressorPrompt, new RegExp(sentinel));
        assert.match(JSON.parse(result.stdout).hookSpecificOutput.additionalContext, /kept raw detail/);
      },
    );
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("UserPromptSubmit repairs a mangled viking:// URI in the compressed digest", async () => {
  const uri = "viking://user/zeus/memories/preferences/editor.md";
  const result = await runEndpointCompressionCase({
    prompt: "Which editor do I prefer?",
    entry: {
      uri,
      score: 0.91,
      type: "preferences",
      mode: "summary",
      summary: "Use Vim",
    },
    rendered: "<memory_group>Use Vim</memory_group>",
    compressorOutput: [
      "OpenViking memory digest:",
      "- [preferences] Use Vim (viking://user/zeus/memories/preference/edtior.md)",
    ].join("\n"),
    extraEnv: { OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS: "0" },
  });

  const injected = result.output.hookSpecificOutput.additionalContext;
  assert.ok(injected.includes(`(${uri})`), injected);
  assert.ok(!injected.includes("edtior"), injected);
  assert.equal(result.compressorCalls, 1);
});

test("UserPromptSubmit passes the configured compressor base URL to Codex", async () => {
  const result = await runEndpointCompressionCase({
    prompt: "Explain HTTP 429",
    entry: {
      uri: "viking://user/zeus/memories/events/retry.md",
      score: 0.91,
      type: "events",
      mode: "summary",
      summary: "Retry with backoff",
    },
    rendered: "<memory_group>Retry with backoff</memory_group>",
    compressorOutput: "NO_RELEVANT_MEMORY",
    extraEnv: {
      OPENVIKING_RECALL_COMPRESS_BASE_URL: "https://compressor.example/v1",
      OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS: "0",
    },
  });

  assert.ok(result.compressorArgs.includes('model_provider="openviking_compressor"'));
  assert.ok(result.compressorArgs.includes('model_providers.openviking_compressor.name="openviking_compressor"'));
  assert.ok(result.compressorArgs.includes('model_providers.openviking_compressor.base_url="https://compressor.example/v1"'));
});

test("UserPromptSubmit falls back to a bounded deterministic digest when endpoint compression fails", async () => {
  const result = await runEndpointCompressionCase({
    prompt: "Which editor do I prefer?",
    entry: {
      uri: "viking://user/zeus/memories/preferences/editor.md",
      score: 0.91,
      type: "preferences",
      mode: "summary",
      summary: "Use Vim",
    },
    rendered: "<memory_group>Use Vim</memory_group>",
    compressorOutput: "",
    exitCode: 1,
    extraEnv: { OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS: "0" },
  });

  assert.match(result.output.hookSpecificOutput.additionalContext, /Use Vim/);
  assert.doesNotMatch(result.output.hookSpecificOutput.additionalContext, /<memory_group>/);
  assert.equal(result.compressorCalls, 1);
});

test("UserPromptSubmit preserves recalled memory when compressor spawn throws synchronously", async () => {
  const preloadDir = await mkdtemp(join(tmpdir(), "ov-sync-spawn-failure-"));
  const preloadPath = join(preloadDir, "throw-codex-spawn.cjs");
  await writeFile(preloadPath, `
const childProcess = require("node:child_process");
const { syncBuiltinESMExports } = require("node:module");
const originalSpawn = childProcess.spawn;
childProcess.spawn = function patchedSpawn(command, args, ...rest) {
  if (Array.isArray(args) && args.includes("exec")) {
    throw Object.assign(new Error("spawn EPERM"), { code: "EPERM" });
  }
  return originalSpawn.call(this, command, args, ...rest);
};
syncBuiltinESMExports();
`);

  try {
    const result = await runEndpointCompressionCase({
      prompt: "Which editor do I prefer?",
      entry: {
        uri: "viking://user/zeus/memories/preferences/editor.md",
        score: 0.91,
        type: "preferences",
        mode: "summary",
        summary: "Use Vim",
      },
      rendered: "<memory_group>Use Vim</memory_group>",
      compressorOutput: "unused",
      extraEnv: {
        NODE_OPTIONS: `--require=${preloadPath}`,
        OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS: "0",
      },
    });

    assert.match(result.output.hookSpecificOutput.additionalContext, /Use Vim/);
    assert.doesNotMatch(result.output.hookSpecificOutput.additionalContext, /<memory_group>/);
    assert.equal(result.compressorCalls, 0);
  } finally {
    await rm(preloadDir, { recursive: true, force: true });
  }
});

test("a failed local compressor is not relaunched for another peer in the same hook", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-compressor-latch-"));
  try {
    await withFakeCodex("unused", async ({ env, callLog }) => {
      const script = `
        const { loadAgentHookConfig } = await import(${JSON.stringify(new URL('../../lib/agent-hook-runtime.mjs', import.meta.url).href)});
        const { createCodexCompressor } = await import(${JSON.stringify(new URL('./host-compressor.mjs', import.meta.url).href)});
        const run = await createCodexCompressor(loadAgentHookConfig('codex'));
        if (!run) throw new Error("missing compressor");
        if (await run("primary peer") !== null) throw new Error("expected failure");
        if (await run("legacy peer") !== null) throw new Error("expected disabled compressor");
      `;
      await new Promise((resolve, reject) => {
        const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
          env: { ...process.env, ...env,
            OPENVIKING_CODEX_STATE_DIR: stateDir, OPENVIKING_HOME: stateDir,
            OPENVIKING_CONFIG_FILE: join(stateDir, 'missing'), OPENVIKING_CLI_CONFIG_FILE: join(stateDir, 'missing-cli'),
            OPENVIKING_RECALL_COMPRESS: 'client', OPENVIKING_RECALL_COMPRESS_MODEL: 'test-model',
            OPENVIKING_RECALL_COMPRESS_TIMEOUT_MS: '1000',
          }, stdio: ['ignore', 'pipe', 'pipe'],
        });
        let stderr = '';
        child.stderr.on('data', data => { stderr += data; });
        child.on('error', reject);
        child.on('close', code => code === 0 ? resolve() : reject(new Error(stderr)));
      });
      assert.equal((await readFile(callLog, 'utf8')).trim().split('\n').length, 1);
    }, { exitCode: 1 });
  } finally { await rm(stateDir, { recursive: true, force: true }); }
});
