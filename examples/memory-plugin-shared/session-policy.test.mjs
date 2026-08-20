import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  applySessionAutoCommitPolicy,
  buildIdleAutoCommitPolicy,
  normalizeIdleTimeoutSeconds,
  readIdleActive,
} from "./lib/session-policy.mjs";

const POLICY = {
  idle_timeout_seconds: 3600,
  pending_token_threshold: 0,
  message_count_threshold: 0,
};

async function cachePath(name) {
  return join(await mkdtemp(join(tmpdir(), `ov-session-policy-${name}-`)), "cache.json");
}

function response({ ok = true, status = 200, result = {}, error } = {}) {
  return { ok, status, result, error };
}

test("normalizes idle timeout values and builds disabled policies", () => {
  assert.equal(normalizeIdleTimeoutSeconds(undefined), 3600);
  assert.equal(normalizeIdleTimeoutSeconds("off"), 0);
  assert.equal(normalizeIdleTimeoutSeconds("FALSE"), 0);
  assert.equal(normalizeIdleTimeoutSeconds(-10), 0);
  assert.equal(normalizeIdleTimeoutSeconds(9999999), 604800);
  assert.deepEqual(buildIdleAutoCommitPolicy(3600), POLICY);
  assert.equal(buildIdleAutoCommitPolicy(0), null);
});

test("reads the idle scheduler feature flag as a tri-state", () => {
  assert.equal(readIdleActive({ auto_commit_idle_enabled: true }), true);
  assert.equal(readIdleActive({ auto_commit_idle_enabled: false }), false);
  assert.equal(readIdleActive({}), null);
});

test("fresh create applies policy and reports active idle scheduler", async () => {
  const calls = [];
  const result = await applySessionAutoCommitPolicy(
    async (path, init) => {
      calls.push({ path, body: JSON.parse(init.body) });
      return response({
        result: {
          auto_commit_policy: POLICY,
          auto_commit_idle_enabled: true,
        },
      });
    },
    "session-a",
    POLICY,
    { legacyCachePath: await cachePath("create") },
  );

  assert.deepEqual(calls, [{
    path: "/api/v1/sessions",
    body: { session_id: "session-a", auto_commit_policy: POLICY },
  }]);
  assert.equal(result.ensured, true);
  assert.equal(result.applied, true);
  assert.equal(result.idleActive, true);
  assert.equal(result.method, "create");
});

test("create without policy echo marks legacy and later sends only policy-less ensure", async () => {
  const cache = await cachePath("legacy-create");
  const calls = [];
  const fetchJSON = async (path, init) => {
    calls.push({ path, body: JSON.parse(init.body) });
    return response({ result: { session_id: "session-b" } });
  };

  const first = await applySessionAutoCommitPolicy(fetchJSON, "session-b", POLICY, {
    legacyCachePath: cache,
    now: 1000,
  });
  const second = await applySessionAutoCommitPolicy(fetchJSON, "session-b", POLICY, {
    legacyCachePath: cache,
    now: 2000,
  });

  assert.equal(first.method, "create-legacy");
  assert.equal(first.ensured, true);
  assert.equal(first.applied, false);
  assert.equal(second.method, "cached-legacy");
  assert.deepEqual(calls[1].body, { session_id: "session-b" });
  assert.ok(JSON.parse(await readFile(cache, "utf8")).legacyUntil > 2000);
});

test("a 200 whose body is not a session result never poisons the legacy cache", async () => {
  // Plugin fetch helpers turn an unparseable 200 (truncated stream, proxy
  // interstitial) into ok:true with an empty object or a raw string. Treating
  // that as "old server" would disable the backstop for the whole 6h TTL.
  for (const [label, garbled] of [
    ["empty object", {}],
    ["raw string", "<html>gateway timeout</html>"],
  ]) {
    const cache = await cachePath(`garbled-${label.replace(/\s/g, "-")}`);
    const calls = [];
    const fetchJSON = async (path, init) => {
      calls.push({ path, body: JSON.parse(init.body) });
      return response({ result: garbled });
    };

    const first = await applySessionAutoCommitPolicy(fetchJSON, "session-g", POLICY, {
      legacyCachePath: cache,
      now: 1000,
    });
    const second = await applySessionAutoCommitPolicy(fetchJSON, "session-g", POLICY, {
      legacyCachePath: cache,
      now: 2000,
    });

    assert.equal(first.method, "create-legacy", label);
    await assert.rejects(() => readFile(cache, "utf8"), `${label} must not write a cache file`);
    // The next attempt still sends the policy instead of short-circuiting.
    assert.equal(second.method, "create-legacy", label);
    assert.deepEqual(calls[1].body, { session_id: "session-g", auto_commit_policy: POLICY }, label);
  }
});

test("policy echo distinguishes inactive and unknown idle scheduler states", async () => {
  const inactive = await applySessionAutoCommitPolicy(
    async () => response({
      result: {
        auto_commit_policy: POLICY,
        auto_commit_idle_enabled: false,
      },
    }),
    "session-c",
    POLICY,
    { legacyCachePath: await cachePath("inactive") },
  );
  const unknown = await applySessionAutoCommitPolicy(
    async () => response({ result: { auto_commit_policy: POLICY } }),
    "session-d",
    POLICY,
    { legacyCachePath: await cachePath("unknown") },
  );

  assert.equal(inactive.applied, true);
  assert.equal(inactive.idleActive, false);
  assert.equal(unknown.applied, true);
  assert.equal(unknown.idleActive, null);
});

test("already-existing session is updated through PATCH", async () => {
  const calls = [];
  const result = await applySessionAutoCommitPolicy(
    async (path) => {
      calls.push(path);
      if (calls.length === 1) {
        return response({
          ok: false,
          status: 409,
          error: { code: "ALREADY_EXISTS" },
        });
      }
      return response({
        result: {
          auto_commit_policy: POLICY,
          auto_commit_idle_enabled: true,
        },
      });
    },
    "session-e",
    POLICY,
    { legacyCachePath: await cachePath("patch") },
  );

  assert.deepEqual(calls, [
    "/api/v1/sessions",
    "/api/v1/sessions/session-e/config",
  ]);
  assert.equal(result.method, "patch");
  assert.equal(result.applied, true);
  assert.equal(result.idleActive, true);
});

for (const status of [404, 405, 422]) {
  test(`PATCH ${status} is cached as a legacy server`, async () => {
    const cache = await cachePath(`patch-${status}`);
    let calls = 0;
    const result = await applySessionAutoCommitPolicy(
      async () => {
        calls += 1;
        if (calls === 1) {
          return response({
            ok: false,
            status: 409,
            error: { code: "ALREADY_EXISTS" },
          });
        }
        return response({ ok: false, status, error: { code: "UNSUPPORTED" } });
      },
      `session-${status}`,
      POLICY,
      { legacyCachePath: cache, now: 1000 },
    );

    assert.equal(result.method, "patch-legacy");
    assert.equal(result.ensured, true);
    assert.ok(JSON.parse(await readFile(cache, "utf8")).legacyUntil > 1000);
  });
}

test("create 422 retries without the policy and keeps the session ensured", async () => {
  const calls = [];
  const result = await applySessionAutoCommitPolicy(
    async (_path, init) => {
      calls.push(JSON.parse(init.body));
      if (calls.length === 1) {
        return response({ ok: false, status: 422, error: { code: "VALIDATION_ERROR" } });
      }
      return response({ result: { session_id: "session-f" } });
    },
    "session-f",
    POLICY,
    { legacyCachePath: await cachePath("create-422") },
  );

  assert.deepEqual(calls, [
    { session_id: "session-f", auto_commit_policy: POLICY },
    { session_id: "session-f" },
  ]);
  assert.equal(result.method, "create-legacy");
  assert.equal(result.ensured, true);
});

test("network failures are retryable and never poison the legacy cache", async () => {
  const cache = await cachePath("network");
  let calls = 0;
  const fetchJSON = async () => {
    calls += 1;
    if (calls === 1) {
      return response({ ok: false, status: 0, error: { message: "offline" } });
    }
    return response({
      result: {
        auto_commit_policy: POLICY,
        auto_commit_idle_enabled: true,
      },
    });
  };

  const first = await applySessionAutoCommitPolicy(fetchJSON, "session-g", POLICY, {
    legacyCachePath: cache,
  });
  const second = await applySessionAutoCommitPolicy(fetchJSON, "session-g", POLICY, {
    legacyCachePath: cache,
  });

  assert.equal(first.method, "error");
  assert.equal(first.retryable, true);
  assert.equal(second.method, "create");
  assert.equal(calls, 2);
});

test("disabled policy makes no request", async () => {
  let called = false;
  const result = await applySessionAutoCommitPolicy(
    async () => {
      called = true;
      return response();
    },
    "session-h",
    null,
  );

  assert.equal(called, false);
  assert.equal(result.method, "disabled");
  assert.equal(result.ensured, false);
});
