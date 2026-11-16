import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { apply } from "./index.mjs";
import { createPendingQueue } from "./shared/pending-queue.mjs";

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
    OPENVIKING_HOME: join(root, "home"),
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

test("capture, recall and commit send each session's connection and peer on the wire", async t => {
  const { root, a, b, writeConfig, start, session } = setup(t);
  writeConfig(b, { peer: { id: "project-b" } });
  writeFileSync(join(root, "ovcli.conf"), JSON.stringify({
    url: "http://a.openviking.test", api_key: "key-a", account: "account-a",
    connections: [{ path: b, url: "http://b.openviking.test", api_key: "key-b", account: "account-b" }],
  }));
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    const path = new URL(url).pathname;
    const body = init.body ? JSON.parse(init.body) : null;
    const headers = new Headers(init.headers);
    requests.push({
      path, body, host: new URL(url).hostname,
      peer: headers.get("X-OpenViking-Actor-Peer"),
      account: headers.get("X-OpenViking-Account"), authorization: headers.get("Authorization"),
    });
    let result = {};
    if (path === "/api/v1/search/search") result = { rendered: "" };
    else if (path === "/api/v1/fs/ls") result = [];
    else if (path.startsWith("/api/v1/sessions/") && !init.method) result = { pending_tokens: 20000 };
    return new Response(JSON.stringify({ status: "ok", result }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  const runtime = start({ endpoint: undefined });
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
      assert.equal(req.host, `${id}.openviking.test`);
      assert.equal(req.account, `account-${id}`);
      assert.equal(req.authorization, `Bearer key-${id}`);
    }
    assert.equal(capture.body.peer_id, peer);
  }
});

test("resumed sessions and offline retries retain their original tenant across cwd changes", async t => {
  const { root, a, b, start, session } = setup(t);
  writeFileSync(join(root, "ovcli.conf"), JSON.stringify({
    url: "http://openviking.test", api_key: "key-a", account: "account-a",
    connections: [{ path: b, api_key: "key-b", account: "account-b" }],
  }));
  const offline = new Set(["account-a", "account-b"]);
  const sent = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    const path = new URL(url).pathname;
    const headers = new Headers(init.headers);
    const account = headers.get("X-OpenViking-Account");
    if (offline.has(account)) return new Response("unavailable", { status: 503 });
    if (path.endsWith("/messages")) {
      sent.push({ account, authorization: headers.get("Authorization"), body: JSON.parse(init.body) });
    }
    return new Response(JSON.stringify({ status: "ok", result: {} }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  const runtime = start();
  const currentA = session(a, "tenant-a");
  const currentB = session(b, "tenant-b");
  for (const current of [currentA, currentB]) {
    runtime.capture(current, {
      type: "user/message", data: {
        role: "user", content: [{ type: "text", text: `Private message for ${current.id}.` }], source: { kind: "user" },
      },
    });
    await runtime.flush(current);
  }
  const queueA = createPendingQueue(runtime.stateFor(currentA).config.connectionScope);
  const queueB = createPendingQueue(runtime.stateFor(currentB).config.connectionScope);
  assert.equal((await queueA.listPending()).length, 1);
  assert.equal((await queueB.listPending()).length, 1);

  offline.delete("account-a");
  await runtime.drainTick();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].account, "account-a");
  assert.equal(sent[0].authorization, "Bearer key-a");
  assert.match(JSON.stringify(sent[0].body), /Private message for tenant-a/);
  assert.equal((await queueA.listPending()).length, 0);
  assert.equal((await queueB.listPending()).length, 1);

  runtime.stopDrainer();
  offline.delete("account-b");
  const resumed = start();
  await resumed.initialize({ session: session(a, currentB.id) });
  assert.equal(resumed.stateFor(session(a, currentB.id)).config.account, "account-b");
  assert.equal(sent.length, 2);
  assert.equal(sent[1].account, "account-b");
  assert.equal(sent[1].authorization, "Bearer key-b");
  assert.match(JSON.stringify(sent[1].body), /Private message for tenant-b/);
  assert.equal((await queueB.listPending()).length, 0);
});

test("a session's workspace recall and capture switches apply to that session", async t => {
  const { b, writeConfig, start, session } = setup(t);
  writeConfig(b, { recall: { enabled: false }, capture: { enabled: false } });
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    const path = new URL(url).pathname;
    requests.push(path);
    const result = path.startsWith("/api/v1/sessions/") && !init.method ? { pending_tokens: 20000 } : {};
    return new Response(JSON.stringify({ status: "ok", result }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  const runtime = start();
  const current = session(b, "quiet");
  const message = { role: "user", content: [{ type: "text", text: "Keep this repository off the server." }], source: { kind: "user" } };
  assert.equal(await runtime.recallMessage({ session: current }, [message]), null);
  runtime.capture(current, { type: "user/message", data: message });
  runtime.maybeCommit(current, { type: "turn/end" });
  await runtime.flush(current);

  assert.ok(!requests.includes("/api/v1/search/search"));
  assert.ok(!requests.includes("/api/v1/sessions/dsh-quiet/messages"));
  assert.ok(!requests.includes("/api/v1/sessions/dsh-quiet/commit"));
});
