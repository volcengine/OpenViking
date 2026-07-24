import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, cpSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptsDir, "..");
const repoRoot = resolve(scriptsDir, "..", "..", "..");
const rootCatalogPath = join(repoRoot, ".claude-plugin", "marketplace.json");
const localCatalogPath = join(repoRoot, "examples", ".claude-plugin", "marketplace.json");
const manifestPath = join(pluginDir, ".claude-plugin", "plugin.json");

const PLUGIN_NAME = "openviking-memory";
const canRunBash = process.platform !== "win32"
  && spawnSync("bash", ["-c", "exit 0"], { stdio: "ignore" }).status === 0;

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function createInstallerFixture() {
  const root = mkdtempSync(join(tmpdir(), "openviking-claude-hook-only-"));
  const checkout = join(root, "checkout");
  const examplesDir = join(checkout, "examples");
  const home = join(root, "home");
  const binDir = join(root, "bin");
  const stateDir = join(root, "state");

  mkdirSync(join(checkout, ".git"), { recursive: true });
  mkdirSync(examplesDir, { recursive: true });
  mkdirSync(home, { recursive: true });
  mkdirSync(binDir, { recursive: true });
  mkdirSync(stateDir, { recursive: true });
  for (const entry of [".claude-plugin", "claude-code-memory-plugin", "memory-plugin-shared"]) {
    cpSync(join(repoRoot, "examples", entry), join(examplesDir, entry), { recursive: true });
  }

  const claudePath = join(binDir, "claude");
  writeFileSync(claudePath, [
    "#!/usr/bin/env bash",
    "set -eu",
    'state="${OPENVIKING_TEST_STATE:?}"',
    'mode="${OPENVIKING_TEST_CLAUDE_MODE:-modern}"',
    'printf "%s\\n" "$*" >> "$state/commands"',
    'if [ "$mode" = "legacy" ] && [ "${1:-}" = "plugin" ]; then exit 1; fi',
    'if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "--help" ]; then exit 0; fi',
    'if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "list" ]; then',
    '  [ -f "$state/installed" ] && cat "$state/installed"',
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "list" ]; then',
    '  if [ -f "$state/marketplace" ]; then',
    '    printf \'[{"name":"openviking","path":"%s"}]\\n\' "$(cat "$state/marketplace")"',
    "  else",
    "    printf '[]\\n'",
    "  fi",
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "add" ]; then',
    '  printf "%s" "${4:-}" > "$state/marketplace"',
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "remove" ]; then',
    '  rm -f "$state/marketplace"',
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "uninstall" ]; then',
    '  rm -f "$state/installed"',
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "plugin" ] && { [ "${2:-}" = "install" ] || [ "${2:-}" = "update" ]; }; then',
    '  printf "%s\\n" "openviking-memory@openviking" > "$state/installed"',
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "mcp" ] && [ "${2:-}" = "add" ]; then',
    '  : > "$state/openviking-mcp"',
    "  exit 0",
    "fi",
    'if [ "${1:-}" = "mcp" ] && [ "${2:-}" = "remove" ]; then',
    '  rm -f "$state/openviking-mcp"',
    "  exit 0",
    "fi",
  ].join("\n"), "utf-8");
  chmodSync(claudePath, 0o755);

  return {
    root,
    checkout,
    home,
    binDir,
    stateDir,
    installer: join(examplesDir, "memory-plugin-shared", "install.sh"),
  };
}

function runClaudeInstaller(fixture, mode, extraArgs = []) {
  return spawnSync("bash", [
    fixture.installer,
    "--harness", "claude",
    "--source", "dev",
    "--lang", "en",
    "--url", "http://127.0.0.1:1933",
    "--api-key", "",
    "--no-statusline",
    "--yes",
    ...extraArgs,
  ], {
    cwd: fixture.checkout,
    encoding: "utf-8",
    env: {
      ...process.env,
      HOME: fixture.home,
      OPENVIKING_HOME: join(fixture.home, ".openviking"),
      OPENVIKING_CLAUDE_BINS: "claude",
      OPENVIKING_TEST_CLAUDE_MODE: mode,
      OPENVIKING_TEST_STATE: fixture.stateDir,
      PATH: [fixture.binDir, process.env.PATH].filter(Boolean).join(delimiter),
    },
  });
}

function assertInstallerSuccess(result) {
  assert.equal(result.status, 0, `${result.stdout ?? ""}\n${result.stderr ?? ""}`);
}

test("repo-root Claude marketplace catalog uses git-subdir source", () => {
  assert.ok(existsSync(rootCatalogPath), `missing catalog at ${rootCatalogPath}`);
  const catalog = readJson(rootCatalogPath);
  assert.equal(catalog.name, "openviking");
  const entry = catalog.plugins?.find((p) => p?.name === PLUGIN_NAME);
  assert.ok(entry, `root catalog must contain ${PLUGIN_NAME}`);
  assert.deepEqual(entry.source, {
    source: "git-subdir",
    url: "https://github.com/volcengine/OpenViking.git",
    path: "examples/claude-code-memory-plugin",
    ref: "main",
  });
});

test("local Claude marketplace entry name matches plugin manifest", () => {
  const catalog = readJson(localCatalogPath);
  const manifest = readJson(manifestPath);
  // Same marketplace name in remote and directory mode keeps the plugin id
  // (openviking-memory@openviking) stable across install modes.
  assert.equal(catalog.name, "openviking");
  const entry = catalog.plugins?.find((p) => p?.name === PLUGIN_NAME);
  assert.ok(entry, `local catalog must contain ${PLUGIN_NAME}`);
  assert.equal(entry.name, manifest.name);
  assert.equal(entry.source, "./claude-code-memory-plugin");
});

test("Claude .mcp.json starts the stdio MCP proxy", () => {
  const mcp = readJson(join(pluginDir, ".mcp.json"));
  const server = mcp.openviking;
  assert.ok(server, ".mcp.json must define openviking server");
  assert.equal(server.command, "node");
  assert.deepEqual(server.args, ["${CLAUDE_PLUGIN_ROOT}/servers/mcp-proxy.mjs"]);
  assert.ok(!("type" in server), ".mcp.json should not keep HTTP MCP type");
  assert.ok(!("url" in server), ".mcp.json should not keep direct HTTP url");
  execFileSync("node", ["--check", join(pluginDir, "servers", "mcp-proxy.mjs")], { stdio: "pipe" });
});

test("hook-only installer generates a Claude marketplace without MCP discovery files", { skip: !canRunBash }, (t) => {
  const fixture = createInstallerFixture();
  t.after(() => rmSync(fixture.root, { recursive: true, force: true }));

  assertInstallerSuccess(runClaudeInstaller(fixture, "modern", ["--claude-hook-only"]));
  const marketplaceDir = readFileSync(join(fixture.stateDir, "marketplace"), "utf-8");
  const generatedPluginDir = join(marketplaceDir, "claude-code-memory-plugin");
  const generatedManifest = readJson(join(generatedPluginDir, ".claude-plugin", "plugin.json"));
  const generatedMarketplace = readJson(join(marketplaceDir, ".claude-plugin", "marketplace.json"));

  assert.equal("mcpServers" in generatedManifest, false);
  assert.equal(existsSync(join(generatedPluginDir, ".mcp.json")), false);
  assert.ok(existsSync(join(generatedPluginDir, "hooks", "hooks.json")));
  assert.equal(generatedMarketplace.plugins?.[0]?.source, "./claude-code-memory-plugin");
});

test("legacy hook-only install removes a prior user MCP registration", { skip: !canRunBash }, (t) => {
  const fixture = createInstallerFixture();
  t.after(() => rmSync(fixture.root, { recursive: true, force: true }));

  assertInstallerSuccess(runClaudeInstaller(fixture, "legacy"));
  assert.ok(existsSync(join(fixture.stateDir, "openviking-mcp")));
  assertInstallerSuccess(runClaudeInstaller(fixture, "legacy", ["--claude-hook-only"]));

  assert.equal(existsSync(join(fixture.stateDir, "openviking-mcp")), false);
  const commands = readFileSync(join(fixture.stateDir, "commands"), "utf-8").trim().split("\n");
  assert.ok(commands.some((command) => command.startsWith("mcp add --scope user openviking -- node ")));
  assert.ok(commands.filter((command) => command === "mcp remove openviking -s user").length >= 2);
});

test("modern hook-only install removes a legacy user MCP registration", { skip: !canRunBash }, (t) => {
  const fixture = createInstallerFixture();
  t.after(() => rmSync(fixture.root, { recursive: true, force: true }));

  assertInstallerSuccess(runClaudeInstaller(fixture, "legacy"));
  assert.ok(existsSync(join(fixture.stateDir, "openviking-mcp")));

  assertInstallerSuccess(runClaudeInstaller(fixture, "modern", ["--claude-hook-only"]));

  assert.equal(existsSync(join(fixture.stateDir, "openviking-mcp")), false);
  const commands = readFileSync(join(fixture.stateDir, "commands"), "utf-8").trim().split("\n");
  assert.ok(commands.some((command) => command.startsWith("mcp add --scope user openviking -- node ")));
  assert.ok(commands.filter((command) => command === "mcp remove openviking -s user").length >= 2);
});

test("Claude hooks include optional skill experience PostToolUse Read hook", () => {
  const hooks = readJson(join(pluginDir, "hooks", "hooks.json"));
  const postToolUse = hooks.hooks?.PostToolUse;
  assert.ok(Array.isArray(postToolUse), "hooks.json must define PostToolUse hooks");
  const readHook = postToolUse.find((entry) => entry?.matcher === "Read");
  assert.ok(readHook, "PostToolUse must include a Read matcher");
  assert.equal(
    readHook.hooks?.[0]?.command,
    "node ${CLAUDE_PLUGIN_ROOT}/scripts/skill-experience.mjs",
  );
  execFileSync("node", ["--check", join(pluginDir, "scripts", "skill-experience.mjs")], { stdio: "pipe" });
});

test("Claude hooks include PreToolUse URI guard for filesystem tools", () => {
  const hooks = readJson(join(pluginDir, "hooks", "hooks.json"));
  const preToolUse = hooks.hooks?.PreToolUse;
  assert.ok(Array.isArray(preToolUse), "hooks.json must define PreToolUse hooks");
  const guardHook = preToolUse.find((entry) => entry?.matcher === "Read|Glob|Grep");
  assert.ok(guardHook, "PreToolUse must guard Read|Glob|Grep");
  assert.equal(
    guardHook.hooks?.[0]?.command,
    "node ${CLAUDE_PLUGIN_ROOT}/scripts/uri-guard.mjs",
  );
  execFileSync("node", ["--check", join(pluginDir, "scripts", "uri-guard.mjs")], { stdio: "pipe" });
});
