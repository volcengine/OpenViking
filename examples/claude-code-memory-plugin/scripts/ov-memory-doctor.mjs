#!/usr/bin/env node

/**
 * Client-side diagnostics for the OpenViking Claude Code memory plugin.
 *
 * Covers the three things that go wrong on a user's machine — the plugin
 * install (marketplace / enablement / hooks / MCP wiring), the client config
 * (which file won, is the JSON valid, what the key claims) and the connection
 * to the server (reachability, auth, tenant-data access, /mcp) — plus the
 * runtime evidence the hooks leave behind. When the server runs on this
 * machine (loopback url) it also checks
 * the port, plugin-only keys in ov.conf and `GET /ready`. Provider-level validation stays with `openviking-server doctor`.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { STATE_DIR } from "./lib/state.mjs";
import {
  assessProbes,
  checkServerHealth,
  collectEnv,
  countDirEntries,
  createReport,
  describeApiKey,
  existsPath,
  fileInfo,
  fmtAge,
  fmtBytes,
  homeShort,
  inspectJsonFile,
  isLoopbackUrl,
  lintBaseUrl,
  parseNodeMajor,
  probeOpenViking,
  readStateFiles,
  runCommand,
  scanDebugLog,
  scanRcFiles,
  unknownOvcliKeys,
  unknownPluginKeys,
  whichCommand,
  checkWorkspace,
  lintPeerScopeDowngrade,
  WORKSPACE_PEER_HINT,
} from "./shared/doctor-core.mjs";
import { isBypassed } from "./shared/session-model.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_ID = "openviking-memory@openviking";
const PLUGIN_NAME = "openviking-memory";
const MARKETPLACE = "openviking";
const LEGACY_MARKETPLACE = "openviking-plugins-local";
const CLAUDE_DIR = join(homedir(), ".claude");
const RC_MARKERS = ["# >>> openviking claude-code memory plugin >>>", "# >>> openviking-codex-plugin >>>"];
const STATE_FILES = ["last-recall.json", "last-capture.json", "last-session-event.json", "daily-stats.json", "server-probe.json", "host-cli-probe.json", "context-face.json"];
const REQUIRED_PLUGIN_FILES = [".claude-plugin/plugin.json", "hooks/hooks.json", ".mcp.json", "servers/mcp-proxy.mjs", "scripts/config.mjs", "scripts/auto-recall.mjs", "scripts/auto-capture.mjs"];

function parseArgs(argv) {
  const opts = { json: false, offline: false, timeoutMs: 5000, color: process.stdout.isTTY && !process.env.NO_COLOR };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--json") opts.json = true;
    else if (arg === "--offline" || arg === "--no-network") opts.offline = true;
    else if (arg === "--no-color") opts.color = false;
    else if (arg === "--timeout") opts.timeoutMs = Math.max(1000, Number(argv[++i]) || 5000);
    else if (arg === "-h" || arg === "--help") {
      console.log("usage: ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]");
      process.exit(0);
    }
  }
  if (opts.json) opts.color = false;
  return opts;
}

function expandHome(p) {
  return p ? resolvePath(p.replace(/^~(?=$|\/)/, homedir())) : p;
}

function tryJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function envBoolValue(name) {
  const v = process.env[name];
  if (v == null || v === "") return undefined;
  const lower = v.trim().toLowerCase();
  if (["0", "false", "no"].includes(lower)) return false;
  if (["1", "true", "yes"].includes(lower)) return true;
  return undefined;
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function checkEnvironment(report) {
  report.section("Environment");
  const nodeMajor = parseNodeMajor(process.version);
  const nodePath = process.execPath;
  if (nodeMajor >= 18) report.ok(`node ${process.version} (${nodePath})`);
  else report.fail(`node ${process.version} is too old`, "hooks and the MCP proxy need Node.js 18+ (global fetch)", "install Node.js 18 or newer and make sure it is first on PATH");
  const nodeOnPath = whichCommand("node");
  if (!nodeOnPath) {
    report.warn("`node` is not on PATH for this process", "hooks and .mcp.json invoke the bare command `node`; if Claude Code was launched without your shell profile (nvm/volta/fnm), hooks never start",
      "put node on PATH for the environment that launches Claude Code, or set PATH in the `env` block of ~/.claude/settings.json");
  } else if (resolvePath(nodeOnPath) !== resolvePath(nodePath)) {
    report.info(`PATH resolves node to ${nodeOnPath} (this run used ${nodePath})`);
  }

  const claude = runCommand("claude", ["--version"], { timeoutMs: 15000 });
  if (claude.ok) {
    report.ok(`claude ${claude.stdout.split("\n")[0]}`);
    const plugin = runCommand("claude", ["plugin", "--help"], { timeoutMs: 15000 });
    if (!plugin.ok) report.warn("`claude plugin` subcommand unavailable", "this Claude Code build predates the plugin system; only the legacy settings.json hook install works", "upgrade Claude Code to 2.0+");
  } else {
    report.info(`claude CLI not found on PATH (${claude.error || "?"}) — install checks that need it are skipped`);
  }
  report.info(`platform ${process.platform} ${process.arch}, cwd ${homeShort(process.cwd())}`);
  return { claudeOnPath: claude.ok };
}

function checkInstall(report, { claudeOnPath }) {
  report.section("Plugin install");
  const manifest = tryJson(join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"));
  const version = manifest?.version || "?";
  const inCache = PLUGIN_ROOT.includes(`${join(".claude", "plugins", "cache")}`);
  report.info(`running from ${homeShort(PLUGIN_ROOT)} (version ${version}, ${inCache ? "marketplace cache" : "directory install / dev checkout"})`);

  const missing = REQUIRED_PLUGIN_FILES.filter((rel) => !existsPath(join(PLUGIN_ROOT, rel)));
  if (missing.length) report.fail("plugin files missing", missing.join(", "), "reinstall: claude plugin uninstall openviking-memory@openviking && claude plugin install openviking-memory@openviking");
  else report.ok("plugin files present (hooks, MCP proxy, scripts)");
  if (!existsPath(join(PLUGIN_ROOT, "skills"))) {
    report.warn("no skills/ directory in this plugin copy", "the installed copy predates the bundled skills; Claude Code caches by version, so `claude plugin update` is a no-op until the version changes",
      "claude plugin marketplace update openviking && claude plugin uninstall openviking-memory@openviking && claude plugin install openviking-memory@openviking");
  }

  // Registry: installed_plugins.json
  const installed = inspectJsonFile(join(CLAUDE_DIR, "plugins", "installed_plugins.json"));
  let installPath = "";
  if (!installed.exists) {
    report.warn("~/.claude/plugins/installed_plugins.json not found", "no plugin has ever been installed through the marketplace on this machine");
  } else if (!installed.ok) {
    report.fail("~/.claude/plugins/installed_plugins.json is unreadable", installed.error);
  } else {
    const plugins = installed.data.plugins || {};
    const ids = Object.keys(plugins).filter((id) => id.startsWith(`${PLUGIN_NAME}@`) || id.startsWith("claude-code-memory-plugin@"));
    const entries = Array.isArray(plugins[PLUGIN_ID]) ? plugins[PLUGIN_ID] : (plugins[PLUGIN_ID] ? [plugins[PLUGIN_ID]] : []);
    if (!entries.length) {
      report.fail(`${PLUGIN_ID} is not registered in installed_plugins.json`, ids.length ? `found instead: ${ids.join(", ")}` : "",
        "bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness claude");
    } else {
      const entry = entries[0];
      installPath = entry.installPath || "";
      report.ok(`registered ${PLUGIN_ID} ${entry.version || "?"} (scope ${entry.scope || "?"}, updated ${entry.lastUpdated || "?"})`);
      if (installPath && resolvePath(installPath) !== PLUGIN_ROOT) {
        const registeredDoctor = join(installPath, "scripts", "ov-memory-doctor.mjs");
        report.warn("this script is not running from the registered install", `registered: ${homeShort(installPath)}\nrunning:    ${homeShort(PLUGIN_ROOT)}`,
          existsPath(registeredDoctor)
            ? `Claude Code executes hooks from the registered copy; check that one with: node ${homeShort(registeredDoctor)}`
            : `Claude Code executes hooks from the registered copy (${entry.version || "?"}), which predates this script — update the plugin, then rerun from there`);
      }
      if (installPath && !existsPath(join(installPath, ".claude-plugin", "plugin.json"))) {
        report.fail("registered installPath no longer exists", homeShort(installPath), "reinstall the plugin");
      }
      if (entries.length > 1) report.warn(`${PLUGIN_ID} has ${entries.length} install records`, entries.map((e) => `${e.scope}: ${homeShort(e.installPath || "")}`).join("\n"));
    }
    const extra = ids.filter((id) => id !== PLUGIN_ID);
    if (extra.length) {
      report.warn("additional openviking plugin ids are installed", extra.join(", "), `claude plugin uninstall <id> for each stale copy (legacy marketplace ${LEGACY_MARKETPLACE})`);
    }
  }

  // Marketplace
  const known = inspectJsonFile(join(CLAUDE_DIR, "plugins", "known_marketplaces.json"));
  if (known.ok) {
    const entry = known.data[MARKETPLACE];
    if (!entry) {
      report.fail(`marketplace '${MARKETPLACE}' is not registered`, `known: ${Object.keys(known.data).join(", ") || "(none)"}`,
        "re-run the installer, or: claude plugin marketplace add ~/.openviking/marketplaces/openviking-claude");
    } else {
      const source = entry.source || {};
      const location = entry.installLocation || source.path || "";
      const desc = source.source === "directory" ? `directory ${homeShort(source.path || "")}` : source.source === "github" ? `github ${source.repo || ""}` : JSON.stringify(source);
      if (/\.json$/i.test(String(source.path || ""))) {
        report.warn(`marketplace '${MARKETPLACE}' is registered as a file (${homeShort(source.path)})`, "file-type marketplaces mis-derive installLocation and `claude plugin marketplace update` fails with EISDIR",
          "claude plugin marketplace remove openviking && re-run the installer (it registers a directory)");
      } else if (location && !existsPath(join(location, ".claude-plugin", "marketplace.json"))) {
        report.fail(`marketplace '${MARKETPLACE}' points at a missing directory`, `${desc}\nexpected ${homeShort(join(location, ".claude-plugin", "marketplace.json"))}`,
          "the checkout/archive was moved or deleted; re-run the installer");
      } else {
        report.ok(`marketplace '${MARKETPLACE}' → ${desc}`);
        if (source.source === "directory" && location) {
          const manifest = tryJson(join(location, ".claude-plugin", "marketplace.json"));
          const pluginSrc = manifest?.plugins?.find?.((p) => p?.name === PLUGIN_NAME)?.source;
          if (pluginSrc && typeof pluginSrc === "object" && pluginSrc.source === "git-subdir") {
            report.info(`marketplace fetches ${pluginSrc.url || "?"}@${pluginSrc.ref || "?"} ${pluginSrc.path || ""} (update: claude plugin marketplace update openviking && claude plugin update ${PLUGIN_ID})`);
          } else if (pluginSrc) {
            report.info("directory marketplace: updates need the installer to be re-run (claude plugin update cannot fetch)");
          }
        }
      }
    }
    if (known.data[LEGACY_MARKETPLACE]) {
      report.warn(`legacy marketplace '${LEGACY_MARKETPLACE}' is still registered`, "", `claude plugin marketplace remove ${LEGACY_MARKETPLACE}`);
    }
  } else if (known.exists) {
    report.fail("~/.claude/plugins/known_marketplaces.json is unreadable", known.error);
  }

  // settings.json
  const settings = inspectJsonFile(join(CLAUDE_DIR, "settings.json"));
  if (settings.exists && !settings.ok) {
    report.fail("~/.claude/settings.json is not valid JSON", settings.error, "fix the JSON — Claude Code ignores the whole file otherwise");
  } else if (settings.ok) {
    const enabled = settings.data.enabledPlugins?.[PLUGIN_ID];
    if (enabled === true) report.ok(`enabledPlugins["${PLUGIN_ID}"] = true`);
    else if (enabled === false) report.fail(`plugin is disabled in ~/.claude/settings.json`, "", `claude plugin enable ${PLUGIN_ID}`);
    else report.warn(`enabledPlugins has no entry for ${PLUGIN_ID}`, "installed but never enabled (or enabled at another scope)", `claude plugin enable ${PLUGIN_ID}`);
    const otherEnabled = Object.entries(settings.data.enabledPlugins || {}).filter(([id, on]) => on && id !== PLUGIN_ID && /openviking|claude-code-memory-plugin/.test(id));
    if (otherEnabled.length) report.warn("more than one openviking plugin is enabled", otherEnabled.map(([id]) => id).join(", "), "disable/uninstall the stale one or hooks fire twice");

    const hooksText = JSON.stringify(settings.data.hooks || {});
    if (/openviking|auto-recall\.mjs|auto-capture\.mjs/.test(hooksText)) {
      report.warn("legacy openviking hooks are still merged into ~/.claude/settings.json", "pre-2.0 installs wrote hooks there; together with the plugin every hook now fires twice (double recall/capture)",
        "remove the openviking entries from .hooks in ~/.claude/settings.json (back it up first)");
    }
    const statusCmd = String(settings.data.statusLine?.command || "");
    if (statusCmd.includes("statusline.mjs")) {
      const m = /node\s+"?([^"]+statusline\.mjs)"?/.exec(statusCmd);
      const path = m ? expandHome(m[1]) : "";
      if (path && !existsPath(path)) report.warn("statusLine points at a missing statusline.mjs", homeShort(path), "re-run the installer or fix .statusLine.command in ~/.claude/settings.json");
      else if (path && !path.startsWith(PLUGIN_ROOT) && !(installPath && path.startsWith(resolvePath(installPath)))) {
        report.warn("statusline runs from a different plugin copy than the hooks", homeShort(path), "point .statusLine.command at the registered install, or accept that the two may differ in version");
      } else report.ok(`statusline registered (${homeShort(path)})`);
    } else {
      report.info("statusline not registered (optional; OPENVIKING_STATUSLINE=off also silences it)");
    }
    const envBlock = settings.data.env || {};
    const envKeys = Object.keys(envBlock).filter((k) => k.startsWith("OPENVIKING_"));
    if (envKeys.length) report.info(`~/.claude/settings.json env block sets ${envKeys.join(", ")}`);
  } else {
    report.warn("~/.claude/settings.json not found", "no enabledPlugins entry can exist without it");
  }

  // Legacy MCP registration + rc blocks
  const claudeJson = tryJson(join(homedir(), ".claude.json"));
  const userMcp = claudeJson?.mcpServers?.openviking;
  if (userMcp) {
    report.warn("a user-scope MCP server named 'openviking' is registered besides the plugin's", JSON.stringify(userMcp).slice(0, 160),
      "claude mcp remove openviking -s user (the plugin ships its own MCP server)");
  }
  const rc = scanRcFiles(RC_MARKERS);
  const blocks = rc.filter((h) => h.kind === "block");
  if (blocks.length) {
    report.warn("shell rc files still carry a legacy openviking wrapper block", blocks.map((h) => `${h.file}: ${h.detail}`).join("\n"),
      "delete the block between the >>> and <<< markers; it exports stale OPENVIKING_* values that override ovcli.conf");
  }
  const CONNECTION_VARS = /^OPENVIKING_(URL|BASE_URL|API_KEY|BEARER_TOKEN|ACCOUNT|USER|MEMORY_ENABLED|CONFIG_FILE|CLI_CONFIG_FILE|HOME)$/;
  for (const h of rc.filter((h) => h.kind === "export")) {
    const conn = h.vars.filter((v) => CONNECTION_VARS.test(v));
    if (conn.length) report.warn(`${h.file} exports ${conn.join(", ")}`, "shell exports override ovcli.conf in every session started from that shell", "remove the export or keep ovcli.conf in sync with it");
    else report.info(`${h.file} exports ${h.detail}`);
  }

  if (claudeOnPath) {
    const list = runCommand("claude", ["plugin", "list", "--json"], { timeoutMs: 30000 });
    if (list.ok) {
      let rows = [];
      try { rows = JSON.parse(list.stdout); } catch { rows = []; }
      const mine = rows.filter((r) => r?.id === PLUGIN_ID);
      const others = rows.filter((r) => r?.id !== PLUGIN_ID && /openviking|claude-code-memory-plugin/.test(String(r?.id)));
      if (!mine.length) report.fail(`claude plugin list does not show ${PLUGIN_ID}`, others.length ? `shows: ${others.map((r) => r.id).join(", ")}` : "", `claude plugin install ${PLUGIN_ID}`);
      else {
        const row = mine[0];
        if (row.enabled === false) report.fail(`claude plugin list: ${PLUGIN_ID} is disabled`, "", `claude plugin enable ${PLUGIN_ID}`);
        else report.ok(`claude plugin list: ${PLUGIN_ID} ${row.version || ""} enabled`);
      }
      if (others.length) report.warn("claude plugin list shows extra openviking plugins", others.map((r) => `${r.id} (${r.enabled ? "enabled" : "disabled"})`).join(", "));
    } else {
      report.info(`claude plugin list --json failed (${list.error || list.stderr.split("\n")[0]})`);
    }
  }
  report.info("MCP wiring: `claude mcp list` shows the plugin server as plugin:openviking-memory:openviking (slow; run it when MCP tools are missing)");
}

function credentialSources(cliConf, ovConf) {
  const env = process.env;
  const cliShort = homeShort(cliConf.path);
  const ovShort = homeShort(ovConf.path);
  const cli = cliConf.ok ? cliConf.data : {};
  const ov = ovConf.ok ? ovConf.data : {};
  const cc = ov.claude_code || {};
  const server = ov.server || {};
  const url = (env.OPENVIKING_URL || env.OPENVIKING_BASE_URL) ? "env" : cli.url ? cliShort : server.url ? ovShort : (server.host || server.port) ? `${ovShort} server.host/port` : "default (http://127.0.0.1:1933)";
  const apiKey = env.OPENVIKING_BEARER_TOKEN ? "env OPENVIKING_BEARER_TOKEN" : env.OPENVIKING_API_KEY ? "env OPENVIKING_API_KEY" : cli.api_key ? cliShort : cc.apiKey ? `${ovShort} claude_code.apiKey` : server.root_api_key ? `${ovShort} server.root_api_key` : "(none)";
  const account = env.OPENVIKING_ACCOUNT ? "env" : cli.account ? cliShort : cc.accountId ? `${ovShort} claude_code.accountId` : "(unset)";
  const user = env.OPENVIKING_USER ? "env" : cli.user ? cliShort : cc.userId ? `${ovShort} claude_code.userId` : "(unset)";
  return { url, apiKey, account, user };
}

function checkConfig(report, cfg) {
  report.section("Configuration");
  const cliPath = expandHome(process.env.OPENVIKING_CLI_CONFIG_FILE || join(homedir(), ".openviking", "ovcli.conf"));
  const ovPath = expandHome(process.env.OPENVIKING_CONFIG_FILE || join(homedir(), ".openviking", "ov.conf"));
  const cliConf = inspectJsonFile(cliPath);
  const ovConf = inspectJsonFile(ovPath);

  for (const [label, conf] of [["ovcli.conf", cliConf], ["ov.conf", ovConf]]) {
    if (!conf.exists) {
      report.info(`${label}: ${homeShort(conf.path)} not present`);
    } else if (!conf.ok) {
      report.fail(`${label} cannot be parsed — the plugin treats it as absent`, `${homeShort(conf.path)}: ${conf.error}`, "fix the JSON (a trailing comma or comment is enough to break it)");
    } else {
      report.ok(`${label}: ${homeShort(conf.path)} (mode ${conf.mode}, ${fmtBytes(conf.size)})`);
      if (label === "ovcli.conf") {
        if (conf.mode !== "600" && conf.mode !== "400") report.warn("ovcli.conf is not private", `mode ${conf.mode}; it holds the api key`, `chmod 600 ${homeShort(conf.path)}`);
        const unknown = unknownOvcliKeys(conf.data);
        if (unknown.length) report.warn("ovcli.conf has keys nobody reads", unknown.join(", "), "typos such as apiKey/base_url/token are silently ignored — use url, api_key, account, user");
        // `plugin` is on the allowlist above, so until now nothing inside it
        // was ever checked and a misspelled knob just sat there doing nothing.
        for (const { key, suggestion } of unknownPluginKeys(conf.data.plugin)) {
          report.warn(`ovcli.conf ${key} is not a knob any plugin reads`, "", suggestion ? `did you mean ${suggestion}?` : "remove it, or check the plugin README for the knob you meant");
        }
        if (conf.data.extra_headers && Object.keys(conf.data.extra_headers).some((h) => /^x-api-key$/i.test(h))) {
          report.warn("ovcli.conf extra_headers sets X-API-Key", "the server prefers X-API-Key over Authorization: Bearer, so it shadows api_key");
        }
      }
      if (label === "ov.conf" && conf.data.claude_code) report.info("ov.conf has a legacy claude_code block (still honoured; prefer ovcli.conf plugin.claude_code or env vars)");
    }
  }

  // Enable verdict
  const enabled = isPluginEnabled();
  const envEnabled = envBoolValue("OPENVIKING_MEMORY_ENABLED");
  let reason;
  if (envEnabled === false) reason = "OPENVIKING_MEMORY_ENABLED is set to off";
  else if (envEnabled === true) reason = "OPENVIKING_MEMORY_ENABLED=1";
  else if (ovConf.ok && ovConf.data.claude_code?.enabled === false) reason = "ov.conf claude_code.enabled = false";
  else if (ovConf.ok || cliConf.ok) reason = `${ovConf.ok ? "ov.conf" : "ovcli.conf"} exists and parses`;
  else reason = "neither ovcli.conf nor ov.conf parses";
  if (enabled) report.ok(`plugin enabled (${reason})`);
  else report.fail(`plugin disabled — every hook exits immediately (${reason})`, "", envEnabled === false ? "unset OPENVIKING_MEMORY_ENABLED" : "create ~/.openviking/ovcli.conf with url + api_key, or set OPENVIKING_MEMORY_ENABLED=1 plus OPENVIKING_URL/OPENVIKING_API_KEY");

  // Resolved values + sources
  const src = credentialSources(cliConf, ovConf);
  report.info(`url      ${cfg.baseUrl}  ← ${src.url}`);
  for (const p of lintBaseUrl(cfg.baseUrl)) report[p.level](p.message, "", p.fix);
  if (src.url.startsWith("default") && !isLoopbackUrl(cfg.baseUrl)) report.info("url fell back to the built-in default");
  else if (src.url.startsWith("default")) report.warn("no url configured — using the built-in default http://127.0.0.1:1933", "only right when the server runs on this machine", "set url in ovcli.conf or OPENVIKING_URL");

  const keyInfo = describeApiKey(cfg.apiKey);
  report.info(`api_key  ${keyInfo.display}  ← ${src.apiKey}`);
  for (const p of keyInfo.problems) report.warn(`api key ${p}`, "", "re-copy the key exactly as issued");
  if (src.apiKey.includes("root_api_key")) {
    report.warn("api key falls back to ov.conf server.root_api_key", "the root key is refused on tenant data APIs and /mcp in api_key mode; against a remote server it is simply the wrong key",
      "put a user/admin key in ovcli.conf api_key (or OPENVIKING_API_KEY)");
  }
  if (!keyInfo.present && !isLoopbackUrl(cfg.baseUrl)) report.warn("no api key configured for a non-local server", "", "set api_key in ovcli.conf or OPENVIKING_API_KEY");
  report.info(`account  ${cfg.accountId || "(unset)"}  ← ${src.account}`);
  report.info(`user     ${cfg.userId || "(unset)"}  ← ${src.user}`);
  if (keyInfo.format === "v2") {
    if (cfg.accountId && keyInfo.account && cfg.accountId !== keyInfo.account) report.warn(`configured account '${cfg.accountId}' differs from the key's account '${keyInfo.account}'`, "in api_key mode the key wins");
    if (cfg.userId && keyInfo.user && cfg.userId !== keyInfo.user) report.warn(`configured user '${cfg.userId}' differs from the key's user '${keyInfo.user}'`, "in api_key mode the key wins");
  }
  const peer = resolveEffectivePeerId({ cfg, cwd: process.cwd() });
  report.info(`peer     ${peer.peerId || "(none)"}  ← ${peer.source} (${peer.origin})`);
  if (peer.origin === "unresolved") {
    report.info(
      "no peer is sent: this directory is in no git repository, so its memories go to your user-level space",
      `to give it a memory of its own, create .openviking/config.json here with ${WORKSPACE_PEER_HINT}`,
    );
  } else if (peer.source === "none") {
    report.warn(
      "no peer is sent, so recall defaults to every memory under this user",
      "sending a peer narrows the search to this workspace",
      'unset OPENVIKING_WORKSPACE_PEER, or set peer.source to "git"',
    );
  }
  if (peer.legacyPeerId) {
    report.info(
      `previous peer  ${peer.legacyPeerId}`,
      cfg.recallPeerScope === "actor"
        ? "recall asks it separately, because peer_scope actor turns off the server's cross-peer sweep"
        : "already covered by the server's cross-peer sweep under peer_scope all",
    );
  }
  for (const p of lintPeerScopeDowngrade()) report[p.level](p.message, p.detail, p.fix);
  report.info(`timeouts ${cfg.timeoutMs}ms request, ${cfg.captureTimeoutMs}ms capture; recall limit ${cfg.recallLimit}, threshold ${cfg.scoreThreshold}`);

  const toggles = [`auto-inject ${cfg.noAutoInject ? "OFF" : "on"}`, `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`, `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`, `recall compress ${cfg.recallRewrite}`, `write path ${cfg.writePathAsync ? "async" : "sync"}`];
  report.info(`toggles  ${toggles.join(", ")}`);
  if (!cfg.autoRecall || !cfg.autoCapture || cfg.noAutoInject) report.warn("one or more injection paths are switched off", toggles.join(", "), "check OPENVIKING_AUTO_RECALL / OPENVIKING_AUTO_CAPTURE / OPENVIKING_NO_AUTO_INJECT and ovcli.conf plugin.claude_code");
  if (cfg.bypassSession) report.warn("OPENVIKING_BYPASS_SESSION is on — every hook skips the server", "", "unset it");
  if (cfg.bypassSessionPatterns?.length) {
    const hit = isBypassed(cfg, { cwd: process.cwd() });
    report[hit ? "warn" : "info"](`bypass patterns: ${cfg.bypassSessionPatterns.join(", ")}${hit ? " — MATCH the current cwd" : ""}`, hit ? "recall/capture are skipped in this directory" : "", hit ? "narrow OPENVIKING_BYPASS_SESSION_PATTERNS" : "");
  }
  report.info(`debug log ${cfg.debug ? "on" : "off"} → ${homeShort(cfg.debugLogPath)}${cfg.debug ? "" : " (set OPENVIKING_DEBUG=1 in Claude Code's environment to record hook errors)"}`);

  // Environment sweep
  const env = collectEnv();
  if (env.openviking.length) {
    report.info("OPENVIKING_* in this environment", env.openviking.map((e) => `${e.name}=${e.value}`).join("\n"));
    if (env.openviking.some((e) => e.name === "OPENVIKING_MCP_URL")) report.warn("OPENVIKING_MCP_URL has no effect on this plugin", "the proxy always targets <url>/mcp", "unset it and fix url instead");
    if (env.openviking.some((e) => ["OPENVIKING_URL", "OPENVIKING_BASE_URL", "OPENVIKING_API_KEY", "OPENVIKING_BEARER_TOKEN"].includes(e.name)) && cliConf.ok && (cliConf.data.url || cliConf.data.api_key)) {
      report.info("env vars override the url/api_key in ovcli.conf — edits to the file do not take effect while they are set");
    }
  } else {
    report.info("no OPENVIKING_* environment variables set");
  }
  if (env.proxy.length) {
    report.warn(`proxy variables set: ${env.proxy.map((e) => e.name).join(", ")}`, "Node's fetch ignores HTTP(S)_PROXY unless NODE_USE_ENV_PROXY=1 (Node 24+); curl honours them, so curl may succeed while hooks fail",
      isLoopbackUrl(cfg.baseUrl) ? "harmless for a local server" : "set NODE_USE_ENV_PROXY=1 (or reach the server without the proxy) in the environment that launches Claude Code");
  }
  if (env.node.length) report.info(`node TLS/proxy env: ${env.node.map((e) => `${e.name}=${e.value}`).join(", ")}`);
  if (/^https:/i.test(cfg.baseUrl) && env.node.some((e) => e.name === "NODE_TLS_REJECT_UNAUTHORIZED" && e.value === "0")) report.warn("NODE_TLS_REJECT_UNAUTHORIZED=0 disables certificate checks", "", "prefer NODE_EXTRA_CA_CERTS=<ca.pem>");

  return { keyInfo, cliConf, ovConf, peer };
}

async function checkConnection(report, cfg, { keyInfo, peer }, opts) {
  report.section("Connection");
  if (opts.offline) {
    report.info("skipped (--offline)");
    return null;
  }
  const conn = { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey, account: cfg.accountId, user: cfg.userId, peerId: peer.peerId, userAgent: cfg.userAgent };
  const probes = await probeOpenViking(conn, { timeoutMs: opts.timeoutMs });
  const summary = assessProbes(report, probes, conn, keyInfo);
  return { probes, summary };
}

function checkActivity(report, cfg, connection) {
  report.section("Recent activity");
  const inject = fileInfo(join(homedir(), ".openviking", "last_inject.md"));
  if (inject.exists) report.info(`last session-start injection ${fmtAge(inject.mtimeMs)}, ${fmtBytes(inject.size)} (~/.openviking/last_inject.md)`);
  else report.info("no session-start injection recorded yet");

  const state = readStateFiles(STATE_DIR, STATE_FILES);
  const recall = state["last-recall.json"];
  if (recall.exists && recall.data) {
    const d = recall.data;
    const line = `last auto-recall ${fmtAge(d.ts)} — ${d.count ?? 0} items, ${d.latency_ms ?? "?"}ms, reason=${d.reason || "?"} (server ${d.server_url || "?"})`;
    if (d.reason === "offline") report.warn(line, "the hook could not reach the server at that time");
    else if (d.reason === "bypass" || d.reason === "disabled") report.warn(line, "recall was switched off for that session");
    else report.info(line);
    if (d.server_url && d.server_url !== cfg.baseUrl) report.warn("last recall talked to a different server than the current config", `${d.server_url} vs ${cfg.baseUrl}`, "config changed since; restart Claude Code so the MCP proxy follows");
    if (typeof d.latency_ms === "number" && d.latency_ms > 45000) report.warn(`recall latency ${d.latency_ms}ms is close to the 60s hook budget`, "", "set OPENVIKING_RECALL_QUERY_EXPANSION=off and OPENVIKING_RECALL_COMPRESS=off, or raise the timeout");
  } else {
    report.info("no auto-recall recorded yet (UserPromptSubmit hook has not run, or state dir differs)");
  }
  const capture = state["last-capture.json"];
  if (capture.exists && capture.data) {
    const d = capture.data;
    const line = `last auto-capture ${fmtAge(d.ts)} — captured ${d.turns_captured ?? 0}, queued ${d.turns_queued ?? 0}, failed ${d.turns_failed ?? 0}, pending ${d.pending_tokens ?? 0}/${d.commit_threshold ?? "?"} tokens, ${d.commit_count ?? 0} commits (session ${d.ov_session_id || "?"})`;
    if ((d.turns_failed ?? 0) > 0) report.warn(line, "turns_failed > 0 means the server rejected writes with a non-retryable status (401/403/404) and those turns were dropped", "fix credentials, then set OPENVIKING_WRITE_PATH_ASYNC=0 temporarily to see the error on stderr");
    else if ((d.turns_queued ?? 0) > 0) report.warn(line, "turns are waiting in the offline queue; they replay at the next session start once /health passes");
    else report.info(line);
  } else {
    report.info("no auto-capture recorded yet (Stop hook has not run)");
  }
  const probe = state["server-probe.json"];
  if (probe.exists && probe.data && probe.data.healthy === false) report.info(`statusline probe last saw the server unhealthy ${fmtAge(probe.data.ts)} (${probe.data.error || "?"})`);
  const face = state["context-face.json"];
  if (face.exists && face.data?.legacyUntil > Date.now()) report.warn("recall is pinned to the legacy /search/recall endpoint", `context-face.json legacyUntil ${new Date(face.data.legacyUntil).toISOString()} — set after one 4xx that mentioned 'mode'`, `rm ${homeShort(face.path)} to re-probe the context endpoint`);
  const hostCli = state["host-cli-probe.json"];
  if (hostCli.exists && hostCli.data && hostCli.data.available === false && cfg.recallRewrite !== "off" && cfg.recallRewrite !== "server") report.info(`local compressor probe says '${hostCli.data.command}' is unavailable (cached 7 days) — rm ${homeShort(hostCli.path)} to re-check`);

  const pendingDir = process.env.OPENVIKING_PENDING_DIR || join(homedir(), ".openviking", "pending");
  const pending = countDirEntries(pendingDir, (n) => n.endsWith(".json") || n.endsWith(".processing"));
  if (pending) report.warn(`${pending} capture payload(s) waiting in ${homeShort(pendingDir)}`, "retryable failures are replayed at the next session start after /health passes", connection?.summary?.reachable ? "start a new Claude Code session to drain the queue" : "bring the server back first");
  else if (pending === 0) report.ok("offline queue empty");

  const log = scanDebugLog(cfg.debugLogPath);
  if (!log.exists) {
    report.info(`no hook log at ${homeShort(cfg.debugLogPath)}${cfg.debug ? " — debug is on but no hook has run since; if a session ran, hooks are not being spawned (node/PATH/plugin registration)" : ""}`);
  } else {
    report.info(`hook log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}, hooks seen: ${log.hooks.join(", ") || "(none)"}`);
    if (log.proxyStart?.data?.mcpUrl) {
      const want = `${cfg.baseUrl.replace(/\/+$/, "")}/mcp`;
      if (log.proxyStart.data.mcpUrl !== want) report.warn(`MCP proxy last started against ${log.proxyStart.data.mcpUrl} (${log.proxyStart.ts})`, `current config resolves to ${want}; a running proxy only re-reads credentials after a 401/403, never a new url`, "if that proxy is still running, restart Claude Code (or /mcp → reconnect); if the line is old, ignore it");
      else report.info(`MCP proxy last started against ${log.proxyStart.data.mcpUrl} (${log.proxyStart.ts})`);
    }
    const notable = log.recentErrors.filter((e) => !(e.hook === "subagent-stop" && e.stage === "transcript_read"));
    if (notable.length) report.warn(`hook errors in the last day of logging (${notable.length} shown)`, notable.map((e) => `${e.ts} ${e.hook}/${e.stage}: ${e.message}`).join("\n"));
    else if (log.recentErrors.length) report.info("only subagent-stop transcript_read ENOENT errors in the log (harmless: that subagent's transcript was already gone)");
  }
}

// ---------------------------------------------------------------------------

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const report = createReport({ color: opts.color });
  const envInfo = checkEnvironment(report);
  checkInstall(report, envInfo);
  const cfg = loadConfig();
  const configInfo = checkConfig(report, cfg);
  const workspace = checkWorkspace(report);
  const connection = await checkConnection(report, cfg, configInfo, opts);
  const serverHealth = await checkServerHealth(report, { baseUrl: cfg.baseUrl, ovConf: configInfo.ovConf, health: connection?.probes?.health, offline: opts.offline, timeoutMs: opts.timeoutMs });
  checkActivity(report, cfg, connection);

  if (opts.json) {
    const out = {
      harness: "claude-code",
      pluginRoot: PLUGIN_ROOT,
      generatedAt: new Date().toISOString(),
      resolved: {
        baseUrl: cfg.baseUrl,
        apiKey: configInfo.keyInfo.display,
        apiKeyFormat: configInfo.keyInfo.format,
        account: cfg.accountId,
        user: cfg.userId,
        peerId: configInfo.peer.peerId,
      },
      workspace,
      server: connection?.summary || null,
      serverHealth,
      ...report.toJSON(),
    };
    console.log(JSON.stringify(out, null, 2));
  } else {
    console.log(report.render());
  }
  process.exitCode = report.exitCode();
}

main().catch((err) => {
  console.error("ov-memory-doctor failed:", err?.stack || err?.message || err);
  process.exit(2);
});
