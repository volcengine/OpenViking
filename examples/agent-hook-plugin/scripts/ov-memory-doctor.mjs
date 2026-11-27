#!/usr/bin/env node

/**
 * Client-side diagnostics for the hook hosts.
 *
 * Cursor, TRAE, TRAE CN and ZCode are installed by writing into the host's own
 * configuration files rather than through a plugin marketplace, so what can go
 * wrong is different from Claude Code's and Codex's: the host config may have
 * lost the OpenViking entries, the assembled runtime beside the integration may
 * be missing or stale, or the client may simply never have been installed.
 * Antigravity, Kimi Code, CodeBuddy Code and Qoder CLI load a plugin directory
 * instead, where the equivalent failure is a plugin the host never registered.
 * The configuration and connection sections are the ones every harness shares.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [<client>] [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * The client defaults to the one this copy was installed for. Exit code 1 when
 * any check fails, 0 otherwise. Never prints a full api key.
 */

import { readdirSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadAgentHookConfig } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import {
  credentialSources,
  existsPath,
  fmtAge,
  fmtBytes,
  homeShort,
  inspectConfigFiles,
  inspectJsonFile,
  reportCredentials,
  reportPeer,
  reportTimeouts,
  reportToggles,
  runDoctor,
  scanDebugLog,
  sweepEnv,
  tryJson,
} from "../../memory-plugin-shared/lib/doctor-core.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const REQUIRED_PLUGIN_FILES = ["scripts/hook.mjs", "scripts/uri-guard.mjs", "servers/mcp-proxy.mjs", "hosts/index.mjs"];
const SKILLS = ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search", "ov-memory-doctor"];

// Kimi Code, CodeBuddy Code and Qoder CLI load a plugin in their own format,
// with this integration two levels inside it.
const hostPlugin = (...rest) => resolvePath(PLUGIN_ROOT, "..", "..", ...rest);
const antigravityPlugin = (...rest) => join(homedir(), ".gemini", "config", "plugins", "openviking-memory", ...rest);
// CodeBuddy Code and Qoder CLI run an installed plugin from a versioned copy in
// their cache. Uninstalling marks that copy orphaned instead of deleting it,
// and disabling only flips the plugin's enabledPlugins entry.
const hostLoaded = (...configDirs) => configDirs.some((dir) => {
  const cache = join(dir, "plugins", "cache", "openviking", "openviking-memory");
  let versions = [];
  try {
    versions = readdirSync(cache);
  } catch {}
  return versions.some((version) => !existsPath(join(cache, version, ".orphaned_at")))
    && tryJson(join(dir, "settings.json"))?.enabledPlugins?.["openviking-memory@openviking"] !== false;
});

/**
 * Where each client keeps what the installer writes.
 *
 * ZCode reads hooks and MCP out of one config file; Cursor and TRAE keep them
 * apart, and TRAE's MCP path is the editor's platform-specific user directory.
 * A client with a `manifest` loads a host plugin that carries its own hooks and
 * MCP server, and `registered` says whether the host took it in. No client
 * names timeout budgets: each hook's requests share one deadline its host
 * adapter sets inside the hook's timeout, so no knob can make one outlive it.
 */
const CLIENTS = {
  cursor: {
    cliName: "cursor",
    launcherHint: "Cursor",
    hooks: () => join(homedir(), ".cursor", "hooks.json"),
    mcp: () => join(homedir(), ".cursor", "mcp.json"),
    skills: () => join(homedir(), ".cursor", "skills"),
    extras: () => [join(homedir(), ".cursor", "rules", "openviking-memory.mdc")],
  },
  trae: {
    cliName: "trae",
    launcherHint: "TRAE",
    hooks: () => join(homedir(), ".trae", "hooks.json"),
    mcp: () => (process.platform === "darwin"
      ? join(homedir(), "Library", "Application Support", "Trae", "User", "mcp.json")
      : join(homedir(), ".trae", "mcp.json")),
    skills: () => join(homedir(), ".trae", "skills"),
  },
  "trae-cn": {
    cliName: "trae",
    launcherHint: "TRAE CN",
    hooks: () => join(homedir(), ".trae-cn", "hooks.json"),
    mcp: () => (process.platform === "darwin"
      ? join(homedir(), "Library", "Application Support", "Trae CN", "User", "mcp.json")
      : join(homedir(), ".trae-cn", "mcp.json")),
    skills: () => join(homedir(), ".trae-cn", "skills"),
  },
  zcode: {
    cliName: "zcode",
    launcherHint: "ZCode",
    hooks: () => join(homedir(), ".zcode", "cli", "config.json"),
    mcp: () => join(homedir(), ".zcode", "cli", "config.json"),
    skills: () => join(homedir(), ".zcode", "skills"),
  },
  antigravity: {
    cliName: "agy",
    launcherHint: "Antigravity",
    hooks: () => antigravityPlugin("hooks.json"),
    mcp: () => antigravityPlugin("mcp_config.json"),
    skills: () => antigravityPlugin("skills"),
  },
  kimicode: {
    cliName: "kimi",
    launcherHint: "Kimi Code",
    manifest: () => hostPlugin("kimi.plugin.json"),
    hooks: () => hostPlugin("kimi.plugin.json"),
    mcp: () => hostPlugin("kimi.plugin.json"),
    skills: () => hostPlugin("skills"),
    registered: () => {
      // Beside managed/, so it is found wherever KIMI_CODE_HOME put the plugin.
      const plugins = tryJson(hostPlugin("..", "..", "installed.json"))?.plugins;
      return Array.isArray(plugins) && plugins.some((plugin) => plugin?.id === "openviking-memory" && plugin.enabled !== false);
    },
  },
  codebuddy: {
    cliName: "codebuddy",
    launcherHint: "CodeBuddy Code",
    manifest: () => hostPlugin(".codebuddy-plugin", "plugin.json"),
    hooks: () => hostPlugin("hooks", "hooks.json"),
    mcp: () => hostPlugin(".mcp.json"),
    skills: () => hostPlugin("skills"),
    registered: () => hostLoaded(join(homedir(), ".codebuddy")),
  },
  qoder: {
    cliName: "qodercli",
    launcherHint: "Qoder CLI",
    manifest: () => hostPlugin(".qoder-plugin", "plugin.json"),
    hooks: () => hostPlugin("hooks", "hooks.json"),
    mcp: () => hostPlugin(".mcp.json"),
    skills: () => hostPlugin("skills"),
    registered: () => hostLoaded(
      process.env.QODER_CONFIG_DIR || join(homedir(), ".qoder"),
      process.env.QODERCN_CONFIG_DIR || join(homedir(), ".qoder-cn"),
    ),
  },
};

/**
 * The client this copy serves: the argument, the install manifest, or the
 * directory an installed copy sits in, which is named after its client.
 */
function resolveClient(argv) {
  const named = argv.find((arg) => CLIENTS[arg]);
  if (named) return named;
  const installed = tryJson(join(PLUGIN_ROOT, "integration.json"))?.client;
  if (CLIENTS[installed]) return installed;
  return CLIENTS[basename(PLUGIN_ROOT)] ? basename(PLUGIN_ROOT) : "cursor";
}

const CLIENT = resolveClient(process.argv.slice(2));
const SPEC = CLIENTS[CLIENT];
// TRAE CN is served by the TRAE host directory.
const HOST_DIR = CLIENT === "trae-cn" ? "trae" : CLIENT;

function hookStateDir() {
  const root = process.env.OPENVIKING_HOOK_STATE_DIR || join(homedir(), ".openviking", "hook-state");
  return join(root, CLIENT);
}

function checkInstall(report) {
  report.section("Plugin install");
  const manifestPath = SPEC.manifest ? SPEC.manifest() : join(PLUGIN_ROOT, "plugin.json");
  const manifest = tryJson(manifestPath);
  const installed = tryJson(join(PLUGIN_ROOT, "integration.json"));
  report.info(`running from ${homeShort(PLUGIN_ROOT)} (version ${manifest?.version || "?"}, client ${CLIENT})`);
  if (installed?.client && installed.client !== CLIENT) {
    report.warn(`this copy was installed for ${installed.client}, not ${CLIENT}`, "", `run the ${installed.client} copy, or pass the client name as an argument`);
  }

  const missing = REQUIRED_PLUGIN_FILES.filter((rel) => !existsPath(join(PLUGIN_ROOT, rel)));
  if (!manifest) missing.unshift(relative(PLUGIN_ROOT, manifestPath));
  if (missing.length) report.fail("plugin files missing", missing.join(", "), `curl -fsSL https://openviking.ai/install | bash -s -- --harness ${CLIENT}`);
  else report.ok("plugin files present (hook entry, URI guard, MCP proxy, host adapters)");

  // The runtime is not vendored: it sits beside the integration, assembled from
  // lib/MANIFEST at install time. A stale or absent copy is ERR_MODULE_NOT_FOUND
  // on the first hook, which the host reports as nothing at all.
  const shared = resolvePath(PLUGIN_ROOT, "..", "memory-plugin-shared", "lib");
  const modules = existsPath(shared) ? readdirSync(shared).filter((name) => name.endsWith(".mjs")) : [];
  if (!modules.length) report.fail("the shared runtime is not assembled beside this integration", homeShort(shared), `re-run the installer with --harness ${CLIENT}`);
  else report.ok(`shared runtime: ${modules.length} modules in ${homeShort(shared)}`);
  if (!SPEC.manifest && !existsPath(join(PLUGIN_ROOT, "hosts", HOST_DIR, "hooks.json"))) {
    report.fail(`no hooks template for ${CLIENT}`, "", "re-run the installer");
  }
  if (SPEC.registered && !SPEC.registered()) {
    report.fail(`${SPEC.launcherHint} has not loaded this plugin`, "it is assembled on disk, but the host's plugin registry does not list it as installed and enabled", `re-run the installer with --harness ${CLIENT}`);
  }

  const hooksPath = SPEC.hooks();
  const hooks = inspectJsonFile(hooksPath);
  if (!hooks.exists) report.fail(`${homeShort(hooksPath)} not found`, `${SPEC.launcherHint} has no hook configuration on this machine`, `re-run the installer with --harness ${CLIENT}`);
  else if (!hooks.ok) report.fail(`${homeShort(hooksPath)} cannot be parsed`, hooks.error, "fix the JSON; the installer refuses to overwrite a file it cannot read");
  else {
    const text = JSON.stringify(hooks.data);
    const declared = hooks.data.hooks?.events || hooks.data.hooks || hooks.data["openviking-memory"] || {};
    const events = Array.isArray(declared) ? declared.map((hook) => hook?.event) : Object.keys(declared);
    // A file shared with the user's own hooks is told apart by the installer's
    // tag; a host plugin's hooks are all ours and name the script directly.
    if (!text.includes(SPEC.manifest ? "scripts/hook.mjs" : "OPENVIKING_INTEGRATION_ID")) report.fail(`${homeShort(hooksPath)} has no OpenViking hooks`, `events present: ${events.join(", ") || "(none)"}`, `re-run the installer with --harness ${CLIENT}`);
    else {
      report.ok(`hooks in ${homeShort(hooksPath)}: ${events.join(", ")}`);
      if (!text.includes("scripts/hook.mjs")) report.warn("the installed hook commands do not name scripts/hook.mjs", "they were written by an older installer", "re-run the installer");
      if (CLIENT === "zcode" && hooks.data.hooks?.enabled !== true) report.fail("ZCode's hook runner is off", "config.json has hooks.enabled != true, so no hook fires", "re-run the installer, or set hooks.enabled = true");
    }
    for (const command of String(text).match(/'([^']*\.mjs)'/gu) || []) {
      const script = command.slice(1, -1);
      if (!existsPath(script)) report.fail("an installed hook names a script that is not on disk", script, "re-run the installer");
    }
  }

  const mcpPath = SPEC.mcp();
  const mcp = inspectJsonFile(mcpPath);
  const server = mcp.ok ? (mcp.data.mcpServers?.openviking || mcp.data.mcp?.servers?.openviking) : null;
  if (installed?.capabilities && !installed.capabilities.includes("mcp")) {
    report.info("the installer wrote no MCP server: the 'openviking' server registered before it is the only one");
  } else if (!mcp.exists) report.warn(`${homeShort(mcpPath)} not found`, "the MCP memory tools are not wired up", `re-run the installer with --harness ${CLIENT}`);
  else if (!mcp.ok) report.fail(`${homeShort(mcpPath)} cannot be parsed`, mcp.error, "fix the JSON");
  else if (!server) report.fail(`no 'openviking' MCP server in ${homeShort(mcpPath)}`, "", `re-run the installer with --harness ${CLIENT}`);
  else {
    report.ok(`MCP server 'openviking' in ${homeShort(mcpPath)}`);
    const proxy = Array.isArray(server.args) ? server.args.find((arg) => String(arg).endsWith("mcp-proxy.mjs")) : "";
    // A host plugin names its proxy relative to the plugin root.
    if (proxy && isAbsolute(proxy) && !existsPath(proxy)) report.fail("the MCP server points at a proxy that is not on disk", proxy, "re-run the installer");
    if (server.env?.OPENVIKING_HOOK_SOURCE && server.env.OPENVIKING_HOOK_SOURCE !== CLIENT) {
      report.warn(`the MCP server is tagged for ${server.env.OPENVIKING_HOOK_SOURCE}`, `this copy serves ${CLIENT}; the proxy reads its configuration under the tagged client`, "re-run the installer");
    }
  }

  const skillsDir = SPEC.skills();
  const absent = SKILLS.filter((skill) => !existsPath(join(skillsDir, skill, "SKILL.md")));
  if (absent.length) report.warn(`skills missing from ${homeShort(skillsDir)}`, absent.join(", "), `re-run the installer with --harness ${CLIENT}`);
  else report.ok(`${SKILLS.length} skills in ${homeShort(skillsDir)}`);
  for (const extra of SPEC.extras?.() || []) {
    if (!existsPath(extra)) report.warn(`${homeShort(extra)} is missing`, "", `re-run the installer with --harness ${CLIENT}`);
  }
}

function checkConfig(report, cfg, host) {
  report.section("Configuration");
  const { cliConf, ovConf } = inspectConfigFiles(report, host);
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    report.fail("no usable config — the plugin falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key (chmod 600)");
  }
  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.account, user: cfg.user });
  report.info(`auth mode ${cfg.authMode} (identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"})`);
  const peer = reportPeer(report, cfg);
  reportTimeouts(report, cfg, host);

  const toggles = [
    `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`,
    `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`,
    `write path ${cfg.writePathAsync ? "async" : "sync"}`,
  ];
  if (CLIENT === "cursor") toggles.push(`commit threshold ${cfg.commitTurnThreshold}`);
  reportToggles(report, cfg, host, toggles);

  sweepEnv(report, cfg, host);
  return { keyInfo, peer, ovConf };
}

/**
 * Whether a session state file shows a captured turn. Cursor, TRAE and the
 * incremental-turn hosts remember the turns they sent by hash or by id;
 * Antigravity's adapter instead advances a `lastStep` cursor over the
 * transcript, so a session that captured without one of the two lists is not
 * idle. A cursor still at -1 has run but not reached a capturable step yet.
 */
function hasCapturedTurn(state) {
  return (state.data?.capturedHashes?.length || state.data?.capturedTurnIds?.length || 0) > 0
    || (Number.isInteger(state.data?.lastStep) && state.data.lastStep >= 0);
}

function checkActivity(report, cfg, connection) {
  report.section("Recent activity");
  const dir = hookStateDir();
  let states = [];
  try {
    states = readdirSync(dir).filter((name) => name.endsWith(".json")).map((name) => {
      const path = join(dir, name);
      return { name, mtimeMs: statSync(path).mtimeMs, data: tryJson(path) };
    }).sort((a, b) => b.mtimeMs - a.mtimeMs);
  } catch {
    report.info(`no hook state in ${homeShort(dir)} yet — no ${SPEC.launcherHint} hook has run (or OPENVIKING_HOOK_STATE_DIR points elsewhere)`);
  }
  if (states.length) {
    const newest = states[0];
    report.info(`${states.length} session state file(s) in ${homeShort(dir)}; newest ${fmtAge(newest.mtimeMs)}`);
    const captured = states.filter(hasCapturedTurn);
    if (states.length > 3 && !captured.length) {
      report.warn("no session has ever captured a turn", "the Stop hook runs but never appends messages", "check the Connection section; set OPENVIKING_DEBUG=1 and read the hook log");
    }
    if (newest.data?.recallBlock === null) report.info("the last prompt recalled nothing — an empty context space, or a failed request the log will name");
  }

  const log = scanDebugLog(cfg.debugLogPath);
  if (!log.exists) {
    report.info(`no hook log at ${homeShort(cfg.debugLogPath)}${cfg.debug ? ` — debug is on but no hook has run since; if a ${SPEC.launcherHint} turn ran, hooks are not being spawned` : ""}`);
  } else {
    report.info(`hook log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}, hooks seen: ${log.hooks.join(", ") || "(none)"}`);
    if (log.recentErrors.length) report.warn(`hook errors in the last day of logging (${log.recentErrors.length} shown)`, log.recentErrors.map((e) => `${e.ts} ${e.hook}/${e.stage}: ${e.message}`).join("\n"));
  }
  if (connection?.summary?.reachable === false) report.info("captures that fail are queued on disk and replayed at the next session start");
}

const HOST = {
  harness: CLIENT,
  pluginRoot: PLUGIN_ROOT,
  cliName: SPEC.cliName,
  launcherHint: SPEC.launcherHint,
  loadConfig: () => loadAgentHookConfig(CLIENT),
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ client: CLIENT, credentialSource: cfg.credentialSource, authMode: cfg.authMode }),
};

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isDirectRun()) {
  runDoctor(HOST).catch((err) => {
    console.error("ov-memory-doctor failed:", err?.stack || err?.message || err);
    process.exit(2);
  });
}
