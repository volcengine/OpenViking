import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import test from "node:test";
import OpenVikingMemory from "../index.mjs";
import { injectContext, loadConfig, recallRequest, request } from "../runtime.mjs";
import { unknownPluginKeys } from "../../memory-plugin-shared/lib/doctor-core.mjs";
import { buildOvHeaders } from "../shared/ov-http.mjs";

async function fixture(t, handler = () => ({ rendered: "viking://~/memories/preference.md\nUse TypeScript." })) {
  const dir = await mkdtemp(join(tmpdir(), "ov-magpie-test-"));
  const calls = [];
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const call = { path: req.url, headers: req.headers, body: raw ? JSON.parse(raw) : null };
    calls.push(call);
    const result = await handler(call, res);
    if (!res.writableEnded && !res.destroyed) {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ status: "ok", result }));
    }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(async () => { server.closeAllConnections(); await new Promise((r) => server.close(r)); await rm(dir, { recursive: true }); });
  const path = join(dir, "ovcli.conf");
  const config = { url: `http://127.0.0.1:${server.address().port}`, api_key: "test-secret", account: "test-account", user: "test-user" };
  const save = async (patch = {}) => writeFile(path, JSON.stringify({ ...config, ...patch }));
  await save();
  return { dir, path, env: { OPENVIKING_CLI_CONFIG_FILE: path }, save, calls, config };
}

function context(patch = {}) {
  const reports = [];
  return { origin: "local", protocol: "chat", query: "Which language do I prefer?", signal: new AbortController().signal,
    report: (...args) => reports.push(args), reports, ...patch };
}
const body = () => ({ model: "test-model", stream: true, tools: [{ type: "function", function: { name: "test" } }],
  messages: [{ role: "user", content: "Which language do I prefer?" }] });

test("the shared doctor recognizes Magpie's documented behavior configuration", () => {
  assert.deepEqual(unknownPluginKeys({ magpie: { enabled: true, autoRecall: true,
    recallContextTimeoutMs: 700, recallMaxTokens: 1600, recallPeerScope: "actor", mcpEnabled: false } }), []);
});

test("configuration is read-only, file-pinned and reloaded after credential rotation", async (t) => {
  const f = await fixture(t);
  const before = await readFile(f.path, "utf8");
  const cfg = await loadConfig({ ...f.env, OPENVIKING_API_KEY: "wrong-key", OPENVIKING_URL: "https://wrong.example", OPENVIKING_AUTH_MODE: "api_key" });
  assert.equal(buildOvHeaders(cfg).Authorization, "Bearer test-secret");
  assert.equal(buildOvHeaders(cfg)["X-OpenViking-User"], "test-user");
  assert.equal(cfg.baseUrl, f.config.url);
  assert.equal(await readFile(f.path, "utf8"), before);
  await f.save({ api_key: "rotated", user: "other-user" });
  assert.equal(buildOvHeaders(await loadConfig(f.env)).Authorization, "Bearer rotated");
  await writeFile(f.path, "broken");
  await assert.rejects(loadConfig(f.env), /invalid/);
});

test("missing/invalid configurations never use localhost or root credentials", async (t) => {
  const f = await fixture(t);
  await assert.rejects(loadConfig({ OPENVIKING_CLI_CONFIG_FILE: join(f.dir, "missing") }), /unconfigured/);
  for (const url of ["", "file:///tmp/x", "http://user:secret@localhost", "https://example.com?token=x"]) {
    await f.save({ url });
    await assert.rejects(loadConfig(f.env), /invalid/);
  }
  await f.save({ api_key: null, root_api_key: "never-use", gateway_token: "never-use" });
  assert.equal(buildOvHeaders(await loadConfig(f.env)).Authorization, undefined);
  assert.equal(f.calls.length, 0);
  await f.save({ api_key: "secret\ninvalid" });
  await assert.rejects(loadConfig(f.env), { message: "OpenViking: invalid" });
});

test("extra headers support gateways but cannot replace authentication or identity", async (t) => {
  const f = await fixture(t);
  await f.save({ extra_headers: { "X-Vault": "tenant", authorization: "wrong", "X-API-Key": "wrong-key", "X-OpenViking-User": "wrong" } });
  const cfg = await loadConfig(f.env);
  await request(cfg, "/test");
  assert.equal(f.calls[0].headers["x-vault"], "tenant");
  assert.equal(f.calls[0].headers.authorization, "Bearer test-secret");
  assert.equal(f.calls[0].headers["x-api-key"], undefined);
  assert.equal(f.calls[0].headers["x-openviking-user"], "test-user");
});

test("existing OIDC and LDAP client files authenticate both recall and tools", async (t) => {
  const f = await fixture(t);
  const previous = process.env.OPENVIKING_CLI_CONFIG_FILE;
  process.env.OPENVIKING_CLI_CONFIG_FILE = f.path;
  t.after(() => { if (previous === undefined) delete process.env.OPENVIKING_CLI_CONFIG_FILE; else process.env.OPENVIKING_CLI_CONFIG_FILE = previous; });
  const { tool } = await OpenVikingMemory();
  for (const [fields, authorization] of [
    [{ auth_mode: "oidc", api_key: null, oidc_token: "fixture-jwt" }, "Bearer fixture-jwt"],
    [{ auth_mode: "ldap", api_key: null, ldap_username: "alice", ldap_password: "p:a:ss" }, `Basic ${Buffer.from("alice:p:a:ss").toString("base64")}`],
  ]) {
    await f.save({ ...fields, extra_headers: { "X-API-Key": "wrong", Authorization: "wrong" }, plugin: { magpie: { mcpEnabled: true } } });
    assert.ok(await recallRequest(body(), context(), f.env));
    await tool.find.execute({ query: "preference" }, { abort: new AbortController().signal });
    for (const call of f.calls.slice(-2)) {
      assert.equal(call.headers.authorization, authorization);
      assert.equal(call.headers["x-api-key"], undefined);
      assert.equal(call.headers["x-openviking-user"], undefined);
    }
  }
});

test("tool errors retain safe status/category and trace IDs while hooks fail open", async (t) => {
  let status = 404;
  const f = await fixture(t, (_, res) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: { message: "private credential fixture-secret", trace_id: "trace-fixture" } }));
  });
  const cfg = await loadConfig(f.env);
  for (const [httpStatus, category] of [[404, "not_found"], [409, "conflict"], [429, "rate_limited"], [500, "unavailable"]]) {
    status = httpStatus;
    await assert.rejects(request(cfg, "/test"), (error) => {
      assert.equal(error.code, category);
      assert.equal(error.status, httpStatus);
      assert.equal(error.traceId, "trace-fixture");
      assert.doesNotMatch(error.message, /fixture-secret/);
      assert.match(error.message, new RegExp(`HTTP ${httpStatus}`));
      return true;
    });
    const ctx = context();
    assert.equal(await recallRequest(body(), ctx, f.env), undefined);
    assert.deepEqual(ctx.reports, [["unavailable", 0]]);
  }
  const controller = new AbortController(); controller.abort();
  await assert.rejects(request(cfg, "/test", { signal: controller.signal }), { code: "cancelled" });
  const slow = await fixture(t, () => new Promise((r) => setTimeout(() => r({}), 250)));
  await assert.rejects(request(await loadConfig(slow.env), "/test", { timeoutMs: 100 }), { code: "timeout" });
});

test("remember places the configured project peer in message data, not just headers", async (t) => {
  const f = await fixture(t, (call) => {
    if (call.path.endsWith("/messages")) {
      // The real Session API deliberately ignores actor-view headers.
      assert.equal(call.body.peer_id, "project-A");
    }
    return { task_id: "task-1" };
  });
  await f.save({ actor_peer_id: "project-A", plugin: { magpie: { mcpEnabled: true } } });
  const previous = process.env.OPENVIKING_CLI_CONFIG_FILE;
  process.env.OPENVIKING_CLI_CONFIG_FILE = f.path;
  t.after(() => { if (previous === undefined) delete process.env.OPENVIKING_CLI_CONFIG_FILE; else process.env.OPENVIKING_CLI_CONFIG_FILE = previous; });
  const { tool } = await OpenVikingMemory();
  await tool.remember.execute({ text: "project setting" }, { abort: new AbortController().signal });
  assert.equal(f.calls[1].body.peer_id, "project-A");
});

test("recall calls the context API with one consistent identity and leaves input untouched", async (t) => {
  const f = await fixture(t);
  const input = body(), original = structuredClone(input), ctx = context();
  const output = await recallRequest(input, ctx, f.env);
  assert.deepEqual(input, original);
  assert.deepEqual(output.tools, original.tools);
  assert.equal(output.model, original.model);
  assert.equal(output.stream, original.stream);
  assert.match(output.messages[0].content, /Use TypeScript/);
  assert.deepEqual(f.calls[0].body, { query: ctx.query, mode: "context", purpose: "coding", score_threshold: 0.35 });
  assert.equal(ctx.reports[0][0], "ready");
});

test("shared, forwarded and unknown callers cannot receive owner memories", async (t) => {
  const f = await fixture(t);
  for (const origin of ["gateway-key", "remote", "", undefined]) {
    const ctx = context({ origin });
    assert.equal(await recallRequest(body(), ctx, f.env), undefined);
    assert.deepEqual(ctx.reports, [["unbound", 0]]);
  }
  assert.equal(f.calls.length, 0);
  assert.ok(await recallRequest(body(), context({ origin: "magpie-key" }), f.env));
});

for (const protocol of ["chat", "responses", "anthropic", "gemini"]) {
  test(`${protocol}: preserves system context, multimodal/tool data and avoids duplicate injection`, () => {
    const input = {
      ...body(), instructions: "existing instructions", system: [{ type: "text", text: "existing", cache_control: { type: "ephemeral" } }],
      systemInstruction: { role: "system", parts: [{ text: "existing" }] },
      input: [{ type: "function_call_output", call_id: "c1", output: "result" }],
      contents: [{ role: "user", parts: [{ inlineData: { mimeType: "image/png", data: "abc" } }] }],
    };
    const original = structuredClone(input);
    const out = injectContext(input, protocol, "remembered");
    assert.ok(JSON.stringify(out).includes("remembered"));
    assert.deepEqual(out.tools, input.tools);
    assert.deepEqual(out.input, input.input);
    assert.deepEqual(out.contents, input.contents);
    assert.deepEqual(input, original);
    assert.equal(injectContext(out, protocol, "remembered"), undefined);
    if (protocol === "anthropic") assert.deepEqual(out.system[0], original.system[0]);
  });
}

test("unsupported shapes and disabled/empty recall keep the request", async (t) => {
  assert.equal(injectContext({}, "unsupported", "memory"), undefined);
  assert.equal(injectContext({ instructions: [] }, "responses", "memory"), undefined);
  const f = await fixture(t, () => ({ rendered: "" }));
  const ctx = context();
  assert.equal(await recallRequest(body(), ctx, f.env), undefined);
  assert.deepEqual(ctx.reports, [["empty", 0]]);
  await f.save({ plugin: { magpie: { autoRecall: false } } });
  assert.equal(await recallRequest(body(), context(), f.env), undefined);
  assert.equal(f.calls.length, 1);
});

test("actor scope needs an explicit peer and does not infer identity from cwd", async (t) => {
  const f = await fixture(t);
  await f.save({ plugin: { magpie: { recallPeerScope: "actor" } } });
  assert.equal(await recallRequest(body(), context({ cwd: "/private/project" }), f.env), undefined);
  assert.equal(f.calls.length, 0);
  await f.save({ actor_peer_id: "my-project", plugin: { magpie: { recallPeerScope: "actor" } } });
  await recallRequest(body(), context(), f.env);
  assert.equal(f.calls[0].headers["x-openviking-actor-peer"], "my-project");
  assert.equal(f.calls[0].body.peer_scope, "actor");
});

test("upstream errors, invalid JSON and cancellation fail open without leaking private text", async (t) => {
  for (const status of [401, 403, 500]) {
    const f = await fixture(t, (_, res) => { res.writeHead(status); res.end("private test-secret"); });
    const ctx = context();
    assert.equal(await recallRequest(body(), ctx, f.env), undefined);
    assert.deepEqual(ctx.reports, [[[401, 403].includes(status) ? "unauthorized" : "unavailable", 0]]);
  }
  const f = await fixture(t, (_, res) => { res.end("not JSON test-secret"); });
  const ctx = context();
  assert.equal(await recallRequest(body(), ctx, f.env), undefined);
  assert.equal(JSON.stringify(ctx.reports).includes("test-secret"), false);
  const controller = new AbortController(); controller.abort();
  assert.equal(await recallRequest(body(), context({ signal: controller.signal }), f.env), undefined);
});

test("slow recall respects its timeout; redirects do not forward credentials", async (t) => {
  const slow = await fixture(t, () => new Promise((r) => setTimeout(() => r({ rendered: "late" }), 250)));
  await slow.save({ plugin: { magpie: { recallContextTimeoutMs: 100 } } });
  const started = performance.now();
  assert.equal(await recallRequest(body(), context(), slow.env), undefined);
  assert.ok(performance.now() - started < 1000);
  const target = await fixture(t);
  const redirect = await fixture(t, (_, res) => { res.writeHead(302, { Location: target.config.url }); res.end(); });
  await assert.rejects(request(await loadConfig(redirect.env), "/test"), /unavailable/);
  assert.equal(target.calls.length, 0);
});

test("concurrent identities cannot share recalled results or credentials", async (t) => {
  const a = await fixture(t, () => ({ rendered: "memory-A" }));
  const b = await fixture(t, () => ({ rendered: "memory-B" }));
  await b.save({ api_key: "key-B", user: "user-B" });
  const [outA, outB] = await Promise.all([recallRequest(body(), context(), a.env), recallRequest(body(), context(), b.env)]);
  assert.match(outA.messages[0].content, /memory-A/);
  assert.doesNotMatch(outA.messages[0].content, /memory-B/);
  assert.match(outB.messages[0].content, /memory-B/);
  assert.equal(b.calls[0].headers.authorization, "Bearer key-B");
});

test("tools require opt-in, validate arguments and use the same file connection", async (t) => {
  const f = await fixture(t, (call) => ({ path: call.path, task_id: "task-1" }));
  const previous = process.env.OPENVIKING_CLI_CONFIG_FILE;
  process.env.OPENVIKING_CLI_CONFIG_FILE = f.path;
  t.after(() => { if (previous === undefined) delete process.env.OPENVIKING_CLI_CONFIG_FILE; else process.env.OPENVIKING_CLI_CONFIG_FILE = previous; });
  const { tool } = await OpenVikingMemory();
  const ctx = { abort: new AbortController().signal };
  await assert.rejects(tool.find.execute({ query: "test" }, ctx), /disabled/);
  await f.save({ plugin: { mcpEnabled: true } });
  await assert.rejects(tool.find.execute({ query: "test" }, ctx), /disabled/);
  await f.save({ plugin: { magpie: { mcpEnabled: true } } });
  assert.deepEqual(await tool.status.execute({}, ctx), { status: "connected", recallEnabled: true });
  await tool.find.execute({ query: "preference" }, ctx);
  await tool.read.execute({ uri: "viking://~/memories/a b.md" }, ctx);
  await tool.ls.execute({ uri: "viking://~/memories" }, ctx);
  const count = f.calls.length;
  for (const args of [{ query: 123 }, { query: "" }, { query: "q", user: "other" }]) {
    await assert.rejects(tool.find.execute(args, ctx), /invalid/);
  }
  await assert.rejects(tool.read.execute({ uri: "https://example.com" }, ctx), /invalid/);
  assert.equal(f.calls.length, count);
  assert.equal((await tool.remember.execute({ text: "Remember TypeScript" }, ctx)).task_id, "task-1");
  const writes = f.calls.slice(-3);
  assert.equal(writes[0].path, "/api/v1/sessions");
  assert.equal(writes[0].body.auto_commit_policy, null);
  assert.equal(writes[1].body.role, "user");
  assert.match(writes[2].path, /\/commit$/);
  assert.ok(f.calls.every((c) => c.headers.authorization === "Bearer test-secret"));
});

test("actual Magpie Bun host loads package, runs recall and exposes working tools", {
  skip: !process.env.MAGPIE_PLUGIN_HOST,
}, async (t) => {
  const f = await fixture(t);
  await f.save({ plugin: { magpie: { mcpEnabled: true } } });
  const target = process.env.MAGPIE_PLUGIN_TARGET || fileURLToPath(new URL("..", import.meta.url));
  const child = spawn(process.env.BUN_BIN || "bun", [process.env.MAGPIE_PLUGIN_HOST], {
    env: { ...process.env, ...f.env, HOME: f.dir, XDG_CONFIG_HOME: join(f.dir, ".config") },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const pending = new Map();
  let id = 0, stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const lines = createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    const message = JSON.parse(line);
    const settle = pending.get(message.id);
    if (settle) { pending.delete(message.id); settle(message); }
  });
  t.after(() => { lines.close(); child.kill(); });
  const rpc = async (method, params) => {
    const key = ++id;
    const promise = new Promise((r) => pending.set(key, r));
    child.stdin.write(JSON.stringify({ id: key, method, params }) + "\n");
    let timer;
    try {
      const message = await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Magpie host timed out: ${stderr}`)), 10000);
      })]);
      assert.equal(message.error, undefined, JSON.stringify(message.error));
      return message.result;
    } finally { clearTimeout(timer); pending.delete(key); }
  };
  const spec = "openviking-test";
  const init = await rpc("init", { directory: f.dir, authPath: join(f.dir, "auth.json"), plugins: [
    { spec, target, gateway: join(target, "hooks.mjs"), options: { gatewayTimeoutMs: 1500 } },
  ] });
  assert.equal(init.plugins[0].error, undefined, JSON.stringify(init));
  assert.equal(init.plugins[0].tools.length, 5);
  for (const protocol of ["chat", "responses", "anthropic", "gemini"]) {
    const run = await rpc("gatewayRequest", { operation: "begin", body: body(),
      context: { origin: "local", protocol, query: "TypeScript?" } });
    assert.match(JSON.stringify(run.body), /Use TypeScript/);
    assert.deepEqual(run.body.tools, body().tools);
    await rpc("gatewayRequest", { operation: "close", id: run.id, token: run.token });
  }
  const result = await rpc("tool", { spec, name: "find", args: { query: "TypeScript" }, ms: 5000 });
  assert.match(result.text, /Use TypeScript/);
  const states = await rpc("gatewayStates", {});
  assert.equal(states[spec].failures, 0);
  assert.equal(states[spec].lastOutcome, "ready");
});
