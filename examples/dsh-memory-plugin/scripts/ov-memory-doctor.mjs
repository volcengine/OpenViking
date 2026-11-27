#!/usr/bin/env node

/**
 * Client-side diagnostics for the DeepSeek Harness bundle.
 *
 * `dsh plugin add` installs the package into a profile under DSH_HOME
 * (~/.dsh/profiles/<profile>): the profile's package.json lists it as a
 * dependency, `dsh.profile.bundles` decides whether it is loaded (Desktop's
 * Plugins switch flips that list), and the copy lands in the profile's
 * node_modules. The MCP server and the skills are mounted by the plugin itself
 * from inside the package, so no host file names them. The configuration and
 * connection sections are the ones every harness shares.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 */

import { readFileSync, readdirSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { resolveConfig } from "../config.mjs";
import { stateDir } from "../shared/workspace-identity.mjs";
import {
  countDirEntries,
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
  runDoctor,
  scanDebugLog,
  sweepEnv,
  tryJson,
} from "../shared/doctor-core.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const MANIFEST = tryJson(join(PLUGIN_ROOT, "package.json"));
const PACKAGE = MANIFEST?.name || "@openviking/dsh-memory-plugin";
const SKILLS = ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search", "ov-memory-doctor"];
const REINSTALL = "curl -fsSL https://openviking.ai/install | bash -s -- --harness dsh   (Desktop: --harness dsh-desktop)";

const profilesDir = () => join(process.env.DSH_HOME || join(homedir(), ".dsh"), "profiles");
// dsh exports the running session's profile to the shells its agent starts.
const sessionProfile = () => (process.env.DSH_SHELL === "1" ? process.env.DSH_PROFILE || "" : "");

function inspectProfile(name) {
  const dir = join(profilesDir(), name);
  const manifest = tryJson(join(dir, "package.json"));
  let patch = "";
  try {
    patch = readFileSync(join(dir, "cordis.patch.yml"), "utf8");
  } catch {}
  return {
    name,
    exists: Boolean(manifest),
    listed: Boolean(manifest?.dependencies?.[PACKAGE]),
    enabled: (manifest?.dsh?.profile?.bundles || []).includes(PACKAGE),
    version: tryJson(join(dir, "node_modules", ...PACKAGE.split("/"), "package.json"))?.version || "",
    patched: patch.includes("openviking-memory-runtime"),
  };
}

// Connection settings given in a profile's cordis.patch.yml outrank the files
// and environment this doctor reads, and the doctor does not parse that file.
let patchedProfile = "";

function checkInstall(report) {
  report.section("Plugin install");
  report.info(`running from ${homeShort(PLUGIN_ROOT)} (version ${MANIFEST?.version || "?"})`);
  // `files` is exactly what the package ships, so anything absent never reached the profile.
  const missing = MANIFEST ? (MANIFEST.files || []).filter((rel) => !existsPath(join(PLUGIN_ROOT, rel))) : ["package.json"];
  if (missing.length) report.fail("plugin files missing", missing.join(", "), REINSTALL);
  else report.ok("plugin files present (entry, MCP proxy, shared runtime, cordis patch)");

  const session = sessionProfile();
  let names = [];
  try {
    names = readdirSync(profilesDir(), { withFileTypes: true }).filter((entry) => entry.isDirectory()).map((entry) => entry.name);
  } catch {}
  const profiles = session ? [inspectProfile(session)] : names.map(inspectProfile).filter((p) => p.listed || p.enabled);
  if (!profiles.length) {
    report.fail(`no DeepSeek Harness profile in ${homeShort(profilesDir())} has the plugin`, `${PACKAGE} is in no profile's package.json`, REINSTALL);
  }
  for (const profile of profiles) {
    const add = `dsh plugin --profile ${profile.name} add ${PACKAGE}@latest`;
    if (!profile.exists) report.fail(`profile ${profile.name} not found in ${homeShort(profilesDir())}`, "this dsh session runs a profile the doctor cannot see (DSH_HOME?)", "");
    else if (!profile.listed && !profile.enabled) report.fail(`the plugin is not installed in profile ${profile.name}`, "this dsh session runs that profile", add);
    else if (!profile.enabled) report.warn(`the plugin is installed in profile ${profile.name} but turned off`, `it is not in dsh.profile.bundles, so ${profile.name} never loads it`, `turn it on in the Plugins page, or run: ${add}`);
    else if (!profile.version) report.fail(`profile ${profile.name} enables the plugin but has no copy of it`, `nothing at node_modules/${PACKAGE}`, add);
    else {
      report.ok(`profile ${profile.name} loads ${PACKAGE} ${profile.version}`);
      if (MANIFEST?.version && profile.version !== MANIFEST.version) report.info(`profile ${profile.name} has ${profile.version}; this copy is ${MANIFEST.version}`);
    }
    if (profile.patched) {
      patchedProfile = profile.name;
      report.info(`profile ${profile.name}'s cordis.patch.yml configures openviking-memory-runtime; settings given there override what this doctor resolves`);
    }
  }

  const skillsDir = join(PLUGIN_ROOT, "skills");
  const absent = SKILLS.filter((skill) => !existsPath(join(skillsDir, skill, "SKILL.md")));
  if (absent.length) report.warn(`skills missing from ${homeShort(skillsDir)}`, absent.join(", "), REINSTALL);
  else report.ok(`${SKILLS.length} skills in ${homeShort(skillsDir)}`);
}

function checkConfig(report, cfg, host) {
  report.section("Configuration");
  const { cliConf, ovConf } = inspectConfigFiles(report, host);
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    if (patchedProfile) report.warn(`no config outside profile ${patchedProfile}'s cordis.patch.yml`, "the checks below use http://127.0.0.1:1933 with no key unless the patch says otherwise, and may not match what the plugin connects to", "compare with endpoint and apiKey in that file, or move them to ~/.openviking/ovcli.conf");
    else report.fail("no usable config — the plugin falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key (chmod 600)");
  }
  if (sessionProfile() && !cfg.hasApiKey) {
    report.info("run from a dsh shell: dsh strips credential-shaped variables such as OPENVIKING_API_KEY from it, so a key set only in dsh's own environment is not seen here");
  }
  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.account, user: cfg.user });
  report.info(`auth mode ${cfg.authMode} (identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"})`);
  const peer = reportPeer(report, cfg);
  reportTimeouts(report, cfg, host);
  reportToggles(report, cfg, host, [
    `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`,
    `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`,
    `MCP tools ${cfg.mcpEnabled === false ? "OFF" : "on"}`,
    `write path ${cfg.writePathAsync ? "async" : "sync"}`,
  ]);
  sweepEnv(report, cfg, host);
  return { keyInfo, peer, ovConf };
}

function checkActivity(report, cfg, connection) {
  report.section("Recent activity");
  const pendingDir = process.env.OPENVIKING_PENDING_DIR || join(homedir(), ".openviking", "pending");
  const pending = countDirEntries(pendingDir, (name) => name.endsWith(".json") || name.endsWith(".processing"));
  // The queue is shared with every other harness on this machine.
  if (pending) report.info(`${pending} queued write(s) in ${homeShort(pendingDir)}; a running dsh replays them once /health passes`);
  const digests = countDirEntries(stateDir(), (name) => name.startsWith("dsh-recall-"));
  if (digests) report.info(`${digests} recall digest cache(s) in ${homeShort(stateDir())}`);

  // The plugin itself logs through dsh's logger; only the MCP proxy writes this file.
  if (!cfg.debugLogPath) {
    report.info("the plugin logs [openviking:dsh] lines through DeepSeek Harness's own log; set OPENVIKING_DEBUG_LOG to also record the MCP proxy");
  } else {
    const log = scanDebugLog(cfg.debugLogPath);
    if (!log.exists) report.info(`no log at ${homeShort(cfg.debugLogPath)} yet`);
    else {
      report.info(`log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}, writers seen: ${log.hooks.join(", ") || "(none)"}`);
      if (log.recentErrors.length) report.warn(`errors in the last day of logging (${log.recentErrors.length} shown)`, log.recentErrors.map((e) => `${e.ts} ${e.hook}/${e.stage}: ${e.message}`).join("\n"));
    }
  }
  if (connection?.summary?.reachable === false) report.info("captures that fail are queued on disk and replayed when the server answers again");
}

const HOST = {
  harness: "dsh",
  pluginRoot: PLUGIN_ROOT,
  cliName: "dsh",
  launcherHint: "DeepSeek Harness",
  // No hook timeouts to check against; this only puts the capture budget on the timeouts line.
  timeoutBudgets: { capture: "captureTimeoutMs" },
  loadConfig: () => resolveConfig({}, process.env, process.cwd()),
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ profile: sessionProfile() || null, credentialSource: cfg.credentialSource, authMode: cfg.authMode }),
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
