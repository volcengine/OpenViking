/**
 * Capture hook end to end (DESIGN.md §5.4/§5.5): the cursor only moves on
 * confirmation and survives to the next process, SessionEnd commits the
 * progress an earlier Stop left uncommitted, a held lock makes the hook stand
 * down, and a torn tail line does not cost the turn that closed before it.
 */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  readRequestBody,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const HOOK = join(SCRIPT_DIR, "auto-capture.mjs");

const SESSION = "s1";
const OV_SESSION = "kc-s1";

/** A Kimi home holding one session whose wire journal is `rows`. */
function workspace(rows) {
  const root = mkdtempSync(join(tmpdir(), "kimicode-capture-"));
  const home = join(root, "kimi-home");
  const wire = join(home, "sessions", "repo", SESSION, "agents", "main", "wire.jsonl");
  mkdirSync(dirname(wire), { recursive: true });
  writeFileSync(wire, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");
  return {
    root,
    home,
    wire,
    state: join(root, "state"),
    pending: join(root, "pending"),
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  };
}

function wireRows(text = "ship it") {
  return [
    { type: "metadata", protocol_version: "1.5", created_at: 1 },
    { type: "turn.prompt", turnId: 0, origin: { kind: "user" }, input: [{ type: "text", text }] },
    {
      type: "context.append_loop_event",
      event: { type: "content.part", turnId: "0", step: 1, stepUuid: "s1", part: { type: "text", text: "done" } },
    },
    { type: "turn.ended", turnId: 0 },
  ];
}

function run(ws, event, base, extraEnv = {}) {
  return runHookScript(HOOK, {
    argv: [event],
    input: JSON.stringify({ session_id: SESSION, cwd: ws.root, hook_event_name: event }),
    env: {
      KIMI_CODE_HOME: ws.home,
      OPENVIKING_KIMICODE_STATE_DIR: ws.state,
      OPENVIKING_PENDING_DIR: ws.pending,
      OPENVIKING_URL: base,
      OPENVIKING_RECALL_TIMEOUT_MS: "2000",
      ...extraEnv,
    },
  });
}

function captureServer(seen) {
  return async (req, res) => {
    const path = new URL(req.url, "http://127.0.0.1").pathname;
    if (path.endsWith("/commit")) {
      seen.push({ kind: "commit", path });
      writeJson(res, { status: "ok", result: { status: "accepted" } });
      return;
    }
    if (path.endsWith("/messages/batch")) {
      const body = await readRequestBody(req);
      seen.push({ kind: "messages", path, count: body?.messages?.length ?? 0 });
      writeJson(res, { status: "ok", result: { ok: true } });
      return;
    }
    if (path.endsWith("/messages")) {
      seen.push({ kind: "messages", path, count: 1 });
      writeJson(res, { status: "ok", result: { ok: true } });
      return;
    }
    seen.push({ kind: "other", path });
    writeJson(res, { status: "ok", result: { ok: true } });
  };
}

test("a second stop sends nothing again, and session-end commits what the first left open", async () => {
  const restore = scrubOpenVikingEnv();
  const ws = workspace(wireRows());
  try {
    const seen = [];
    await withMockOpenViking(captureServer(seen), async (base) => {
      const first = await run(ws, "stop", base);
      assert.equal(first.code, 0, first.stderr);
      const messagesAfterFirst = seen.filter((entry) => entry.kind === "messages").length;
      assert.equal(messagesAfterFirst, 1, `the first stop sends the turn's payloads; saw ${JSON.stringify(seen)}`);
      assert.ok(seen.some((entry) => entry.kind === "commit") === false, "2 turns stay under the threshold");

      // The cursor has to survive the process, or the second hook re-sends.
      const state = JSON.parse(readFileSync(join(ws.state, `${SESSION}.json`), "utf-8"));
      assert.equal(state.lastTurnId, "0");

      const second = await run(ws, "stop", base);
      assert.equal(second.code, 0, second.stderr);
      assert.equal(
        seen.filter((entry) => entry.kind === "messages").length,
        messagesAfterFirst,
        "a second stop with no new turns must not re-send",
      );

      // SessionEnd plans nothing, yet has to commit the 2 captured turns.
      const end = await run(ws, "session-end", base);
      assert.equal(end.code, 0, end.stderr);
      assert.equal(seen.filter((entry) => entry.kind === "commit").length, 1, "session-end commits");
    });
  } finally {
    ws.cleanup();
    restore();
  }
});

test("a held lock makes the hook stand down instead of running unserialised", async () => {
  const restore = scrubOpenVikingEnv();
  const ws = workspace(wireRows());
  try {
    mkdirSync(join(ws.state, `${SESSION}.lock`), { recursive: true });
    const seen = [];
    await withMockOpenViking(captureServer(seen), async (base) => {
      const result = await run(ws, "interrupt", base);
      assert.equal(result.code, 0, result.stderr);
      assert.equal(
        seen.filter((entry) => entry.kind === "messages").length,
        0,
        "a hook that never got the lock must not send",
      );
    });
  } finally {
    ws.cleanup();
    restore();
  }
});

test("a torn tail line does not cost the turn that closed before it", async () => {
  const restore = scrubOpenVikingEnv();
  const rows = wireRows();
  const ws = workspace(rows);
  try {
    // The host appends as it goes; the last line can be half written.
    writeFileSync(ws.wire, `${rows.map((row) => JSON.stringify(row)).join("\n")}\n{"type":"context.append_loop`);
    const seen = [];
    await withMockOpenViking(captureServer(seen), async (base) => {
      const result = await run(ws, "stop", base);
      assert.equal(result.code, 0, result.stderr);
      assert.ok(
        seen.filter((entry) => entry.kind === "messages").length >= 1,
        "the closed turn is still captured",
      );
    });
  } finally {
    ws.cleanup();
    restore();
  }
});

test("a corrupt state file is reported, not silently treated as empty", async () => {
  const restore = scrubOpenVikingEnv();
  const ws = workspace(wireRows());
  const logPath = join(ws.root, "hooks.log");
  try {
    mkdirSync(ws.state, { recursive: true });
    writeFileSync(join(ws.state, `${SESSION}.json`), "{ not json");
    const seen = [];
    await withMockOpenViking(captureServer(seen), async (base) => {
      const result = await run(ws, "stop", base, {
        OPENVIKING_DEBUG: "1",
        OPENVIKING_DEBUG_LOG: logPath,
      });
      assert.equal(result.code, 0, result.stderr);
      const log = readFileSync(logPath, "utf-8");
      assert.match(log, /state_corrupt/, "the reset must leave a trace");
    });
  } finally {
    ws.cleanup();
    restore();
  }
});

test("interrupt gives up inside its own budget and parks what it could not send", async () => {
  const restore = scrubOpenVikingEnv();
  const ws = workspace(wireRows());
  try {
    const seen = [];
    await withMockOpenViking(
      async (req, res) => {
        seen.push(new URL(req.url, "http://127.0.0.1").pathname);
        // Never answer: the hook has to bound itself, the host will not.
        await new Promise(() => {});
      },
      async (base) => {
        const started = Date.now();
        const result = await run(ws, "interrupt", base);
        const elapsed = Date.now() - started;
        assert.equal(result.code, 0, result.stderr);
        assert.ok(elapsed < 6000, `interrupt took ${elapsed}ms; its budget is 2s`);
        assert.ok(seen.length >= 1, "the hook must have tried to send");
      },
    );
    const state = JSON.parse(readFileSync(join(ws.state, `${SESSION}.json`), "utf-8"));
    assert.equal(state.lastTurnId ?? null, null, "nothing was confirmed, so the cursor stays put");
  } finally {
    ws.cleanup();
    restore();
  }
});
