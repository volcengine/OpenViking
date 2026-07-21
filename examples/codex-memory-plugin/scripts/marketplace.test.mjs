/**
 * Contract test for the repo-root Codex marketplace catalog
 * (.agents/plugins/marketplace.json) and its coherence with this plugin.
 *
 * These checks guard the `codex plugin marketplace add <owner>/OpenViking`
 * install path: the catalog must exist, be valid JSON, point at this plugin,
 * and the plugin's manifest / hooks / mcp wiring must stay consistent with the
 * marketplace-install assumptions (native ${PLUGIN_ROOT}, stdio MCP proxy,
 * no stale tool names).
 */

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptsDir, "..");
const repoRoot = resolve(scriptsDir, "..", "..", "..");
const catalogPath = join(repoRoot, ".agents", "plugins", "marketplace.json");
const manifestPath = join(pluginDir, ".codex-plugin", "plugin.json");
const mcpEndpointPath = join(repoRoot, "openviking", "server", "mcp_endpoint.py");

const PLUGIN_NAME = "openviking-memory";
const REAL_MCP_TOOLS = [
  "find", "search", "recall", "read", "list", "remember", "add_resource",
  "list_watches", "cancel_watch", "grep", "glob", "forget", "code_outline",
  "code_search", "code_expand", "health",
];
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

test("examples/.agents catalog backs the directory-marketplace install path", () => {
  // The shared installer registers examples/ itself as a local marketplace in
  // dev/archive mode, so a Codex catalog must exist there too and stay
  // consistent with the repo-root one (same marketplace name -> same plugin id
  // openviking-memory@openviking across all install modes).
  const localCatalogPath = join(repoRoot, "examples", ".agents", "plugins", "marketplace.json");
  assert.ok(existsSync(localCatalogPath), `missing catalog at ${localCatalogPath}`);
  const localCatalog = readJson(localCatalogPath);
  const rootCatalog = readJson(catalogPath);
  assert.equal(localCatalog.name, rootCatalog.name, "examples/.agents catalog must keep the same marketplace name as the repo root");
  const entry = localCatalog.plugins.find((p) => p && p.name === PLUGIN_NAME);
  assert.ok(entry, `examples/.agents catalog must contain "${PLUGIN_NAME}"`);
  assert.equal(resolve(repoRoot, "examples", sourcePath(entry.source)), pluginDir, "examples/.agents catalog source must resolve to this plugin directory");
});

test("required plugin files are present", () => {
  for (const rel of [".codex-plugin/plugin.json", ".mcp.json", "hooks/hooks.json"]) {
    assert.ok(existsSync(join(pluginDir, rel)), `missing required plugin file: ${rel}`);
  }
});

test("plugin bundles the Experience Memory skill", () => {
  const skillPath = join(pluginDir, "skills", "ov-experience-memory", "SKILL.md");
  assert.ok(existsSync(skillPath), `missing bundled skill: ${skillPath}`);
  const content = readFileSync(skillPath, "utf-8");
  assert.match(content, /^---[\s\S]*name:\s*ov-experience-memory[\s\S]*---/);
  assert.match(content, /`search_experience`/);
  assert.match(content, /`read_experience`/);
});

test("Experience Memory skill examples match the search_experience input schema", () => {
  const skillPaths = [
    join(pluginDir, "skills", "ov-experience-memory", "SKILL.md"),
    join(repoRoot, "examples", "skills", "ov-experience-memory", "SKILL.md"),
  ];
  for (const skillPath of skillPaths) {
    const content = readFileSync(skillPath, "utf-8");
    const section = content.match(/## Tool: search_experience([\s\S]*?)## Tool: read_experience/)?.[1];
    assert.ok(section, `missing search_experience section in ${skillPath}`);
    const inputExample = section.match(/Input schema:\s*```json\s*([\s\S]*?)```/)?.[1];
    assert.ok(inputExample, `missing search_experience input example in ${skillPath}`);
    assert.deepEqual(Object.keys(JSON.parse(inputExample)).sort(), ["limit", "query"]);
  }
});

test("plugin.json does not describe legacy MCP tool names", () => {
  const manifest = readJson(manifestPath);
  const interfaceText = JSON.stringify(manifest.interface || {});
  for (const legacy of LEGACY_TOOL_NAMES) {
    assert.ok(!interfaceText.includes(legacy), `plugin interface must not reference legacy tool name "${legacy}"`);
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

test(".mcp.json starts the stdio MCP proxy from the plugin root", () => {
  const mcp = readJson(join(pluginDir, ".mcp.json"));
  const server = mcp.mcpServers?.[PLUGIN_NAME];
  assert.ok(server, `.mcp.json must define mcpServers["${PLUGIN_NAME}"]`);
  assert.equal(server.command, "node");
  assert.deepEqual(server.args, ["servers/mcp-proxy.mjs"]);
  assert.equal(server.cwd, ".");
  assert.equal(server.startup_timeout_sec, 30);
  assert.ok(!("url" in server), ".mcp.json should not keep streamable-HTTP url wiring");
  assert.ok(!("bearer_token_env_var" in server), ".mcp.json should not require Codex env-var bearer wiring");

  execFileSync("node", ["--check", join(pluginDir, "servers", "mcp-proxy.mjs")], { stdio: "pipe" });
});

test("Codex MCP entrypoint wires the Experience tool provider", () => {
  const entrypoint = readFileSync(join(pluginDir, "servers", "mcp-proxy.mjs"), "utf-8");
  assert.match(entrypoint, /createExperienceToolProvider/);
  assert.match(entrypoint, /localToolProvider/);
});

test("canonical MCP tool list matches server registrations", () => {
  const source = readFileSync(mcpEndpointPath, "utf-8");
  const registered = [
    ...source.matchAll(/@mcp\.tool\((?:name="([a-z_]+)")?\)\s*\nasync def ([a-z_]+)\(/g),
  ].map((match) => match[1] || match[2]);
  assert.deepEqual(registered, REAL_MCP_TOOLS);
});
