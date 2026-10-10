/**
 * Capture-path tests.
 *
 * These drive the real hook script against a mock OpenViking, so the transcript
 * reader, the shared part shaper, the batching, the increment cursor and the
 * offline queue are all exercised together. The cursor is the part worth
 * guarding: it must advance only on an acknowledged write, or a transient
 * failure silently drops turns, and it must not advance twice for the same
 * turns, or a Stop re-delivery duplicates messages in OV.
 *
 * Every state directory the hook touches is redirected into a throwaway
 * directory — `CODEBUDDY_PLUGIN_DATA` for the cursor, `OPENVIKING_PENDING_DIR`
 * for the queue, `OPENVIKING_HOME` for the state snapshots. A test that missed
 * one would write into the developer's real `~/.openviking`.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  readRequestBody,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const restoreEnv = scrubOpenVikingEnv();
test.after(() => restoreEnv());

const ENTRY = resolve(dirname(fileURLToPath(import.meta.url)), "auto-capture.mjs");

const line = (o) => JSON.stringify(o);

function fixture() {
  return [
    // A real ask wrapped in host markup.
    line({ type: "message", role: "user", content: [{ type: "input_text", text: "<system-reminder>n</system-reminder>\n<user_query>请记住：我偏好用 tabs 缩进</user_query>" }] }),
    line({ type: "message", role: "assistant", content: [{ type: "output_text", text: "好的，已记录。" }] }),
    line({ type: "function_call", name: "Bash", arguments: '{"command":"ls"}', callId: "c1" }),
    line({ type: "function_call_result", name: "Bash", callId: "c1", status: "success", output: "a.txt" }),
    // Host-authored turn: no human text, must not be captured.
    line({ type: "message", role: "user", content: [{ type: "input_text", text: "<task-notification>x</task-notification>" }], providerData: { isMeta: true } }),
    line({ type: "message", role: "user", content: [{ type: "input_text", text: "那 A 呢？" }] }),
  ].join("\n");
}

/** A throwaway cwd + transcript + state dirs, cleaned up by the caller. */
function sandbox() {
  const dir = mkdtempSync(join(tmpdir(), "cb-capture-test-"));
  const transcript = join(dir, "session.jsonl");
  writeFileSync(transcript, fixture());
  return {
    dir,
    transcript,
    env: {
      OPENVIKING_API_KEY: "k",
      OPENVIKING_MEMORY_ENABLED: "1",
      // Keep the write inline so the test observes it; the host default is to
      // detach, which would move the work into a process the test cannot await.
      OPENVIKING_WRITE_PATH_ASYNC: "0",
      CODEBUDDY_PLUGIN_DATA: join(dir, "plugin-data"),
      OPENVIKING_PENDING_DIR: join(dir, "pending"),
      OPENVIKING_HOME: join(dir, "ov-home"),
    },
  };
}

const stopPayload = (transcript, dir, sessionId = "sess-1") => JSON.stringify({
  hook_event_name: "Stop",
  session_id: sessionId,
  transcript_path: transcript,
  cwd: dir,
});

/** A mock that records batch sends and answers the read/session/commit routes. */
function healthyMock(seen) {
  return async (req, res) => {
    const { pathname } = new URL(req.url, "http://127.0.0.1");
    if (pathname === "/health") { seen.health++; return writeJson(res, { status: "ok" }); }
    if (pathname.endsWith("/messages/batch")) {
      seen.batches.push(await readRequestBody(req));
      return writeJson(res, { status: "ok", data: { added: 1 } });
    }
    if (pathname.endsWith("/commit")) { seen.commit++; return writeJson(res, { status: "ok", trace_id: "t" }); }
    if (/^\/api\/v1\/sessions\/[^/]+$/.test(pathname)) {
      return writeJson(res, { status: "ok", data: { pending_tokens: 0, commit_count: 0, total_message_count: 3 } });
    }
    return writeJson(res, { status: "error", error: `unexpected ${pathname}` }, 404);
  };
}

test("a healthy server receives one batch with the human turns and the tool-carrying assistant turn", async () => {
  const box = sandbox();
  const seen = { health: 0, batches: [], commit: 0 };
  try {
    await withMockOpenViking(healthyMock(seen), async (url) => {
      const r = await runHookScript(ENTRY, {
        input: stopPayload(box.transcript, box.dir),
        env: { ...box.env, OPENVIKING_URL: url },
      });
      assert.equal(r.code, 0);
    });

    assert.equal(seen.batches.length, 1);
    const messages = seen.batches[0].messages;
    assert.equal(messages.filter((m) => m.role === "user").length, 2);
    assert.equal(messages.filter((m) => m.role === "assistant").length, 1);

    const all = JSON.stringify(messages);
    assert.ok(!all.includes("task-notification"), "the host-authored turn must not be captured");
    assert.ok(!all.includes("system-reminder"), "host markup must not leak into captured text");
    assert.ok(!all.includes("<user_query>"), "the wrapper must be stripped, not captured verbatim");
    assert.ok(all.includes("那 A 呢"), "the second human ask must be captured");
    assert.ok(all.includes("我偏好用 tabs"), "the first human ask must be captured");

    const toolParts = messages.flatMap((m) => (m.parts || []).filter((p) => p.type === "tool"));
    assert.deepEqual(toolParts.map((p) => [p.tool_name, p.tool_status]), [["Bash", "running"], ["Bash", "completed"]]);
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a second Stop on the same session sends nothing — the cursor is idempotent", async () => {
  const box = sandbox();
  const seen = { health: 0, batches: [], commit: 0 };
  try {
    await withMockOpenViking(healthyMock(seen), async (url) => {
      const env = { ...box.env, OPENVIKING_URL: url };
      const input = stopPayload(box.transcript, box.dir);
      assert.equal((await runHookScript(ENTRY, { input, env })).code, 0);
      assert.equal((await runHookScript(ENTRY, { input, env })).code, 0);
    });
    assert.equal(seen.batches.length, 1, "re-delivery must not re-send the same turns");
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a downed server queues the turns durably instead of losing them", async () => {
  const box = sandbox();
  try {
    const r = await runHookScript(ENTRY, {
      // Nothing listens on port 9.
      input: stopPayload(box.transcript, box.dir),
      env: { ...box.env, OPENVIKING_URL: "http://127.0.0.1:9" },
    });
    assert.equal(r.code, 0);
    assert.match(r.stdout, /queued \d+ turns to pending queue/);

    const { readdirSync } = await import("node:fs");
    const queued = readdirSync(box.env.OPENVIKING_PENDING_DIR).filter((f) => f.endsWith(".json"));
    assert.ok(queued.length >= 1, "the offline queue must hold the turns");
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});

test("a disabled plugin exits cleanly and touches the network not at all", async () => {
  const box = sandbox();
  let hits = 0;
  try {
    await withMockOpenViking(async (req, res) => { hits++; return writeJson(res, { status: "ok" }); }, async (url) => {
      const r = await runHookScript(ENTRY, {
        input: stopPayload(box.transcript, box.dir),
        env: { OPENVIKING_URL: url, OPENVIKING_MEMORY_ENABLED: "0", CODEBUDDY_PLUGIN_DATA: box.env.CODEBUDDY_PLUGIN_DATA },
      });
      assert.equal(r.code, 0);
    });
    assert.equal(hits, 0);
  } finally {
    rmSync(box.dir, { recursive: true, force: true });
  }
});
