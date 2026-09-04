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
  postRecall,
  readPeerScopeDowngrade,
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

  // The server pipeline is serial: the 5s expansion fuse, retrieval and body
  // reads all run before the 30s rewrite fuse even starts, so covering the
  // rewrite alone still aborts requests that stayed inside every server budget.
  assert.ok(
    timeouts[0] > 35000,
    `deadline must outlast both server fuses plus the work between, got ${timeouts[0]}`,
  );
  assert.equal(timeouts[1], undefined);
  assert.equal(
    contextRequestTimeoutMs({ ...cfg, recallContextTimeoutMs: 50000 }, { rewrite: true }),
    50000,
  );
});

test("the deadline follows the stages the request actually asks for", async () => {
  const cfg = { timeoutMs: 5000 };

  // A bare retrieval spends no server fuse, so the caller keeps its own budget.
  assert.equal(contextRequestTimeoutMs(cfg, {}), undefined);
  assert.equal(contextRequestTimeoutMs(cfg, { session_id: "s", query_expansion: "off" }), undefined);

  // A session engages query expansion, which the server defaults to "auto".
  // Without headroom a 5s caller aborts a request the expansion fuse alone may
  // consume, then falls back to the path with no dedup and no expansion.
  const withSession = contextRequestTimeoutMs(cfg, { session_id: "s" });
  assert.ok(withSession > 5000, `expansion needs headroom over the caller budget, got ${withSession}`);

  // A digest costs the rewrite fuse on top of everything above it.
  const withRewrite = contextRequestTimeoutMs(cfg, { session_id: "s", rewrite: true });
  assert.ok(withRewrite > withSession, "a digest must outlast a plain expanded request");
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

function recordingFetch(responses) {
  const sent = [];
  const queue = [...responses];
  return {
    sent,
    fetchJSON: async (_path, init) => {
      sent.push(JSON.parse(init.body));
      return queue.shift() ?? { ok: true, status: 200, result: {} };
    },
  };
}

test("postRecall drops peer_scope only when the server rejects the field itself", async () => {
  const memoPath = await tempPath("peer-scope.json");
  const { sent, fetchJSON } = recordingFetch([
    { ok: false, status: 422, error: "unexpected keyword argument 'peer_scope'" },
    { ok: true, status: 200, result: {} },
  ]);

  const res = await postRecall(fetchJSON, { query: "q", peer_scope: "actor" }, { peerScopeMemoPath: memoPath });

  assert.equal(res.ok, true);
  assert.equal(sent.length, 2);
  assert.equal(sent[0].peer_scope, "actor");
  assert.equal(sent[1].peer_scope, undefined);

  const memo = await readPeerScopeDowngrade(memoPath);
  assert.equal(memo.scope, "actor");
  assert.equal(memo.status, 422);
});

test("postRecall keeps peer_scope when a 400 is about something else", async () => {
  const memoPath = await tempPath("peer-scope.json");
  const { sent, fetchJSON } = recordingFetch([
    { ok: false, status: 400, error: "query must not be empty" },
  ]);

  const res = await postRecall(fetchJSON, { query: "", peer_scope: "actor" }, { peerScopeMemoPath: memoPath });

  assert.equal(res.ok, false);
  assert.equal(sent.length, 1, "an unrelated 400 must not be retried at a wider scope");
  assert.equal(await readPeerScopeDowngrade(memoPath), null);
});

test("a remembered downgrade skips the rejected request on later turns", async () => {
  const memoPath = await tempPath("peer-scope.json");
  const first = recordingFetch([
    { ok: false, status: 400, error: "extra fields not permitted" },
    { ok: true, status: 200, result: {} },
  ]);
  await postRecall(first.fetchJSON, { query: "q", peer_scope: "actor" }, { peerScopeMemoPath: memoPath });

  const second = recordingFetch([{ ok: true, status: 200, result: {} }]);
  await postRecall(second.fetchJSON, { query: "q", peer_scope: "actor" }, { peerScopeMemoPath: memoPath });

  assert.equal(second.sent.length, 1);
  assert.equal(second.sent[0].peer_scope, undefined);
});

test("a request without peer_scope is never retried", async () => {
  const memoPath = await tempPath("peer-scope.json");
  const { sent, fetchJSON } = recordingFetch([{ ok: false, status: 422, error: "extra fields not permitted" }]);

  await postRecall(fetchJSON, { query: "q" }, { peerScopeMemoPath: memoPath });

  assert.equal(sent.length, 1);
});

test("under actor scope, recall also asks the peer this workspace used before", async () => {
  const asked = [];
  const fetchJSON = async (path, init, options) => {
    asked.push(options?.actorPeerId || "");
    return {
      ok: true,
      result: {
        rendered: `<memory uri="viking://${options?.actorPeerId}/a.md">from ${options?.actorPeerId}</memory>`,
        entries: [{ uri: `viking://${options?.actorPeerId}/a.md` }],
        stats: {},
      },
    };
  };

  const block = await buildRecallBlock(fetchJSON, { recallPeerScope: "actor" }, "hello", {
    actorPeerId: "github.com-o-r",
    legacyPeerId: "-Users-x-src-r",
    legacyCachePath: await tempPath("context-face.json"),
  });

  assert.deepEqual(asked, ["github.com-o-r", "-Users-x-src-r"]);
  assert.match(block, /from github\.com-o-r/);
  assert.match(block, /from -Users-x-src-r/);
});

test("under the default scope the server's own sweep covers it, so nothing extra is sent", async () => {
  const asked = [];
  const fetchJSON = async (_path, _init, options) => {
    asked.push(options?.actorPeerId || "");
    return { ok: true, result: { rendered: '<memory uri="viking://a">body</memory>', entries: [{ uri: "viking://a" }] } };
  };

  await buildRecallBlock(fetchJSON, { recallPeerScope: "all" }, "hello", {
    actorPeerId: "github.com-o-r",
    legacyPeerId: "-Users-x-src-r",
    legacyCachePath: await tempPath("context-face.json"),
  });

  assert.deepEqual(asked, ["github.com-o-r"]);
});

test("a legacy id equal to the effective one is not asked twice", async () => {
  const asked = [];
  const fetchJSON = async (_path, _init, options) => {
    asked.push(options?.actorPeerId || "");
    return { ok: true, result: { rendered: '<memory uri="viking://a">body</memory>', entries: [{ uri: "viking://a" }] } };
  };

  await buildRecallBlock(fetchJSON, { recallPeerScope: "actor" }, "hello", {
    actorPeerId: "same",
    legacyPeerId: "same",
    legacyCachePath: await tempPath("context-face.json"),
  });

  assert.deepEqual(asked, ["same"]);
});
