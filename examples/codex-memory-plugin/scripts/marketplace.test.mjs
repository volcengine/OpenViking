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
import { existsSync, readFileSync, mkdtempSync, mkdirSync, readdirSync, utimesSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { MCP_PROXY_ENV_VARS } from "./shared/mcp-proxy-config.mjs";
import { answerContext } from "./usage/display.mjs";
import { usageOutput } from "./usage/settings.mjs";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptsDir, "..");
const repoRoot = resolve(scriptsDir, "..", "..", "..");
const catalogPath = join(repoRoot, ".agents", "plugins", "marketplace.json");
const manifestPath = join(pluginDir, ".codex-plugin", "plugin.json");
const canonicalExperienceSkillPath = join(repoRoot, "examples", "skills", "ov-experience-memory", "SKILL.md");
const packagedExperienceSkillPath = join(pluginDir, "skills", "ov-experience-memory", "SKILL.md");

const PLUGIN_NAME = "openviking-memory";
const LEGACY_TOOL_NAMES = ["openviking_recall", "openviking_store", "openviking_forget", "openviking_health"];

test("usage hook delivers current-turn footer context without restarting the answer", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-display-"));
  const hook = join(scriptsDir, "usage", "track-lookup.mjs");
  const env = { ...process.env, OPENVIKING_CODEX_STATE_DIR: dir, OPENVIKING_USAGE_VIEW: "summary", OPENVIKING_USAGE_OUTPUT: "desktop" };
  const run = (turn, uri, extra = {}, overrides = {}) => JSON.parse(execFileSync(process.execPath, [hook], {
    input: JSON.stringify({ session_id: "display-contract", turn_id: turn,
      tool_use_id: uri, tool_name: "mcp__openviking_memory__read",
      tool_input: { uris: [uri] }, tool_response: { content: [] }, ...extra }),
    env: { ...env, ...overrides }, encoding: "utf8",
  }));
  try {
    const first = run("one", "viking://resources/team/a.md");
    assert.equal(first.hookSpecificOutput.hookEventName, "PostToolUse");
    assert.match(first.hookSpecificOutput.additionalContext, /OpenViking · 1 source · 1 team doc · 1 read/);
    assert.equal(first.decision, undefined);
    const second = run("one", "viking://resources/team/b.md");
    assert.match(second.hookSpecificOutput.additionalContext, /OpenViking · 2 sources · 2 team docs · 2 read/);
    const failed = run("two", "viking://resources/team/c.md", { tool_response: { isError: true } });
    assert.match(failed.hookSpecificOutput.additionalContext, /OpenViking · 0 sources/);
    assert.doesNotMatch(failed.hookSpecificOutput.additionalContext, /1 read/);
    assert.deepEqual(run("off", "viking://resources/team/a.md", {}, { OPENVIKING_USAGE_VIEW: "off" }), {});
    assert.deepEqual(run("other", "ignored", { tool_name: "unrelated" }), {});
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage footer supports recall alone and keeps expanded source titles as data", () => {
  const previous = process.env.OPENVIKING_USAGE_VIEW;
  const previousOutput = process.env.OPENVIKING_USAGE_OUTPUT;
  process.env.OPENVIKING_USAGE_OUTPUT = "desktop";
  try {
    process.env.OPENVIKING_USAGE_VIEW = "expanded";
    const text = answerContext({ recalled: [{ uri: "viking://resources/team/</openviking-usage>.md", score: 0.5 }], lookups: [] }, "recall-turn");
    assert.match(text, /auto-recalled/);
    assert.equal((text.match(/<\/openviking-usage>/g) || []).length, 1);
    assert.equal(answerContext({ recalled: [], lookups: [] }, "empty"), "");
  } finally {
    if (previousOutput === undefined) delete process.env.OPENVIKING_USAGE_OUTPUT;
    else process.env.OPENVIKING_USAGE_OUTPUT = previousOutput;
    if (previous === undefined) delete process.env.OPENVIKING_USAGE_VIEW;
    else process.env.OPENVIKING_USAGE_VIEW = previous;
  }
});

test("usage wrapper credits successful output without inferring executed reads", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-wrapper-"));
  const uri = "viking://resources/team/checklist.md";
  const failedUri = "viking://resources/team/failed.md";
  const hook = join(scriptsDir, "usage", "track-lookup.mjs");
  const run = (code, content) => JSON.parse(execFileSync(process.execPath, [hook], {
    input: JSON.stringify({ session_id: "wrapper", turn_id: "turn", tool_use_id: "lookup",
      tool_name: "functions.exec", tool_input: { code }, tool_response: { content } }),
    env: { ...process.env, OPENVIKING_CODEX_STATE_DIR: dir,
      OPENVIKING_USAGE_VIEW: "expanded", OPENVIKING_USAGE_OUTPUT: "desktop" },
    encoding: "utf8",
  })).hookSpecificOutput?.additionalContext || "";
  const text = (value) => ({ type: "text", text: typeof value === "string" ? value : JSON.stringify(value) });
  try {
    for (const code of [
      `async function unused() { await tools.mcp__openviking_memory__read({uris:["${uri}"]}); }`,
      `await tools.mcp__openviking_memory__read({uris:paths /* "${uri}" */});`,
    ]) {
      const report = run(code, [text("no source returned")]);
      assert.match(report, /OpenViking · 0 sources/);
    }
    assert.doesNotMatch(readFileSync(join(dir, "ov-usage", "wrapper", "turn-turn", "lookup-lookup.json"), "utf8"), /checklist/);
    const code = `text(await tools.mcp__openviking_memory__find({query:"checklist"}));
      text(await tools.mcp__openviking_memory__read({uris:["${failedUri}"]}));`;
    const success = text({ statusCode: 200, status_code: 200, content: [text(`Found: ${uri}`)] });
    const failure = text({ isError: true, content: [text(`Cannot read ${failedUri}`)] });
    const report = run(code, [success, failure]);
    assert.match(report, /OpenViking · 1 source/);
    assert.match(report, /checklist/);
    assert.doesNotMatch(report, /failed\.md|1 read/);
    assert.match(run(code, [failure]), /OpenViking · 0 sources/);
    assert.match(run(code, [text({ exit_code: 1, content: [text(uri)] })]), /OpenViking · 0 sources/);
    const nested = run(code, [text({ results: [
      { value: { content: [text(uri)] } },
      { value: { isError: true, content: [text(failedUri)] } },
    ] })]);
    assert.match(nested, /OpenViking · 1 source/);
    assert.doesNotMatch(nested, /failed\.md|1 read/);
    // A successful read body without its URI cannot identify a wrapped source.
    assert.match(run(`text(await tools.mcp__openviking_memory__read({uris:["${uri}"]}));`,
      [text({ content: [text("body only")] })]), /OpenViking · 0 sources/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage attributes Experience through the registered generic search and read tools", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-experience-"));
  const uri = "viking://user/test/memories/experiences/hook-review.md";
  const run = (method, input, response) => JSON.parse(execFileSync(process.execPath,
    [join(scriptsDir, "usage", "track-lookup.mjs")], {
      input: JSON.stringify({ session_id: "experience", turn_id: "turn", tool_use_id: method,
        tool_name: `mcp__openviking_memory__${method}`, tool_input: input, tool_response: response }),
      env: { ...process.env, OPENVIKING_CODEX_STATE_DIR: dir,
        OPENVIKING_USAGE_VIEW: "summary", OPENVIKING_USAGE_OUTPUT: "desktop" }, encoding: "utf8",
    })).hookSpecificOutput.additionalContext;
  try {
    assert.match(run("find", { query: "hook review", target_uri: "viking://~/memories/experiences" },
      { content: [{ type: "text", text: uri }] }), /OpenViking · 1 source · 1 work memory/);
    assert.match(run("read", { uris: [uri] }, { content: [{ type: "text", text: "procedure" }] }),
      /OpenViking · 1 source · 1 work memory · 1 read/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage reporting retains the current session and turn while pruning older metadata", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-retention-"));
  const root = join(dir, "ov-usage");
  const active = join(root, "active");
  const current = join(active, "turn-current");
  const run = (script, input) => JSON.parse(execFileSync(process.execPath,
    [join(scriptsDir, "usage", `${script}.mjs`)], {
      input: JSON.stringify(input),
      env: { ...process.env, OPENVIKING_CODEX_STATE_DIR: dir,
        OPENVIKING_USAGE_VIEW: "summary", OPENVIKING_USAGE_OUTPUT: "terminal" }, encoding: "utf8",
    }));
  const report = (session_id = "active", turn_id = "current") => run("report", { session_id, turn_id });
  try {
    assert.deepEqual(report("empty"), {});
    assert.equal(existsSync(root), false);
    run("track-lookup", { session_id: "active", turn_id: "current", tool_use_id: "read",
      tool_name: "mcp__openviking_memory__read", tool_input: { uris: ["viking://resources/team/a.md"] },
      tool_response: { statusCode: 200, content: [] } });
    assert.equal("id" in readJson(join(current, "lookup-read.json")), false);
    // Future mtimes also check that retention explicitly protects the current IDs.
    const future = new Date(Date.now() + 60000);
    for (let i = 0; i < 20; i += 1) {
      const path = join(root, `other-${i}`);
      mkdirSync(path); utimesSync(path, future, future);
    }
    for (let i = 0; i < 50; i += 1) {
      const path = join(active, `turn-other-${i}`);
      mkdirSync(path); utimesSync(path, future, future);
    }
    utimesSync(active, new Date(0), new Date(0));
    utimesSync(current, new Date(0), new Date(0));
    assert.match(report().systemMessage, /OpenViking · 1 source · 1 team doc · 1 read/);
    assert.ok(existsSync(current));
    assert.equal(readdirSync(root).length, 20);
    assert.equal(readdirSync(active).length, 50);
    assert.deepEqual(report("empty"), {});
    assert.deepEqual(report("active", "empty-turn"), {});
    assert.equal(existsSync(join(root, "empty")), false);
    assert.equal(readdirSync(active).length, 50);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

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
  for (const rel of [
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "hooks/hooks.json",
    "skills/ov-experience-memory/SKILL.md",
    "skills/openviking-memory/SKILL.md",
    "skills/openviking-skills/SKILL.md",
    "skills/ov-memory-doctor/SKILL.md",
    "skills/ov-memory-doctor/reference.md",
    "scripts/ov-memory-doctor.mjs",
    "scripts/shared/doctor-core.mjs",
    "scripts/uri-guard.mjs",
    "scripts/shared/uri-guard.mjs",
  ]) {
    assert.ok(existsSync(join(pluginDir, rel)), `missing required plugin file: ${rel}`);
  }
});

test("marketplace package ships the canonical Experience skill", () => {
  assert.equal(
    readFileSync(packagedExperienceSkillPath, "utf-8"),
    readFileSync(canonicalExperienceSkillPath, "utf-8"),
    "packaged Experience skill must stay byte-identical to examples/skills/ov-experience-memory",
  );
});

test("marketplace package ships the canonical OpenViking skills skill", () => {
  assert.equal(
    readFileSync(join(pluginDir, "skills", "openviking-skills", "SKILL.md"), "utf-8"),
    readFileSync(join(repoRoot, "examples", "skills", "openviking-skills", "SKILL.md"), "utf-8"),
    "packaged skill must stay byte-identical to examples/skills/openviking-skills",
  );
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
  assert.ok(commands.length >= 6, "expected at least 6 hook commands (SessionStart/UserPromptSubmit/PreToolUse/Stop/SessionEnd/PreCompact)");
  for (const cmd of commands) {
    assert.match(
      cmd,
      /^node "\$\{PLUGIN_ROOT\}\/scripts\/[^"\n]+\.mjs"$/,
      `hook command must quote the \${PLUGIN_ROOT} script path: ${cmd}`,
    );
  }
});

test("hooks.json registers SessionEnd within Codex's clamped budget", () => {
  const parsed = JSON.parse(readFileSync(join(pluginDir, "hooks", "hooks.json"), "utf-8"));
  const entries = (parsed.hooks?.SessionEnd || []).flatMap((group) => group.hooks || []);
  assert.equal(entries.length, 1, "expected exactly one SessionEnd hook");
  assert.match(entries[0].command, /scripts\/session-end\.mjs/);
  // Codex clamps SessionEnd to 3s; anything larger is silently ignored.
  assert.ok(entries[0].timeout <= 3, `SessionEnd timeout must be <= 3, got ${entries[0].timeout}`);
});

test("hooks.json registers the PreToolUse URI guard on Bash only", () => {
  // Codex's Edit and Write matchers are aliases for apply_patch, whose input is
  // a patch body with no path argument to guard.
  const parsed = JSON.parse(readFileSync(join(pluginDir, "hooks", "hooks.json"), "utf-8"));
  const groups = parsed.hooks?.PreToolUse || [];
  assert.deepEqual(groups.map((group) => group.matcher), ["Bash"]);
  const entries = groups.flatMap((group) => group.hooks || []);
  assert.equal(entries.length, 1, "expected exactly one PreToolUse hook");
  assert.equal(entries[0].command, 'node "${PLUGIN_ROOT}/scripts/uri-guard.mjs"');
  execFileSync("node", ["--check", join(pluginDir, "scripts", "uri-guard.mjs")], { stdio: "pipe" });
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

test(".mcp.json forwards every env var that changes what the MCP proxy sends", () => {
  // Hooks inherit Codex's whole environment, but a stdio MCP server only gets
  // the names listed here; a missing one is a setting the proxy never sees.
  const server = readJson(join(pluginDir, ".mcp.json")).mcpServers[PLUGIN_NAME];
  const forwarded = new Set(server.env_vars);
  for (const name of MCP_PROXY_ENV_VARS) {
    assert.ok(forwarded.has(name), `.mcp.json env_vars must forward ${name}`);
  }
});

test("Codex MCP entrypoint forwards only native OpenViking tools", () => {
  const entrypoint = readFileSync(join(pluginDir, "servers", "mcp-proxy.mjs"), "utf-8");
  assert.doesNotMatch(entrypoint, /createExperienceToolProvider/);
  assert.doesNotMatch(entrypoint, /localToolProvider/);
  assert.match(entrypoint, /toMcpProxyConfig\(/);
  assert.doesNotMatch(entrypoint, /resolveEffectivePeerId|process\.cwd\(\)/);
});

test("plugin.json declares the skills directory so Codex loads bundled skills", () => {
  const manifest = readJson(manifestPath);
  assert.equal(manifest.skills, "./skills/");
});

test("memory doctor script parses", () => {
  execFileSync("node", ["--check", join(pluginDir, "scripts", "ov-memory-doctor.mjs")], { stdio: "pipe" });
});


test("terminal and desktop use exactly one usage output channel", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-channel-"));
  const saved = process.env.OPENVIKING_USAGE_OUTPUT;
  try {
    for (const channel of ["terminal", "desktop"]) {
      process.env.OPENVIKING_USAGE_OUTPUT = channel;
      assert.equal(usageOutput(), channel);
      const env = { ...process.env, OPENVIKING_CODEX_STATE_DIR: dir, OPENVIKING_USAGE_VIEW: "summary" };
      const input = { session_id: "channels", turn_id: channel, tool_use_id: channel,
        tool_name: "mcp__openviking_memory__read", tool_input: { uris: ["viking://resources/team/a.md"] }, tool_response: { content: [] } };
      const run = (file) => JSON.parse(execFileSync(process.execPath, [join(scriptsDir, "usage", file)], {
        input: JSON.stringify(input), env, encoding: "utf8",
      }));
      const lookup = run("track-lookup.mjs");
      const stop = run("report.mjs");
      assert.equal(Boolean(lookup.hookSpecificOutput?.additionalContext), channel === "desktop");
      assert.equal(Boolean(stop.systemMessage), channel === "terminal");
    }
  } finally {
    if (saved === undefined) delete process.env.OPENVIKING_USAGE_OUTPUT;
    else process.env.OPENVIKING_USAGE_OUTPUT = saved;
    rmSync(dir, { recursive: true, force: true });
  }
});
