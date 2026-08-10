import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { resolveCodexLaunch, trySpawnCodex } from "./codex-launch.mjs";
import { markRecallCompressorRuntimeFailed } from "./recall-compressor-profile.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

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

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      try {
        resolve(raw ? JSON.parse(raw) : null);
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res, value) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

function writeStatusJson(res, status, value) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

async function withMockOpenViking(handler, fn) {
  const server = http.createServer((req, res) => {
    handler(req, res).catch((err) => {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: String(err?.stack || err) }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function runAutoRecall(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "auto-recall.mjs")], {
      env: { ...cleanEnv, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`auto-recall exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

async function withFakeCodex(output, fn, { exitCode = 0 } = {}) {
  const binDir = await mkdtemp(join(tmpdir(), "ov-fake-codex-"));
  const executable = join(binDir, "codex");
  const callLog = join(binDir, "calls.log");
  await writeFile(executable, `#!/bin/sh
output_path=""
model=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-m" ]; then
    shift
    model="$1"
  fi
  if [ "$1" = "--output-last-message" ]; then
    shift
    output_path="$1"
  fi
  shift
done
cat >/dev/null
printf '%s\\n' "$model" >> "$FAKE_CODEX_CALL_LOG"
if [ "$FAKE_CODEX_HANG" = "1" ]; then
  while :; do :; done
fi
if [ -n "$FAKE_CODEX_FAIL_MODEL" ] && [ "$model" = "$FAKE_CODEX_FAIL_MODEL" ]; then
  exit 1
fi
if [ "$FAKE_CODEX_EXIT_CODE" -ne 0 ]; then
  exit "$FAKE_CODEX_EXIT_CODE"
fi
printf '%s' "$FAKE_CODEX_OUTPUT" > "$output_path"
`);
  await chmod(executable, 0o755);
  try {
    return await fn({
      callLog,
      env: {
        PATH: `${binDir}:${process.env.PATH}`,
        FAKE_CODEX_CALL_LOG: callLog,
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
  seedFailedModels = [],
}) {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-endpoint-compress-"));
  let requestBody = null;
  try {
    if (seedFailedModels.length > 0) {
      const previousStateDir = process.env.OPENVIKING_CODEX_STATE_DIR;
      process.env.OPENVIKING_CODEX_STATE_DIR = stateDir;
      try {
        await markRecallCompressorRuntimeFailed({
          recallCompress: true,
          recallCompressModel: "",
          recallCompressThinking: "",
          recallCompressConfigured: false,
          recallCompressDetectTtlMs: 604_800_000,
        }, { failedModels: seedFailedModels });
      } finally {
        if (previousStateDir === undefined) delete process.env.OPENVIKING_CODEX_STATE_DIR;
        else process.env.OPENVIKING_CODEX_STATE_DIR = previousStateDir;
      }
    }
    return await withFakeCodex(compressorOutput, async ({ callLog, env }) => {
      const startedAt = Date.now();
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
        { prompt, session_id: "codex:endpoint-compress" },
        {
          ...env,
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS: "1",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
          ...extraEnv,
        },
      ));
      const compressorCallLog = await readFile(callLog, "utf-8").catch(() => "");
      const compressorModels = compressorCallLog.trim().split("\n").filter(Boolean);
      const cachedProfile = JSON.parse(
        await readFile(join(stateDir, "recall-compressor-profile.json"), "utf-8")
          .catch(() => "null"),
      )?.profile || null;
      return {
        output: JSON.parse(result.stdout.trim()),
        compressorCalls: compressorModels.length,
        compressorModels,
        cachedProfile,
        elapsedMs: Date.now() - startedAt,
        requestBody,
      };
    }, { exitCode });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
}

test("auto-recall asks the context face with the derived OpenViking session id", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-state-"));
  const requests = [];

  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        const body = await readRequestBody(req);
        requests.push({ path: url.pathname, body });
        writeJson(res, {
          status: "ok",
          result: {
            entries: [{
              uri: "viking://user/zeus/memories/events/context-search.md",
              category: "events",
              detail: "full",
              score: 0.9,
              text: "context-aware recalled detail",
            }],
            rendered: '<memory uri="viking://user/zeus/memories/events/context-search.md" type="events" score="0.90" detail="full">\ncontext-aware recalled detail\n</memory>',
            digest: "",
            stats: { returned: 1, used_tokens: 40 },
          },
        });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "please use prior context", session_id: "codex:123" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "1",
          OPENVIKING_RECALL_MAX_TOKENS: "800",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      assert.match(
        output.hookSpecificOutput.additionalContext,
        /context-aware recalled detail/,
      );
    });

    assert.equal(requests.length, 1);
    assert.equal(requests[0].body.mode, "context");
    assert.equal(requests[0].body.session_id, "cx-codex_123");
    assert.equal(requests[0].body.purpose, "coding");
    assert.equal(requests[0].body.limit, undefined);
    assert.equal(
      Object.values(requests[0].body.quotas).reduce((sum, quota) => sum + quota, 0),
      1,
    );
    assert.equal(requests[0].body.max_tokens, 800);
    assert.equal(requests[0].body.dedup_turns, 5);
    assert.equal(requests[0].body.quotas.resources, 1);
    assert.equal(requests[0].body.quotas.experiences, 0);
    assert.equal(requests[0].body.target_uri, undefined);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-recall prefers the server recall endpoint when available", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-endpoint-"));
  const requests = [];

  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/recall") {
        const body = await readRequestBody(req);
        requests.push({ path: url.pathname, body });
        writeJson(res, {
          status: "ok",
          result: {
            entries: [{
              uri: "viking://user/zeus/memories/events/launch.md",
              score: 0.9,
              type: "events",
              mode: "summary",
              summary: "Launch summary",
            }],
            rendered: '<memory_group type="events" count="1">\n<memory index="1" type="summary">\n  <uri>viking://user/zeus/memories/events/launch.md</uri>\n  <summary>Launch summary</summary>\n</memory>\n</memory_group>',
            stats: { returned: 1 },
          },
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        requests.push({ path: url.pathname, body: await readRequestBody(req) });
        // Pre-context-face deployment: extra fields are rejected outright.
        writeStatusJson(res, 400, {
          status: "error",
          error: "Extra inputs are not permitted: mode",
        });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "please use server recall", session_id: "codex:recall" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "2",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      assert.match(output.hookSpecificOutput.additionalContext, /OpenViking memory digest/);
      assert.match(output.hookSpecificOutput.additionalContext, /Launch summary/);
    });

    assert.deepEqual(requests.map((request) => request.path), [
      "/api/v1/search/search",
      "/api/v1/search/recall",
    ]);
    assert.equal(Object.values(requests[1].body.quotas).reduce((sum, quota) => sum + quota, 0), 2);
    assert.equal(requests[1].body.quotas.experiences, 0);
    assert.equal(requests[1].body.max_chars, 6500);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-recall authoritatively filters deprecated Experience entries and rebuilds rendered context", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-experience-context-"));
  const activeUri = "viking://user/zeus/memories/experiences/active-case.md";
  const deprecatedUri = "viking://user/zeus/memories/experiences/old-case.md";
  const eventUri = "viking://user/zeus/memories/events/safe-event.md";
  const rawReads = [];

  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        writeJson(res, {
          status: "ok",
          result: {
            entries: [
              { uri: activeUri, category: "experiences", score: 0.9, text: "ACTIVE EXPERIENCE SUMMARY" },
              { uri: deprecatedUri, category: "experiences", score: 0.8, text: "DEPRECATED SECRET BODY" },
              { uri: eventUri, category: "events", score: 0.7, text: "SAFE EVENT SUMMARY" },
            ],
            rendered: [
              `<memory><uri>${activeUri}</uri>ACTIVE EXPERIENCE SUMMARY</memory>`,
              `<memory><uri>${deprecatedUri}</uri>DEPRECATED SECRET BODY</memory>`,
              `<memory><uri>${eventUri}</uri>SAFE EVENT SUMMARY</memory>`,
            ].join("\n"),
            stats: { returned: 3 },
          },
        });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
        const uri = url.searchParams.get("uri");
        rawReads.push({ uri, raw: url.searchParams.get("raw") });
        if (uri === activeUri) {
          // Legacy Experience: non-empty authoritative raw with no lifecycle
          // metadata remains eligible with status="".
          writeJson(res, { status: "ok", result: "authoritative legacy active body" });
        } else {
          writeJson(res, {
            status: "ok",
            result: "authoritative deprecated body\n\n<!-- MEMORY_FIELDS\n{\"status\":\"deprecated\",\"version\":1}\n-->",
          });
        }
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "reuse a relevant past case", session_id: "codex:experience-context" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "3",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      const context = output.hookSpecificOutput.additionalContext;
      assert.match(context, /authoritative legacy active body/);
      assert.match(context, /SAFE EVENT SUMMARY/);
      assert.doesNotMatch(context, /ACTIVE EXPERIENCE SUMMARY/);
      assert.doesNotMatch(context, /DEPRECATED SECRET BODY/);
      assert.doesNotMatch(context, /<memory>/);
    });

    assert.deepEqual(rawReads, [
      { uri: activeUri, raw: "true" },
      { uri: deprecatedUri, raw: "true" },
    ]);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("legacy search fails closed for Experience raw read, empty raw, and metadata parse failures", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-experience-legacy-"));
  const uris = {
    failed: "viking://user/zeus/memories/experiences/read-failed.md",
    empty: "viking://user/zeus/memories/experiences/empty.md",
    malformed: "viking://user/zeus/memories/experiences/malformed.md",
  };
  const rawReads = [];

  try {
    await withMockOpenViking(async (req, res) => {
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
          writeStatusJson(res, 400, {
            status: "error",
            error: "Extra inputs are not permitted: mode",
          });
          return;
        }
        if (body.target_uri === "viking://user/zeus/memories") {
          writeJson(res, {
            status: "ok",
            result: {
              memories: Object.values(uris).map((uri, index) => ({
                uri,
                level: 2,
                score: 0.9 - index / 10,
                category: "experiences",
                abstract: `candidate ${index}`,
              })),
              skills: [],
            },
          });
          return;
        }
        writeJson(res, { status: "ok", result: { memories: [], skills: [] } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
        const uri = url.searchParams.get("uri");
        rawReads.push({ uri, raw: url.searchParams.get("raw") });
        if (uri === uris.failed) {
          writeStatusJson(res, 503, { status: "error", error: "unavailable" });
        } else if (uri === uris.empty) {
          writeJson(res, { status: "ok", result: "" });
        } else {
          writeJson(res, {
            status: "ok",
            result: "body\n\n<!-- MEMORY_FIELDS\n{not-json}\n-->",
          });
        }
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "find prior cases", session_id: "codex:experience-legacy" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_USER: "zeus",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "3",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );
      assert.deepEqual(JSON.parse(result.stdout.trim()), {});
    });

    assert.deepEqual(
      rawReads.map(({ uri }) => uri).sort(),
      Object.values(uris).sort(),
    );
    assert.ok(rawReads.every(({ raw }) => raw === "true"));
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("legacy search backfills a lower eligible memory after an archived top Experience", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-experience-backfill-"));
  const archivedUri = "viking://user/zeus/memories/experiences/archived-top.md";
  const eventUri = "viking://user/zeus/memories/events/lower-eligible.md";
  const contentReads = [];

  try {
    await withMockOpenViking(async (req, res) => {
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
          writeStatusJson(res, 400, {
            status: "error",
            error: "Extra inputs are not permitted: mode",
          });
          return;
        }
        if (body.target_uri === "viking://user/zeus/memories") {
          writeJson(res, {
            status: "ok",
            result: {
              memories: [
                {
                  uri: archivedUri,
                  level: 2,
                  score: 0.99,
                  category: "experiences",
                  abstract: "archived top candidate",
                },
                {
                  uri: eventUri,
                  level: 2,
                  score: 0.5,
                  category: "events",
                  abstract: "lower eligible candidate",
                },
              ],
              skills: [],
            },
          });
          return;
        }
        writeJson(res, { status: "ok", result: { memories: [], skills: [] } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
        const uri = url.searchParams.get("uri");
        contentReads.push({ uri, raw: url.searchParams.get("raw") });
        if (uri === archivedUri) {
          writeJson(res, {
            status: "ok",
            result: "old guidance\n\n<!-- MEMORY_FIELDS\n{\"status\":\"archived\"}\n-->",
          });
        } else {
          writeJson(res, { status: "ok", result: "lower eligible recalled detail" });
        }
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "find the eligible memory", session_id: "codex:experience-backfill" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_USER: "zeus",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "1",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );
      const context = JSON.parse(result.stdout.trim()).hookSpecificOutput.additionalContext;
      assert.match(context, /lower eligible recalled detail/);
      assert.doesNotMatch(context, /old guidance|archived top candidate/);
    });

    assert.deepEqual(contentReads, [
      { uri: archivedUri, raw: "true" },
      { uri: eventUri, raw: null },
    ]);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-recall applies the relevance compressor to server recall entries", async () => {
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
  });

  assert.deepEqual(result.output, {});
  assert.equal(result.compressorCalls, 1);
  assert.equal(result.requestBody.max_chars, 18000);
});

test("auto-recall tries the next compressor model after a runtime failure", async () => {
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
    compressorOutput: [
      "OpenViking memory digest:",
      "- Use Vim (viking://user/zeus/memories/preferences/editor.md)",
    ].join("\n"),
    extraEnv: { FAKE_CODEX_FAIL_MODEL: "gpt-5.3-codex-spark" },
  });

  assert.match(result.output.hookSpecificOutput.additionalContext, /Use Vim/);
  assert.equal(result.compressorCalls, 2);
  assert.deepEqual(result.compressorModels, ["gpt-5.3-codex-spark", "gpt-5.6-luna"]);
  assert.equal(result.cachedProfile.model, "gpt-5.6-luna");
  assert.equal(result.cachedProfile.enabled, true);
  assert.ok(result.elapsedMs < 2000, `immediate fallback took ${result.elapsedMs}ms`);
});

test("auto-recall promotes a successful attempt-zero profile after prior failures exclude primary", async () => {
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
    compressorOutput: [
      "OpenViking memory digest:",
      "- Use Vim (viking://user/zeus/memories/preferences/editor.md)",
    ].join("\n"),
    seedFailedModels: ["gpt-5.3-codex-spark"],
  });

  assert.deepEqual(result.compressorModels, ["gpt-5.6-luna"]);
  assert.equal(result.cachedProfile.enabled, true);
  assert.equal(result.cachedProfile.model, "gpt-5.6-luna");
  assert.equal(result.cachedProfile.thinking, "low");
});

test("auto-recall fails closed when every compressor model fails", async () => {
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
  });

  assert.deepEqual(result.output, {});
  assert.equal(result.compressorCalls, 2);
  assert.deepEqual(result.cachedProfile.failedModels, [
    "gpt-5.3-codex-spark",
    "gpt-5.6-luna",
  ]);
});

test("compressor retries share one total timeout budget", async () => {
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
      FAKE_CODEX_HANG: "1",
      OPENVIKING_RECALL_COMPRESS_TIMEOUT_MS: "1000",
    },
  });

  assert.deepEqual(result.output, {});
  assert.equal(result.compressorCalls, 2);
  assert.ok(result.elapsedMs < 1800, `shared timeout budget took ${result.elapsedMs}ms`);
});

test("auto-recall fails closed when compressor spawn throws synchronously", async () => {
  const preloadDir = await mkdtemp(join(tmpdir(), "ov-sync-spawn-failure-"));
  const preloadPath = join(preloadDir, "throw-codex-spawn.cjs");
  await writeFile(preloadPath, `
const childProcess = require("node:child_process");
const { syncBuiltinESMExports } = require("node:module");
const originalSpawn = childProcess.spawn;
childProcess.spawn = function patchedSpawn(command, ...args) {
  if (command === "codex") {
    throw Object.assign(new Error("spawn EPERM"), { code: "EPERM" });
  }
  return originalSpawn.call(this, command, ...args);
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
      extraEnv: { NODE_OPTIONS: `--require=${preloadPath}` },
    });

    assert.deepEqual(result.output, {});
    assert.equal(result.compressorCalls, 0);
  } finally {
    await rm(preloadDir, { recursive: true, force: true });
  }
});

test("auto-recall keeps deterministic recall when compression is explicitly disabled", async () => {
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
    extraEnv: { OPENVIKING_RECALL_COMPRESS_MODEL: "off" },
  });

  assert.match(result.output.hookSpecificOutput.additionalContext, /Use Vim/);
  assert.doesNotMatch(result.output.hookSpecificOutput.additionalContext, /<memory_group>/);
  assert.equal(result.compressorCalls, 0);
});

test("auto-recall expands configured user in memory search target", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-user-target-"));
  const requests = [];

  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        const body = await readRequestBody(req);
        if (body.mode === "context") {
          // Exercise the legacy per-scope sweep below.
          writeStatusJson(res, 400, {
            status: "error",
            error: "Extra inputs are not permitted: mode",
          });
          return;
        }
        requests.push({ path: url.pathname, body });
        if (body.target_uri === "viking://user/zeus/memories") {
          writeJson(res, {
            status: "ok",
            result: {
              memories: [{
                uri: "viking://user/zeus/memories/entities/project/example.md",
                level: 2,
                score: 0.9,
                category: "entities",
                abstract: "configured user memory",
              }],
              skills: [],
            },
          });
          return;
        }
        writeJson(res, { status: "ok", result: { memories: [], skills: [] } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
        writeJson(res, { status: "ok", result: "configured user recalled detail" });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "please use configured user memory", session_id: "codex:456" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_USER: "zeus",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "1",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      assert.match(
        output.hookSpecificOutput.additionalContext,
        /configured user recalled detail/,
      );
    });

    // Memory and skill searches run in parallel; arrival order is not guaranteed.
    const memorySearch = requests.find(
      (request) => request.body.target_uri === "viking://user/zeus/memories",
    );
    assert.ok(memorySearch, "expected a memories search request");
    assert.equal(memorySearch.body.session_id, "cx-codex_456");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("auto-recall preserves explicit default user memory target", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-default-user-"));
  const requests = [];

  try {
    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        writeJson(res, { status: "ok", result: { ok: true } });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/v1/search/search") {
        const body = await readRequestBody(req);
        if (body.mode === "context") {
          // Exercise the legacy per-scope sweep below.
          writeStatusJson(res, 400, {
            status: "error",
            error: "Extra inputs are not permitted: mode",
          });
          return;
        }
        requests.push({ path: url.pathname, body });
        if (body.target_uri === "viking://user/default/memories") {
          writeJson(res, {
            status: "ok",
            result: {
              memories: [{
                uri: "viking://user/default/memories/preferences/default-food.md",
                level: 2,
                score: 0.9,
                category: "preferences",
                abstract: "explicit default user memory",
              }],
              skills: [],
            },
          });
          return;
        }
        writeJson(res, { status: "ok", result: { memories: [], skills: [] } });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/v1/content/read") {
        writeJson(res, { status: "ok", result: "explicit default user recalled detail" });
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: "not found" }));
    }, async (baseUrl) => {
      const result = await runAutoRecall(
        { prompt: "please use default user memory", session_id: "codex:789" },
        {
          OPENVIKING_AUTO_RECALL: "1",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_USER: "default",
          OPENVIKING_RECALL_COMPRESS: "0",
          OPENVIKING_RECALL_LIMIT: "1",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      );

      const output = JSON.parse(result.stdout.trim());
      assert.match(
        output.hookSpecificOutput.additionalContext,
        /explicit default user recalled detail/,
      );
    });

    // Memory and skill searches run in parallel; arrival order is not guaranteed.
    const memorySearch = requests.find(
      (request) => request.body.target_uri === "viking://user/default/memories",
    );
    assert.ok(memorySearch, "expected a memories search request");
    assert.equal(memorySearch.body.session_id, "cx-codex_789");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
