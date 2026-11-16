import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildContextSearchBody,
  buildRecallEndpointBody,
  buildRecallBlock,
  buildRecallBlockDetailed,
  contextRequestTimeoutMs,
  estimateTokens,
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

test("context requests send an event decay window only when one is configured", () => {
  assert.equal(buildContextSearchBody({}).events_time_decay_protection, undefined);
  assert.equal(buildContextSearchBody({ recallEventsDecayProtection: " " }).events_time_decay_protection, undefined);
  assert.equal(buildContextSearchBody({ recallEventsDecayProtection: "7d" }).events_time_decay_protection, "7d");
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
  // Recalled history is labelled as internal context, not external evidence.
  assert.match(block, /this user's own saved history/);
  assert.match(block, /not an external source or established fact/);
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

test("explicit recall context timeout applies without rewrite or expansion", () => {
  const timeout = contextRequestTimeoutMs(
    { recallContextTimeoutMs: 25000, timeoutMs: 15000 },
    { session_id: "s1", query_expansion: "off" },
  );

  assert.equal(timeout, 25000);
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

function fallbackFetch(memories) {
  return async (path) => {
    if (path === "/api/v1/search/search") return { ok: false, status: 503 };
    if (path === "/api/v1/search/recall") return { ok: false, status: 404 };
    if (path === "/api/v1/search/find") return { ok: true, result: { memories, skills: [] } };
    return { ok: false, status: 404 };
  };
}

test("a server-assembled block reports itself as one budgeted unit", async () => {
  const detailed = await buildRecallBlockDetailed(async () => ({
    ok: true,
    result: { rendered: "- viking://a/b.md — body", entries: [{ uri: "viking://a/b.md" }], stats: {} },
  }), {}, "hello", { legacyCachePath: await tempPath("context-face.json") });

  assert.equal(detailed.stage, "server_assembled");
  assert.equal(detailed.contentCount, 1);
  assert.equal(detailed.hintCount, 0);
  assert.equal(detailed.budgetUsed, estimateTokens(detailed.block));
});

test("the ranked fallback reports what fitted the budget and what degraded", async () => {
  const detailed = await buildRecallBlockDetailed(fallbackFetch([
    { uri: "viking://user/default/memories/entities/a.md", score: 0.9, abstract: "x".repeat(500), level: 1 },
    { uri: "viking://user/default/memories/entities/b.md", score: 0.9, abstract: "y".repeat(500), level: 1 },
  ]), {
    recallTokenBudget: 200,
    recallMaxContentChars: 500,
    recallPreferAbstract: true,
  }, "what happened", { legacyCachePath: await tempPath("context-face.json") });

  assert.equal(detailed.stage, "ranked");
  const visibleContent = ["x".repeat(500), "y".repeat(500)].filter((text) => detailed.block.includes(text));
  const citedUris = [...detailed.block.matchAll(/viking:\/\/[^\s<>]+/g)].map((match) => match[0]);
  assert.equal(detailed.contentCount, visibleContent.length);
  assert.equal(detailed.hintCount, citedUris.length - visibleContent.length);
  assert.equal(detailed.budgetUsed, estimateTokens(detailed.block));
  assert.ok(detailed.budgetUsed <= 200, "budget includes the wrapper, citations, and body");
});

test("an empty recall says whether the server had nothing or the threshold took it", async () => {
  const legacyCachePath = await tempPath("context-face.json");
  const nothing = await buildRecallBlockDetailed(fallbackFetch([]), {}, "hello", { legacyCachePath });
  const belowThreshold = await buildRecallBlockDetailed(fallbackFetch([
    { uri: "viking://user/default/memories/entities/a.md", score: 0.1, abstract: "barely related", level: 1 },
  ]), {}, "hello", { legacyCachePath: await tempPath("context-face.json") });

  assert.equal(nothing.stage, "no_results");
  assert.equal(nothing.block, "");
  assert.equal(belowThreshold.stage, "filtered_out");
  assert.equal(await buildRecallBlock(fallbackFetch([]), {}, "hello", { legacyCachePath }), null);
});

for (const source of ["context", "legacy"]) {
  test(`${source} recall keeps a server digest in client mode without recompressing`, async () => {
    const paths = [];
    let compressorCalls = 0;
    const recalled = await buildRecallBlockDetailed(async (path) => {
      paths.push(path);
      if (source === "legacy" && path === "/api/v1/search/search") return { ok: false, status: 404 };
      return { ok: true, result: {
        rendered: "RAW_CONTENT_SHOULD_NOT_APPEAR",
        digest: "- server fact viking://user/default/memories/a.md",
        entries: [{ uri: "viking://user/default/memories/a.md", text: "raw fact" }],
        stats: { rewrite: "ok" },
      } };
    }, { recallRewrite: "client", recallCompressMinInputChars: 0 }, "remember fact", {
      legacyCachePath: await tempPath("context-face.json"),
      digestCachePath: await tempPath("digest.json"),
      localCompressorAvailable: true,
      runCompressor: async () => { compressorCalls++; return "- unwanted local rewrite"; },
    });
    assert.equal(compressorCalls, 0);
    assert.match(recalled.block, /server fact/);
    assert.doesNotMatch(recalled.block, /RAW_CONTENT_SHOULD_NOT_APPEAR/);
    assert.equal(paths.length, source === "context" ? 1 : 2);
  });

  test(`${source} no_relevant neither compresses nor falls back to raw entries`, async () => {
    const paths = [];
    let compressorCalls = 0;
    const recalled = await buildRecallBlockDetailed(async (path) => {
      paths.push(path);
      if (source === "legacy" && path === "/api/v1/search/search") return { ok: false, status: 404 };
      return { ok: true, result: {
        rendered: "RAW_IRRELEVANT_CONTENT",
        entries: [{ uri: "viking://user/default/memories/a.md", text: "raw fact" }],
        stats: { rewrite: "no_relevant" },
      } };
    }, { recallRewrite: "client", recallCompressMinInputChars: 0 }, "unrelated question", {
      legacyCachePath: await tempPath("context-face.json"),
      localCompressorAvailable: true,
      runCompressor: async () => { compressorCalls++; return "- unwanted local rewrite"; },
    });
    assert.equal(compressorCalls, 0);
    assert.equal(recalled.block, "");
    assert.equal(paths.length, source === "context" ? 1 : 2);
  });
}

test("legacy recall feeds normalized entries to the shared local compressor", async () => {
  const uri = "viking://user/default/memories/editor.md";
  const prompts = [];
  const recalled = await buildRecallBlockDetailed(async (path) => {
    if (path === "/api/v1/search/search") return { ok: false, status: 404 };
    assert.equal(path, "/api/v1/search/recall");
    return { ok: true, result: {
      rendered: `LEGACY_FULL_CONTENT ${uri}`,
      entries: [{ uri, type: "preferences", mode: "summary", summary: "Vim" }],
    } };
  }, { recallRewrite: "client", recallCompressMinInputChars: 0 }, "preferred editor", {
    legacyCachePath: await tempPath("context-face.json"),
    digestCachePath: await tempPath("digest.json"),
    localCompressorAvailable: true,
    runCompressor: async (prompt) => { prompts.push(prompt); return `- Choose Vim ${uri}`; },
  });
  assert.equal(prompts.length, 1);
  assert.match(prompts[0], /LEGACY_FULL_CONTENT/);
  assert.match(recalled.block, /Choose Vim/);
  assert.match(recalled.block, /viking:\/\/user\/default\/memories\/editor\.md/);
});

test("raw fallback compresses bounded full content before injection truncation", async () => {
  const uri = "viking://user/default/memories/detail.md";
  const prompts = [];
  const recalled = await buildRecallBlockDetailed(async (path, init) => {
    if (path === "/api/v1/search/search" || path === "/api/v1/search/recall") return { ok: false, status: 404 };
    if (path === "/api/v1/search/find") {
      const body = JSON.parse(init.body);
      return { ok: true, result: { memories: body.target_uri.endsWith("/memories")
        ? [{ uri, level: 2, score: 0.9, abstract: "short summary" }] : [], skills: [] } };
    }
    if (path.startsWith("/api/v1/content/read?")) return { ok: true,
      result: `${"x".repeat(320)}DETAIL_AFTER_INJECTION_LIMIT${"y".repeat(5000)}OUTSIDE_COMPRESSION_BUDGET` };
    assert.fail(`unexpected request ${path}`);
  }, {
    recallRewrite: "client", recallCompressMinInputChars: 0,
    recallMaxContentChars: 80, recallCompressMaxInputChars: 1200, recallTokenBudget: 200,
  }, "use the full detail", {
    legacyCachePath: await tempPath("context-face.json"),
    digestCachePath: await tempPath("digest.json"),
    localCompressorAvailable: true,
    runCompressor: async (prompt) => { prompts.push(prompt); return `- retained detail ${uri}`; },
  });
  assert.equal(prompts.length, 1);
  assert.match(prompts[0], /DETAIL_AFTER_INJECTION_LIMIT/);
  assert.doesNotMatch(prompts[0], /OUTSIDE_COMPRESSION_BUDGET/);
  assert.match(recalled.block, /retained detail/);
});

for (const user of ["zeus", "default"]) {
  test(`raw session recall retries without session and keeps explicit ${user} then home alias`, async () => {
    const requests = [];
    const uri = `viking://user/${user}/memories/fact.md`;
    const recalled = await buildRecallBlockDetailed(async (path, init) => {
      const body = init?.body ? JSON.parse(init.body) : {};
      if (path === "/api/v1/search/recall" || body.mode === "context") return { ok: false, status: 404 };
      assert.equal(path, "/api/v1/search/search");
      requests.push(body);
      const hit = !body.session_id && body.target_uri === "viking://~/memories";
      return { ok: true, result: { memories: hit
        ? [{ uri, score: 0.9, level: 1, abstract: "recovered memory" }] : [], skills: [] } };
    }, { user, recallPreferAbstract: true }, "recover the fact", {
      sessionId: "cx-shared-session", legacyCachePath: await tempPath("context-face.json"),
    });
    const memoryRequests = requests.filter((body) => body.target_uri.endsWith("/memories"));
    assert.deepEqual(memoryRequests.map((body) => [body.target_uri, body.session_id || ""]), [
      [`viking://user/${user}/memories`, "cx-shared-session"],
      ["viking://~/memories", "cx-shared-session"],
      [`viking://user/${user}/memories`, ""],
      ["viking://~/memories", ""],
    ]);
    assert.match(recalled.block, /recovered memory/);
  });
}

test("raw session recall uses find when the search endpoint is unsupported", async () => {
  const requests = [];
  const recalled = await buildRecallBlockDetailed(async (path, init) => {
    const body = init?.body ? JSON.parse(init.body) : {};
    if (body.mode === "context" || path === "/api/v1/search/recall") return { ok: false, status: 404 };
    requests.push({ path, body });
    if (path === "/api/v1/search/search") return { ok: false, status: 405 };
    assert.equal(path, "/api/v1/search/find");
    return { ok: true, result: { memories: body.target_uri.endsWith("/memories")
      ? [{ uri: "viking://user/default/memories/a.md", score: 0.9, level: 1, abstract: "old server fact" }]
      : [], skills: [] } };
  }, { recallPreferAbstract: true }, "old server fact", {
    sessionId: "cx-old-server", legacyCachePath: await tempPath("context-face.json"),
  });
  assert.ok(requests.some(({ path, body }) => path.endsWith("/search") && body.session_id === "cx-old-server"));
  assert.ok(requests.some(({ path }) => path.endsWith("/find")));
  assert.match(recalled.block, /old server fact/);
});


for (const textLength of [20, 500]) {
  test(`raw fallback charges long URI citations and ${textLength}-character bodies to its whole-block budget`, async () => {
    const items = Array.from({ length: 10 }, (_, index) => ({
      uri: `viking://user/default/memories/${"long-path/".repeat(40)}${index}.md`,
      abstract: `body-${index}-${"x".repeat(textLength)}`,
      score: 0.9,
      level: 1,
    }));
    const recalled = await buildRecallBlockDetailed(fallbackFetch(items), {
      recallTokenBudget: 200, recallLimit: 10, recallMaxContentChars: 1000,
      recallPreferAbstract: true,
    }, "use remembered details", { legacyCachePath: await tempPath("context-face.json") });
    assert.ok(recalled.block, "at least one complete URI fits the budget");
    assert.ok(estimateTokens(recalled.block) <= 200, "URI-only hints cannot bypass the budget");
    assert.equal(recalled.budgetUsed, estimateTokens(recalled.block));
    const citedUris = [...recalled.block.matchAll(/viking:\/\/[^\s<>]+/g)].map((match) => match[0]);
    const visibleContent = items.filter((item) => recalled.block.includes(item.abstract));
    assert.ok(citedUris.length < items.length, "entries that do not fit must be omitted");
    assert.ok(citedUris.every((uri) => items.some((item) => item.uri === uri)), "never truncate a URI to make it fit");
    assert.ok(visibleContent.every((item) => citedUris.includes(item.uri)), "every included body retains its full source URI");
    assert.equal(recalled.contentCount, visibleContent.length);
    assert.equal(recalled.hintCount, citedUris.length - visibleContent.length);
  });
}


for (const rewrite of ["no_relevant", "off"]) {
  test(`automatic Rules survive ${rewrite} without entering the local compressor`, async () => {
    const ruleText = "Never publish credentials; exception: synthetic fixtures only.";
    const compressorInputs = [];
    let request;
    const result = await buildRecallBlockDetailed(async (path, init) => {
      request = JSON.parse(init.body);
      return { ok: true, result: {
        entries: [{ uri: "viking://user/default/memories/a.md", text: "Memory only." }],
        rendered: "Memory only.", stats: { rewrite },
        rules: { rendered: ruleText, delivery: { complete: true } },
      } };
    }, { rulesAutoInject: true, rulesMaxTokens: 256, recallRewrite: "client", recallCompressMinInputChars: 0 }, "release", {
      legacyCachePath: await tempPath("rules-context.json"),
      digestCachePath: await tempPath("rules-digest.json"),
      localCompressorAvailable: true,
      runCompressor: async (prompt) => { compressorInputs.push(prompt); return "NO_RELEVANT_MEMORY"; },
    });
    assert.deepEqual(request.rules, { task_context: "release", max_tokens: 256 });
    assert.ok(result.block.includes(ruleText));
    for (const prompt of compressorInputs) assert.ok(!prompt.includes(ruleText));
  });
}

test("Rules are off by default and incomplete delivery is never silently accepted", async () => {
  for (const rules of [undefined, { rendered: "PARTIAL_RULE", delivery: { complete: false } }]) {
    const result = await buildRecallBlockDetailed(async () => ({ ok: true, result: {
      entries: [], rendered: "", stats: {}, rules,
    } }), { rulesAutoInject: true }, "release", { legacyCachePath: await tempPath("rules.json") });
    assert.match(result.block, /Rules check is incomplete/);
    assert.doesNotMatch(result.block, /PARTIAL_RULE/);
  }
  let request;
  await buildRecallBlockDetailed(async (_path, init) => {
    request = JSON.parse(init.body);
    return { ok: true, result: { entries: [], rendered: "", stats: {} } };
  }, {}, "release", { legacyCachePath: await tempPath("rules-off.json") });
  assert.equal(request.rules, undefined);
});

test("old servers show a Rules warning without poisoning the context compatibility cache", async () => {
  const cache = await tempPath("rules-unsupported.json");
  const result = await buildRecallBlockDetailed(async (path) => {
    if (path === "/api/v1/search/search") return {
      ok: false, status: 422, error: { detail: [{ type: "extra_forbidden", loc: ["body", "rules"] }] },
    };
    return { ok: true, result: { entries: [], rendered: "legacy memory", stats: {} } };
  }, { rulesAutoInject: true }, "release", { legacyCachePath: cache });
  assert.match(result.block, /Rules check is incomplete/);
  assert.match(result.block, /legacy memory/);
  assert.equal(await isContextFaceLegacy(cache), false);
});

test("legacy peer migration does not repeat the user Rules lookup", async () => {
  const requests = [];
  await buildRecallBlockDetailed(async (_path, init) => {
    requests.push(JSON.parse(init.body));
    return { ok: true, result: {
      entries: [], rendered: "memory", stats: {},
      rules: { rendered: "Rule", delivery: { complete: true } },
    } };
  }, { rulesAutoInject: true, recallPeerScope: "actor" }, "release", {
    actorPeerId: "current", legacyPeerId: "previous", legacyCachePath: await tempPath("rules-peers.json"),
  });
  assert.equal(requests.length, 2);
  assert.ok(requests[0].rules);
  assert.equal(requests[1].rules, undefined);
});


test("local CJK compression cannot expand beyond the server's shared Rule budget", async () => {
  const result = await buildRecallBlockDetailed(async () => ({ ok: true, result: {
    entries: [{ uri: "viking://user/default/memories/a.md", text: "memory" }],
    rendered: "memory", stats: { max_tokens: 64, rewrite: "off" },
    rules: { rendered: "Keep this rule.", delivery: { complete: true } },
  } }), { rulesAutoInject: true, recallRewrite: "client", recallCompressMinInputChars: 0 }, "release", {
    legacyCachePath: await tempPath("rules-cjk.json"),
    digestCachePath: await tempPath("rules-cjk-digest.json"),
    localCompressorAvailable: true,
    runCompressor: async () => "- " + "发布配置".repeat(30),
  });
  assert.match(result.block, /Keep this rule/);
  assert.match(result.block, /memory/);
  assert.doesNotMatch(result.block, /发布配置/);
});

for (const host of ["claude-code", "codex"]) {
  test(`${host} prompt hook delivers Rules over HTTP even for a short prompt`, async (t) => {
    const { createServer } = await import("node:http");
    const { spawn } = await import("node:child_process");
    const { writeFile, rm } = await import("node:fs/promises");
    const { fileURLToPath } = await import("node:url");
    const home = await mkdtemp(join(tmpdir(), "ov-rules-hook-"));
    const requests = [];
    const rule = "Run tests before release. Exception: documentation-only changes.";
    const server = createServer(async (req, res) => {
      let body = "";
      for await (const chunk of req) body += chunk;
      res.setHeader("content-type", "application/json");
      if (req.url === "/health") return res.end(JSON.stringify({ status: "ok" }));
      requests.push({ path: req.url, body: JSON.parse(body || "{}") });
      res.end(JSON.stringify({ status: "ok", result: {
        entries: [], rendered: "", stats: { rewrite: "no_relevant", max_tokens: 1600 },
        rules: { rendered: rule, delivery: { complete: true } },
      } }));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    t.after(async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await rm(home, { recursive: true, force: true });
    });
    const config = join(home, "ovcli.conf");
    await writeFile(config, JSON.stringify({
      url: `http://127.0.0.1:${server.address().port}`,
      plugin: { rulesAutoInject: true, recallCompress: "off", minQueryLength: 100 },
    }));
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("OPENVIKING_")));
    Object.assign(env, { HOME: home, OPENVIKING_CLI_CONFIG_FILE: config,
      OPENVIKING_CONFIG_FILE: join(home, "missing.conf"), OPENVIKING_HOME: join(home, "state") });
    const hook = fileURLToPath(new URL(`../${host}-memory-plugin/scripts/auto-recall.mjs`, import.meta.url));
    const output = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [hook], { env, cwd: home, timeout: 15000 });
      let stdout = "", stderr = "";
      child.stdout.on("data", (chunk) => { stdout += chunk; });
      child.stderr.on("data", (chunk) => { stderr += chunk; });
      child.on("error", reject);
      child.on("close", (code) => code === 0 ? resolve(stdout) : reject(new Error(`hook exit ${code}: ${stderr}`)));
      child.stdin.end(JSON.stringify({ prompt: "Go", cwd: home, session_id: "rules-smoke" }));
    });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].path, "/api/v1/search/search");
    assert.equal(requests[0].body.rules.task_context, "Go");
    assert.ok(JSON.parse(output).hookSpecificOutput.additionalContext.includes(rule));
  });
}


test("legacy memory cannot crowd the incomplete Rules notice out of the budget", async () => {
  const result = await buildRecallBlockDetailed(async (path) => {
    if (path === "/api/v1/search/search") return { ok: false, status: 404 };
    return { ok: true, result: { entries: [], rendered: "legacy memory ".repeat(200), stats: {} } };
  }, { rulesAutoInject: true, recallMaxTokens: 128 }, "release", {
    legacyCachePath: await tempPath("rules-legacy-budget.json"),
  });
  assert.match(result.block, /Rules check is incomplete/);
  assert.doesNotMatch(result.block, /legacy memory/);
});
