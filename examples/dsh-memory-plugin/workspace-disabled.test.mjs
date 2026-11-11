import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { apply } from "./index.mjs";

/**
 * A workspace that sets `enabled: false` in `.openviking/config.json` is the
 * feature under test: its sessions must not create a session, send a profile,
 * recall, capture or commit — while a session in a neighboring workspace keeps
 * every one of those behaviors on the same runtime.
 */
function setup(t) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "dsh-ws-disabled-")));
  const originalEnv = process.env;
  process.env = { ...originalEnv };
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("OPENVIKING_")) delete process.env[key];
  }
  Object.assign(process.env, {
    OPENVIKING_CONFIG_FILE: join(root, "absent-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(root, "ovcli.conf"),
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_PENDING_DIR: join(root, "pending"),
  });
  const open = join(root, "open");
  const shut = join(root, "shut");
  mkdirSync(open);
  mkdirSync(shut);
  const writeConfig = (cwd, value, file = "config.json") => {
    mkdirSync(join(cwd, ".openviking"), { recursive: true });
    writeFileSync(join(cwd, ".openviking", file), JSON.stringify({ version: 1, ...value }));
  };
  writeConfig(shut, { enabled: false });
  // No cwd at launch is one of the two workspace dirs, so the boot configuration
  // is not the thing that decides this — the session's own directory is.
  t.mock.method(process, "cwd", () => open);
  const runtimes = [];
  t.after(() => {
    for (const runtime of runtimes) runtime.stopDrainer();
    process.env = originalEnv;
    rmSync(root, { recursive: true, force: true });
  });
  const start = (input = {}) => {
    let runtime;
    const handlers = new Map();
    apply({
      logger: { debug() {} },
      provide(name, value) {
        if (name === "openvikingMemory") runtime = value;
      },
      effect(execute) { execute(); },
      on(event, handler) {
        handlers.set(event, handler);
      },
      plugin() {},
    }, { endpoint: "http://openviking.test", ...input });
    runtimes.push(runtime);
    return { runtime, handlers };
  };
  const session = (cwd, id = cwd) => ({ id, header: { cwd } });
  return { root, open, shut, writeConfig, start, session };
}

function recordRequests(t) {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    const path = new URL(url).pathname;
    requests.push({ path, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null });
    let result = {};
    if (path === "/health") result = { status: "ok" };
    else if (path === "/api/v1/search/search" || path === "/api/v1/search/find") result = { rendered: "" };
    else if (path === "/api/v1/fs/ls") result = [];
    else if (path === "/api/v1/skills") result = { skills: [] };
    else if (path === "/api/v1/system/status") result = { user: "tester" };
    else if (path.startsWith("/api/v1/sessions/") && !init.method) result = { pending_tokens: 20000 };
    return new Response(JSON.stringify({ status: "ok", result }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  return requests;
}

function userEvent(text) {
  return {
    type: "user/message",
    data: {
      role: "user",
      content: [{ type: "text", text }],
      source: { kind: "user" },
    },
  };
}

test("a workspace with enabled:false settles into skipped without touching the server", async t => {
  const { shut, start, session } = setup(t);
  const requests = recordRequests(t);
  const { runtime } = start();
  const state = runtime.stateFor(session(shut, "disabled"));

  assert.equal(state.config.enabled, false, "the per-session config must carry the workspace switch");
  const settled = await runtime.initialize({ session: session(shut, "disabled") });
  assert.equal(settled.skipped, true);
  assert.equal(settled.ready, false);
  assert.deepEqual(requests, [], "initialization must not create the session, probe health or fetch the profile");

  // Every downstream gate reads the same flag, so none of them may send
  // anything even if they are reached without the index-level skip.
  runtime.capture(session(shut, "disabled"), userEvent("secret"));
  runtime.maybeCommit(session(shut, "disabled"), { type: "turn/end" });
  await runtime.flush(session(shut, "disabled"));
  await runtime.dispose(session(shut, "disabled"));
  assert.deepEqual(requests, []);
});

test("profile and recall return nothing for a disabled workspace", async t => {
  const { shut, start, session } = setup(t);
  recordRequests(t);
  const { runtime } = start();
  const agent = { session: session(shut, "disabled"), status: "idle", inject() {} };
  assert.equal(await runtime.profileMessage(agent), null);
  const message = { role: "user", content: [{ type: "text", text: "remember this" }], source: { kind: "user" } };
  assert.equal(await runtime.recallMessage(agent, [message]), null);
});

test("a neighboring workspace keeps capturing and recalling on one runtime", async t => {
  const { open, shut, start, session } = setup(t);
  const requests = recordRequests(t);
  const { runtime } = start();
  assert.equal(runtime.isDisabled(session(shut, "disabled")), true);
  assert.equal(runtime.isDisabled(session(open, "enabled")), false);

  const agent = { session: session(open, "enabled"), status: "idle", inject() {} };
  await runtime.initialize(agent);
  const message = { role: "user", content: [{ type: "text", text: "Remember the deployment." }], source: { kind: "user" } };
  await runtime.recallMessage(agent, [message]);
  runtime.capture(session(open, "enabled"), userEvent("Remember the deployment."));
  runtime.maybeCommit(session(open, "enabled"), { type: "turn/end" });
  await runtime.flush(session(open, "enabled"));

  const paths = requests.map(req => req.path);
  assert.ok(paths.includes("/api/v1/sessions"), "the session is still created");
  assert.ok(paths.includes("/api/v1/search/search"), "recall still runs");
  assert.ok(
    paths.includes("/api/v1/sessions/dsh-enabled/messages"),
    `capture still goes out: ${paths.join(", ")}`,
  );
  assert.ok(
    paths.some(path => path === "/api/v1/sessions/dsh-enabled/commit"),
    "the commit still happens past the threshold",
  );
  assert.ok(
    !paths.some(path => path.includes("dsh-disabled")),
    "no request may carry the disabled ws session",
  );
});

test("the disabled workspace stays out even when the boot cwd has memory on", async t => {
  const { open, shut, start, session } = setup(t);
  const requests = recordRequests(t);
  const { runtime, handlers } = start();
  // The launch directory has memory on; the switched-off workspace is decided by
  // the session's own cwd alone.
  assert.equal(runtime.config.enabled, true);
  const agent = {
    session: session(shut, "disabled-boot"),
    status: "idle",
    inject() {
      throw new Error("a disabled workspace must not inject a profile");
    },
    ctx: { effect(execute) { execute(); } },
    inbox: { nextTurn: [], nextStep: [] },
  };
  await handlers.get("agent/created")?.({ agent, source: "user" });
  handlers.get("agent/pre-step")?.({
    agent,
    messages: [userEvent("hi")],
    signal: new AbortController().signal,
  }, async () => ({ kind: "enter", messages: [userEvent("hi")] }));
  assert.deepEqual(requests, []);
});
