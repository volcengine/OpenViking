import assert from "node:assert/strict";
import test from "node:test";

import {
  buildVscodeConfig,
  buildCopilotCliConfig,
  buildGithubRepoConfig,
  buildStdioProxyConfig,
  normalizeSpec,
  _internals,
} from "./build-configs.mjs";

const SPEC = { url: "https://ov.example.com/mcp", apiKey: "sk-test-123" };

test("normalizeSpec requires an http(s) URL", () => {
  assert.throws(() => normalizeSpec({}), /OPENVIKING_URL is required/);
  assert.throws(
    () => normalizeSpec({ url: "ov.example.com" }),
    /must start with http/,
  );
  assert.deepEqual(
    normalizeSpec({ url: "https://x/mcp" }),
    { url: "https://x/mcp", apiKey: "", serverName: "openviking" },
  );
  assert.equal(
    normalizeSpec({ url: " https://x/mcp ", apiKey: " k " }).apiKey,
    "k",
    "whitespace is trimmed",
  );
});

test("VSCode config uses top-level `servers` (NOT mcpServers) with http type", () => {
  const cfg = buildVscodeConfig(SPEC);
  assert.ok(cfg.servers, "VSCode config must use the `servers` key");
  assert.ok(!cfg.mcpServers, "VSCode must NOT use `mcpServers`");
  const srv = cfg.servers.openviking;
  assert.equal(srv.type, "http");
  assert.equal(srv.url, SPEC.url);
  assert.deepEqual(srv.headers, { Authorization: "Bearer sk-test-123" });
});

test("VSCode config omits headers when no API key (local unauthenticated server)", () => {
  const cfg = buildVscodeConfig({ url: "http://127.0.0.1:1933/mcp" });
  assert.equal(cfg.servers.openviking.headers, undefined);
});

test("Copilot CLI config uses top-level `mcpServers` with type, url, headers, tools", () => {
  const cfg = buildCopilotCliConfig(SPEC);
  assert.ok(cfg.mcpServers, "CLI config must use the `mcpServers` key");
  const srv = cfg.mcpServers.openviking;
  assert.equal(srv.type, "http");
  assert.equal(srv.url, SPEC.url);
  assert.deepEqual(srv.headers, { Authorization: "Bearer sk-test-123" });
  assert.ok(Array.isArray(srv.tools) && srv.tools.length > 0);
  // Default tool list must include the core recall/remember pair — otherwise
  // the plugin silently provides no memory capability.
  assert.ok(srv.tools.includes("recall"));
  assert.ok(srv.tools.includes("remember"));
});

test("Copilot CLI config accepts a custom tools list including *", () => {
  const cfg = buildCopilotCliConfig(SPEC, { tools: ["*"] });
  assert.deepEqual(cfg.mcpServers.openviking.tools, ["*"]);
});

test("GitHub repo config always uses ${COPILOT_MCP_*} for the API key, never a hard-coded value", () => {
  // Even when the caller passes a real apiKey in the spec, the repo config
  // must substitute the Agents-secret variable form — pasting a real key into
  // the GitHub UI would leak it.
  const cfg = buildGithubRepoConfig(SPEC);
  const srv = cfg.mcpServers.openviking;
  assert.equal(srv.type, "http");
  assert.ok(srv.tools?.length > 0, "repo-level requires a tools allowlist");
  const auth = srv.headers?.Authorization || "";
  assert.match(auth, /\$\{COPILOT_MCP_[A-Z_]+\}/);
  assert.ok(!auth.includes("sk-test-123"), "real API key must NOT appear in the repo config");
});

test("GitHub repo config can rename the secret variable via options", () => {
  const cfg = buildGithubRepoConfig(SPEC, { apiKeySecret: "${COPILOT_MCP_OV_KEY}" });
  assert.equal(cfg.mcpServers.openviking.headers.Authorization, "Bearer ${COPILOT_MCP_OV_KEY}");
});

test("GitHub repo config restricts to read-only tools by default (cloud agent runs autonomously)", () => {
  // Per the GitHub docs: tools enabled at repo level are invoked autonomously,
  // no approval. Default must NOT include write tools (remember is intentionally
  // excluded from the cloud-agent default to avoid runaway memory writes).
  const cfg = buildGithubRepoConfig(SPEC);
  const tools = cfg.mcpServers.openviking.tools;
  assert.ok(tools.includes("recall"));
  assert.ok(tools.includes("search"));
  // `remember` is intentionally NOT in the cloud-agent default; the user opts in.
  assert.ok(!tools.includes("remember"));
});

test("stdio proxy config points at servers/mcp-proxy.mjs with the plugin-root token", () => {
  const cfg = buildStdioProxyConfig({});
  assert.equal(cfg.command, "node");
  assert.ok(cfg.args[0].endsWith("/servers/mcp-proxy.mjs"));
  assert.ok(cfg.args[0].includes("__OPENVIKING_COPILOT_ROOT__"));
});

test("serverName override propagates to every config", () => {
  const spec = { ...SPEC, serverName: "openviking-memory" };
  assert.ok(buildVscodeConfig(spec).servers["openviking-memory"]);
  assert.ok(buildCopilotCliConfig(spec).mcpServers["openviking-memory"]);
  assert.ok(buildGithubRepoConfig(spec).mcpServers["openviking-memory"]);
});

test("DEFAULT_TOOLS includes the core recall/search/remember/read pair", () => {
  for (const name of ["recall", "search", "find", "remember", "read", "list", "grep", "glob"]) {
    assert.ok(_internals.DEFAULT_TOOLS.includes(name), `${name} should be in DEFAULT_TOOLS`);
  }
});
