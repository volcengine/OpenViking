#!/usr/bin/env node

/**
 * Client-side diagnostics for the OpenViking Codex memory plugin.
 *
 * Covers the plugin install (marketplace, config.toml enablement, hook trust
 * state, MCP wiring), the client config (which file won, is the JSON valid,
 * what the key claims) and the connection to the server (reachability, auth,
 * tenant-data access, /mcp), plus the runtime evidence the hooks leave in
 * ~/.openviking/codex-plugin-state. When the server runs on this machine
 * (loopback url) it also checks
 * the port, plugin-only keys in ov.conf and `GET /ready`. Provider-level validation stays with `openviking-server doctor`.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 */

import { readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig } from "./config.mjs";
import { getStateDir } from "./session-state.mjs";
import {
  credentialSources,
  existsPath,
  fmtAge,
  fmtBytes,
  homeShort,
  inspectConfigFiles,
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

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_ID = "openviking-memory@openviking";
const PLUGIN_NAME = "openviking-memory";
const MARKETPLACE = "openviking";
const LEGACY_MARKETPLACE = "openviking-plugins-local";
const CODEX_DIR = join(homedir(), ".codex");
const CODEX_CONFIG = process.env.CODEX_CONFIG_FILE || join(CODEX_DIR, "config.toml");
const CACHE_DIR = join(CODEX_DIR, "plugins", "cache", MARKETPLACE, PLUGIN_NAME);
const HOOK_EVENTS = ["session_start", "user_prompt_submit", "stop", "session_end", "pre_compact"];
const REQUIRED_PLUGIN_FILES = [".codex-plugin/plugin.json", "hooks/hooks.json", ".mcp.json", "servers/mcp-proxy.mjs", "scripts/config.mjs", "scripts/auto-recall.mjs", "scripts/auto-capture.mjs", "scripts/session-end.mjs", "scripts/ov-session.mjs"];

/**
 * Minimal TOML reader — enough for config.toml's [section] headers (including
 * quoted dotted names) and scalar `key = value` lines. Values keep their raw
 * text except strings (unquoted) and booleans.
 */
function readToml(path) {
  let text;
  try {
    text = readFileSync(path, "utf-8");
  } catch {
    return null;
  }
  const sections = { "": {} };
  let current = sections[""];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const header = /^\[\[?(.+?)\]\]?$/.exec(line);
    if (header) {
      const name = header[1].trim();
      sections[name] = sections[name] || {};
      current = sections[name];
      continue;
    }
    const kv = /^([A-Za-z0-9_."'-]+)\s*=\s*(.*)$/.exec(line);
    if (!kv) continue;
    const key = kv[1].replace(/^["']|["']$/g, "");
    let value = kv[2].trim();
    if (/^"(.*)"$/.test(value)) value = value.slice(1, -1);
    else if (value === "true") value = true;
    else if (value === "false") value = false;
    current[key] = value;
  }
  return sections;
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

export function parseFeaturesList(stdout) {
  const map = new Map();
  if (typeof stdout !== "string") return map;
  for (const line of stdout.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(/\s+/);
    if (parts.length >= 3) {
      const key = parts[0];
      const stateStr = parts[parts.length - 1].toLowerCase();
      const stage = parts.slice(1, -1).join(" ");
      map.set(key, {
        stage,
        enabled: stateStr === "true",
      });
    }
  }
  return map;
}

export function assessHooksFeature(features, cliFeatures = null) {
  const hooks = features?.hooks;
  const pluginHooks = features?.plugin_hooks;

  if (hooks === true) {
    return { status: "ok", message: "[features] hooks = true" };
  }
  if (hooks === false) {
    return {
      status: "fail",
      message: `hooks disabled in [features] (hooks=${hooks ?? "unset"}, plugin_hooks=${pluginHooks ?? "unset"})`,
      detail: "no plugin hook fires when hooks feature is disabled",
      fix: "set hooks = true under [features] in ~/.codex/config.toml",
    };
  }

  if (cliFeatures instanceof Map) {
    const liveHooks = cliFeatures.get("hooks");
    if (liveHooks?.enabled === true) {
      return {
        status: "ok",
        message: `[features] hooks enabled by default (Codex stage: ${liveHooks.stage || "stable"})`,
      };
    }
    if (liveHooks?.enabled === false) {
      return {
        status: "fail",
        message: "hooks feature is disabled in Codex",
        detail: "Codex reports hooks feature effective state is false",
        fix: "set hooks = true under [features] in ~/.codex/config.toml",
      };
    }
  }

  // Legacy config only applies when the modern feature cannot be determined.
  if (pluginHooks === true) {
    return { status: "ok", message: "[features] plugin_hooks = true (legacy; modern Codex uses hooks = true)" };
  }
  if (pluginHooks === false) {
    return {
      status: "fail",
      message: "hooks disabled in [features] (plugin_hooks=false)",
      detail: "legacy plugin hooks are explicitly disabled",
      fix: "set plugin_hooks = true for older Codex, or hooks = true for modern Codex under [features] in ~/.codex/config.toml",
    };
  }

  return {
    status: "info",
    message: "[features] hooks is not set (modern Codex enables hooks by default; older Codex needs plugin_hooks = true)",
    detail: "if hooks do not fire, verify with `codex features list` or add hooks = true",
    fix: "add hooks = true under [features] in ~/.codex/config.toml (or plugin_hooks = true for older Codex)",
  };
}

function checkInstall(report, { cliOnPath }) {
  report.section("Plugin install");
  const manifest = tryJson(join(PLUGIN_ROOT, ".codex-plugin", "plugin.json"));
  const version = manifest?.version || "?";
  const inCache = PLUGIN_ROOT.startsWith(CACHE_DIR);
  report.info(`running from ${homeShort(PLUGIN_ROOT)} (version ${version}, ${inCache ? "plugin cache" : "marketplace checkout / dev directory"})`);
  const missing = REQUIRED_PLUGIN_FILES.filter((rel) => !existsPath(join(PLUGIN_ROOT, rel)));
  if (missing.length) report.fail("plugin files missing", missing.join(", "), `codex plugin remove ${PLUGIN_ID} && codex plugin add ${PLUGIN_ID}`);
  else report.ok("plugin files present (hooks, MCP proxy, scripts)");
  if (manifest && !manifest.skills) report.warn("plugin.json does not declare skills", "Codex only loads skills/ when the manifest has \"skills\": \"./skills/\"; this copy predates that", "update the plugin");

  let cacheVersions = [];
  try {
    cacheVersions = readdirSync(CACHE_DIR).filter((n) => existsPath(join(CACHE_DIR, n, ".codex-plugin", "plugin.json"))).sort();
  } catch { /* no cache */ }
  if (cacheVersions.length) {
    report.info(`plugin cache ${homeShort(CACHE_DIR)}: ${cacheVersions.join(", ")}`);
    const newest = cacheVersions[cacheVersions.length - 1];
    if (!inCache && newest !== version) report.warn(`cached plugin ${newest} differs from this copy (${version})`, "Codex runs hooks from the cache", "re-run the installer or `codex plugin marketplace upgrade openviking` and restart Codex");
  }

  // codex CLI view
  let listed = null;
  if (cliOnPath) {
    const list = runCommand("codex", ["plugin", "list", "--json"], { timeoutMs: 30000 });
    if (list.ok) {
      const rows = tryJsonText(list.stdout)?.installed || [];
      const mine = rows.filter((r) => r?.pluginId === PLUGIN_ID);
      const others = rows.filter((r) => r?.pluginId !== PLUGIN_ID && r?.name === PLUGIN_NAME && r?.installed);
      if (!mine.length) report.fail(`codex plugin list does not show ${PLUGIN_ID}`, others.length ? `found: ${others.map((r) => r.pluginId).join(", ")}` : "",
        "bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness codex");
      else {
        listed = mine[0];
        const path = listed.source?.path || "";
        if (listed.installed === false) report.fail(`${PLUGIN_ID} is known to the marketplace but not installed`, "", `codex plugin add ${PLUGIN_ID}`);
        else if (listed.enabled === false) report.fail(`${PLUGIN_ID} is installed but disabled`, "", `set [plugins."${PLUGIN_ID}"] enabled = true in ${homeShort(CODEX_CONFIG)}`);
        else report.ok(`codex plugin list: ${PLUGIN_ID} ${listed.version || ""} installed, enabled`);
        if (path) {
          report.info(`marketplace copy: ${homeShort(path)}`);
          if (!existsPath(join(path, ".codex-plugin", "plugin.json"))) report.fail("marketplace copy no longer exists on disk", homeShort(path), "re-run the installer");
        }
        if (listed.version && listed.version !== version && inCache) report.info(`marketplace lists ${listed.version}; this cache copy is ${version}`);
      }
      if (others.length) report.warn("more than one copy of openviking-memory is installed", others.map((r) => `${r.pluginId} (${r.enabled ? "enabled" : "disabled"})`).join(", "), "remove the stale one or hooks fire twice");
    } else {
      report.info(`codex plugin list --json failed (${list.error || list.stderr.split("\n")[0]})`);
    }
    const mkt = runCommand("codex", ["plugin", "marketplace", "list", "--json"], { timeoutMs: 30000 });
    if (mkt.ok) {
      const rows = tryJsonText(mkt.stdout)?.marketplaces || [];
      const entry = rows.find((m) => m?.name === MARKETPLACE);
      if (!entry) report.fail(`marketplace '${MARKETPLACE}' is not registered`, `known: ${rows.map((m) => m.name).join(", ") || "(none)"}`, "re-run the installer");
      else {
        const src = entry.marketplaceSource || {};
        report.ok(`marketplace '${MARKETPLACE}' → ${src.sourceType || "?"} ${src.source || ""}`.trim());
        if (entry.root && !existsPath(entry.root)) report.fail("marketplace root is missing on disk", homeShort(entry.root), "re-run the installer");
        if (src.sourceType === "git") report.info("update: codex plugin marketplace upgrade openviking (keeps the pinned ref); the installer re-registers with the current ref");
        else report.info("local directory marketplace: re-run the installer to update");
      }
      if (rows.some((m) => m?.name === LEGACY_MARKETPLACE)) report.warn(`legacy marketplace '${LEGACY_MARKETPLACE}' is still registered`, "", `codex plugin marketplace remove ${LEGACY_MARKETPLACE}`);
    }
  }

  // config.toml
  const toml = readToml(CODEX_CONFIG);
  if (!toml) {
    report.warn(`${homeShort(CODEX_CONFIG)} not found`, "Codex has never been configured on this machine");
  } else {
    let cliFeatures = null;
    if (cliOnPath) {
      const featRes = runCommand("codex", ["features", "list"], { timeoutMs: 5000 });
      if (featRes.ok) cliFeatures = parseFeaturesList(featRes.stdout);
    }

    const hooksAssessment = assessHooksFeature(toml.features, cliFeatures);
    if (hooksAssessment.status === "ok") {
      report.ok(hooksAssessment.message);
    } else if (hooksAssessment.status === "warn") {
      report.warn(hooksAssessment.message, hooksAssessment.detail, hooksAssessment.fix?.replace("~/.codex/config.toml", homeShort(CODEX_CONFIG)));
    } else if (hooksAssessment.status === "info") {
      report.info(hooksAssessment.message);
    } else {
      report.fail(
        hooksAssessment.message,
        hooksAssessment.detail,
        hooksAssessment.fix?.replace("~/.codex/config.toml", homeShort(CODEX_CONFIG)),
      );
    }
    const pluginSection = toml[`plugins."${PLUGIN_ID}"`];
    if (!pluginSection) report.warn(`no [plugins."${PLUGIN_ID}"] section`, "the installer normally writes enabled = true here");
    else if (pluginSection.enabled === false) report.fail(`[plugins."${PLUGIN_ID}"] enabled = false`, "", "set enabled = true");
    else report.ok(`[plugins."${PLUGIN_ID}"] enabled = ${pluginSection.enabled ?? "(unset)"}`);
    const mktSection = toml[`marketplaces.${MARKETPLACE}`];
    if (mktSection?.source) report.info(`[marketplaces.${MARKETPLACE}] ${mktSection.source_type || ""} ${mktSection.source} ref=${mktSection.ref || "?"}`);

    const trusted = [];
    const untrusted = [];
    const disabled = [];
    for (const event of HOOK_EVENTS) {
      const section = toml[`hooks.state."${PLUGIN_ID}:hooks/hooks.json:${event}:0:0"`];
      if (!section) untrusted.push(event);
      else if (section.enabled === false) disabled.push(event);
      else trusted.push(event);
    }
    if (disabled.length) report.fail(`hooks disabled in [hooks.state]: ${disabled.join(", ")}`, "", `remove enabled = false from those [hooks.state] sections in ${homeShort(CODEX_CONFIG)}`);
    if (untrusted.length) report.info(`hooks without a trust record yet: ${untrusted.join(", ")} (Codex records trusted_hash the first time a hook is approved; a changed hooks.json — including a newly added event such as session_end — needs re-approval)`);
    if (trusted.length === HOOK_EVENTS.length) report.ok(`all ${HOOK_EVENTS.length} hooks have trust records in config.toml`);
    const legacyKeys = Object.keys(toml).filter((k) => k.includes(LEGACY_MARKETPLACE));
    if (legacyKeys.length) report.info(`config.toml still has ${legacyKeys.length} section(s) for the legacy id ${LEGACY_MARKETPLACE} (harmless)`);
  }

  // Legacy artifacts
  const rc = scanRcFiles([]);
  const CONNECTION_VARS = /^OPENVIKING_(URL|BASE_URL|API_KEY|BEARER_TOKEN|ACCOUNT|USER|CONFIG_FILE|CLI_CONFIG_FILE|CREDENTIAL_SOURCE|HOME)$/;
  for (const h of rc.filter((h) => h.kind === "export")) {
    const conn = h.vars.filter((v) => CONNECTION_VARS.test(v));
    if (conn.length) report.warn(`${h.file} exports ${conn.join(", ")}`, "shell exports override ovcli.conf in every session started from that shell", "remove the export or keep ovcli.conf in sync with it");
    else report.info(`${h.file} exports ${h.detail}`);
  }
  if (existsPath(join(homedir(), ".openviking", "codex-memory-plugin", "runtime"))) report.info("~/.openviking/codex-memory-plugin/runtime is a leftover from the pre-marketplace installer (safe to delete)");
  return { listed };
}

function tryJsonText(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function checkConfig(report, cfg, host) {
  report.section("Configuration");
  const { cliConf, ovConf } = inspectConfigFiles(report, host);
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    report.fail("no usable config — the plugin falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key (chmod 600)");
  }

  const modeEnv = process.env.OPENVIKING_CREDENTIAL_SOURCE || process.env.OPENVIKING_CREDENTIALS_SOURCE;
  const modeText = cfg.credentialSource === "ovcli" ? "ovcli.conf only (env and ov.conf ignored)" : cfg.credentialSource === "env" ? "environment variables win" : "fell through to ov.conf / defaults";
  report.info(`credential source: ${cfg.credentialSource} — ${modeText}${modeEnv ? ` (OPENVIKING_CREDENTIAL_SOURCE=${modeEnv})` : ""}`);
  if (modeEnv && !/^(env|environment|cli|ovcli|file|config|auto)$/i.test(modeEnv)) report.warn(`OPENVIKING_CREDENTIAL_SOURCE=${modeEnv} is not a recognised value`, "valid: env, cli (ovcli/file/config), auto", "fix or unset it");

  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.account, user: cfg.user });
  report.info(`auth mode ${cfg.authMode} (identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"}; trusted is implied when account/user are set)`);
  const peer = reportPeer(report, cfg);
  reportTimeouts(report, cfg, host);

  const toggles = [`auto-inject ${cfg.noAutoInject ? "OFF" : "on"}`, `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`, `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`, `commit on compact ${cfg.autoCommitOnCompact ? "on" : "OFF"}`, `recall compress ${cfg.recallCompress ? "on" : "off"}`, `write path ${cfg.writePathAsync ? "async" : "sync"}`];
  reportToggles(report, cfg, host, toggles);

  sweepEnv(report, cfg, host, (r, env) => {
    if (env.openviking.some((e) => e.name === "OPENVIKING_MEMORY_ENABLED")) r.warn("OPENVIKING_MEMORY_ENABLED has no effect on the Codex plugin", "disable it with OPENVIKING_AUTO_RECALL=0 / OPENVIKING_AUTO_CAPTURE=0, or codex plugin remove", "");
    if (cfg.credentialSource === "env") r.info("credential env vars override ovcli.conf — edits to the file (and `ov config switch`) do not take effect while they are set");
  });

  return { keyInfo, peer, ovConf };
}

function checkActivity(report, cfg, connection) {
  report.section("Recent activity");
  const stateDir = getStateDir();
  let states = [];
  try {
    states = readdirSync(stateDir).filter((n) => n.endsWith(".json") && n !== "recall-compressor-profile.json").map((n) => {
      const path = join(stateDir, n);
      const st = statSync(path);
      return { name: n, mtimeMs: st.mtimeMs, data: tryJson(path) };
    }).sort((a, b) => b.mtimeMs - a.mtimeMs);
  } catch {
    report.info(`no session state dir at ${homeShort(stateDir)} yet — no Codex hook has run (or OPENVIKING_CODEX_STATE_DIR points elsewhere)`);
  }
  if (states.length) {
    const newest = states[0];
    const d = newest.data || {};
    report.info(`${states.length} session state file(s) in ${homeShort(stateDir)}; newest ${fmtAge(newest.mtimeMs)}: ${d.ovSessionId || "(committed)"} captured ${d.capturedTurnCount ?? "?"} turns`);
    if (states.length > 3 && states.every((s) => (s.data?.capturedTurnCount ?? 0) === 0)) report.warn("no session has ever captured a turn", "the Stop hook runs but never appends messages", "check the Connection section; enable OPENVIKING_DEBUG=1 and read the hook log");
    const idleTtl = Number(process.env.OPENVIKING_CODEX_IDLE_TTL_MS) || 30 * 60 * 1000;
    // Markers are `<id>.ended.<ts>`; the bare `<id>.ended` is a pre-0.8.1 leftover.
    const ended = new Set(
      readdirSync(stateDir)
        .map((n) => /^(.*)\.ended(?:\.\d+)?$/.exec(n)?.[1])
        .filter(Boolean),
    );
    const orphans = states.filter((s) => s.data?.ovSessionId
      && (ended.has(s.name.slice(0, -5)) || Date.now() - (s.data.lastUpdatedAt || s.mtimeMs) > idleTtl));
    if (orphans.length > 10) report.warn(`${orphans.length} sessions still uncommitted`, "SessionEnd commits a thread when it exits; the SessionStart sweep retries ended and idle ones, so a growing pile usually means commits are failing", "check the Connection section, then start a new Codex session to trigger the sweep");
    else if (orphans.length) report.info(`${orphans.length} session(s) waiting for the SessionStart sweep`);
  }
  const profile = tryJson(join(stateDir, "recall-compressor-profile.json"));
  if (profile?.profile) {
    const p = profile.profile;
    if (p.enabled === false && p.source === "runtime_failed") report.info(`local recall compressor disabled after a runtime failure (${p.failedModel || "?"}); re-detected at the next SessionStart`);
    else report.info(`recall compressor: ${p.enabled ? `${p.model || "?"} (${p.source || "?"})` : `off (${p.source || "?"})`}`);
  }

  const log = scanDebugLog(cfg.debugLogPath);
  if (!log.exists) {
    report.info(`no hook log at ${homeShort(cfg.debugLogPath)}${cfg.debug ? " — debug is on but no hook has run since; if a Codex turn ran, hooks are not being spawned (hooks, trust, node)" : ""}`);
  } else {
    report.info(`hook log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}, hooks seen: ${log.hooks.join(", ") || "(none)"}`);
    if (log.proxyStart?.data?.mcpUrl) {
      const want = `${cfg.baseUrl.replace(/\/+$/, "")}/mcp`;
      if (log.proxyStart.data.mcpUrl !== want) report.warn(`MCP proxy last started against ${log.proxyStart.data.mcpUrl} (${log.proxyStart.ts})`, `current config resolves to ${want}; a running proxy only re-reads credentials after a 401/403, never a new url`, "if that proxy is still running, restart Codex; if the line is old, ignore it");
      else report.info(`MCP proxy last started against ${log.proxyStart.data.mcpUrl} (${log.proxyStart.ts})`);
    }
    if (log.recentErrors.length) report.warn(`hook errors in the last day of logging (${log.recentErrors.length} shown)`, log.recentErrors.map((e) => `${e.ts} ${e.hook}/${e.stage}: ${e.message}`).join("\n"));
  }
  const ccLog = join(homedir(), ".openviking", "logs", "cc-hooks.log");
  if (existsPath(ccLog) && !log.exists) report.info("~/.openviking/logs/cc-hooks.log belongs to the Claude Code plugin, not Codex");
  if (connection?.summary?.reachable === false) report.info("state files are kept when commits fail, so they replay once the server is back");
}

// ---------------------------------------------------------------------------

const HOST = {
  harness: "codex",
  pluginRoot: PLUGIN_ROOT,
  cliName: "codex",
  launcherHint: "Codex",
  // Recall has a timeout of its own here, so it is the knob the prompt hook spends.
  timeoutBudgets: { UserPromptSubmit: "recallTimeoutMs", Stop: "captureTimeoutMs" },
  loadConfig,
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ credentialSource: cfg.credentialSource, authMode: cfg.authMode }),
  onSummary(report, summary, cfg) {
    if (summary.authMode && summary.authMode !== "dev" && summary.authMode !== cfg.authMode) {
      report.warn(`plugin auth mode '${cfg.authMode}' differs from the server's '${summary.authMode}'`, cfg.authMode === "trusted" ? "identity headers are sent but the server ignores them in api_key mode" : "the server expects X-OpenViking-Account/User headers", "set account/user in ovcli.conf for trusted servers, or remove them (or set OPENVIKING_AUTH_MODE) for api_key servers");
    }
  },
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
