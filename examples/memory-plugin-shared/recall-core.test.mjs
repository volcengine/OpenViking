import test from "node:test";
import assert from "node:assert/strict";
import { buildRecallBlock, buildRecallEndpointBody } from "./lib/recall-core.mjs";

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
