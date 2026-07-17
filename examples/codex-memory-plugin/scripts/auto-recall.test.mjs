import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

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
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    output_path="$1"
  fi
  shift
done
cat >/dev/null
printf 'called\\n' >> "$FAKE_CODEX_CALL_LOG"
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

async function runEndpointCompressionCase({ prompt, entry, rendered, compressorOutput, exitCode = 0 }) {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-auto-recall-endpoint-compress-"));
  let requestBody = null;
  try {
    return await withFakeCodex(compressorOutput, async ({ callLog, env }) => {
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
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS: "1",
          OPENVIKING_RECALL_TIMEOUT_MS: "10000",
          OPENVIKING_MIN_QUERY_LENGTH: "1",
          OPENVIKING_SCORE_THRESHOLD: "0",
          OPENVIKING_TIMEOUT_MS: "5000",
          OPENVIKING_URL: baseUrl,
        },
      ));
      return {
        output: JSON.parse(result.stdout.trim()),
        compressorCalls: (await readFile(callLog, "utf-8")).trim().split("\n").length,
        requestBody,
      };
    }, { exitCode });
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
}

test("auto-recall uses context-aware search with the derived OpenViking session id", async () => {
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
        if (body.target_uri === "viking://user/memories") {
          writeJson(res, {
            status: "ok",
            result: {
              memories: [{
                uri: "viking://user/zeus/memories/events/context-search.md",
                level: 2,
                score: 0.9,
                category: "events",
                abstract: "context search memory",
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
        writeJson(res, { status: "ok", result: "context-aware recalled detail" });
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
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
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
        /context-aware recalled detail/,
      );
    });

    assert.equal(requests.length, 3);
    assert.deepEqual(
      requests.map((request) => [request.body.target_uri, Boolean(request.body.session_id)]).sort(),
      [
        ["viking://user/memories", true],
        ["viking://user/skills", false],
        ["viking://user/skills", true],
      ],
    );
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
        writeStatusJson(res, 500, { status: "error", error: "should not fallback" });
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

    assert.deepEqual(requests.map((request) => request.path), ["/api/v1/search/recall"]);
    assert.equal(requests[0].body.quotas.events, 2);
    assert.equal(requests[0].body.max_chars, 6500);
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

test("auto-recall falls back to a bounded deterministic digest when endpoint compression fails", async () => {
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

  assert.match(result.output.hookSpecificOutput.additionalContext, /Use Vim/);
  assert.doesNotMatch(result.output.hookSpecificOutput.additionalContext, /<memory_group>/);
  assert.equal(result.compressorCalls, 1);
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
