/**
 * Recall-path tests.
 *
 * ⚠️ `UserPromptSubmit` fires only in the interactive TUI, so the *delivery* of a
 * recall block to the model cannot be tested here — that was verified in a real
 * session (docs/HOST-CONTRACT.md §4 and the plan's P3 record). What these tests
 * pin is the other half, the half that can silently ruin a user's turn: every
 * degradation path must return a valid empty response and exit 0, and the
 * host-generated-continuation guard must skip before any search happens.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const restoreEnv = scrubOpenVikingEnv();
test.after(() => restoreEnv());

const ENTRY = resolve(dirname(fileURLToPath(import.meta.url)), "auto-recall.mjs");

function sandbox() {
  const dir = mkdtempSync(join(tmpdir(), "cb-recall-test-"));
  return {
    dir,
    env: {
      OPENVIKING_API_KEY: "k",
      OPENVIKING_MEMORY_ENABLED: "1",
      CODEBUDDY_PLUGIN_DATA: join(dir, "plugin-data"),
      OPENVIKING_PENDING_DIR: join(dir, "pending"),
      OPENVIKING_HOME: join(dir, "ov-home"),
    },
  };
}

const promptPayload = (prompt, extra = {}) => JSON.stringify({
  hook_event_name: "UserPromptSubmit",
  session_id: "sess-1",
  cwd: "/tmp",
  prompt,
  ...extra,
});

/** Parse the hook's stdout; `{}` when it printed nothing meaningful. */
function envelope(stdout) {
  const text = (stdout || "").trim();
  return text ? JSON.parse(text) : {};
}

/** The skip paths return a valid no-op envelope; what matters is that nothing was injected. */
function assertNoInjection(out) {
  assert.equal(out.hookSpecificOutput, undefined, "no context may be injected on this path");
}

function recallState(box) {
  try {
    return JSON.parse(readFileSync(join(box.env.OPENVIKING_HOME, "state", "last-recall.json"), "utf8"));
  } catch {
    return null;
  }
}

test("a downed server yields an empty, non-blocking response", async () => {
  const box = sandbox();
  try {
    const r = await runHookScript(ENTRY, {
      input: promptPayload("一个足够长的问题看看会不会召回什么"),
      env: { ...box.env, OPENVIKING_URL: "http://127.0.0.1:9" },
    });
    assert.equal(r.code, 0);
    assertNoInjection(envelope(r.stdout));
    assert.equal(recallState(box)?.reason, "offline");
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a disabled plugin yields an empty response and makes no request", async () => {
  const box = sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const r = await runHookScript(ENTRY, {
        input: promptPayload("一个足够长的问题看看会不会召回什么"),
        env: { OPENVIKING_URL: url, OPENVIKING_MEMORY_ENABLED: "0", CODEBUDDY_PLUGIN_DATA: box.env.CODEBUDDY_PLUGIN_DATA },
      });
      assert.equal(r.code, 0);
      assertNoInjection(envelope(r.stdout));
    });
    assert.equal(hits, 0);
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a bypassed session yields an empty response without searching", async () => {
  const box = sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const r = await runHookScript(ENTRY, {
        input: promptPayload("一个足够长的问题看看会不会召回什么"),
        env: { ...box.env, OPENVIKING_URL: url, OPENVIKING_BYPASS_SESSION: "1" },
      });
      assert.equal(r.code, 0);
      assertNoInjection(envelope(r.stdout));
    });
    assert.equal(hits, 0);
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a host-generated continuation is skipped, not searched", async () => {
  const box = sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const r = await runHookScript(ENTRY, {
        // is_internal_continuation is undocumented; it marks host-driven turns,
        // which are not user asks and must not trigger a search.
        input: JSON.stringify({
          hook_event_name: "UserPromptSubmit",
          session_id: "sess-1",
          cwd: "/tmp",
          prompt: "这是一个足够长的续写轮",
          is_internal_continuation: true,
        }),
        env: { ...box.env, OPENVIKING_URL: url },
      });
      assert.equal(r.code, 0);
      assertNoInjection(envelope(r.stdout));
      assert.equal(hits, 0, "no request may be made for a host-generated continuation");
      assert.equal(recallState(box)?.reason, "internal_continuation");
    });
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a short prompt is skipped without searching", async () => {
  const box = sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const r = await runHookScript(ENTRY, {
        input: promptPayload("嗯"),
        env: { ...box.env, OPENVIKING_URL: url },
      });
      assert.equal(r.code, 0);
      assertNoInjection(envelope(r.stdout));
    });
    assert.equal(hits, 0);
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a healthy server that recalls nothing still returns a well-formed response", async () => {
  const box = sandbox();
  try {
    await withMockOpenViking(async (req, res) => {
      const { pathname } = new URL(req.url, "http://127.0.0.1");
      if (pathname === "/health") return writeJson(res, { status: "ok" });
      if (pathname === "/api/v1/system/status") return writeJson(res, { status: "ok", result: {} });
      if (pathname.startsWith("/api/v1/search/")) return writeJson(res, { status: "ok", result: { results: [], entries: [] } });
      return writeJson(res, { status: "ok", result: {} });
    }, async (url) => {
      const r = await runHookScript(ENTRY, {
        input: promptPayload("一个足够长的问题看看会不会召回什么"),
        env: { ...box.env, OPENVIKING_URL: url },
      });
      assert.equal(r.code, 0, `hook must exit 0 (stderr: ${r.stderr})`);
      const out = envelope(r.stdout);
      // Nothing was recalled, so no block should be injected…
      assert.ok(!JSON.stringify(out).includes("<openviking-context>"));
      // …but if anything was injected it must be a valid UserPromptSubmit envelope.
      const specific = out.hookSpecificOutput;
      if (specific) {
        assert.equal(specific.hookEventName, "UserPromptSubmit");
        assert.equal(typeof specific.additionalContext, "string");
      }
    });
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});
