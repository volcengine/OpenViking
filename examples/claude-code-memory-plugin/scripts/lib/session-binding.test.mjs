import assert from "node:assert/strict";
import { access, mkdir, mkdtemp, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

// state.mjs fixes its directory when it is first imported.
const home = await mkdtemp(join(tmpdir(), "ov-cc-binding-"));
process.env.OPENVIKING_HOME = home;
const { bindCurrentSession, currentOvSessionId } = await import("./session-binding.mjs");
const { statePath } = await import("./state.mjs");

test.after(() => rm(home, { recursive: true, force: true }));

test("a /clear or /resume moves the proxy to the new session", () => {
  const startedAt = Date.now() - 1000;
  const env = { CLAUDE_PID: "41001", CLAUDE_CODE_SESSION_ID: "first" };
  assert.equal(bindCurrentSession("first", env), true);
  const proxy = { pid: 41001, startedAt, env: { CLAUDE_CODE_SESSION_ID: "first" } };
  assert.equal(currentOvSessionId(proxy), "cc-first");

  bindCurrentSession("second", env);
  assert.equal(currentOvSessionId(proxy), "cc-second");
});

test("concurrent Claude Code processes each read their own session", () => {
  const startedAt = Date.now() - 1000;
  bindCurrentSession("alpha", { CLAUDE_PID: "41011" });
  bindCurrentSession("beta", { CLAUDE_PID: "41012" });
  assert.equal(currentOvSessionId({ pid: 41011, startedAt, env: {} }), "cc-alpha");
  assert.equal(currentOvSessionId({ pid: 41012, startedAt, env: {} }), "cc-beta");
});

test("no record, or one older than the proxy for another session, is 'cannot tell'", async () => {
  const startedAt = Date.now();
  assert.equal(currentOvSessionId({ pid: 41021, startedAt, env: { CLAUDE_CODE_SESSION_ID: "s" } }), "");

  // Left by an earlier process that had the same pid.
  await mkdir(dirname(statePath("cc-session-41022.json")), { recursive: true });
  await writeFile(statePath("cc-session-41022.json"), JSON.stringify({ session_id: "stale", ts: startedAt - 60000 }));
  assert.equal(currentOvSessionId({ pid: 41022, startedAt, env: { CLAUDE_CODE_SESSION_ID: "now" } }), "");
  // The same record names the session this proxy started in: SessionStart ran
  // just before the proxy did.
  assert.equal(currentOvSessionId({ pid: 41022, startedAt, env: { CLAUDE_CODE_SESSION_ID: "stale" } }), "cc-stale");
});

test("without CLAUDE_PID nothing is recorded", () => {
  assert.equal(bindCurrentSession("orphan", {}), false);
  assert.equal(bindCurrentSession("orphan", { CLAUDE_PID: "not-a-pid" }), false);
  assert.equal(bindCurrentSession("", { CLAUDE_PID: "41031" }), false);
  assert.equal(currentOvSessionId({ pid: undefined, startedAt: 0, env: {} }), "");
});

test("writing a record removes binding files untouched for over a week, and only those", async () => {
  const old = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000);
  const recent = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000);
  const files = {
    stale: statePath("cc-session-41041.json"),
    recent: statePath("cc-session-41042.json"),
    otherState: statePath("last-capture.json"),
  };
  await mkdir(dirname(files.stale), { recursive: true });
  for (const path of Object.values(files)) await writeFile(path, "{}");
  await utimes(files.stale, old, old);
  await utimes(files.recent, recent, recent);
  await utimes(files.otherState, old, old);

  assert.equal(bindCurrentSession("live", { CLAUDE_PID: "41043" }), true);

  const exists = (path) => access(path).then(() => true, () => false);
  assert.equal(await exists(files.stale), false);
  assert.equal(await exists(files.recent), true);
  assert.equal(await exists(files.otherState), true);
  assert.equal(await exists(statePath("cc-session-41043.json")), true);
});
