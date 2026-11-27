import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const pluginDir = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const repoRoot = resolve(pluginDir, "..", "..");
const PLUGIN_NAME = "openviking-memory";

const readJson = (path) => JSON.parse(readFileSync(path, "utf-8"));
const hooks = () => readJson(join(pluginDir, "hooks", "hooks.json")).hooks;

test("both marketplace catalogs point at this directory under the manifest's name", () => {
  const manifest = readJson(join(pluginDir, ".claude-plugin", "plugin.json"));
  assert.equal(manifest.name, PLUGIN_NAME);
  const root = readJson(join(repoRoot, ".claude-plugin", "marketplace.json"));
  assert.equal(root.name, "openviking");
  assert.deepEqual(root.plugins.find((p) => p?.name === PLUGIN_NAME)?.source, {
    source: "git-subdir",
    url: "https://github.com/volcengine/OpenViking.git",
    path: "examples/agent-hook-plugin",
    ref: "main",
  });
  const local = readJson(join(repoRoot, "examples", ".claude-plugin", "marketplace.json"));
  assert.equal(local.name, "openviking");
  assert.equal(local.plugins.find((p) => p?.name === PLUGIN_NAME)?.source, "./agent-hook-plugin");
  for (const rel of [manifest.mcpServers, manifest.types]) assert.ok(existsSync(join(pluginDir, rel)), rel);
});

test(".mcp.json starts the shared stdio proxy as the claude-code host", () => {
  const server = readJson(join(pluginDir, ".mcp.json")).openviking;
  assert.equal(server.command, "node");
  assert.deepEqual(server.args, ["${CLAUDE_PLUGIN_ROOT}/servers/mcp-proxy.mjs"]);
  assert.deepEqual(server.env, { OPENVIKING_HOOK_SOURCE: "claude-code" });
});

test("the tool hooks keep their matchers", () => {
  const { PreToolUse, PostToolUse } = hooks();
  assert.equal(PreToolUse.find((e) => e.matcher === "Read|Glob|Grep|Edit|Write|Bash")?.hooks[0].command,
    'node "${CLAUDE_PLUGIN_ROOT}/scripts/uri-guard.mjs" claude-code');
  assert.equal(PostToolUse.find((e) => e.matcher === "Read")?.hooks[0].command,
    'node "${CLAUDE_PLUGIN_ROOT}/hosts/claude-code/skill-experience.mjs"');
});

test("the doctor skill names the doctor entry, which hands over to Claude Code's doctor", () => {
  const skill = readFileSync(join(pluginDir, "skills", "ov-memory-doctor", "SKILL.md"), "utf-8");
  assert.match(skill, /node <directory of this skill>\/\.\.\/\.\.\/scripts\/ov-memory-doctor\.mjs/);
  assert.ok(existsSync(join(pluginDir, "scripts", "ov-memory-doctor.mjs")));
  assert.ok(existsSync(join(pluginDir, "hosts", "claude-code", "ov-memory-doctor.mjs")));
  assert.match(readFileSync(join(pluginDir, "commands", "ov.md"), "utf-8"), /hosts\/claude-code\/ov-status\.mjs/);
});

test("hook commands run from plugin roots containing spaces", {
  skip: process.platform === "win32",
}, async (t) => {
  const home = mkdtempSync(join(tmpdir(), "ov-claude-hooks-"));
  const pluginRoot = join(home, "Application Support [plugin]");
  symlinkSync(pluginDir, pluginRoot, "dir");
  try {
    for (const [event, entries] of Object.entries(hooks())) {
      for (const { hooks: commands } of entries) {
        for (const hook of commands) {
          await t.test(`${event}: ${hook.command}`, () => {
            execFileSync("sh", ["-c", hook.command], {
              input: "{}",
              stdio: ["pipe", "pipe", "pipe"],
              env: {
                ...process.env,
                HOME: home,
                CLAUDE_PLUGIN_ROOT: pluginRoot,
                OPENVIKING_HOME: join(home, ".openviking"),
                OPENVIKING_CONFIG_FILE: join(home, "ov.conf"),
                OPENVIKING_CLI_CONFIG_FILE: join(home, "ovcli.conf"),
                OPENVIKING_MEMORY_ENABLED: "0",
              },
            });
          });
        }
      }
    }
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
