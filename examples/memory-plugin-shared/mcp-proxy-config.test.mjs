import assert from "node:assert/strict";
import { homedir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  buildMcpProxyConfig,
  DEFAULT_PROXY_TIMEOUT_MS,
  defaultCredentialPaths,
  normalizeConfigPath,
  trimSlash,
} from "./lib/mcp-proxy-config.mjs";

const OVCLI = join(homedir(), ".openviking", "ovcli.conf");
const OV = join(homedir(), ".openviking", "ov.conf");

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

test("path and URL helpers stay exported for entrypoints that need them", () => {
  assert.equal(trimSlash("http://x/"), "http://x");
  assert.equal(normalizeConfigPath(""), "");
  assert.equal(normalizeConfigPath("~"), homedir());
  assert.equal(normalizeConfigPath("~/a"), join(homedir(), "a"));
  assert.equal(defaultCredentialPaths({}).length, 2);
});
