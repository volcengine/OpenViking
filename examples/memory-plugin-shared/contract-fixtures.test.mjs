import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { sendSessionMessages } from "./lib/batch-send.mjs";
import { buildUserAgent } from "./lib/credentials.mjs";
import { buildOvHeaders } from "./lib/ov-http.mjs";
import { buildContextSearchBody } from "./lib/recall-core.mjs";
import { isRetryableFailure } from "./lib/retryable.mjs";

/**
 * The wire behaviour every plugin shares lives in language-neutral fixtures
 * under `testing/contract/`, so the JavaScript library and the Hermes plugin
 * (Python) are held to the same data. This file is the JavaScript half; the
 * fixtures' README says what each one pins and what it leaves out.
 */

function loadFixture(name) {
  return JSON.parse(readFileSync(new URL(`./testing/contract/${name}`, import.meta.url), "utf-8"));
}

function fill(text, placeholders = {}) {
  return String(text).replace(/\{(\w+)\}/g, (match, key) => (key in placeholders ? placeholders[key] : match));
}

function fillHeaders(headers, placeholders) {
  return Object.fromEntries(Object.entries(headers).map(([key, value]) => [key, fill(value, placeholders)]));
}

// --- headers.json ---------------------------------------------------------

const headersFixture = loadFixture("headers.json");

function headerArgs(input, placeholders) {
  const cfg = {
    apiKey: input.api_key,
    account: input.account,
    user: input.user,
    sendIdentityHeaders: input.trusted_identity,
    userAgent: input.user_agent === undefined ? undefined : fill(input.user_agent, placeholders),
  };
  const options = { actorPeerId: input.actor_peer_id };
  if ("identity_override" in input) options.identityHeaders = input.identity_override;
  return [cfg, options];
}

for (const { name, input, expected, placeholders } of headersFixture.cases) {
  test(`headers: ${name}`, () => {
    const headers = buildOvHeaders(...headerArgs(input, placeholders));
    assert.deepEqual(headers, fillHeaders(expected, placeholders));
    for (const forbidden of headersFixture.never_present) {
      assert.equal(
        Object.keys(headers).some((key) => key.toLowerCase() === forbidden.toLowerCase()),
        false,
        `${forbidden} must not be sent`,
      );
    }
  });
}

for (const { plugin, version, expected } of headersFixture.user_agent.cases) {
  test(`headers: user agent for ${plugin}@${version || "(no version)"}`, () => {
    assert.equal(buildUserAgent(plugin, version), expected);
    assert.equal(fill(headersFixture.user_agent.template, { plugin, version: version || "0.0.0" }), expected);
    assert.equal(buildOvHeaders({ userAgent: expected })["User-Agent"], expected);
  });
}

// --- retryable.json -------------------------------------------------------

const retryableFixture = loadFixture("retryable.json");

for (const { name, result, retryable } of retryableFixture.cases) {
  test(`retryable: ${name}`, () => {
    assert.equal(isRetryableFailure(result), retryable);
  });
}

const batchContract = retryableFixture.batch_endpoint;

function pathFor(template, sessionId) {
  return fill(template, { session_id: encodeURIComponent(sessionId) });
}

test("batch endpoint: chunks at the fixture's batch limit", async () => {
  const sizes = [];
  const messages = Array.from({ length: batchContract.batch_limit + 1 }, (_, i) => ({ role: "user", content: `m${i}` }));
  await sendSessionMessages(async (path, init) => {
    assert.equal(path, pathFor(batchContract.batch_path, "s-1"));
    sizes.push(JSON.parse(init.body).messages.length);
    return { ok: true, status: 200, result: {} };
  }, "s-1", messages);
  assert.deepEqual(sizes, [batchContract.batch_limit, 1]);
});

for (const status of batchContract.fallback_statuses) {
  test(`batch endpoint: ${status} falls back to the serial path`, async () => {
    const paths = [];
    const res = await sendSessionMessages(async (path) => {
      paths.push(path);
      return path.endsWith("/batch") ? { ok: false, status } : { ok: true, status: 200, result: {} };
    }, "s-1", [{ role: "user", content: "a" }, { role: "user", content: "b" }]);
    assert.deepEqual(paths, [
      pathFor(batchContract.batch_path, "s-1"),
      pathFor(batchContract.serial_path, "s-1"),
      pathFor(batchContract.serial_path, "s-1"),
    ]);
    assert.equal(res.usedBatch, false);
    assert.equal(res.sent, 2);
    assert.equal(res.failed, 0);
  });
}

for (const status of batchContract.no_fallback_statuses) {
  test(`batch endpoint: ${status} does not fall back`, async () => {
    const paths = [];
    const res = await sendSessionMessages(async (path) => {
      paths.push(path);
      return { ok: false, status };
    }, "s-1", [{ role: "user", content: "a" }]);
    assert.deepEqual(paths, [pathFor(batchContract.batch_path, "s-1")]);
    assert.equal(res.usedBatch, true);
    assert.equal(res.failed, 1);
    assert.equal(res.retryable, isRetryableFailure({ ok: false, status }));
  });
}

test("batch endpoint: fallback statuses are not retryable failures", () => {
  for (const status of batchContract.fallback_statuses) {
    assert.equal(isRetryableFailure({ ok: false, status }), false);
  }
});

// --- recall-request.json --------------------------------------------------

const recallFixture = loadFixture("recall-request.json");

function recallArgs(input) {
  const cfg = {};
  if ("recall_limit" in input) {
    cfg.recallLimit = input.recall_limit;
    cfg.recallLimitConfigured = true;
  }
  if ("recall_max_tokens" in input) {
    cfg.recallMaxTokens = input.recall_max_tokens;
    cfg.recallMaxTokensConfigured = true;
  }
  if ("score_threshold" in input) cfg.scoreThreshold = input.score_threshold;
  if ("peer_scope" in input) cfg.recallPeerScope = input.peer_scope;
  if ("dedup_turns" in input) cfg.recallDedupTurns = input.dedup_turns;
  return [cfg, { sessionId: input.session_id, excludeUris: input.exclude_uris }];
}

for (const { name, input, expected } of recallFixture.cases) {
  test(`recall request: ${name}`, () => {
    const body = buildContextSearchBody(...recallArgs(input));
    assert.equal(typeof body.query, "string");
    const common = Object.fromEntries(
      recallFixture.common_fields.filter((field) => field in body).map((field) => [field, body[field]]),
    );
    assert.deepEqual(common, expected);
  });
}

test("recall request: no case expects a field outside the common set", () => {
  for (const { expected } of recallFixture.cases) {
    for (const field of Object.keys(expected)) assert.ok(recallFixture.common_fields.includes(field), field);
  }
});
