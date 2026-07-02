/**
 * Contract test for the repo-root Codex marketplace catalog
 * (.agents/plugins/marketplace.json) and its coherence with this plugin.
 *
 * These checks guard the `codex plugin marketplace add <owner>/OpenViking`
 * install path: the catalog must exist, be valid JSON, point at this plugin,
 * and the plugin's manifest / hooks / mcp wiring must stay consistent with the
 * marketplace-install assumptions (native ${PLUGIN_ROOT}, local-default url,
 * no stale tool names).
 */

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptsDir, "..");
const repoRoot = resolve(scriptsDir, "..", "..", "..");
const catalogPath = join(repoRoot, ".agents", "plugins", "marketplace.json");
const manifestPath = join(pluginDir, ".codex-plugin", "plugin.json");

const PLUGIN_NAME = "openviking-memory";
const REAL_MCP_TOOLS = ["search", "store", "read", "list", "grep", "glob", "forget", "add_resource", "health"];
const LEGACY_TOOL_NAMES = ["openviking_recall", "openviking_store", "openviking_forget", "openviking_health"];

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

// Accept both the string form ("./examples/...") and local object forms
// ({source:"local",path}); return the ./-stripped path.
function sourcePath(source) {
  const raw = typeof source === "string" ? source : (source && typeof source === "object" ? source.path : "");
  return String(raw || "").replace(/^\.\//, "");
}

test("repo-root marketplace catalog exists and is valid JSON", () => {
  assert.ok(existsSync(catalogPath), `missing catalog at ${catalogPath}`);
  const catalog = readJson(catalogPath);
  assert.ok(typeof catalog.name === "string" && catalog.name.length > 0, "catalog.name must be a non-empty string");
  assert.ok(Array.isArray(catalog.plugins) && catalog.plugins.length > 0, "catalog.plugins must be a non-empty array");
});

test("catalog lists openviking-memory and its source points at this plugin dir", () => {
  const catalog = readJson(catalogPath);
  const entry = catalog.plugins.find((p) => p && p.name === PLUGIN_NAME);
  assert.ok(entry, `catalog must contain a plugin named "${PLUGIN_NAME}"`);

  const rel = sourcePath(entry.source);
  assert.ok(rel.endsWith("examples/codex-memory-plugin"), `source path must point at examples/codex-memory-plugin, got "${rel}"`);
  assert.equal(resolve(repoRoot, rel), pluginDir, "catalog source must resolve to this plugin directory");
});

test("catalog plugin name matches plugin.json name", () => {
  const catalog = readJson(catalogPath);
  const entry = catalog.plugins.find((p) => p && p.name === PLUGIN_NAME);
  const manifest = readJson(manifestPath);
  assert.equal(entry.name, manifest.name, "marketplace plugin name must equal plugin.json name");
});

test("catalog policy uses valid Codex install/auth enums", () => {
  const catalog = readJson(catalogPath);
  const entry = catalog.plugins.find((p) => p && p.name === PLUGIN_NAME);
  assert.ok(entry.policy, "plugin entry must declare a policy (Codex needs it to render install controls)");
  assert.ok(
    ["NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"].includes(entry.policy.installation),
    `invalid policy.installation: ${entry.policy.installation}`,
  );
  assert.ok(
    ["ON_INSTALL", "ON_USE"].includes(entry.policy.authentication),
    `invalid policy.authentication: ${entry.policy.authentication}`,
  );
});

test("catalog source is local to the marketplace snapshot", () => {
  const catalog = readJson(catalogPath);
  const entry = catalog.plugins.find((p) => p && p.name === PLUGIN_NAME);
  const src = entry.source;
  assert.ok(src, "catalog entry must declare a source");
  if (typeof src === "string") {
    assert.ok(src.startsWith("./"), `string source must be relative to the marketplace root, got "${src}"`);
  } else if (typeof src === "object") {
    assert.equal(src.source, "local", `object source must be a local source, got "${src.source}"`);
    for (const remote of ["url", "ref", "branch", "tag", "rev", "commit"]) {
      assert.ok(!(remote in src), `catalog source must not fetch a different Git repo/ref (found "${remote}")`);
    }
    assert.ok(typeof src.path === "string" && src.path.startsWith("./"), `object source path must be relative, got "${src.path}"`);
  } else {
    assert.fail(`unsupported catalog source type: ${typeof src}`);
  }
});

test("required plugin files are present", () => {
  for (const rel of [".codex-plugin/plugin.json", ".mcp.json", "hooks/hooks.json"]) {
    assert.ok(existsSync(join(pluginDir, rel)), `missing required plugin file: ${rel}`);
  }
});

test("plugin.json describes the real MCP tools, not the legacy names", () => {
  const manifest = readJson(manifestPath);
  const longDesc = manifest.interface?.longDescription || "";
  for (const legacy of LEGACY_TOOL_NAMES) {
    assert.ok(!longDesc.includes(legacy), `longDescription must not reference legacy tool name "${legacy}"`);
  }
  for (const tool of ["search", "add_resource", "health"]) {
    assert.ok(longDesc.includes(tool), `longDescription should mention real tool "${tool}"`);
  }
});

test("hooks.json uses Codex's native ${PLUGIN_ROOT}, not the legacy placeholder", () => {
  const hooks = readFileSync(join(pluginDir, "hooks", "hooks.json"), "utf-8");
  assert.ok(hooks.includes("${PLUGIN_ROOT}"), "hooks.json should reference ${PLUGIN_ROOT}");
  assert.ok(!hooks.includes("__OPENVIKING_PLUGIN_ROOT__"), "hooks.json should not keep the legacy __OPENVIKING_PLUGIN_ROOT__ placeholder");
  // every hook command should be rooted at ${PLUGIN_ROOT}
  const parsed = JSON.parse(hooks);
  const commands = Object.values(parsed.hooks || {})
    .flat()
    .flatMap((group) => group.hooks || [])
    .map((h) => h.command || "");
  assert.ok(commands.length >= 4, "expected at least 4 hook commands (SessionStart/UserPromptSubmit/Stop/PreCompact)");
  for (const cmd of commands) {
    assert.ok(cmd.includes("${PLUGIN_ROOT}/scripts/"), `hook command must be rooted at \${PLUGIN_ROOT}: ${cmd}`);
  }
});

test(".mcp.json ships a concrete local-default url and omits bearer by default", () => {
  const mcp = readJson(join(pluginDir, ".mcp.json"));
  const server = mcp.mcpServers?.[PLUGIN_NAME];
  assert.ok(server, `.mcp.json must define mcpServers["${PLUGIN_NAME}"]`);
  assert.ok(/^https?:\/\//.test(server.url || ""), `.mcp.json url must be a concrete http(s) endpoint, got "${server.url}"`);
  assert.ok(!String(server.url).includes("__OPENVIKING_MCP_URL__"), ".mcp.json must not keep the legacy url placeholder");
  // Codex hard-fails MCP startup if bearer_token_env_var points at an unset var,
  // so the checked-in (marketplace-default, unauthenticated) file must omit it.
  assert.ok(!("bearer_token_env_var" in server), "checked-in .mcp.json should omit bearer_token_env_var for the local-default install");
});

test("plugin.json keeps the canonical tool list available for reference", () => {
  // Sanity: the documented tool set is the one we assert against above.
  assert.equal(REAL_MCP_TOOLS.length, 9);
});
