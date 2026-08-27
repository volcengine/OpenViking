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

import { readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig } from "./config.mjs";
import { getStateDir } from "./session-state.mjs";
import {
  assessProbes,
  checkServerHealth,
  collectEnv,
  createReport,
  describeApiKey,
  existsPath,
  fmtAge,
  fmtBytes,
  homeShort,
  inspectJsonFile,
  isLoopbackUrl,
  lintBaseUrl,
  parseNodeMajor,
  probeOpenViking,
  runCommand,
  scanDebugLog,
  scanRcFiles,
  unknownOvcliKeys,
  whichCommand,
} from "./shared/doctor-core.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_ID = "openviking-memory@openviking";
const PLUGIN_NAME = "openviking-memory";
const MARKETPLACE = "openviking";
const LEGACY_MARKETPLACE = "openviking-plugins-local";
const CODEX_DIR = join(homedir(), ".codex");
const CODEX_CONFIG = process.env.CODEX_CONFIG_FILE || join(CODEX_DIR, "config.toml");
const CACHE_DIR = join(CODEX_DIR, "plugins", "cache", MARKETPLACE, PLUGIN_NAME);
const HOOK_EVENTS = ["session_start", "user_prompt_submit", "stop", "pre_compact"];
const RC_MARKERS = ["# >>> openviking-codex-plugin >>>", "codex-plugin.rc.sh"];
const REQUIRED_PLUGIN_FILES = [".codex-plugin/plugin.json", "hooks/hooks.json", ".mcp.json", "servers/mcp-proxy.mjs", "scripts/config.mjs", "scripts/auto-recall.mjs", "scripts/auto-capture.mjs"];

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

function tryJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

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

function checkEnvironment(report) {
  report.section("Environment");
  const nodeMajor = parseNodeMajor(process.version);
  if (nodeMajor >= 18) report.ok(`node ${process.version} (${process.execPath})`);
  else report.fail(`node ${process.version} is too old`, "hooks and the MCP proxy need Node.js 18+ (global fetch)", "install Node.js 18 or newer");
  if (!whichCommand("node")) report.warn("`node` is not on PATH for this process", "hooks and .mcp.json invoke the bare command `node`", "put node on PATH for the environment that launches Codex");

  const codex = runCommand("codex", ["--version"], { timeoutMs: 15000 });
  if (codex.ok) report.ok(codex.stdout.split("\n")[0]);
  else report.info(`codex CLI not found on PATH (${codex.error || "?"}) — install checks that need it are skipped`);
  report.info(`platform ${process.platform} ${process.arch}, cwd ${homeShort(process.cwd())}`);
  return { codexOnPath: codex.ok };
}

function checkInstall(report, { codexOnPath }) {
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
  if (codexOnPath) {
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
    const hooksOn = toml.features?.plugin_hooks;
    if (hooksOn === true) report.ok("[features] plugin_hooks = true");
    else report.fail(`[features] plugin_hooks is ${hooksOn === undefined ? "not set" : hooksOn}`, "no plugin hook fires at all", `add plugin_hooks = true under [features] in ${homeShort(CODEX_CONFIG)}`);
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
    if (untrusted.length) report.info(`hooks without a trust record yet: ${untrusted.join(", ")} (Codex records trusted_hash the first time a hook is approved; a changed hooks.json needs re-approval)`);
    if (trusted.length === HOOK_EVENTS.length) report.ok("all four hooks have trust records in config.toml");
    const legacyKeys = Object.keys(toml).filter((k) => k.includes(LEGACY_MARKETPLACE));
    if (legacyKeys.length) report.info(`config.toml still has ${legacyKeys.length} section(s) for the legacy id ${LEGACY_MARKETPLACE} (harmless)`);
  }

  // Legacy artifacts
  const rc = scanRcFiles(RC_MARKERS);
  const blocks = rc.filter((h) => h.kind === "block");
  if (blocks.length) report.warn("shell rc files still source the legacy codex wrapper", blocks.map((h) => `${h.file}: ${h.detail}`).join("\n"), "remove the block; it exports stale OPENVIKING_* values that override ovcli.conf");
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

function credentialSources(cfg, cliConf, ovConf) {
  const env = process.env;
  const cliShort = homeShort(cliConf.path);
  const ovShort = homeShort(ovConf.path);
  const cli = cliConf.ok ? cliConf.data : {};
  const ov = ovConf.ok ? ovConf.data : {};
  const cx = ov.codex || {};
  const server = ov.server || {};
  const cliMode = cfg.credentialSource === "ovcli";
  const envUrl = env.OPENVIKING_URL || env.OPENVIKING_BASE_URL;
  const url = (!cliMode && envUrl) ? "env" : cli.url ? cliShort : server.url ? ovShort : (server.host || server.port) ? `${ovShort} server.host/port` : "default (http://127.0.0.1:1933)";
  const apiKey = cliMode
    ? (cli.api_key ? cliShort : "(none — ovcli.conf mode ignores env and ov.conf)")
    : env.OPENVIKING_BEARER_TOKEN ? "env OPENVIKING_BEARER_TOKEN" : env.OPENVIKING_API_KEY ? "env OPENVIKING_API_KEY" : cli.api_key ? cliShort : cx.apiKey ? `${ovShort} codex.apiKey` : server.root_api_key ? `${ovShort} server.root_api_key` : "(none)";
  const account = (!cliMode && env.OPENVIKING_ACCOUNT) ? "env" : (cli.account || cli.account_id) ? cliShort : (!cliMode && cx.accountId) ? `${ovShort} codex.accountId` : "(unset)";
  const user = (!cliMode && env.OPENVIKING_USER) ? "env" : (cli.user || cli.user_id) ? cliShort : (!cliMode && cx.userId) ? `${ovShort} codex.userId` : "(unset)";
  return { url, apiKey, account, user };
}

function checkConfig(report, cfg) {
  report.section("Configuration");
  const expand = (p) => (p ? resolvePath(p.replace(/^~(?=$|\/)/, homedir())) : p);
  const cliPath = expand(process.env.OPENVIKING_CLI_CONFIG_FILE || join(homedir(), ".openviking", "ovcli.conf"));
  const ovPath = expand(process.env.OPENVIKING_CONFIG_FILE || join(homedir(), ".openviking", "ov.conf"));
  const cliConf = inspectJsonFile(cliPath);
  const ovConf = inspectJsonFile(ovPath);

  for (const [label, conf] of [["ovcli.conf", cliConf], ["ov.conf", ovConf]]) {
    if (!conf.exists) report.info(`${label}: ${homeShort(conf.path)} not present`);
    else if (!conf.ok) report.fail(`${label} cannot be parsed — the plugin treats it as absent`, `${homeShort(conf.path)}: ${conf.error}`, "fix the JSON (a trailing comma or comment is enough to break it)");
    else {
      report.ok(`${label}: ${homeShort(conf.path)} (mode ${conf.mode}, ${fmtBytes(conf.size)})`);
      if (label === "ovcli.conf") {
        if (conf.mode !== "600" && conf.mode !== "400") report.warn("ovcli.conf is not private", `mode ${conf.mode}; it holds the api key`, `chmod 600 ${homeShort(conf.path)}`);
        const unknown = unknownOvcliKeys(conf.data);
        if (unknown.length) report.warn("ovcli.conf has keys nobody reads", unknown.join(", "), "typos such as apiKey/base_url/token are silently ignored — use url, api_key, account, user");
        if (conf.data.plugin?.claude_code && !conf.data.plugin?.codex) report.info("ovcli.conf plugin.claude_code settings do not apply to Codex (use plugin.codex)");
      }
      if (label === "ov.conf" && conf.data.codex) report.info("ov.conf has a legacy codex block (still honoured; prefer ovcli.conf plugin.codex or env vars)");
    }
  }
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    report.fail("no usable config — the plugin falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key (chmod 600)");
  }

  const modeEnv = process.env.OPENVIKING_CREDENTIAL_SOURCE || process.env.OPENVIKING_CREDENTIALS_SOURCE;
  const modeText = cfg.credentialSource === "ovcli" ? "ovcli.conf only (env and ov.conf ignored)" : cfg.credentialSource === "env" ? "environment variables win" : "fell through to ov.conf / defaults";
  report.info(`credential source: ${cfg.credentialSource} — ${modeText}${modeEnv ? ` (OPENVIKING_CREDENTIAL_SOURCE=${modeEnv})` : ""}`);
  if (modeEnv && !/^(env|environment|cli|ovcli|file|config|auto)$/i.test(modeEnv)) report.warn(`OPENVIKING_CREDENTIAL_SOURCE=${modeEnv} is not a recognised value`, "valid: env, cli (ovcli/file/config), auto", "fix or unset it");

  const src = credentialSources(cfg, cliConf, ovConf);
  report.info(`url      ${cfg.baseUrl}  ← ${src.url}`);
  for (const p of lintBaseUrl(cfg.baseUrl)) report[p.level](p.message, "", p.fix);
  if (src.url.startsWith("default")) report.warn("no url configured — using the built-in default http://127.0.0.1:1933", "only right when the server runs on this machine", "set url in ovcli.conf or OPENVIKING_URL");

  const keyInfo = describeApiKey(cfg.apiKey);
  report.info(`api_key  ${keyInfo.display}  ← ${src.apiKey}`);
  for (const p of keyInfo.problems) report.warn(`api key ${p}`, "", "re-copy the key exactly as issued");
  if (src.apiKey.includes("root_api_key")) report.warn("api key falls back to ov.conf server.root_api_key", "the root key is refused on tenant data APIs and /mcp in api_key mode; against a remote server it is simply the wrong key", "put a user/admin key in ovcli.conf api_key (or OPENVIKING_API_KEY)");
  if (!keyInfo.present && !isLoopbackUrl(cfg.baseUrl)) report.warn("no api key configured for a non-local server", "", "set api_key in ovcli.conf or OPENVIKING_API_KEY");
  report.info(`account  ${cfg.account || "(unset)"}  ← ${src.account}`);
  report.info(`user     ${cfg.user || "(unset)"}  ← ${src.user}`);
  if (keyInfo.format === "v2") {
    if (cfg.account && keyInfo.account && cfg.account !== keyInfo.account) report.warn(`configured account '${cfg.account}' differs from the key's account '${keyInfo.account}'`, "in api_key mode the key wins");
    if (cfg.user && keyInfo.user && cfg.user !== keyInfo.user) report.warn(`configured user '${cfg.user}' differs from the key's user '${keyInfo.user}'`, "in api_key mode the key wins");
  }
  report.info(`auth mode ${cfg.authMode} (identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"}; trusted is implied when account/user are set)`);
  const peer = resolveEffectivePeerId({ cfg, cwd: process.cwd() });
  report.info(`peer     ${peer.peerId || "(none)"}  ← ${peer.source}${peer.source === "workspace" ? " (derived from cwd; changes when the directory moves)" : ""}`);

  report.info(`timeouts ${cfg.timeoutMs}ms request, ${cfg.recallTimeoutMs}ms recall, ${cfg.captureTimeoutMs}ms capture; recall limit ${cfg.recallLimit}, threshold ${cfg.scoreThreshold}`);
  const hooks = tryJson(join(PLUGIN_ROOT, "hooks", "hooks.json"))?.hooks || {};
  const budget = (event) => Number(hooks[event]?.[0]?.hooks?.[0]?.timeout) * 1000 || 0;
  if (budget("UserPromptSubmit") && cfg.recallTimeoutMs > budget("UserPromptSubmit")) report.warn(`recall timeout ${cfg.recallTimeoutMs}ms exceeds the UserPromptSubmit hook budget ${budget("UserPromptSubmit")}ms`, "Codex kills the hook before the request can finish", "lower OPENVIKING_RECALL_TIMEOUT_MS");
  if (budget("Stop") && cfg.captureTimeoutMs > budget("Stop")) report.warn(`capture timeout ${cfg.captureTimeoutMs}ms exceeds the Stop hook budget ${budget("Stop")}ms`, "Codex kills the hook before the request can finish", "lower OPENVIKING_CAPTURE_TIMEOUT_MS");

  const toggles = [`auto-inject ${cfg.noAutoInject ? "OFF" : "on"}`, `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`, `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`, `commit on compact ${cfg.autoCommitOnCompact ? "on" : "OFF"}`, `recall compress ${cfg.recallCompress ? "on" : "off"}`, `write path ${cfg.writePathAsync ? "async" : "sync"}`];
  report.info(`toggles  ${toggles.join(", ")}`);
  if (!cfg.autoRecall || !cfg.autoCapture || cfg.noAutoInject) report.warn("one or more injection paths are switched off", toggles.join(", "), "check OPENVIKING_AUTO_RECALL / OPENVIKING_AUTO_CAPTURE / OPENVIKING_NO_AUTO_INJECT and ovcli.conf plugin.codex");
  report.info(`debug log ${cfg.debug ? "on" : "off"} → ${homeShort(cfg.debugLogPath)}${cfg.debug ? "" : " (set OPENVIKING_DEBUG=1 in Codex's environment to record hook errors)"}`);

  const env = collectEnv();
  if (env.openviking.length) {
    report.info("OPENVIKING_* in this environment", env.openviking.map((e) => `${e.name}=${e.value}`).join("\n"));
    if (env.openviking.some((e) => e.name === "OPENVIKING_MEMORY_ENABLED")) report.warn("OPENVIKING_MEMORY_ENABLED has no effect on the Codex plugin", "disable it with OPENVIKING_AUTO_RECALL=0 / OPENVIKING_AUTO_CAPTURE=0, or codex plugin remove", "");
    if (cfg.credentialSource === "env") report.info("credential env vars override ovcli.conf — edits to the file (and `ov config switch`) do not take effect while they are set");
  } else {
    report.info("no OPENVIKING_* environment variables set");
  }
  if (env.proxy.length) report.warn(`proxy variables set: ${env.proxy.map((e) => e.name).join(", ")}`, "Node's fetch ignores HTTP(S)_PROXY unless NODE_USE_ENV_PROXY=1 (Node 24+); curl honours them, so curl may succeed while hooks fail", isLoopbackUrl(cfg.baseUrl) ? "harmless for a local server" : "set NODE_USE_ENV_PROXY=1 (or reach the server without the proxy) in the environment that launches Codex");
  if (env.node.length) report.info(`node TLS/proxy env: ${env.node.map((e) => `${e.name}=${e.value}`).join(", ")}`);
  return { keyInfo, peer, ovConf };
}

async function checkConnection(report, cfg, { keyInfo, peer }, opts) {
  report.section("Connection");
  if (opts.offline) {
    report.info("skipped (--offline)");
    return null;
  }
  const conn = { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey, account: cfg.sendIdentityHeaders ? cfg.account : "", user: cfg.sendIdentityHeaders ? cfg.user : "", peerId: peer.peerId, userAgent: cfg.userAgent };
  const probes = await probeOpenViking(conn, { timeoutMs: opts.timeoutMs });
  const summary = assessProbes(report, probes, { ...conn, account: cfg.account, user: cfg.user }, keyInfo);
  if (summary.authMode && summary.authMode !== "dev" && summary.authMode !== cfg.authMode) {
    report.warn(`plugin auth mode '${cfg.authMode}' differs from the server's '${summary.authMode}'`, cfg.authMode === "trusted" ? "identity headers are sent but the server ignores them in api_key mode" : "the server expects X-OpenViking-Account/User headers", "set account/user in ovcli.conf for trusted servers, or remove them (or set OPENVIKING_AUTH_MODE) for api_key servers");
  }
  return { probes, summary };
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
    const orphans = states.filter((s) => s.data?.ovSessionId && Date.now() - (s.data.lastUpdatedAt || s.mtimeMs) > idleTtl);
    if (orphans.length > 10) report.warn(`${orphans.length} idle sessions still uncommitted`, "the idle sweep at SessionStart commits them; a growing pile usually means commits are failing", "check the Connection section, then start a new Codex session to trigger the sweep");
    else if (orphans.length) report.info(`${orphans.length} idle session(s) waiting for the SessionStart sweep`);
  }
  const profile = tryJson(join(stateDir, "recall-compressor-profile.json"));
  if (profile?.profile) {
    const p = profile.profile;
    if (p.enabled === false && p.source === "runtime_failed") report.info(`local recall compressor disabled after a runtime failure (${p.failedModel || "?"}); re-detected at the next SessionStart`);
    else report.info(`recall compressor: ${p.enabled ? `${p.model || "?"} (${p.source || "?"})` : `off (${p.source || "?"})`}`);
  }

  const log = scanDebugLog(cfg.debugLogPath);
  if (!log.exists) {
    report.info(`no hook log at ${homeShort(cfg.debugLogPath)}${cfg.debug ? " — debug is on but no hook has run since; if a Codex turn ran, hooks are not being spawned (plugin_hooks, trust, node)" : ""}`);
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

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const report = createReport({ color: opts.color });
  const envInfo = checkEnvironment(report);
  checkInstall(report, envInfo);
  const cfg = loadConfig();
  const configInfo = checkConfig(report, cfg);
  const connection = await checkConnection(report, cfg, configInfo, opts);
  const serverHealth = await checkServerHealth(report, { baseUrl: cfg.baseUrl, ovConf: configInfo.ovConf, health: connection?.probes?.health, offline: opts.offline, timeoutMs: opts.timeoutMs });
  checkActivity(report, cfg, connection);

  if (opts.json) {
    console.log(JSON.stringify({
      harness: "codex",
      pluginRoot: PLUGIN_ROOT,
      generatedAt: new Date().toISOString(),
      resolved: {
        baseUrl: cfg.baseUrl,
        apiKey: configInfo.keyInfo.display,
        apiKeyFormat: configInfo.keyInfo.format,
        account: cfg.account,
        user: cfg.user,
        peerId: configInfo.peer.peerId,
        credentialSource: cfg.credentialSource,
        authMode: cfg.authMode,
      },
      server: connection?.summary || null,
      serverHealth,
      ...report.toJSON(),
    }, null, 2));
  } else {
    console.log(report.render());
  }
  process.exitCode = report.exitCode();
}

main().catch((err) => {
  console.error("ov-memory-doctor failed:", err?.stack || err?.message || err);
  process.exit(2);
});
