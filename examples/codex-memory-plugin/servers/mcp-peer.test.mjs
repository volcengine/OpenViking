import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import test from "node:test";

const pluginRoot = fileURLToPath(new URL("../", import.meta.url));

async function runProxy(t, { scope, peer = "", cliPeer } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "ov-mcp-peer-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const configPath = join(dir, "ov.conf");
  writeFileSync(configPath, "{}");
  const requests = [];
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const message = JSON.parse(Buffer.concat(chunks).toString());
    requests.push({ headers: req.headers, message });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { tools: [] } }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith("OPENVIKING_")));
  Object.assign(env, {
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_CONFIG_FILE: configPath,
    OPENVIKING_CLI_CONFIG_FILE: join(dir, "missing.conf"),
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_API_KEY: "test-key",
    OPENVIKING_ACCOUNT: "test-account",
    OPENVIKING_USER: "test-user",
    OPENVIKING_PEER_ID: peer,
  });
  if (scope !== undefined) env.OPENVIKING_RECALL_PEER_SCOPE = scope;
  if (cliPeer !== undefined) {
    const cliPath = join(dir, "ovcli.conf");
    writeFileSync(cliPath, JSON.stringify({
      url: env.OPENVIKING_URL,
      api_key: "cli-key",
      account_id: "cli-account",
      user_id: "cli-user",
      actor_peer_id: cliPeer,
    }));
    env.OPENVIKING_CLI_CONFIG_FILE = cliPath;
    env.OPENVIKING_CREDENTIAL_SOURCE = "cli";
  }
  const child = spawn(process.execPath, [join(pluginRoot, "servers/mcp-proxy.mjs")], {
    cwd: pluginRoot, env, stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => child.kill());
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const lines = createInterface({ input: child.stdout });
  t.after(() => lines.close());
  const closed = once(child, "close").then(([code]) => ({ code, stderr, requests }));
  const response = once(lines, "line").then(([line]) => ({ response: JSON.parse(line) }));
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" })}\n`);
  const outcome = await Promise.race([closed, response]);
  if (!outcome.response) return outcome;
  // Drain both output streams before checking warnings: stdout and stderr
  // are independent pipes and may deliver their data in either order.
  child.stdin.end();
  return { ...await closed, ...outcome };
}

test("broad recall never uses the plugin launch directory as an actor peer", { timeout: 5000 }, async (t) => {
  const result = await runProxy(t);
  assert.ok(result.response, result.stderr);
  assert.equal(result.requests[0].headers["x-openviking-actor-peer"], undefined);
  assert.equal(result.requests[0].headers.authorization, "Bearer test-key");
  assert.equal(result.requests[0].headers["x-openviking-user"], "test-user");
});

test("broad recall remains broad when hooks have an explicit workspace peer", { timeout: 5000 }, async (t) => {
  const result = await runProxy(t, { scope: "all", peer: "workspace-a" });
  assert.ok(result.response, result.stderr);
  assert.equal(result.requests[0].headers["x-openviking-actor-peer"], undefined);
});

test("concurrent actor-scoped proxies keep distinct explicit workspace peers", { timeout: 5000 }, async (t) => {
  const results = await Promise.all([
    runProxy(t, { scope: "actor", peer: "workspace-a" }),
    runProxy(t, { scope: "actor", peer: "workspace-b" }),
  ]);
  for (const result of results) assert.ok(result.response, result.stderr);
  assert.deepEqual(results.map((r) => r.requests[0].headers["x-openviking-actor-peer"]), ["workspace-a", "workspace-b"]);
});

test("actor scope uses the selected ovcli identity instead of competing environment values", { timeout: 5000 }, async (t) => {
  const result = await runProxy(t, { scope: "actor", peer: "env-workspace", cliPeer: "cli-workspace" });
  assert.ok(result.response, result.stderr);
  const headers = result.requests[0].headers;
  assert.equal(headers["x-openviking-actor-peer"], "cli-workspace");
  assert.equal(headers.authorization, "Bearer cli-key");
  assert.equal(headers["x-openviking-account"], "cli-account");
  assert.equal(headers["x-openviking-user"], "cli-user");
});

test("actor scope without explicit identity warns on stderr and falls back to broad recall", { timeout: 5000 }, async (t) => {
  const result = await runProxy(t, { scope: "actor" });
  assert.deepEqual(result.response, { jsonrpc: "2.0", id: 1, result: { tools: [] } });
  assert.match(result.stderr, /explicit peer id/);
  assert.match(result.stderr, /Falling back to broad recall/);
  assert.equal(result.requests[0].headers["x-openviking-actor-peer"], undefined);
});
