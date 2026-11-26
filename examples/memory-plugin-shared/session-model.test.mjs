import assert from "node:assert/strict";
import test from "node:test";

import { thresholdCommitDue } from "./lib/session-model.mjs";

const POLICY = {
  pending_token_threshold: 100000,
  message_count_threshold: 0,
  idle_timeout_seconds: 1800,
  keep_recent_count: 0,
  min_commit_interval_seconds: 0,
};
const local = { commitTokenThreshold: 100000, commitIdleSeconds: 1800 };
const server = { ...local, serverAutoCommit: true };

/** Runs the check and returns its answer with the policies it sent. */
async function check(cfg, meta) {
  const sent = [];
  const due = await thresholdCommitDue(async (path, init) => {
    assert.equal(`${init.method} ${path}`, "PATCH /api/v1/sessions/cc%20a%2Fb/config");
    sent.push(JSON.parse(init.body).auto_commit_policy);
  }, "cc a/b", cfg, meta);
  return { due, sent };
}

test("the plugin commits at the pending-token threshold", async () => {
  assert.deepEqual(await check(local, { pending_tokens: 99999 }), { due: false, sent: [] });
  assert.deepEqual(await check(local, { pending_tokens: 100000 }), { due: true, sent: [] });
  assert.deepEqual(await check(local, null), { due: false, sent: [] });
});

test("serverAutoCommit leaves the commit to a session that carries the policy", async () => {
  const meta = { pending_tokens: 500000, auto_commit_policy: POLICY };
  assert.deepEqual(await check(server, meta), { due: false, sent: [] });
});

test("a session without the policy gets it, and this turn is still the plugin's", async () => {
  // The second is what a server with session auto-commit on gives a new session.
  for (const stored of [null, { ...POLICY, pending_token_threshold: 150000, message_count_threshold: 100 }]) {
    assert.deepEqual(await check(server, { pending_tokens: 500000, auto_commit_policy: stored }), { due: true, sent: [POLICY] });
    assert.deepEqual(await check(server, { pending_tokens: 5, auto_commit_policy: stored }), { due: false, sent: [POLICY] });
  }
});

test("the idle commit is off at 0 and never below what the server accepts", async () => {
  for (const [commitIdleSeconds, sent] of [[0, 0], [60, 300], [7200, 7200]]) {
    const result = await check({ ...server, commitIdleSeconds }, {});
    assert.equal(result.sent[0].idle_timeout_seconds, sent);
  }
});
