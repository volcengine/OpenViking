import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { apply } from "./index.mjs";

function setup(t) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "dsh-session-workspace-")));
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
  const a = join(root, "a");
  const b = join(root, "b");
  const writeConfig = (cwd, value, file = "config.json") => {
    mkdirSync(join(cwd, ".openviking"), { recursive: true });
    writeFileSync(join(cwd, ".openviking", file), JSON.stringify({ version: 1, ...value }));
  };
  writeConfig(a, { peer: { id: "project-a", source: "a-{dir}" } });
  mkdirSync(b);
  t.mock.method(process, "cwd", () => a);
  const runtimes = [];
  t.after(() => {
    for (const runtime of runtimes) runtime.stopDrainer();
    process.env = originalEnv;
    rmSync(root, { recursive: true, force: true });
  });
  const start = (input = {}) => {
    let runtime;
    apply({
      logger: { debug() {} },
      provide(name, value) {
        if (name === "openvikingMemory") runtime = value;
      },
      effect(execute) { execute(); },
      on() {},
      plugin() {},
    }, { endpoint: "http://openviking.test", ...input });
    runtimes.push(runtime);
    return runtime;
  };
  const session = (cwd, id = cwd) => ({ id, header: { cwd } });
  return { root, a, b, writeConfig, start, session };
}

test("sessions load their own workspace peer and keep it for their lifetime", t => {
  const { a, b, writeConfig, start, session } = setup(t);
  writeConfig(b, { peer: { id: "project-b", source: "b-{dir}" } });
  const runtime = start();
  assert.equal(runtime.config.peerId, "project-a");
  const stateA = runtime.stateFor(session(a));
  const stateB = runtime.stateFor(session(b));
  assert.equal(stateA.config.peerId, "project-a");
  assert.equal(stateB.config.peerId, "project-b");
  assert.equal(stateB.config.legacyPeerId, "");

  writeConfig(b, { peer: { id: "project-b-updated" } });
  assert.equal(runtime.stateFor(session(b)), stateB);
  assert.equal(stateB.config.peerId, "project-b");
  assert.equal(runtime.stateFor(session(b, "new-b")).config.peerId, "project-b-updated");
  assert.equal(runtime.stateFor(session(a)), stateA);
  assert.equal(runtime.stateFor({ id: "no-cwd" }).config.peerId, "project-a");
});

test("a session never inherits the launch workspace's peer id or source", t => {
  const { b, writeConfig, start, session } = setup(t);
  writeConfig(b, {});
  const runtime = start();
  // A non-git workspace with no peer settings has no project peer.
  assert.equal(runtime.stateFor(session(b, "plain")).config.peerId, "");
  writeConfig(b, { peer: { source: ["{git_remote}", "b-{dir}"] } });
  assert.equal(runtime.stateFor(session(b, "template")).config.peerId, "b-b");
  writeConfig(b, { peer: { source: "none" } });
  assert.equal(runtime.stateFor(session(b, "none")).config.peerId, "");
  writeConfig(b, { peer: { id: "team-b" } });
  writeConfig(b, { peer: { id: "local-b" } }, "config.local.json");
  assert.equal(runtime.stateFor(session(b, "local")).config.peerId, "local-b");
});

test("session peer resolution preserves host, environment and pinned credential precedence", t => {
  const { root, b, writeConfig, start, session } = setup(t);
  writeConfig(b, { peer: { id: "project-b" } });
  process.env.OPENVIKING_PEER_ID = "env-peer";
  assert.equal(start().stateFor(session(b)).config.peerId, "env-peer");
  assert.equal(start({ peerId: "host-peer" }).stateFor(session(b)).config.peerId, "host-peer");

  // Pinning credentials to ovcli.conf intentionally excludes the env peer.
  process.env.OPENVIKING_CREDENTIAL_SOURCE = "ovcli";
  writeFileSync(join(root, "ovcli.conf"), JSON.stringify({ actor_peer_id: "account-peer" }));
  assert.equal(start().stateFor(session(b)).config.peerId, "account-peer");
  assert.equal(start({ peerId: "host-peer" }).stateFor(session(b)).config.peerId, "host-peer");
  delete process.env.OPENVIKING_PEER_ID;
  assert.equal(start().stateFor(session(b)).config.peerId, "project-b");
  writeConfig(b, {});
  assert.equal(start().stateFor(session(b)).config.peerId, "account-peer");

  delete process.env.OPENVIKING_CREDENTIAL_SOURCE;
  writeFileSync(join(root, "ovcli.conf"), JSON.stringify({ plugin: { dsh: { peerSource: "global-{dir}" } } }));
  assert.equal(start().stateFor(session(b)).config.peerId, "global-b");
  writeConfig(b, { peer: { source: "workspace-{dir}" } });
  process.env.OPENVIKING_PEER_SOURCE = "env-{dir}";
  assert.equal(start().stateFor(session(b)).config.peerId, "env-b");
  process.env.OPENVIKING_WORKSPACE_PEER = "0";
  assert.equal(start().stateFor(session(b)).config.peerId, "");
});

test("capture, recall and commit send each session's peer on the wire", async t => {
  const { a, b, writeConfig, start, session } = setup(t);
  writeConfig(b, { peer: { id: "project-b" } });
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    const path = new URL(url).pathname;
    const body = init.body ? JSON.parse(init.body) : null;
    requests.push({ path, body, peer: new Headers(init.headers).get("X-OpenViking-Actor-Peer") });
    let result = {};
    if (path === "/api/v1/search/search") result = { rendered: "" };
    else if (path === "/api/v1/fs/ls") result = [];
    else if (path.startsWith("/api/v1/sessions/") && !init.method) result = { pending_tokens: 20000 };
    return new Response(JSON.stringify({ status: "ok", result }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  const runtime = start();
  for (const [cwd, id, peer] of [[a, "a", "project-a"], [b, "b", "project-b"]]) {
    const current = session(cwd, id);
    const message = { role: "user", content: [{ type: "text", text: `Remember the deployment for project ${id}.` }], source: { kind: "user" } };
    const before = requests.length;
    await runtime.recallMessage({ session: current }, [message]);
    runtime.capture(current, { type: "user/message", data: message });
    runtime.maybeCommit(current, { type: "turn/end" });
    await runtime.flush(current);

    const sent = requests.slice(before);
    const created = sent.find(req => req.path === "/api/v1/sessions");
    assert.equal(created.body.session_id, `dsh-${id}`);
    const recall = sent.find(req => req.path === "/api/v1/search/search");
    const capture = sent.find(req => req.path === `/api/v1/sessions/dsh-${id}/messages`);
    const commit = sent.find(req => req.path === `/api/v1/sessions/dsh-${id}/commit`);
    for (const req of [created, recall, capture, commit]) {
      assert.ok(req, `missing session request for ${id}`);
      assert.equal(req.peer, peer, req.path);
    }
    assert.equal(capture.body.peer_id, peer);
  }
});
