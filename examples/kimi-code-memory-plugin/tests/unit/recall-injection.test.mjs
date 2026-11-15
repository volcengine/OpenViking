/**
 * Recall and profile: the difference between "nothing" and "failed".
 *
 * The first implementation treated an empty profile as a completed injection
 * and an empty recall as a failure. Both are backwards: a user with no profile
 * is done, and a server that answered "no matches" is working.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { loadConfig } from "../../scripts/config.mjs";
import { makeAgentFetchJSON } from "../../scripts/shared/agent-hook-runtime.mjs";
import {
  PROFILE_MAX_ATTEMPTS,
  recallBreaker,
  recallStageIsAnswer,
  runPromptInjection,
} from "../../scripts/recall.mjs";
import { emptySessionState } from "../../scripts/session-state.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome } from "../fixtures/kimi-home.mjs";

async function run({ home, server, state = emptySessionState(), promptText = "how do I deploy?", cfgOverrides = {} }) {
  const cfg = {
    ...loadConfig(home, { env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home } }),
    ...cfgOverrides,
  };
  const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
  return await runPromptInjection({
    cfg,
    state,
    cwd: home,
    promptText,
    ovSessionId: "kc-x",
    fetchJSON,
    log: () => {},
    deadline: Date.now() + 60000,
    now: Date.now(),
  });
}

test("an empty profile counts as a finished injection, not a failure", async () => {
  const server = await startFakeOv((request) => {
    // Every read succeeds and finds nothing.
    if (request.path?.startsWith("/api/v1/content/read")) return { status: 404, body: { status: "error" } };
    if (request.path?.startsWith("/api/v1/fs/ls")) return { body: { status: "ok", result: [] } };
    if (request.path === "/api/v1/search/search") {
      return { body: { status: "ok", result: { rendered: "", entries: [] } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      const first = await run({ home, server });
      assert.equal(first.profileInjected, true, "no profile is a finished attempt");
      assert.ok(first.reasons.includes("profile_empty"));

      // The next prompt does not ask again.
      const second = await run({ home, server, state: first.state });
      assert.ok(second.reasons.includes("profile_already_injected"));
    });
  } finally {
    await server.close();
  }
});

test("a failed profile read is retried once and then given up on", async () => {
  let profileReads = 0;
  const server = await startFakeOv((request) => {
    if (request.path?.startsWith("/api/v1/content/read")) {
      profileReads += 1;
      return { status: 503, body: { status: "error", error: { message: "down" } } };
    }
    if (request.path?.startsWith("/api/v1/fs/ls")) return { status: 503, body: { status: "error" } };
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      const first = await run({ home, server });
      assert.equal(first.profileInjected, false);
      assert.ok(first.reasons.includes("profile_failed_retry"));
      assert.equal(profileReads > 0, true);

      // A retry before the backoff window does not ask again.
      const early = await run({ home, server, state: first.state });
      assert.ok(early.reasons.includes("profile_backoff"));

      // After the window, one more attempt is made and then it stops.
      const later = await run({
        home,
        server,
        state: { ...first.state, profileFailureUntil: Date.now() - 1 },
      });
      assert.equal(later.profileInjected, true, `after ${PROFILE_MAX_ATTEMPTS} attempts the session stops asking`);

      const after = await run({ home, server, state: later.state });
      assert.ok(after.reasons.includes("profile_already_injected"));
    });
  } finally {
    await server.close();
  }
});

test("a partially failed profile is retried before the partial block is injected", async () => {
  let failPreferences = true;
  const server = await startFakeOv((request) => {
    if (request.path?.startsWith("/api/v1/content/read")) {
      return { body: { status: "ok", result: "# Profile\n- likes tea" } };
    }
    if (request.path?.startsWith("/api/v1/fs/ls")) {
      if (failPreferences && request.path.includes("preferences")) {
        return { status: 503, body: { status: "error" } };
      }
      return { body: { status: "ok", result: [] } };
    }
    if (request.path === "/api/v1/search/search") {
      return { body: { status: "ok", result: { rendered: "", entries: [] } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      const first = await run({ home, server });
      assert.equal(first.profileInjected, false);
      assert.ok(first.reasons.includes("profile_failed_retry"));
      assert.equal(first.text.includes("likes tea"), false, "a partial block is not injected on the first failure");

      failPreferences = false;
      const second = await run({
        home,
        server,
        state: { ...first.state, profileFailureUntil: Date.now() - 1 },
      });
      assert.equal(second.profileInjected, true);
      assert.match(second.text, /likes tea/);
    });
  } finally {
    await server.close();
  }
});

test("a recall that finds nothing is not a failure and resets the breaker", async () => {
  const server = await startFakeOv((request) => {
    if (request.path?.startsWith("/api/v1/content/read")) return { status: 404, body: { status: "error" } };
    if (request.path?.startsWith("/api/v1/fs/ls")) return { body: { status: "ok", result: [] } };
    if (request.path === "/api/v1/search/search") {
      return { body: { status: "ok", result: { rendered: "", entries: [] } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      // Start with a tripped breaker, as two real failures would leave it.
      const tripped = {
        ...emptySessionState(),
        profileInjected: true,
        recallFailures: 2,
        recallFailureUntil: Date.now() - 1,
      };
      const result = await run({ home, server, state: tripped });
      assert.ok(result.reasons.includes("recall_no_results"));
      // A successful-but-empty answer is a success: the count resets.
      assert.equal(result.state.recallFailures, 0);
      assert.equal(recallBreaker(result.state).open, false);
    });
  } finally {
    await server.close();
  }
});

test("a recall that fails trips the breaker after two attempts", async () => {
  const server = await startFakeOv((request) => {
    if (request.path?.startsWith("/api/v1/content/read")) return { status: 404, body: { status: "error" } };
    if (request.path?.startsWith("/api/v1/fs/ls")) return { body: { status: "ok", result: [] } };
    if (request.path === "/api/v1/search/search") {
      return { status: 503, body: { status: "error", error: { message: "down" } } };
    }
    if (request.path === "/api/v1/search/recall") {
      return { status: 503, body: { status: "error", error: { message: "down" } } };
    }
    if (request.path?.startsWith("/api/v1/search/find")) {
      return { status: 503, body: { status: "error", error: { message: "down" } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      const first = await run({ home, server });
      assert.equal(first.state.recallFailures, 1);
      assert.equal(recallBreaker(first.state).open, false);

      const second = await run({ home, server, state: first.state });
      assert.equal(second.state.recallFailures, 2);
      assert.equal(recallBreaker(second.state).open, true, "two failures pause recall");

      // While paused, no request is made.
      const paused = await run({ home, server, state: second.state });
      assert.ok(paused.reasons.includes("recall_paused"));
    });
  } finally {
    await server.close();
  }
});

test("recallStageIsAnswer separates an answered query from a broken one", () => {
  assert.equal(recallStageIsAnswer("no_results"), true);
  assert.equal(recallStageIsAnswer("filtered_out"), true);
  for (const stage of ["error", "unreachable", "timeout", undefined, ""]) {
    assert.equal(recallStageIsAnswer(stage), false, String(stage));
  }
});

test("the breaker pauses for a minute on the second failure and five on the third", async () => {
  const { noteRecallResult } = await import("../../scripts/recall.mjs");
  const now = 1_000_000;
  let state = { recallFailures: 0, recallFailureUntil: 0 };
  state = noteRecallResult(state, false, now);
  assert.equal(state.recallFailures, 1);
  assert.equal(state.recallFailureUntil, now, "one failure does not pause");

  state = noteRecallResult(state, false, now);
  assert.equal(state.recallFailures, 2);
  assert.equal(state.recallFailureUntil, now + 60_000, "the second failure pauses for a minute");

  state = noteRecallResult(state, false, now);
  assert.equal(state.recallFailures, 3);
  assert.equal(state.recallFailureUntil, now + 300_000, "the third pauses for five minutes");

  // Any success resets the curve.
  state = noteRecallResult(state, true, now);
  assert.equal(state.recallFailures, 0);
  assert.equal(state.recallFailureUntil, 0);
});

test("recallQueryFilters rewrite and drop the query before the length gate", async () => {
  const server = await startFakeOv((request) => {
    if (request.path?.startsWith("/api/v1/content/read")) return { status: 404, body: { status: "error" } };
    if (request.path?.startsWith("/api/v1/fs/ls")) return { body: { status: "ok", result: [] } };
    if (request.path === "/api/v1/search/search") {
      return { body: { status: "ok", result: { rendered: "a remembered fact", entries: [] } } };
    }
    return undefined;
  });
  try {
    await withKimiHome(async ({ home }) => {
      // `s/^please //` rewrites the query; the server must see the rewritten one.
      const rewritten = await run({
        home,
        server,
        state: { ...emptySessionState(), profileInjected: true },
        promptText: "please explain the deploy pipeline",
        cfgOverrides: { recallQueryFilters: ["s/^please //"] },
      });
      assert.ok(rewritten.text.length > 0, "the rewrite must not suppress recall");
      const searched = server.requests.filter((request) => request.path === "/api/v1/search/search");
      assert.equal(searched.length, 1);
      assert.equal(searched[0].body.query, "explain the deploy pipeline");

      // `d/^internal:/` drops the query entirely: no request, and a reason.
      server.reset();
      const dropped = await run({
        home,
        server,
        state: { ...emptySessionState(), profileInjected: true },
        promptText: "internal: how do I deploy?",
        cfgOverrides: { recallQueryFilters: ["d/^internal:/"] },
      });
      assert.equal(server.requests.filter((request) => request.path === "/api/v1/search/search").length, 0);
      assert.ok(dropped.reasons.includes("query_filtered"));
    });
  } finally {
    await server.close();
  }
});
