import test from "node:test";
import assert from "node:assert/strict";
import { buildRecallBlock, buildRecallEndpointBody, postRecall } from "./lib/recall-core.mjs";

test("buildRecallEndpointBody maps quotas and max chars", () => {
  const body = buildRecallEndpointBody({
    recallLimit: 6,
    recallMaxContentChars: 500,
    scoreThreshold: 0.35,
  });
  assert.deepEqual(body.quotas, {
    events: 6,
    entities: 6,
    preferences: 3,
    experiences: 0,
  });
  assert.equal(body.max_chars, 3000);
  assert.equal(body.min_score, 0.35);
  assert.equal(body.render, true);
  assert.equal(body.peer_scope, undefined);
});

test("buildRecallEndpointBody only sends actor peer scope when explicitly configured", () => {
  assert.equal(buildRecallEndpointBody({ recallPeerScope: "all" }).peer_scope, undefined);
  assert.equal(buildRecallEndpointBody({ recallPeerScope: "actor" }).peer_scope, "actor");
});

test("buildRecallBlock uses recall endpoint render when available", async () => {
  const calls = [];
  const block = await buildRecallBlock(async (path, init) => {
    calls.push({ path, body: init?.body ? JSON.parse(init.body) : null });
    return { ok: true, result: { rendered: "- [memory 90%] viking://user/default/memories/a.md" } };
  }, {
    recallLimit: 2,
    recallMaxContentChars: 500,
    scoreThreshold: 0.35,
  }, "hello world");

  assert.equal(calls[0].path, "/api/v1/search/recall");
  assert.equal(calls[0].body.quotas.events, 2);
  assert.match(block, /^<openviking-context>/);
  assert.match(block, /Relevant memory from OpenViking/);
  assert.match(block, /viking:\/\/user\/default\/memories\/a\.md/);
  assert.match(block, /<\/openviking-context>$/);
});

test("buildRecallBlock falls back to find and keeps first item over budget", async () => {
  const calls = [];
  const longAbstract = "x".repeat(1200);
  const fetchJSON = async (path) => {
    calls.push(path);
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
            abstract: longAbstract,
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
  }, "what happened yesterday");

  assert.ok(calls.includes("/api/v1/search/recall"));
  assert.ok(calls.includes("/api/v1/search/find"));
  assert.match(block, /^<openviking-context>/);
  assert.match(block, /\[memory 90%\]/);
  assert.match(block, /x{100}/);
});

test("postRecall downgrades peer_scope on 400 and 422", async () => {
  for (const status of [400, 422]) {
    const bodies = [];
    const logs = [];
    const res = await postRecall(async (path, init, opts) => {
      bodies.push({ path, body: JSON.parse(init.body), opts });
      return bodies.length === 1
        ? { ok: false, status }
        : { ok: true, status: 200, result: { rendered: "ok" } };
    }, {
      query: "hello",
      peer_scope: "actor",
    }, {
      actorPeerId: "peer-a",
      log: (stage, data) => logs.push({ stage, data }),
    });

    assert.equal(res.ok, true);
    assert.equal(bodies.length, 2);
    assert.equal(bodies[0].body.peer_scope, "actor");
    assert.equal(bodies[1].body.peer_scope, undefined);
    assert.equal(bodies[0].opts.actorPeerId, "peer-a");
    assert.deepEqual(logs, [{ stage: "recall_peer_scope_downgrade", data: { status } }]);
  }
});

test("postRecall does not retry default body or server errors", async () => {
  const noScopeBodies = [];
  const noScope = await postRecall(async (path, init) => {
    noScopeBodies.push(JSON.parse(init.body));
    return { ok: false, status: 400 };
  }, { query: "hello" });
  assert.equal(noScope.ok, false);
  assert.equal(noScopeBodies.length, 1);

  const serverErrorBodies = [];
  const serverError = await postRecall(async (path, init) => {
    serverErrorBodies.push(JSON.parse(init.body));
    return { ok: false, status: 500 };
  }, { query: "hello", peer_scope: "actor" });
  assert.equal(serverError.ok, false);
  assert.equal(serverErrorBodies.length, 1);
});
