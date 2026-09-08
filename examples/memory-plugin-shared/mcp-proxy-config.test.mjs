import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { buildProxyConnection } from "./lib/credentials.mjs";
import {
  buildMcpProxyConfig,
  DEFAULT_PROXY_TIMEOUT_MS,
  defaultCredentialPaths,
  normalizeConfigPath,
  resolveMcpActorPeerId,
  trimSlash,
} from "./lib/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "./lib/mcp-proxy-core.mjs";
import { ROOT } from "./sync.mjs";

const OVCLI = join(homedir(), ".openviking", "ovcli.conf");
const OV = join(homedir(), ".openviking", "ov.conf");

// Every stdio MCP entrypoint in the tree, discovered rather than listed so a
// new harness cannot ship a proxy that skips the shared shaping. The count is
// pinned because a renamed directory would otherwise empty the loop and turn
// the assertions below into a no-op.
const MCP_PROXY_COUNT = 6;

const MCP_PROXIES = [
  ...readdirSync(join(ROOT, "examples"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => `examples/${entry.name}/servers/mcp-proxy.mjs`)
    .filter((rel) => existsSync(join(ROOT, rel))),
  "agent-plugins/servers/mcp-proxy.mjs",
].map((rel) => ({ rel, source: readFileSync(join(ROOT, rel), "utf-8") }));

test("a base URL becomes the /mcp endpoint exactly once", () => {
  assert.equal(buildMcpProxyConfig({ baseUrl: "http://x:1933" }).mcpUrl, "http://x:1933/mcp");
  assert.equal(buildMcpProxyConfig({ baseUrl: "http://x:1933/" }).mcpUrl, "http://x:1933/mcp");
  assert.equal(buildMcpProxyConfig({ baseUrl: "http://x:1933///" }).mcpUrl, "http://x:1933/mcp");
});

test("an explicit MCP URL wins over the derived one", () => {
  const cfg = buildMcpProxyConfig({ baseUrl: "http://x:1933", mcpUrl: "http://y/custom" });
  assert.equal(cfg.mcpUrl, "http://y/custom");
});

test("the timeout is clamped and defaulted", () => {
  assert.equal(buildMcpProxyConfig({}).timeoutMs, DEFAULT_PROXY_TIMEOUT_MS);
  assert.equal(buildMcpProxyConfig({ timeoutMs: 0 }).timeoutMs, DEFAULT_PROXY_TIMEOUT_MS);
  assert.equal(buildMcpProxyConfig({ timeoutMs: "nope" }).timeoutMs, DEFAULT_PROXY_TIMEOUT_MS);
  assert.equal(buildMcpProxyConfig({ timeoutMs: 5 }).timeoutMs, 1000);
  assert.equal(buildMcpProxyConfig({ timeoutMs: 30000 }).timeoutMs, 30000);
});

test("harness paths come first, shared defaults follow, duplicates collapse", () => {
  const cfg = buildMcpProxyConfig({
    watchedPaths: ["/a/config.json", "", null, OVCLI],
    env: {},
  });
  assert.deepEqual(cfg.watchedPaths, ["/a/config.json", OVCLI, OV]);
});

test("the two config-file env overrides are watched and tilde-expanded", () => {
  const cfg = buildMcpProxyConfig({
    env: { OPENVIKING_CLI_CONFIG_FILE: "~/custom/ovcli.conf" },
  });
  assert.equal(cfg.watchedPaths[0], join(homedir(), "custom", "ovcli.conf"));
  assert.ok(cfg.watchedPaths.includes(OV));
});

test("missing fields normalize to the shapes the proxy core expects", () => {
  const cfg = buildMcpProxyConfig({ env: {} });
  assert.equal(cfg.apiKey, "");
  assert.equal(cfg.account, "");
  assert.equal(cfg.user, "");
  assert.equal(cfg.peerId, "");
  assert.equal(cfg.userAgent, "");
  assert.equal(cfg.credentialSource, "auto");
  assert.equal(cfg.credentialPath, "");
  assert.equal(cfg.debug, false);
});

test("debug stays strictly boolean-true opt-in", () => {
  assert.equal(buildMcpProxyConfig({ debug: "true" }).debug, false);
  assert.equal(buildMcpProxyConfig({ debug: 1 }).debug, false);
  assert.equal(buildMcpProxyConfig({ debug: true }).debug, true);
});

test("broad MCP recall does not send an actor peer header", () => {
  assert.equal(resolveMcpActorPeerId({ peerId: "workspace-a", recallPeerScope: "all" }), "");
  assert.equal(resolveMcpActorPeerId({ peerId: "workspace-a" }), "");
});

test("actor-scoped MCP recall takes and trims an explicit peer", () => {
  assert.equal(
    resolveMcpActorPeerId({ peerId: " workspace-a ", recallPeerScope: "actor" }),
    "workspace-a",
  );
});

test("a missing actor peer warns and widens instead of taking the proxy down", () => {
  const warnings = [];
  assert.equal(
    resolveMcpActorPeerId({ recallPeerScope: "actor", onWarn: (m) => warnings.push(m) }),
    "",
    "no header means broad recall, which is the default anyway",
  );
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /OPENVIKING_PEER_ID/, "the warning says how to fix it");
});

test("path and URL helpers stay exported for entrypoints that need them", () => {
  assert.equal(trimSlash("http://x/"), "http://x");
  assert.equal(normalizeConfigPath(""), "");
  assert.equal(normalizeConfigPath("~"), homedir());
  assert.equal(normalizeConfigPath("~/a"), join(homedir(), "a"));
  assert.equal(defaultCredentialPaths({}).length, 2);
});

test("every MCP proxy shapes its config through the shared builder", () => {
  assert.equal(
    MCP_PROXIES.length,
    MCP_PROXY_COUNT,
    `expected ${MCP_PROXY_COUNT} proxies, found ${MCP_PROXIES.map((p) => p.rel).join(", ")}`,
  );
  for (const { rel, source } of MCP_PROXIES) {
    assert.match(
      source,
      /buildMcpProxyConfig\(/,
      `${rel} must shape its config through buildMcpProxyConfig`,
    );
  }
});

test("no MCP proxy derives its peer from the launch directory", () => {
  // A proxy is long-lived and may start anywhere, so unlike a hook it cannot
  // re-derive a peer per turn. Codex already had this rule; it holds for all.
  for (const { rel, source } of MCP_PROXIES) {
    assert.doesNotMatch(source, /resolveEffectivePeerId/, `${rel} must not derive a peer`);
    assert.doesNotMatch(source, /cwd:\s*process\.cwd\(\)/, `${rel} must not key a peer off its cwd`);
  }
});

test("the default peer scope puts no actor-peer header on the wire", async () => {
  const sent = [];
  const proxyFor = (harnessConfig) => createOpenVikingMcpProxy({
    stdout: { write(_line, cb) { if (cb) cb(); return true; } },
    fetchImpl: (_url, init) => {
      sent.push(init.headers);
      return Promise.resolve({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} }),
      });
    },
    readConfig: () => buildMcpProxyConfig({
      mcpUrl: "http://127.0.0.1:1933/mcp",
      peerId: resolveMcpActorPeerId(harnessConfig),
    }),
    loggerFactory: () => ({ log() {}, logError() {} }),
  });
  const initialize = { jsonrpc: "2.0", id: 1, method: "initialize", params: {} };

  await proxyFor({ peerId: "workspace-a" }).handleMessage({ ...initialize });
  assert.equal(
    sent[0]["X-OpenViking-Actor-Peer"],
    undefined,
    "the default scope is broad recall, so pinning an actor would narrow it behind the user's back",
  );

  await proxyFor({ peerId: "workspace-a", recallPeerScope: "actor" }).handleMessage({ ...initialize });
  assert.equal(sent[1]["X-OpenViking-Actor-Peer"], "workspace-a");
});

test("the identity headers follow the auth mode, not a resolved account", async () => {
  const sent = [];
  const proxyFor = (extra) => createOpenVikingMcpProxy({
    stdout: { write(_line, cb) { if (cb) cb(); return true; } },
    fetchImpl: (_url, init) => {
      sent.push(init.headers);
      return Promise.resolve({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} }),
      });
    },
    readConfig: () => buildMcpProxyConfig({
      mcpUrl: "http://127.0.0.1:1933/mcp",
      account: "acme",
      user: "alice",
      ...extra,
    }),
    loggerFactory: () => ({ log() {}, logError() {} }),
  });
  const initialize = { jsonrpc: "2.0", id: 1, method: "initialize", params: {} };

  await proxyFor({}).handleMessage({ ...initialize });
  assert.equal(
    sent[0]["X-OpenViking-Account"],
    undefined,
    "an api_key server reads the identity out of the key and every proxy on the path would see it",
  );
  assert.equal(sent[0]["X-OpenViking-User"], undefined);

  await proxyFor({ sendIdentityHeaders: true }).handleMessage({ ...initialize });
  assert.equal(sent[1]["X-OpenViking-Account"], "acme");
  assert.equal(sent[1]["X-OpenViking-User"], "alice");
});

async function credentialFiles(prefix, { ovcli, ov }) {
  const dir = await mkdtemp(join(tmpdir(), prefix));
  const cliPath = join(dir, "ovcli.conf");
  const ovPath = join(dir, "ov.conf");
  await writeFile(cliPath, JSON.stringify(ovcli, null, 2) + "\n");
  await writeFile(ovPath, JSON.stringify(ov, null, 2) + "\n");
  return {
    dir,
    cliPath,
    ovPath,
    env: { OPENVIKING_CLI_CONFIG_FILE: cliPath, OPENVIKING_CONFIG_FILE: ovPath },
  };
}

// The portable bundle ships without an installer, so nobody moves a working
// install's key out of ov.conf for it. ovcli.conf naming only a url pins the
// credential chain to that file, and the chain has to keep going anyway.
test("a portable proxy keeps the server key when ovcli.conf names only a url", async () => {
  const files = await credentialFiles("ov-proxy-rootkey-", {
    ovcli: { url: "https://ov.example.com" },
    ov: { server: { root_api_key: "root-key" } },
  });
  try {
    const cfg = buildProxyConnection("agent-plugins", { env: files.env });
    assert.equal(cfg.baseUrl, "https://ov.example.com");
    assert.equal(cfg.mcpUrl, "https://ov.example.com/mcp");
    assert.equal(cfg.apiKey, "root-key");
    assert.equal(cfg.hasApiKey, true);
    assert.equal(cfg.apiKeySource, "ov");
    assert.equal(cfg.credentialPath, files.ovPath);
    assert.equal(cfg.credentialSource, "ovcli", "the mode is still what the chain ran in");
  } finally {
    await rm(files.dir, { recursive: true, force: true });
  }
});

test("a portable proxy reports the auth mode, its own log path, and the files to watch", async () => {
  const files = await credentialFiles("ov-proxy-shape-", {
    ovcli: { url: "https://ov.example.com", api_key: "cli-key", account: "acme", user: "alice" },
    ov: {},
  });
  try {
    const cfg = buildProxyConnection("agent-plugins", { env: files.env, version: "1.2.3" });
    assert.equal(cfg.apiKeySource, "ovcli");
    assert.equal(cfg.authMode, "trusted", "a credential layer supplied an identity");
    assert.equal(cfg.sendIdentityHeaders, true);
    assert.equal(cfg.userAgent, "openviking-memory-agent-plugins/1.2.3");
    assert.equal(cfg.timeoutMs, DEFAULT_PROXY_TIMEOUT_MS);
    assert.equal(cfg.debug, false);
    assert.equal(
      cfg.debugLogPath,
      join(homedir(), ".openviking", "logs", "agent-plugins.log"),
    );
    assert.deepEqual(
      buildMcpProxyConfig({ watchedPaths: cfg.watchedPaths, env: files.env }).watchedPaths,
      [files.cliPath, files.ovPath, OVCLI, OV],
    );

    const named = buildProxyConnection("agent-plugins", {
      env: { ...files.env, OPENVIKING_DEBUG: "1", OPENVIKING_DEBUG_LOG: "/tmp/ov.log", OPENVIKING_TIMEOUT_MS: "60000" },
    });
    assert.equal(named.debug, true);
    assert.equal(named.debugLogPath, "/tmp/ov.log");
    assert.equal(named.timeoutMs, 60000);
  } finally {
    await rm(files.dir, { recursive: true, force: true });
  }
});

test("an api_key deployment keeps the operator's identity off the wire", async () => {
  const files = await credentialFiles("ov-proxy-apikey-", {
    ovcli: { url: "https://ov.example.com", api_key: "cli-key", account: "acme" },
    ov: { server: { auth_mode: "api_key" } },
  });
  try {
    const cfg = buildProxyConnection("agent-plugins", { env: files.env });
    assert.equal(cfg.authMode, "api_key");
    assert.equal(cfg.sendIdentityHeaders, false);
  } finally {
    await rm(files.dir, { recursive: true, force: true });
  }
});
