/**
 * The packaging facts the Codex install path depends on: both marketplace
 * catalogs name `openviking-memory` at the merged plugin directory, and its
 * Codex manifest points hooks, MCP and skills at hosts/codex/ and turns off
 * the root `commands/` directory, which is Claude Code's.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { MCP_PROXY_ENV_VARS } from "../../lib/mcp-proxy-config.mjs";
import host from "./host.mjs";

const hostDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(hostDir, "..", "..");
const repoRoot = resolve(pluginDir, "..", "..");
const PLUGIN_NAME = "openviking-memory";

const readJson = (path) => JSON.parse(readFileSync(path, "utf-8"));
const manifest = readJson(join(pluginDir, ".codex-plugin", "plugin.json"));
const fromManifest = (rel) => resolve(pluginDir, rel);

function sourcePath(source) {
  const raw = typeof source === "string" ? source : source?.path;
  return String(raw || "").replace(/^\.\//, "");
}

for (const [label, catalogPath, base] of [
  ["repo-root", join(repoRoot, ".agents", "plugins", "marketplace.json"), repoRoot],
  ["examples/", join(repoRoot, "examples", ".agents", "plugins", "marketplace.json"), join(repoRoot, "examples")],
]) {
  test(`${label} catalog names openviking-memory at the merged plugin, from a local source`, () => {
    const catalog = readJson(catalogPath);
    assert.equal(catalog.name, "openviking");
    const entry = catalog.plugins.find((p) => p?.name === PLUGIN_NAME);
    assert.ok(entry);
    assert.equal(entry.name, manifest.name);
    assert.equal(entry.source.source, "local");
    assert.ok(entry.source.path.startsWith("./"));
    assert.equal(resolve(base, sourcePath(entry.source)), pluginDir);
    assert.ok(["NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"].includes(entry.policy.installation));
    assert.ok(["ON_INSTALL", "ON_USE"].includes(entry.policy.authentication));
  });
}

test("the Codex manifest points hooks, MCP and assets at hosts/codex, skills at the plugin's own, and declares no commands", () => {
  assert.equal(manifest.hooks, "./hosts/codex/hooks.json");
  assert.equal(manifest.mcpServers, "./hosts/codex/.mcp.json");
  assert.equal(manifest.skills, "./skills/");
  assert.deepEqual(manifest.commands, []);
  for (const rel of [manifest.hooks, manifest.mcpServers, manifest.interface.logo, manifest.interface.composerIcon]) {
    assert.ok(existsSync(fromManifest(rel)), rel);
  }
  for (const skill of ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search", "ov-memory-doctor"]) {
    assert.ok(existsSync(join(fromManifest(manifest.skills), skill, "SKILL.md")), skill);
  }
});

test("hooks.json runs every mapped event through hook.mjs within its budget, and the usage hooks beside them", () => {
  const { hooks } = readJson(fromManifest(manifest.hooks));
  assert.equal(hooks.PreToolUse, undefined);
  assert.deepEqual(hooks.SessionStart.map((group) => group.matcher), ["clear|startup|resume|compact"]);
  for (const event of Object.keys(host.events)) {
    const entries = hooks[event].flatMap((group) => group.hooks);
    const [main] = entries.filter((entry) => entry.command.includes("/scripts/hook.mjs"));
    assert.equal(main.command, `node "\${PLUGIN_ROOT}/scripts/hook.mjs" ${event} codex`);
    if (host.detachEvents.includes(event)) assert.ok(main.timeout <= 3, `${event} timeout ${main.timeout}`);
    else assert.ok(main.timeout * 1000 - host.budgets[event] >= 3000, `${event} timeout ${main.timeout}`);
  }
  const usage = Object.entries(hooks).flatMap(([event, groups]) => groups.flatMap((group) => group.hooks.map((entry) => [event, entry.command])))
    .filter(([, command]) => command.includes("/usage/"));
  assert.deepEqual(usage, [
    ["Stop", 'node "${PLUGIN_ROOT}/hosts/codex/usage/report.mjs"'],
    ["PostToolUse", 'node "${PLUGIN_ROOT}/hosts/codex/usage/track-lookup.mjs"'],
  ]);
  for (const [, command] of usage) assert.ok(existsSync(join(pluginDir, /\}\/([^"]+)"/.exec(command)[1])));
});

test(".mcp.json starts the shared proxy from the plugin root as the codex host and forwards every proxy env var", () => {
  const server = readJson(fromManifest(manifest.mcpServers)).mcpServers[PLUGIN_NAME];
  assert.equal(server.command, "node");
  assert.deepEqual(server.args, ["servers/mcp-proxy.mjs"]);
  assert.equal(server.cwd, ".");
  assert.deepEqual(server.env, { OPENVIKING_HOOK_SOURCE: "codex" });
  const forwarded = new Set(server.env_vars);
  for (const name of MCP_PROXY_ENV_VARS) assert.ok(forwarded.has(name), name);
  execFileSync(process.execPath, ["--check", join(pluginDir, server.args[0])], { stdio: "pipe" });
});

test("the proxy fills session_history's session from the call's _meta.sessionId", async () => {
  const { historySessionFor } = await import("../../servers/mcp-proxy.mjs");
  const fill = historySessionFor(host);
  assert.equal(fill({ name: "session_history", _meta: { sessionId: "019a-thread" } }), "cx-019a-thread");
  assert.equal(fill({ name: "session_history", _meta: { threadId: "019a-thread" } }), "");
  assert.equal(fill({ name: "session_history" }), "");
});

test("the packaged skills shared with examples/skills stay byte-identical", () => {
  for (const skill of ["ov-experience-memory", "openviking-skills"]) {
    assert.equal(
      readFileSync(join(fromManifest(manifest.skills), skill, "SKILL.md"), "utf-8"),
      readFileSync(join(repoRoot, "examples", "skills", skill, "SKILL.md"), "utf-8"),
      skill,
    );
  }
});

test("the doctor and the setup wizard parse", () => {
  for (const file of ["ov-memory-doctor.mjs", "setup.mjs"]) {
    execFileSync(process.execPath, ["--check", join(hostDir, file)], { stdio: "pipe" });
  }
});
