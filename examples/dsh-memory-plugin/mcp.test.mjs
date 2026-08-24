import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { Config } from "@deepseek-ai/dsh-mcp-client";
import { resolveConfig } from "./config.mjs";
import { apply } from "./index.mjs";
import { buildMcpConfig, mountOpenVikingMcp, PROXY_PATH } from "./mcp.mjs";

test("the mount config validates against the pinned bridge's own schema", () => {
  const config = resolveConfig({
    endpoint: "http://127.0.0.1:1933",
    apiKey: "secret",
    workspacePeer: false,
  }, {});
  const parsed = Config(buildMcpConfig(config));

  // stdio, not streamable-http: every other OpenViking integration reaches the
  // server through this same proxy, and it owns the transport quirks.
  assert.equal(parsed.transport, "stdio");
  assert.equal(parsed.serverName, "openviking");
  assert.equal(parsed.command, process.execPath);
  assert.deepEqual(parsed.args, [PROXY_PATH]);
  assert.equal(parsed.toolCallTimeoutMs, 60000);
  // Startup must not be fatal: recall, capture, and commit keep working when
  // only the MCP endpoint is down, and the bridge reconnects on its own.
  assert.equal(parsed.failOnStartupError, false);
  assert.equal(parsed.reconnect.enabled, true);
});

test("the shipped proxy entrypoint exists at the mounted path", () => {
  assert.equal(existsSync(PROXY_PATH), true);
});

test("credentials resolved by the plugin reach the proxy through the child env", async () => {
  const config = resolveConfig({
    endpoint: "http://ov.example.com/",
    apiKey: "secret",
    account: "acme",
    user: "casey",
    peerId: "workspace-peer",
  }, {});
  const { env } = buildMcpConfig(config);

  assert.deepEqual(env, {
    OPENVIKING_URL: "http://ov.example.com",
    OPENVIKING_API_KEY: "secret",
    OPENVIKING_ACCOUNT: "acme",
    OPENVIKING_USER: "casey",
    OPENVIKING_PEER_ID: "workspace-peer",
  });

  // The proxy must reconstruct the same target from that env alone — DSH
  // scrubs credential-shaped names out of the inherited environment, so this
  // is the only channel patch-provided values travel on.
  const { readProxyConfig } = await import("./servers/mcp-proxy.mjs");
  const proxy = readProxyConfig(env, "/workspace");
  assert.equal(proxy.mcpUrl, "http://ov.example.com/mcp");
  assert.equal(proxy.apiKey, "secret");
  assert.equal(proxy.account, "acme");
  assert.equal(proxy.user, "casey");
  assert.equal(proxy.peerId, "workspace-peer");
});

test("an anonymous local server forwards no credential env", () => {
  // Built from a literal rather than resolveConfig: the credential chain
  // reaches ~/.openviking/*.conf, so a developer's own key would leak in.
  const { env } = buildMcpConfig({
    endpoint: "http://127.0.0.1:1933",
    mcpToolCallTimeoutMs: 60000,
  });

  assert.deepEqual(env, { OPENVIKING_URL: "http://127.0.0.1:1933" });
});

test("mounting passes the bridge plugin and its config to the host context", () => {
  const mounted = [];
  mountOpenVikingMcp(
    { plugin: (plugin, config) => mounted.push({ plugin, config }) },
    resolveConfig({ endpoint: "http://127.0.0.1:1933", workspacePeer: false }, {}),
  );

  assert.equal(mounted.length, 1);
  assert.equal(mounted[0].plugin.name, "mcp-client");
  assert.equal(typeof mounted[0].plugin.apply, "function");
  assert.equal(mounted[0].config.serverName, "openviking");
});

test("apply mounts the tool surface instead of registering tools itself", () => {
  const mounted = [];
  const registered = [];
  apply({
    logger: { debug() {} },
    provide() {},
    effect(execute) {
      execute();
      return async () => {};
    },
    plugin: (plugin, config) => mounted.push({ plugin, config }),
    tools: { register: definition => registered.push(definition) },
    on() {},
  }, { endpoint: "http://127.0.0.1:1933", workspacePeer: false });

  assert.deepEqual(registered, []);
  const bridge = mounted.find(entry => entry.plugin.name === "mcp-client");
  assert.ok(bridge, "the MCP bridge must be mounted");
  assert.equal(bridge.config.env.OPENVIKING_URL, "http://127.0.0.1:1933");
});
