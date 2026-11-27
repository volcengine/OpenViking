/** `scripts/hook.mjs` and `scripts/uri-guard.mjs` as a host runs them, through one real host. */

import assert from "node:assert/strict";
import { copyFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  expectExit,
  readRequestBody,
  runHookScript,
  withMockOpenViking,
  writeCredentialFiles,
  writeJson,
} from "./testing/support.mjs";

const PLUGIN = join(dirname(fileURLToPath(import.meta.url)), "..", "agent-hook-plugin");
const HOOK = join(PLUGIN, "scripts", "hook.mjs");
const GUARD = join(PLUGIN, "scripts", "uri-guard.mjs");
const TRANSCRIPT = join(PLUGIN, "hosts", "qoder", "fixtures", "tooluse.jsonl");

async function withSession(t, fn) {
  const files = await writeCredentialFiles("ov-hook-entry-", { ovcli: { url: "http://127.0.0.1:9", api_key: "k" }, ov: {} });
  // A detached worker may still be writing its state when the test ends.
  t.after(() => rmSync(files.dir, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 }));
  const transcript = join(files.dir, "session.jsonl");
  copyFileSync(TRANSCRIPT, transcript);
  const seen = [];
  await withMockOpenViking(async (req, res) => {
    seen.push({ path: new URL(req.url, "http://127.0.0.1").pathname, body: await readRequestBody(req) });
    writeJson(res, { status: "ok", result: {} });
  }, (url) => fn({
    seen,
    run: (event, { input, env = {} } = {}) => runHookScript(HOOK, {
      argv: [event, "qoder"],
      cwd: files.dir,
      inheritOpenVikingEnv: false,
      input: input ?? { session_id: "s1", cwd: files.dir, transcript_path: transcript },
      env: {
        ...files.env,
        OPENVIKING_URL: url,
        OPENVIKING_HOOK_STATE_DIR: join(files.dir, "hook-state"),
        OPENVIKING_PENDING_DIR: join(files.dir, "pending"),
        OPENVIKING_STATE_DIR: join(files.dir, "state"),
        ...env,
      },
    }),
  }));
}

const batches = (seen) => seen.filter((entry) => entry.path.endsWith("/messages/batch"));

test("a write event answers at once and the detached worker sends the turns", async (t) => {
  await withSession(t, async ({ run, seen }) => {
    const stop = expectExit(await run("stop"));
    assert.equal(stop.stdout, "");
    const deadline = Date.now() + 10_000;
    while (batches(seen).length === 0 && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(batches(seen)[0].body.messages.length, 3);
  });
});

test("with writePathAsync off the hook itself sends, and a session end commits", async (t) => {
  await withSession(t, async ({ run, seen }) => {
    const env = { OPENVIKING_WRITE_PATH_ASYNC: "0" };
    expectExit(await run("stop", { env }));
    assert.equal(batches(seen).length, 1);
    expectExit(await run("session-end", { env }));
    assert.equal(seen.filter((entry) => entry.path.endsWith("/commit")).length, 1);
  });
});

test("an injection event prints the host's envelope", async (t) => {
  await withSession(t, async ({ run }) => {
    const start = expectExit(await run("session-start", { input: { session_id: "s1", source: "resume" } }));
    const { hookSpecificOutput } = JSON.parse(start.stdout);
    assert.equal(hookSpecificOutput.hookEventName, "SessionStart");
    assert.match(hookSpecificOutput.additionalContext, /source="session-history"/);
  });
});

test("an unknown host, an unknown event and unreadable stdin all exit clean and silent", async (t) => {
  await withSession(t, async ({ run, seen }) => {
    const unknownHost = expectExit(await runHookScript(HOOK, { argv: ["stop", "no-such-host"], input: {} }));
    assert.equal(unknownHost.stdout, "");
    assert.match(unknownHost.stderr, /no hook adapter for no-such-host/);
    assert.equal(expectExit(await run("no-such-event")).stdout, "");
    assert.equal(expectExit(await run("user-prompt-submit", { input: "not json" })).stdout, "");
    assert.equal(seen.length, 0);
  });
});

test("the guard entry prints a deny for a viking:// path and nothing otherwise", async () => {
  const input = (path) => ({ tool_name: "Read", tool_input: { file_path: path } });
  const denied = expectExit(await runHookScript(GUARD, { argv: ["qoder"], input: input("viking://user/me/a.md") }));
  assert.equal(JSON.parse(denied.stdout).hookSpecificOutput.permissionDecision, "deny");
  assert.equal(expectExit(await runHookScript(GUARD, { argv: ["qoder"], input: input("/tmp/a.md") })).stdout, "");
  assert.equal(expectExit(await runHookScript(GUARD, { argv: ["qoder"], input: "" })).stdout, "");
});
