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

import { realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { STATE_DIR } from "./lib/state.mjs";
import {
  countDirEntries,
  existsPath,
  expandHome,
  fileInfo,
  fmtAge,
  fmtBytes,
  credentialSources,
  homeShort,
  inspectConfigFiles,
  inspectJsonFile,
  readStateFiles,
  reportCredentials,
  reportPeer,
  reportTimeouts,
  reportToggles,
  runCommand,
  runDoctor,
  scanDebugLog,
  scanRcFiles,
  sweepEnv,
  tryJson,
} from "./shared/doctor-core.mjs";
import { isBypassed } from "./shared/session-model.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_ID = "openviking-memory@openviking";
const PLUGIN_NAME = "openviking-memory";
const MARKETPLACE = "openviking";
const LEGACY_MARKETPLACE = "openviking-plugins-local";
const CLAUDE_DIR = join(homedir(), ".claude");
const STATE_FILES = ["last-recall.json", "last-capture.json", "last-session-event.json", "daily-stats.json", "server-probe.json", "host-cli-probe.json", "context-face.json"];
const REQUIRED_PLUGIN_FILES = [".claude-plugin/plugin.json", "hooks/hooks.json", ".mcp.json", "servers/mcp-proxy.mjs", "scripts/config.mjs", "scripts/auto-recall.mjs", "scripts/auto-capture.mjs"];

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

function checkInstall(report, { cliOnPath }) {
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
  const rc = scanRcFiles([]);
  const CONNECTION_VARS = /^OPENVIKING_(URL|BASE_URL|API_KEY|BEARER_TOKEN|ACCOUNT|USER|MEMORY_ENABLED|CONFIG_FILE|CLI_CONFIG_FILE|HOME)$/;
  for (const h of rc.filter((h) => h.kind === "export")) {
    const conn = h.vars.filter((v) => CONNECTION_VARS.test(v));
    if (conn.length) report.warn(`${h.file} exports ${conn.join(", ")}`, "shell exports override ovcli.conf in every session started from that shell", "remove the export or keep ovcli.conf in sync with it");
    else report.info(`${h.file} exports ${h.detail}`);
  }

  if (cliOnPath) {
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

function checkConfig(report, cfg, host) {
  report.section("Configuration");
  const { cliConf, ovConf } = inspectConfigFiles(report, host);

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
  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.accountId, user: cfg.userId });
  const peer = reportPeer(report, cfg);
  reportTimeouts(report, cfg, host);

  const toggles = [`auto-inject ${cfg.noAutoInject ? "OFF" : "on"}`, `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`, `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`, `recall compress ${cfg.recallRewrite}`, `write path ${cfg.writePathAsync ? "async" : "sync"}`];
  reportToggles(report, cfg, host, toggles);
  if (cfg.bypassSession) report.warn("OPENVIKING_BYPASS_SESSION is on — every hook skips the server", "", "unset it");
  if (cfg.bypassSessionPatterns?.length) {
    const hit = isBypassed(cfg, { cwd: process.cwd() });
    report[hit ? "warn" : "info"](`bypass patterns: ${cfg.bypassSessionPatterns.join(", ")}${hit ? " — MATCH the current cwd" : ""}`, hit ? "recall/capture are skipped in this directory" : "", hit ? "narrow OPENVIKING_BYPASS_SESSION_PATTERNS" : "");
  }

  sweepEnv(report, cfg, host, (r, env) => {
    if (env.openviking.some((e) => e.name === "OPENVIKING_MCP_URL")) r.warn("OPENVIKING_MCP_URL has no effect on this plugin", "the proxy always targets <url>/mcp", "unset it and fix url instead");
    if (env.openviking.some((e) => ["OPENVIKING_URL", "OPENVIKING_BASE_URL", "OPENVIKING_API_KEY", "OPENVIKING_BEARER_TOKEN"].includes(e.name)) && cliConf.ok && (cliConf.data.url || cliConf.data.api_key)) {
      r.info("env vars override the url/api_key in ovcli.conf — edits to the file do not take effect while they are set");
    }
  });

  return { keyInfo, cliConf, ovConf, peer };
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

const HOST = {
  harness: "claude-code",
  pluginRoot: PLUGIN_ROOT,
  cliName: "claude",
  launcherHint: "Claude Code",
  nodePathFix: "put node on PATH for the environment that launches Claude Code, or set PATH in the `env` block of ~/.claude/settings.json",
  // Recall runs on the plain request timeout here; only Codex gives it its own.
  timeoutBudgets: { UserPromptSubmit: "timeoutMs", Stop: "captureTimeoutMs" },
  onCliFound(report, cli) {
    report.ok(`claude ${cli.stdout.split("\n")[0]}`);
    const plugin = runCommand("claude", ["plugin", "--help"], { timeoutMs: 15000 });
    if (!plugin.ok) report.warn("`claude plugin` subcommand unavailable", "this Claude Code build predates the plugin system; only the legacy settings.json hook install works", "upgrade Claude Code to 2.0+");
  },
  loadConfig,
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.accountId, user: cfg.userId }),
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
