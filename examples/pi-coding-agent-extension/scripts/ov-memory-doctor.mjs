#!/usr/bin/env node

/**
 * Client-side diagnostics for the pi extension.
 *
 * pi has no plugin registry to consult: the installer copies this extension
 * into `<agent dir>/extensions/openviking`, one of pi's auto-discovery roots,
 * and pi loads its index.ts from there. What goes wrong is therefore a copy pi
 * does not look at (PI_CODING_AGENT_DIR moved the agent dir), a copy without
 * its generated shared runtime or npm dependencies (the MCP tools need
 * @modelcontextprotocol/client), or a settings.json that disables it or also
 * registers it as a package, which loads it twice.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 */

import { readdirSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig } from "../config.ts";
import {
  credentialSources,
  existsPath,
  expandHome,
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
} from "../shared/doctor-core.mjs";
import { defaultLedgerDir } from "../lib/recall-ledger.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const REQUIRED_FILES = ["index.ts", "config.ts", "lib/mcp-bridge.mjs", "shared/credentials.mjs", "shared/ov-http.mjs", "shared/mcp-proxy-config.mjs"];
const MCP_CLIENT = join("node_modules", "@modelcontextprotocol", "client", "package.json");
const SKILLS = ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search", "ov-memory-doctor"];
const REINSTALL = "curl -fsSL https://openviking.ai/install | bash -s -- --harness pi";

const agentDir = () => (process.env.PI_CODING_AGENT_DIR ? expandHome(process.env.PI_CODING_AGENT_DIR) : join(homedir(), ".pi", "agent"));
const extensionDir = () => join(agentDir(), "extensions", "openviking");

function checkInstall(report) {
  report.section("Plugin install");
  const dest = extensionDir();
  const manifest = inspectJsonFile(join(dest, "package.json"));
  report.info(`running from ${homeShort(PLUGIN_ROOT)}; pi loads ${homeShort(dest)} (version ${manifest.data?.version || "?"})`);
  if (process.env.PI_CODING_AGENT_DIR) report.info(`PI_CODING_AGENT_DIR moves pi's agent dir to ${homeShort(agentDir())}; the installer always writes under ~/.pi/agent`);

  const missing = REQUIRED_FILES.filter((rel) => !existsPath(join(dest, rel)));
  if (missing.length === REQUIRED_FILES.length) report.fail(`the extension is not installed where pi discovers it`, homeShort(dest), REINSTALL);
  else if (missing.length) report.fail("extension files missing", missing.join(", "), REINSTALL);
  else report.ok("extension files present (entry, MCP bridge, shared runtime)");

  if (!missing.length && !existsPath(join(dest, MCP_CLIENT))) {
    report.fail("the MCP client dependency is not installed", `${homeShort(join(dest, MCP_CLIENT))} is missing; index.ts imports it, so pi cannot load the extension at all`, `npm ci --prefix ${homeShort(dest)} --omit=dev --ignore-scripts`);
  }

  // `pi config` turns an extension off with a `-<entry>` override, in the user
  // settings or in the project's, and that override outranks every other
  // pattern. `!` glob exclusions are not evaluated here.
  const entry = join(dest, "index.ts");
  for (const baseDir of [agentDir(), join(process.cwd(), ".pi")]) {
    const settingsPath = join(baseDir, "settings.json");
    const settings = inspectJsonFile(settingsPath);
    if (settings.exists && !settings.ok) report.fail(`${homeShort(settingsPath)} cannot be parsed`, settings.error, "fix the JSON; pi cannot read its settings");
    if (!settings.ok) continue;
    const overrides = Array.isArray(settings.data.extensions) ? settings.data.extensions.map(String) : [];
    if (overrides.some((p) => p.startsWith("-") && resolvePath(baseDir, p.slice(1).replace(/^~(?=$|\/)/u, homedir())) === entry)) {
      report.fail("pi's settings turn this extension off", `a "-" override for ${homeShort(entry)} in ${homeShort(settingsPath)}`, `turn it back on in \`pi config\`, or remove the entry from extensions in ${homeShort(settingsPath)}`);
    }
  }
  if (JSON.stringify(inspectJsonFile(join(agentDir(), "settings.json")).data?.packages || []).includes("extensions/openviking")) {
    report.warn("the extension is also registered as a pi package, so it loads twice", "", `pi remove ${homeShort(dest)}`);
  }

  if (missing.length === REQUIRED_FILES.length) return;
  const absent = SKILLS.filter((skill) => !existsPath(join(dest, "skills", skill, "SKILL.md")));
  if (absent.length) report.warn(`skills missing from ${homeShort(join(dest, "skills"))}`, absent.join(", "), REINSTALL);
  else report.ok(`${SKILLS.length} skills in ${homeShort(join(dest, "skills"))}`);
}

function checkConfig(report, cfg, host) {
  report.section("Configuration");
  const { cliConf, ovConf } = inspectConfigFiles(report, host);
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    report.fail("no usable config — the extension falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key (chmod 600)");
  }
  if (!cfg.enabled) report.fail("the extension is disabled", "it returns before registering anything", "unset OPENVIKING_MEMORY_ENABLED, or drop plugin.pi.enabled = false");
  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.account, user: cfg.user });
  report.info(`auth mode ${cfg.authMode} (identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"})`);
  const peer = reportPeer(report, cfg);
  reportTimeouts(report, cfg, host);
  reportToggles(report, cfg, host, [
    `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`,
    `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`,
    `MCP tools ${cfg.mcpEnabled === false ? "OFF" : "on"}`,
    `takeover ${cfg.takeoverEnabled ? "on" : "off"}`,
  ]);
  // pi logs only to a path it is given; OPENVIKING_DEBUG alone records nothing.
  sweepEnv(report, { ...cfg, debug: Boolean(cfg.debugLogPath), debugLogPath: cfg.debugLogPath || "(no path)" }, host);
  if (!cfg.debugLogPath) report.info("to record extension errors, set OPENVIKING_DEBUG_LOG=<file> (or plugin.pi.debugLogPath) in the environment that launches pi");
  return { keyInfo, peer, ovConf };
}

function newestJson(dir) {
  try {
    const times = readdirSync(dir).filter((name) => name.endsWith(".json")).map((name) => statSync(join(dir, name)).mtimeMs);
    return { count: times.length, newest: Math.max(0, ...times) };
  } catch {
    return null;
  }
}

function checkActivity(report, cfg, connection) {
  report.section("Recent activity");
  const ledgerDir = defaultLedgerDir();
  const ledgers = cfg.recallLedger ? newestJson(ledgerDir) : null;
  if (ledgers?.count) report.info(`${ledgers.count} recall ledger(s) in ${homeShort(ledgerDir)}; newest ${fmtAge(ledgers.newest)}`);
  else if (cfg.recallLedger) report.info(`no recall ledger in ${homeShort(ledgerDir)} yet — no pi session has recalled with this extension`);

  const pendingDir = process.env.OPENVIKING_PENDING_DIR || join(homedir(), ".openviking", "pending");
  const pending = newestJson(pendingDir);
  if (pending?.count) report.info(`${pending.count} queued write(s) in ${homeShort(pendingDir)}, newest ${fmtAge(pending.newest)} — replayed at the next session start`);
  if (connection?.summary?.reachable === false) report.info("captures that fail are queued on disk and replayed at the next session start");

  if (!cfg.debugLogPath) return;
  const log = scanDebugLog(cfg.debugLogPath);
  if (!log.exists) report.info(`no extension log at ${homeShort(cfg.debugLogPath)} — no pi session has started with this path set`);
  else {
    report.info(`extension log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}`);
    if (log.recentErrors.length) report.warn(`extension errors in the last day of logging (${log.recentErrors.length} shown)`, log.recentErrors.map((e) => `${e.ts} ${e.stage}: ${e.message}`).join("\n"));
  }
}

const HOST = {
  harness: "pi",
  pluginRoot: PLUGIN_ROOT,
  cliName: "pi",
  launcherHint: "pi",
  onCliFound: (report, cli) => report.ok(`pi ${cli.stdout.split("\n")[0]}`),
  loadConfig: () => loadConfig(process.cwd()),
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ extensionDir: extensionDir(), credentialSource: cfg.credentialSource, authMode: cfg.authMode }),
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
