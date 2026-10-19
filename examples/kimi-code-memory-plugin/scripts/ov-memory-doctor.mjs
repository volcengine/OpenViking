#!/usr/bin/env node

/**
 * Client-side diagnostics for the OpenViking Kimi Code memory plugin.
 *
 * Covers the plugin install (managed copy, manifest diagnostics, hook/MCP
 * wiring), the client config (which file won, is the JSON valid, what the key
 * claims) and the connection to the server (reachability, auth, tenant-data
 * access, /mcp), plus the runtime evidence the hooks leave in
 * ~/.openviking/kimicode-plugin-state. When the server runs on this machine
 * (loopback url) it also checks the port, plugin-only keys in ov.conf and
 * `GET /ready`. Provider-level validation stays with `openviking-server doctor`.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 */

import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig } from "./config.mjs";
import { getStateDir } from "./session-state.mjs";
import {
  credentialSources,
  fmtAge,
  inspectConfigFiles,
  reportCredentials,
  reportPeer,
  reportTimeouts,
  reportToggles,
  runDoctor,
} from "./shared/doctor-core.mjs";

const PLUGIN_ROOT = resolvePath(fileURLToPath(import.meta.url), "..", "..");
const PLUGIN_NAME = "openviking-memory";

const HOST = {
  harness: "kimicode",
  pluginRoot: PLUGIN_ROOT,
  cliName: "kimi",
  launcherHint: "Kimi Code",
  // Recall has a timeout of its own here, so it is the knob the prompt hook
  // spends. Capture spends `timeoutMs`: nothing in this plugin reads
  // `captureTimeoutMs`, and checking it would never fire.
  timeoutBudgets: { UserPromptSubmit: "recallTimeoutMs", Stop: "timeoutMs" },
  loadConfig,
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ credentialSource: cfg.credentialSource, authMode: cfg.authMode }),
};

/**
 * The Kimi CLI copies a plugin to `$KIMI_CODE_HOME/plugins/managed/<name>/`
 * on `/plugins install` and always runs the managed copy. There is no config
 * file to merge — installation state lives in `plugins/installed.json` — so
 * this check reports the record instead of editing anything.
 */
function checkInstall(report) {
  report.section("Install");
  const kimiHome = process.env.KIMI_CODE_HOME || join(homedir(), ".kimi-code");
  const installedPath = join(kimiHome, "plugins", "installed.json");
  if (!existsSync(installedPath)) {
    report.info(`no ${installedPath} — the plugin is not installed in this Kimi home (or Kimi Code never ran)`);
    return;
  }

  let installed;
  try {
    installed = JSON.parse(readFileSync(installedPath, "utf-8"));
  } catch (err) {
    report.fail("plugins/installed.json cannot be parsed", err?.message || String(err), "run /plugins reload after fixing it");
    return;
  }
  const plugins = Array.isArray(installed?.plugins) ? installed.plugins : [];
  const record = plugins.find((p) => p?.id === PLUGIN_NAME || p?.manifest?.name === PLUGIN_NAME);
  if (!record) {
    report.fail(`plugins/installed.json has no ${PLUGIN_NAME} record`, "", `/plugins install <dir | zip URL | GitHub URL>`);
    return;
  }

  const managedRoot = record.root || join(kimiHome, "plugins", "managed", PLUGIN_NAME);
  const enabled = record.enabled !== false;
  (enabled ? report.ok : report.fail)(`installed record: ${PLUGIN_NAME} enabled=${enabled}`, "", enabled ? "" : `/plugins enable ${PLUGIN_NAME}`);

  try {
    const manifest = JSON.parse(readFileSync(join(managedRoot, "kimi.plugin.json"), "utf-8"));
    const hooks = manifest.hooks ?? [];
    const events = hooks.map((h) => h.event).filter(Boolean);
    report.info(`manifest declares ${hooks.length} hooks (${events.join(", ") || "none"}) and ${manifest.mcpServers ? "an" : "no"} MCP server`);
  } catch {
    report.warn(`managed copy at ${managedRoot} is missing or unreadable`, "the CLI runs the managed copy, not the source dir", `/plugins install ${PLUGIN_ROOT}`);
    return;
  }

  let sourceVersion = "";
  try {
    sourceVersion = JSON.parse(readFileSync(join(PLUGIN_ROOT, "kimi.plugin.json"), "utf-8"))?.version || "";
  } catch { /* source manifest unreadable; the managed comparison below still stands */ }
  const managedVersion = JSON.parse(readFileSync(join(managedRoot, "kimi.plugin.json"), "utf-8"))?.version || "?";
  const managedRootReal = existsSync(managedRoot) ? realpathSync(managedRoot) : managedRoot;
  if (managedRootReal === realpathSync(PLUGIN_ROOT)) {
    report.ok(`running from the managed copy at ${managedRoot} (version ${managedVersion})`);
  } else {
    report.info(`managed copy: ${managedRoot} (version ${managedVersion}) — this doctor runs from ${PLUGIN_ROOT}${sourceVersion ? ` (version ${sourceVersion})` : ""}`);
    if (sourceVersion && managedVersion !== sourceVersion && managedVersion !== "?") {
      report.warn(`managed copy is ${managedVersion}, this source is ${sourceVersion}`, "the CLI always runs the managed copy; edits to the source dir require reinstall", `/plugins install ${PLUGIN_ROOT} again to update`);
    }
  }
}

/**
 * The manifest declares hooks as a flat array, which the shared budget check
 * does not read. Comparing each hook's own timeout against the knob that
 * governs it is the difference between "the doctor checked the budget" and
 * "the doctor printed a line".
 */
const HOOK_BUDGET_KNOBS = [
  // Capture clamps every request to what remains of its own budget, so only
  // recall's knob can claim more time than its hook is given.
  ["UserPromptSubmit", "recallTimeoutMs"],
];

function reportHookBudgets(report, cfg) {
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(join(PLUGIN_ROOT, "kimi.plugin.json"), "utf-8"));
  } catch {
    return;
  }
  const hooks = Array.isArray(manifest?.hooks) ? manifest.hooks : [];
  for (const [event, knob] of HOOK_BUDGET_KNOBS) {
    const hook = hooks.find((item) => item?.event === event);
    const budgetMs = Number(hook?.timeout) * 1000 || 0;
    const value = Number(cfg[knob]) || 0;
    if (!budgetMs || !value || value <= budgetMs) continue;
    report.warn(
      `${knob} ${value}ms exceeds the ${event} hook budget ${budgetMs}ms`,
      "the host kills the hook before the request can finish",
      `lower ${knob} or raise the manifest timeout`,
    );
  }
}

function checkConfig(report, cfg, host) {
  report.section("Configuration");
  const { cliConf, ovConf } = inspectConfigFiles(report, host);
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    report.fail("no usable config — the plugin falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key");
  }

  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.account, user: cfg.user });
  report.info(`credential source: ${cfg.credentialSource} (auth mode ${cfg.authMode}; identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"})`);
  const peer = reportPeer(report, cfg);
  reportTimeouts(report, cfg, host);
  reportHookBudgets(report, cfg);
  // Same source of truth as pending-queue.mjs: OPENVIKING_PENDING_REPLAY_LIMIT
  // wins; SessionStart otherwise defaults the env to 2 in code (the shared
  // fallback when no entry point sets it is 50).
  const replayLimitEnv = parseInt(process.env.OPENVIKING_PENDING_REPLAY_LIMIT || "", 10);
  const replayLimit = Number.isFinite(replayLimitEnv) && replayLimitEnv > 0 ? replayLimitEnv : 2;
  reportToggles(report, cfg, host, [
    `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`,
    `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`,
    `commit every ${cfg.commitTurnThreshold || "∞"} captured turns`,
    `pending replay limit ${replayLimit}/session-start (env override; shared fallback 50)`,
  ]);
  return { keyInfo, peer, ovConf };
}

function checkActivity(report, cfg) {
  report.section("Recent activity");
  const stateDir = getStateDir();
  if (!existsSync(stateDir)) {
    report.info(`no session state dir at ${stateDir} yet — no Kimi hook has run (or OPENVIKING_KIMICODE_STATE_DIR points elsewhere)`);
    return;
  }
  const states = readdirSync(stateDir)
    .filter((n) => n.endsWith(".json"))
    .map((n) => {
      const path = join(stateDir, n);
      const st = statSync(path);
      let data = null;
      try { data = JSON.parse(readFileSync(path, "utf-8")); } catch { /* unreadable entry; still list it */ }
      return { name: n, mtimeMs: st.mtimeMs, data };
    })
    .sort((a, b) => b.mtimeMs - a.mtimeMs);
  if (!states.length) {
    report.info(`no session state in ${stateDir} yet`);
    return;
  }
  for (const s of states.slice(0, 5)) {
    const d = s.data;
    const detail = d
      ? `turn ${d.lastTurnId ?? "?"} · ${Array.isArray(d.capturedTurnIds) ? d.capturedTurnIds.length : "?"} acked · ${d.capturedSinceCommit ?? 0} since commit · ov session ${d.ovSessionId || d.ov_session_id || "?"}`
      : "state file unreadable";
    report.info(`${s.name}  ${fmtAge(s.mtimeMs)} ago — ${detail}`);
  }
  if (states.length > 5) report.info(`… and ${states.length - 5} more state file(s)`);
}

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
