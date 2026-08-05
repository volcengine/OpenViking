import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildContextSearchBody,
  buildRecallEndpointBody,
  buildRecallBlock,
  contextRequestTimeoutMs,
  isContextFaceLegacy,
} from "./lib/recall-core.mjs";

async function tempPath(name) {
  const dir = await mkdtemp(join(tmpdir(), "ov-recall-"));
  return join(dir, name);
}

test("context requests preserve the configured recall width and server budget", () => {
  const body = buildContextSearchBody({
    recallLimit: 1,
    recallLimitConfigured: true,
    recallMaxTokens: 800,
    recallMaxTokensConfigured: true,
    recallCompressMaxInputChars: 18000,
  });

  assert.equal(Object.values(body.quotas).reduce((sum, quota) => sum + quota, 0), 6);
  assert.equal(body.quotas.resources, 1);
  assert.equal(body.quotas.skills, 1);
  assert.equal(body.max_tokens, 800);
  assert.equal(body.purpose, "coding");
});

test("context requests omit defaults owned by the server", () => {
  const body = buildContextSearchBody({
    recallLimit: 10,
    recallLimitConfigured: false,
    recallMaxTokens: 1600,
    recallMaxTokensConfigured: false,
    recallQueryExpansion: "auto",
    recallQueryExpansionConfigured: false,
    recallCompressMaxBullets: 6,
    recallCompressMaxBulletsConfigured: false,
  }, { sessionId: "cx-defaults" });

  assert.equal(body.limit, undefined);
  assert.equal(body.quotas, undefined);
  assert.equal(body.max_tokens, undefined);
  assert.equal(body.query_expansion, undefined);
  assert.equal(body.rewrite_max_bullets, undefined);
  assert.equal(body.purpose, "coding");
  assert.equal(body.score_threshold, 0.35);
  assert.equal(body.session_id, "cx-defaults");
  assert.equal(body.dedup_turns, 5);
});

test("coding-agent fallback recall explicitly uses the 0.35 threshold", () => {
  const body = buildRecallEndpointBody({});

  assert.equal(body.min_score, 0.35);
});

test("buildRecallBlock injects context assembled by the server", async () => {
  const calls = [];
  const legacyCachePath = await tempPath("context-face.json");
  const block = await buildRecallBlock(async (path, init) => {
    calls.push({ path, body: init?.body ? JSON.parse(init.body) : null });
    return {
      ok: true,
      result: {
        rendered: '<memory uri="viking://user/default/memories/a.md" type="events">body</memory>',
        entries: [{ uri: "viking://user/default/memories/a.md" }],
        stats: { used_tokens: 42, rewrite: "off" },
      },
    };
  }, { recallMaxTokens: 1600 }, "hello world", { legacyCachePath });

  assert.equal(calls[0].path, "/api/v1/search/search");
  assert.equal(calls[0].body.mode, "context");
  assert.equal(calls[0].body.limit, undefined);
  assert.equal(calls[0].body.max_tokens, undefined);
  assert.match(block, /^<openviking-context>/);
  assert.match(block, /viking:\/\/user\/default\/memories\/a\.md/);
  assert.match(block, /<\/openviking-context>$/);
});

test("a server-side digest outlasts the ordinary request timeout", async () => {
  const timeouts = [];
  const fetchJSON = async (path, init, options) => {
    timeouts.push(options?.timeoutMs);
    return {
      ok: true,
      result: { rendered: '<memory uri="viking://a">body</memory>', entries: [{ uri: "viking://a" }] },
    };
  };

  const cfg = { timeoutMs: 15000, recallRewrite: "server" };
  await buildRecallBlock(fetchJSON, cfg, "hello", {
    legacyCachePath: await tempPath("context-face.json"),
  });
  await buildRecallBlock(fetchJSON, { timeoutMs: 15000 }, "hello", {
    legacyCachePath: await tempPath("context-face.json"),
  });

  assert.ok(timeouts[0] > 30000, `server rewrite must outlast the 30s fuse, got ${timeouts[0]}`);
  assert.equal(timeouts[1], undefined);
  assert.equal(contextRequestTimeoutMs({ ...cfg, recallContextTimeoutMs: 45000 }, true), 45000);
});

test("buildRecallBlock prefers a cited server digest", async () => {
  const legacyCachePath = await tempPath("context-face.json");
  const block = await buildRecallBlock(async () => ({
    ok: true,
    result: {
      rendered: '<memory uri="viking://a">body</memory>',
      digest: "OpenViking memory digest:\n- fact 来源：viking://a",
      entries: [{ uri: "viking://a" }],
      stats: { rewrite: "ok" },
    },
  }), {}, "hello", { legacyCachePath });

  assert.match(block, /OpenViking memory digest:/);
  assert.doesNotMatch(block, /<memory /);
});

test("buildRecallBlock injects nothing when server compression finds no relevant memory", async () => {
  const block = await buildRecallBlock(async () => ({
    ok: true,
    result: {
      rendered: '<memory uri="viking://a">irrelevant body</memory>',
      digest: "",
      entries: [{ uri: "viking://a" }],
      stats: { rewrite: "no_relevant" },
    },
  }), { recallRewrite: "server" }, "hello", {
    legacyCachePath: await tempPath("context-face.json"),
  });

  assert.equal(block, null);
});

test("buildRecallBlock uses local compression when configured", async () => {
  const legacyCachePath = await tempPath("context-face.json");
  const digestCachePath = await tempPath("recall-digest.json");
  const block = await buildRecallBlock(async () => ({
    ok: true,
    result: {
      rendered: `<memory uri="viking://a">${"x".repeat(2000)}</memory>`,
      entries: [{ uri: "viking://a" }],
      stats: {},
    },
  }), { recallRewrite: "client" }, "hello", {
    legacyCachePath,
    digestCachePath,
    runCompressor: async () => "- local fact 来源：viking://a",
  });

  assert.match(block, /OpenViking memory digest:/);
  assert.match(block, /local fact/);
});

test("buildRecallBlock injects nothing when local compression finds no relevant memory", async () => {
  const legacyCachePath = await tempPath("context-face.json");
  const digestCachePath = await tempPath("recall-digest.json");
  const block = await buildRecallBlock(async () => ({
    ok: true,
    result: {
      rendered: `<memory uri="viking://a">${"irrelevant ".repeat(200)}</memory>`,
      entries: [{ uri: "viking://a" }],
      stats: {},
    },
  }), { recallRewrite: "client" }, "hello", {
    legacyCachePath,
    digestCachePath,
    runCompressor: async () => "NO_RELEVANT_MEMORY",
  });

  assert.equal(block, null);
});

test("buildRecallBlock remembers a server that only supports v1 recall", async () => {
  const legacyCachePath = await tempPath("context-face.json");
  const paths = [];
  const fetchJSON = async (path) => {
    paths.push(path);
    if (path === "/api/v1/search/search") {
      return { ok: false, status: 400, error: { message: "Extra inputs: mode" } };
    }
    if (path === "/api/v1/search/recall") {
      return { ok: true, result: { rendered: '<memory uri="viking://a" />' } };
    }
    return { ok: false, status: 404 };
  };

  await buildRecallBlock(fetchJSON, {}, "hello", { legacyCachePath });
  assert.deepEqual(paths, ["/api/v1/search/search", "/api/v1/search/recall"]);
  assert.equal(await isContextFaceLegacy(legacyCachePath), true);

  paths.length = 0;
  await buildRecallBlock(fetchJSON, {}, "hello again", { legacyCachePath });
  assert.deepEqual(paths, ["/api/v1/search/recall"]);
});

test("unrelated request errors do not mark the server as legacy", async () => {
  const legacyCachePath = await tempPath("context-face.json");
  const fetchJSON = async (path) => {
    if (path === "/api/v1/search/search") return { ok: false, status: 400, error: "bad query" };
    if (path === "/api/v1/search/recall") return { ok: true, result: { rendered: "ok" } };
    return { ok: false, status: 404 };
  };

  await buildRecallBlock(fetchJSON, {}, "hello", { legacyCachePath });

  assert.equal(await isContextFaceLegacy(legacyCachePath), false);
});

test("buildRecallBlock falls back to find when neither context endpoint works", async () => {
  const calls = [];
  const legacyCachePath = await tempPath("context-face.json");
  const fetchJSON = async (path) => {
    calls.push(path);
    if (path === "/api/v1/search/search") return { ok: false, status: 503 };
    if (path === "/api/v1/search/recall") return { ok: false, status: 404 };
    if (path === "/api/v1/system/status") return { ok: true, result: { user: "default" } };
    if (path.startsWith("/api/v1/fs/ls")) return { ok: true, result: [] };
    if (path === "/api/v1/search/find") {
      return {
        ok: true,
        result: {
          memories: [{
            uri: "viking://user/default/memories/events/a.md",
            score: 0.9,
            abstract: "x".repeat(1200),
            level: 1,
            category: "events",
          }],
          skills: [],
        },
      };
    }
    return { ok: false, status: 404 };
  };

  const block = await buildRecallBlock(fetchJSON, {
    recallLimit: 1,
    recallMaxContentChars: 500,
    recallTokenBudget: 20,
    scoreThreshold: 0.35,
    recallPreferAbstract: true,
  }, "what happened yesterday", { legacyCachePath });

  assert.ok(calls.includes("/api/v1/search/find"));
  assert.match(block, /^<openviking-context>/);
  assert.match(block, /\[memory 90%\]/);
});
