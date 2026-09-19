import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PLUGIN_DIR = dirname(SCRIPT_DIR);
const SESSION_ID = "recall-branch-session";

function writeJson(res, statusCode, value) {
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

async function withMockOpenViking(handler, fn) {
  const server = http.createServer((req, res) => {
    Promise.resolve(handler(req, res)).catch((err) => {
      writeJson(res, 500, { status: "error", error: String(err?.stack || err) });
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

function runAutoRecall(stdin, env) {
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
    child.stdin.end(stdin);
  });
}

function hookEnv(root, baseUrl, extra = {}) {
  return {
    HOME: root,
    TMPDIR: root,
    OPENVIKING_HOME: join(root, ".openviking"),
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_AUTO_RECALL: "1",
    OPENVIKING_RECALL_COMPRESS: "off",
    OPENVIKING_TIMEOUT_MS: "5000",
    OPENVIKING_URL: baseUrl,
    ...extra,
  };
}

async function readRecallState(root) {
  const raw = await readFile(join(root, ".openviking", "state", "last-recall.json"), "utf-8");
  return JSON.parse(raw);
}

function injectedContext(stdout) {
  const out = JSON.parse(stdout.trim());
  assert.equal(out.decision, "approve");
  return out.hookSpecificOutput?.additionalContext ?? null;
}

/**
 * One turn against a stubbed server: `routes` maps "METHOD /path" to a handler
 * returning the JSON body, so each test only spells out the endpoints its own
 * branch reaches.
 */
async function recallTurn({ routes = {}, env = {}, input = {} } = {}) {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-recall-"));
  try {
    return await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      const route = routes[`${req.method} ${url.pathname}`];
      if (!route) {
        writeJson(res, 404, { status: "error", error: { message: "no route" } });
        return;
      }
      const { status = 200, body } = await route(req);
      writeJson(res, status, body);
    }, async (baseUrl) => {
      const stdin = typeof input === "string" ? input : JSON.stringify({
        prompt: "remember the ranking pipeline please",
        session_id: SESSION_ID,
        cwd: root,
        ...input,
      });
      const run = await runAutoRecall(stdin, hookEnv(root, baseUrl, env));
      return { ...run, state: await readRecallState(root), root };
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

const HEALTHY = { "GET /health": () => ({ body: { status: "ok", result: { healthy: true } } }) };
const NO_SERVER_ASSEMBLY = {
  ...HEALTHY,
  "POST /api/v1/search/search": () => ({ status: 503, body: { status: "error", error: { message: "unavailable" } } }),
  "POST /api/v1/search/recall": () => ({ status: 404, body: { status: "error", error: { message: "gone" } } }),
};

function memory(uri, score, abstract) {
  return { uri, score, abstract, level: 1, category: "entities" };
}

test("unparseable stdin is recorded as bad_stdin", async () => {
  const { state } = await recallTurn({ routes: HEALTHY, input: "{not json" });

  assert.equal(state.reason, "bad_stdin");
  assert.equal(state.count, 0);
});

test("a disabled recall switch is recorded as disabled", async () => {
  const { stdout, state } = await recallTurn({
    routes: HEALTHY,
    env: { OPENVIKING_AUTO_RECALL: "0" },
  });

  assert.equal(state.reason, "disabled");
  assert.equal(state.count, 0);
  assert.equal(injectedContext(stdout), null);
});

test("a bypassed session is recorded as bypass", async () => {
  const { state } = await recallTurn({
    routes: HEALTHY,
    env: { OPENVIKING_BYPASS_SESSION: "1" },
  });

  assert.equal(state.reason, "bypass");
  assert.equal(state.count, 0);
  assert.equal(state.cc_session_id, SESSION_ID);
});

test("a prompt below the minimum length is recorded as short_query", async () => {
  const { state } = await recallTurn({ routes: HEALTHY, input: { prompt: "hi" } });

  assert.equal(state.reason, "short_query");
  assert.equal(state.count, 0);
  assert.equal(state.cc_session_id, SESSION_ID);
});

test("an unreachable server is recorded as offline", async () => {
  const { state } = await recallTurn({
    routes: { "GET /health": () => ({ status: 503, body: { status: "error", error: { message: "down" } } }) },
  });

  assert.equal(state.reason, "offline");
  assert.equal(state.count, 0);
});

test("an empty server-assembled context is recorded as no_results", async () => {
  const { stdout, state } = await recallTurn({
    routes: {
      ...HEALTHY,
      "POST /api/v1/search/search": () => ({ body: { status: "ok", result: { rendered: "", entries: [], stats: {} } } }),
    },
  });

  assert.equal(state.reason, "no_results");
  assert.equal(state.count, 0);
  assert.equal(injectedContext(stdout), null);
});

test("a server-assembled block counts its URIs and its own token cost", async () => {
  const rendered = [
    "- viking://user/default/memories/entities/plugin.md — the shared recall core",
    "- viking://user/default/memories/events/rollout.md — shipped last week",
  ].join("\n");
  const { stdout, state } = await recallTurn({
    routes: {
      ...HEALTHY,
      "POST /api/v1/search/search": () => ({
        body: { status: "ok", result: { rendered, entries: [], stats: { used_tokens: 40 } } },
      }),
    },
  });

  const block = injectedContext(stdout);
  assert.match(block, /^<openviking-context>/);
  assert.ok(block.includes(rendered));
  assert.equal(state.reason, "ok");
  assert.equal(state.count, 2);
  assert.equal(state.content_items, 1);
  assert.equal(state.hint_items, 0);
  assert.equal(state.tokens_used, Math.ceil(block.length / 4));
  assert.equal(state.tokens_budget, 2000);
  assert.equal(state.cc_session_id, SESSION_ID);
});

test("no search hits at all are recorded as no_results", async () => {
  const { stdout, state } = await recallTurn({
    routes: {
      ...NO_SERVER_ASSEMBLY,
      "POST /api/v1/search/find": () => ({ body: { status: "ok", result: { memories: [], skills: [] } } }),
    },
  });

  assert.equal(state.reason, "no_results");
  assert.equal(state.count, 0);
  assert.equal(injectedContext(stdout), null);
});

test("hits that all fall under the score threshold are recorded as filtered_out", async () => {
  const { stdout, state } = await recallTurn({
    routes: {
      ...NO_SERVER_ASSEMBLY,
      "POST /api/v1/search/find": () => ({
        body: {
          status: "ok",
          result: {
            memories: [memory("viking://user/default/memories/entities/a.md", 0.1, "barely related")],
            skills: [],
          },
        },
      }),
    },
  });

  assert.equal(state.reason, "filtered_out");
  assert.equal(state.count, 0);
  assert.equal(injectedContext(stdout), null);
});

test("the client-ranked fallback degrades past the budget to URI hints", async () => {
  const first = "x".repeat(500);
  const second = "y".repeat(500);
  const { stdout, state } = await recallTurn({
    env: { OPENVIKING_RECALL_TOKEN_BUDGET: "200" },
    routes: {
      ...NO_SERVER_ASSEMBLY,
      "POST /api/v1/search/find": () => ({
        body: {
          status: "ok",
          result: {
            memories: [
              memory("viking://user/default/memories/entities/a.md", 0.9, first),
              memory("viking://user/default/memories/entities/b.md", 0.9, second),
            ],
            skills: [],
          },
        },
      }),
    },
  });

  const block = injectedContext(stdout);
  assert.ok(block.includes(`- [memory 90%] ${first}`));
  assert.ok(block.includes("- [memory 90%] viking://user/default/memories/entities/b.md"));
  assert.equal(state.reason, "ok");
  assert.equal(state.count, 2);
  assert.equal(state.content_items, 1);
  assert.equal(state.hint_items, 1);
  assert.equal(state.tokens_used, Math.ceil(`- [memory 90%] ${first}`.length / 4));
  assert.equal(state.tokens_budget, 200);
});

// The ranking pipeline lives in the shared recall core and the transcript walk
// in the shared capture utilities. A second copy of either here is what this
// plugin spent a release drifting away from.
const SHARED_ONLY_SYMBOLS = [
  "buildQueryProfile",
  "lexicalOverlapBoost",
  "rankItem",
  "extractAllTurns",
];

async function ownSourceFiles(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "shared" || entry.name.startsWith(".")) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...await ownSourceFiles(full));
    else if (entry.name.endsWith(".mjs")) out.push(full);
  }
  return out;
}

test("the plugin keeps no ranking or transcript copy of its own", async () => {
  const pattern = new RegExp(`function\\s+(${SHARED_ONLY_SYMBOLS.join("|")})\\b`);
  const hits = [];
  for (const file of await ownSourceFiles(PLUGIN_DIR)) {
    if (pattern.test(await readFile(file, "utf-8"))) hits.push(file.slice(PLUGIN_DIR.length + 1));
  }

  assert.deepEqual(hits, []);
});
