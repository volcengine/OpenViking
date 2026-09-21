import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Config } from "@deepseek-ai/dsh-mcp-client";
import { resolveConfig } from "./config.mjs";
import { apply } from "./index.mjs";
import { buildMcpConfig, mountOpenVikingMcp, PROXY_PATH } from "./mcp.mjs";

/**
 * Run `fn` with an env whose credential files live in a throwaway directory,
 * so a developer's own ~/.openviking never leaks into what is forwarded.
 */
function withCredentialFiles({ ovcli = null, ov = null } = {}, fn) {
  const dir = mkdtempSync(join(tmpdir(), "ov-dsh-mcp-"));
  try {
    const env = {
      OPENVIKING_CLI_CONFIG_FILE: join(dir, "ovcli.conf"),
      OPENVIKING_CONFIG_FILE: join(dir, "ov.conf"),
      OPENVIKING_HOME: join(dir, "home"),
    };
    if (ovcli) writeFileSync(env.OPENVIKING_CLI_CONFIG_FILE, JSON.stringify(ovcli));
    if (ov) writeFileSync(env.OPENVIKING_CONFIG_FILE, JSON.stringify(ov));
    return fn({ env, dir });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

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
  assert.equal(parsed.env.ELECTRON_RUN_AS_NODE, "1");
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
  const { readProxyConfig } = await import("./servers/mcp-proxy.mjs");
  await withCredentialFiles({}, ({ env: files, dir }) => {
    const config = resolveConfig({
      endpoint: "http://ov.example.com/",
      apiKey: "secret",
      account: "acme",
      user: "casey",
      peerId: "workspace-peer",
    }, files, dir);
    const { env } = buildMcpConfig(config);

    assert.deepEqual(env, {
      ELECTRON_RUN_AS_NODE: "1",
      OPENVIKING_CREDENTIAL_SOURCE: "env",
      OPENVIKING_URL: "http://ov.example.com",
      OPENVIKING_BASE_URL: "",
      OPENVIKING_MCP_URL: "http://ov.example.com/mcp",
      OPENVIKING_BEARER_TOKEN: "",
      OPENVIKING_API_KEY: "secret",
      OPENVIKING_ACCOUNT: "acme",
      OPENVIKING_USER: "casey",
      OPENVIKING_PEER_ID: "workspace-peer",
      OPENVIKING_AUTH_MODE: "trusted",
      OPENVIKING_TIMEOUT_MS: "10000",
    });

    // The proxy must reconstruct the same target from that env alone — DSH
    // scrubs credential-shaped names out of the inherited environment, so this
    // is the only channel patch-provided values travel on.
    const proxy = readProxyConfig(env, dir);
    assert.equal(proxy.mcpUrl, "http://ov.example.com/mcp");
    assert.equal(proxy.apiKey, "secret");
    assert.equal(proxy.account, "acme");
    assert.equal(proxy.user, "casey");
    assert.equal(proxy.sendIdentityHeaders, true);
    assert.equal(proxy.peerId, "workspace-peer");
  });
});

test("a key the plugin left empty stays empty in the proxy", async () => {
  // ovcli.conf naming only a url pins the chain to that file, so the runtime
  // sends no key. The proxy's env names a url, which would have unpinned the
  // chain there and let it fall through to the machine's root key.
  const { readProxyConfig } = await import("./servers/mcp-proxy.mjs");
  await withCredentialFiles({
    ovcli: { url: "https://ov.example.com" },
    ov: { server: { root_api_key: "root-key" } },
  }, ({ env: files, dir }) => {
    const config = resolveConfig({ workspacePeer: false }, files, dir);
    assert.equal(config.apiKey, "");

    // The file paths stand in for the default ~/.openviking the child can
    // still reach.
    const proxy = readProxyConfig({ ...files, ...buildMcpConfig(config).env }, dir);
    assert.equal(proxy.mcpUrl, "https://ov.example.com/mcp");
    assert.equal(proxy.apiKey, "");
  });
});

test("what DSH lets the proxy inherit cannot fill a gap the runtime left", async () => {
  const { readProxyConfig } = await import("./servers/mcp-proxy.mjs");
  await withCredentialFiles({ ovcli: { url: "https://ov.example.com", api_key: "cli-key" } }, ({ env: files, dir }) => {
    // DSH drops only names matching /KEY|PASSWORD|SECRET|TOKEN/i from what a
    // child inherits, so these reach the proxy beside the forwarded ones. The
    // repository makes the launch directory one a peer could be derived from.
    const repo = join(dir, "repo");
    mkdirSync(join(repo, ".git"), { recursive: true });
    writeFileSync(join(repo, ".git", "HEAD"), "ref: refs/heads/main\n");
    const inherited = {
      ...files,
      OPENVIKING_CREDENTIALS_SOURCE: "cli",
      OPENVIKING_ACCOUNT: "stale-account",
      OPENVIKING_USER: "stale-user",
      OPENVIKING_PEER_ID: "stale-peer",
    };
    const config = resolveConfig({ workspacePeer: false }, inherited, dir);
    const seen = (c) => ({ account: c.account, user: c.user, peerId: c.peerId, sendIdentityHeaders: c.sendIdentityHeaders });
    assert.deepEqual(seen(config), { account: "", user: "", peerId: "", sendIdentityHeaders: false });

    const proxy = readProxyConfig({ ...inherited, ...buildMcpConfig(config).env }, repo);
    assert.deepEqual(seen(proxy), seen(config));
  });
});

test("the auth mode and timeout the host named reach the proxy too", async () => {
  const { readProxyConfig } = await import("./servers/mcp-proxy.mjs");
  await withCredentialFiles({}, ({ env: files, dir }) => {
    const config = resolveConfig({
      endpoint: "http://ov.example.com",
      apiKey: "secret",
      account: "acme",
      user: "casey",
      authMode: "api_key",
      timeoutMs: 45000,
    }, { ...files, OPENVIKING_AUTH_MODE: "trusted" }, dir);
    assert.equal(config.sendIdentityHeaders, false, "the host's answer outranks the environment");

    // Left to derive them, the proxy would call an account and user "trusted"
    // and fall back to the harness default timeout.
    const proxy = readProxyConfig(buildMcpConfig(config).env, dir);
    assert.equal(proxy.sendIdentityHeaders, false);
    assert.equal(proxy.timeoutMs, 45000);
  });
});

test("an anonymous local server forwards no key or identity", () => {
  withCredentialFiles({}, ({ env: files, dir }) => {
    const { env } = buildMcpConfig(resolveConfig({ endpoint: "http://127.0.0.1:1933", workspacePeer: false }, files, dir));

    assert.deepEqual(env, {
      ELECTRON_RUN_AS_NODE: "1",
      OPENVIKING_CREDENTIAL_SOURCE: "env",
      OPENVIKING_URL: "http://127.0.0.1:1933",
      OPENVIKING_BASE_URL: "",
      OPENVIKING_MCP_URL: "http://127.0.0.1:1933/mcp",
      OPENVIKING_BEARER_TOKEN: "",
      OPENVIKING_API_KEY: "",
      OPENVIKING_ACCOUNT: "",
      OPENVIKING_USER: "",
      OPENVIKING_PEER_ID: "",
      OPENVIKING_AUTH_MODE: "api_key",
      OPENVIKING_TIMEOUT_MS: "10000",
    });
  });
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
