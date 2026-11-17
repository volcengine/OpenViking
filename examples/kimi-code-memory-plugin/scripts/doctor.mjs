#!/usr/bin/env node

/**
 * Diagnostics for the Kimi Code memory plugin.
 *
 * It answers four questions in order, because each depends on the one before:
 * is the plugin installed and enabled, is its configuration the one the hooks
 * will actually read, can this machine reach the server, and what have the
 * hooks left behind. `runDoctor` owns the environment, config, connection and
 * server-health sections; this file owns only what Kimi Code knows — its
 * manifest, its plugin registry, its session layout and its state files.
 *
 * The plugin is installed by the host's own `/plugins install`, so there is no
 * private installer to check and no `kimi plugin install` command to suggest.
 * Every suggestion printed here is a command the host really has.
 *
 * Usage:
 *   node ./scripts/doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 */

import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig, readManifest } from "./config.mjs";
import {
  countDirEntries,
  credentialSources,
  existsPath,
  fmtAge,
  fmtBytes,
  homeShort,
  inspectConfigFiles,
  inspectJsonFile,
  readStateFiles,
  reportCredentials,
  reportPeer,
  reportToggles,
  runDoctor,
  scanDebugLog,
  sweepEnv,
  tryJson,
} from "./shared/doctor-core.mjs";
import { describeInputFilters } from "./shared/input-filters.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { listPendingReadOnly } from "./shared/pending-queue.mjs";
import { HARNESS } from "./config.mjs";
import { kimiCodeHome } from "./session-locator.mjs";
import { captureFailureBlocks, isSweepCandidate } from "./session-state.mjs";
import { BUDGETS } from "./hook-stage.mjs";

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_ID = "openviking-memory";
const REQUIRED_FILES = [
  "kimi.plugin.json",
  "scripts/session-start.mjs",
  "scripts/user-prompt.mjs",
  "scripts/uri-guard.mjs",
  "scripts/stop.mjs",
  "scripts/pre-compact.mjs",
  "scripts/session-end.mjs",
  "servers/mcp-proxy.mjs",
];
const EXPECTED_HOOKS = {
  SessionStart: 10,
  UserPromptSubmit: 20,
  PreToolUse: 5,
  Stop: 10,
  PreCompact: 15,
  SessionEnd: 10,
};

function stateRoot() {
  return process.env.OPENVIKING_HOOK_STATE_DIR || join(homedir(), ".openviking", "hook-state");
}

/**
 * The plugin as the host sees it.
 *
 * The manifest is the contract: six inline hooks with no matcher, one inline
 * stdio MCP server, and a skills directory. A per-hook `env` or `cwd` would be
 * silently ignored by this host, so their presence is a real finding — the
 * author expected something the host does not do.
 */
function checkInstall(report) {
  report.section("Plugin install");
  const manifestPath = join(PLUGIN_ROOT, "kimi.plugin.json");
  const manifest = tryJson(manifestPath);
  if (!manifest) {
    report.fail("kimi.plugin.json is missing or not valid JSON", homeShort(manifestPath), "reinstall with /plugins install <path or zip url>");
    return { manifest: null, installPath: "" };
  }
  report.info(`running from ${homeShort(PLUGIN_ROOT)} (${manifest.name || "?"} ${manifest.version || "?"})`);

  const missing = REQUIRED_FILES.filter((rel) => !existsPath(join(PLUGIN_ROOT, rel)));
  if (missing.length) {
    report.fail("plugin files missing", missing.join(", "), "reinstall with /plugins install <path or zip url>");
  } else {
    report.ok("entry points present (six hooks, MCP proxy)");
  }

  const hooks = Array.isArray(manifest.hooks) ? manifest.hooks : [];
  if (!Array.isArray(manifest.hooks)) {
    report.fail("hooks is not an inline array", "this host reads only inline hook objects", "reinstall from the released package");
  } else {
    const events = hooks.map((hook) => hook.event);
    for (const [event, timeout] of Object.entries(EXPECTED_HOOKS)) {
      const hook = hooks.find((item) => item.event === event);
      if (!hook) {
        report.fail(`${event} hook is not registered`, "", "reinstall with /plugins install <path or zip url>");
        continue;
      }
      if (hook.matcher !== undefined) {
        report.fail(`${event} has a matcher`, "this host tests matchers as JavaScript regexes; `*` never matches", "remove the matcher (this plugin decides inside each entry)");
      }
      if (hook.timeout !== timeout) {
        report.warn(`${event} timeout is ${hook.timeout}s, expected ${timeout}s`, "the internal budget is derived from this value");
      }
      for (const extra of Object.keys(hook)) {
        if (!["event", "matcher", "command", "timeout"].includes(extra)) {
          report.fail(`${event} carries '${extra}'`, "this host ignores unknown hook fields, and per-hook cwd/env are not supported", "remove it from kimi.plugin.json");
        }
      }
    }
    if (events.length !== Object.keys(EXPECTED_HOOKS).length) {
      report.warn(`${events.length} hooks registered, expected ${Object.keys(EXPECTED_HOOKS).length}`, events.join(", "));
    }
  }

  const mcp = manifest.mcpServers?.openviking;
  if (!mcp) {
    report.fail("mcpServers.openviking is missing", "", "reinstall with /plugins install <path or zip url>");
  } else if (typeof mcp !== "string" && mcp.transport === "stdio" && mcp.command === "node") {
    report.ok(`MCP server 'openviking' → ${mcp.command} ${(mcp.args || []).join(" ")}`);
  } else {
    report.fail("MCP server is not an inline stdio entry", JSON.stringify(mcp), "this host does not read an mcpServers path; keep the object inline");
  }

  if (typeof manifest.skills === "string" && manifest.skills.startsWith("./") && existsPath(join(PLUGIN_ROOT, manifest.skills))) {
    const skills = countDirEntries(join(PLUGIN_ROOT, manifest.skills), (name) => !name.startsWith("."));
    // `null` means the directory could not be read, which is a permission
    // problem — not a directory holding no skills.
    if (skills === null) report.warn("skills directory cannot be read", manifest.skills, "check the directory permissions");
    else report.ok(`skills directory present (${skills} entries)`);
  } else if (manifest.skills !== undefined) {
    report.warn("skills directory declared but not found", String(manifest.skills), "reinstall; a missing skills directory silently disables the workflows");
  }

  // The host's own registry. Read-only: this plugin never writes it.
  const home = kimiCodeHome();
  const registryPath = join(home, "plugins", "installed.json");
  const registry = inspectJsonFile(registryPath);
  let installPath = "";
  if (!registry.exists) {
    report.warn(`${homeShort(registryPath)} not found`, "the plugin was never installed through /plugins on this machine", `run /plugins install ${homeShort(PLUGIN_ROOT)} in Kimi Code`);
  } else if (!registry.ok) {
    report.fail(`${homeShort(registryPath)} is unreadable`, registry.error, "fix the JSON; the host ignores the whole registry otherwise");
  } else {
    const entry = findRegistryEntry(registry.data, PLUGIN_ID);
    if (!entry) {
      const known = Array.isArray(registry.data.plugins)
        ? registry.data.plugins.map((entry) => entry?.id).filter(Boolean)
        : Object.keys(registry.data.plugins || registry.data || {});
      report.warn(`${PLUGIN_ID} is not registered in installed.json`, `known ids: ${known.join(", ") || "(none)"}`, `run /plugins install ${homeShort(PLUGIN_ROOT)}`);
    } else {
      installPath = entry.root;
      report.ok(`registered ${PLUGIN_ID} ${entry.version || "?"}${entry.enabled === false ? " (disabled)" : ""}`);
      if (entry.enabled === false) {
        report.warn("the plugin is installed but disabled", "", `/plugins enable ${PLUGIN_ID}`);
      }
      if (installPath && !sameRealPath(installPath, PLUGIN_ROOT)) {
        report.warn("this script is not running from the registered copy", `registered: ${homeShort(installPath)}\nrunning:    ${homeShort(PLUGIN_ROOT)}`, "check the registered copy with its own scripts/doctor.mjs");
      }
      if (installPath && !existsPath(join(installPath, "kimi.plugin.json"))) {
        report.fail("the registered install path no longer exists", homeShort(installPath), "reinstall with /plugins install <path or zip url>");
      }
    }
  }

  // A hand-written hook for the same events would fire beside the plugin's.
  const legacyConfig = join(home, "config.toml");
  if (existsSync(legacyConfig)) {
    const text = readFileSyncSafe(legacyConfig);
    if (/openviking|auto-recall|auto-capture/i.test(text) && /\[\[hooks\]\]|hooks\s*=/.test(text)) {
      report.warn("config.toml still declares OpenViking hooks", "a pre-plugin install wrote them there; together with the plugin every hook fires twice", "remove the OpenViking hook entries from config.toml after confirming they are the old ones");
    }
  }

  return { manifest, installPath };
}

/**
 * One entry from the host's `installed.json`.
 *
 * The host writes `{version, plugins: [...]}` with each entry keyed by `id` and
 * pointing at `root`. It has used an object keyed by id in the past, so both
 * shapes are read; guessing only one of them makes the doctor claim a plugin is
 * missing while it is installed.
 */
function findRegistryEntry(registry, id) {
  if (!registry || typeof registry !== "object") return null;
  const plugins = registry.plugins ?? registry;
  const list = Array.isArray(plugins)
    ? plugins
    : Object.entries(plugins).map(([key, value]) => (value && typeof value === "object" ? { id: key, ...value } : null)).filter(Boolean);
  for (const entry of list) {
    const entryId = String(entry.id ?? entry.name ?? "");
    if (entryId !== id && !entryId.startsWith(`${id}@`)) continue;
    return {
      ...entry,
      root: entry.root || entry.path || entry.installPath || entry.dir || "",
    };
  }
  return null;
}

function readFileSyncSafe(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return "";
  }
}

/**
 * Are these two paths the same directory, after symlinks are resolved?
 *
 * The plugin manager commonly installs through a link, and a registry `root`
 * may be relative. A plain string compare reports "not the registered copy"
 * for a copy that is exactly the registered one.
 */
function sameRealPath(a, b) {
  const real = (value) => {
    try {
      return realpathSync(resolvePath(value));
    } catch {
      return resolvePath(value);
    }
  };
  return real(a) === real(b);
}

/** Configuration, as the hooks will resolve it for this directory. */
function checkConfig(report, cfg) {
  report.section("Configuration");
  const files = inspectConfigFiles(report, { harness: HARNESS, launcherHint: "Kimi Code" });
  const sources = credentialSources(cfg, files.cliConf, files.ovConf, { section: HARNESS });
  const keyInfo = reportCredentials(report, cfg, sources, { account: cfg.account, user: cfg.user });
  const peer = reportPeer(report, cfg, { cwd: process.cwd() });

  report.info(`workspace  ${homeShort(process.cwd())} (the hook re-resolves this from the payload's cwd)`);
  report.info(`enabled    ${cfg.enabled === false ? "no (hooks are off)" : "yes"}; autoCapture ${isCaptureEnabled(cfg) ? "on" : "off"}; autoRecall ${cfg.autoRecall ? "on" : "off"}; noAutoInject ${cfg.noAutoInject ? "on" : "off"}`);
  if (cfg.enabled === false) {
    report.warn("the plugin is enabled in the host but switched off in configuration", "OPENVIKING_MEMORY_ENABLED or enabled=false", "unset it, or use /plugins disable openviking-memory to stop everything including MCP");
  }
  reportToggles(report, cfg, { harness: HARNESS }, ["autoCapture", "autoRecall", "noAutoInject"]);
  report.info(`commit     token threshold ${cfg.commitTokenThreshold}, turn threshold ${cfg.commitTurnThreshold}; keep_recent_count is fixed at 0`);
  reportInlineHookBudgets(report, cfg);
  const filters = describeInputFilters(cfg);
  for (const filter of filters) {
    if (filter.errors?.length) report.warn(`${filter.label} have invalid rules`, filter.errors.map((error) => error.message).join("; "), "fix the rule syntax (s/…/…/, d/…/, k/…/)");
    else if (filter.active) report.info(`${filter.label}: ${filter.summary}`);
  }
  sweepEnv(report, cfg, { launcherHint: "Kimi Code" });
  return { keyInfo, peer, ovConf: files.ovConf, cliConf: files.cliConf };
}

/**
 * Each hook's manifest timeout against the budget the plugin uses inside it.
 *
 * This host declares hooks inline in `kimi.plugin.json`; it has no
 * `hooks/hooks.json`, so the shared `reportTimeouts` finds nothing and silently
 * skips. Comparing against `BUDGETS` is the check that actually applies.
 */
function reportInlineHookBudgets(report, cfg) {
  const manifest = tryJson(join(PLUGIN_ROOT, "kimi.plugin.json"));
  const hooks = Array.isArray(manifest?.hooks) ? manifest.hooks : [];
  if (hooks.length === 0) {
    report.warn("kimi.plugin.json declares no hooks", "", "reinstall the plugin");
    return;
  }
  const budgetFor = {
    SessionStart: BUDGETS["session-start"],
    UserPromptSubmit: BUDGETS["user-prompt"],
    PreToolUse: BUDGETS["uri-guard"],
    Stop: BUDGETS.stop,
    PreCompact: BUDGETS["pre-compact"],
    SessionEnd: BUDGETS["session-end"],
  };
  for (const hook of hooks) {
    const budget = budgetFor[hook.event];
    if (!budget) continue;
    const hostMs = Number(hook.timeout) * 1000;
    if (budget >= hostMs) {
      report.fail(
        `${hook.event} internal budget ${budget}ms does not fit the manifest timeout ${hostMs}ms`,
        "the host kills the hook before it can finish",
        "raise the manifest timeout or lower the budget in scripts/hook-stage.mjs",
      );
    }
  }
  const recallBudget = BUDGETS.recall;
  const promptHook = hooks.find((hook) => hook.event === "UserPromptSubmit");
  if (promptHook && recallBudget >= Number(promptHook.timeout) * 1000) {
    report.fail("the recall budget does not fit the UserPromptSubmit timeout", "", "lower BUDGETS.recall");
  }
  report.info(`hook budgets fit: ${hooks.map((hook) => `${hook.event} ${hook.timeout}s`).join(", ")}`);
  // `recallTimeoutMs` is not what bounds a recall request here: `user-prompt.mjs`
  // caps every request with `getRemainingMs` against `BUDGETS.recall`, so a large
  // schema default cannot outlive the hook. Comparing the two would report a
  // risk that cannot happen and suggest an environment variable that changes
  // nothing on this path.
  report.info(`recall requests are capped by the ${recallBudget}ms recall budget, not by recallTimeoutMs (${cfg.recallTimeoutMs}ms)`);
}

/** What the hooks left behind, and what still needs a human. */
async function checkActivity(report, cfg) {
  report.section("Sessions and state");
  const home = kimiCodeHome();
  const index = join(home, "session_index.jsonl");
  if (!existsPath(index)) {
    report.info(`no session index at ${homeShort(index)} — no Kimi Code session has run in this home`);
  } else {
    report.ok(`session index ${homeShort(index)} (${fmtBytes(fileSize(index))})`);
  }
  const sessionsRoot = join(home, "sessions");
  const sessions = countDirEntries(sessionsRoot);
  if (sessions === null) report.warn(`sessions root cannot be read: ${homeShort(sessionsRoot)}`, "", "check the directory permissions");
  else report.info(`${sessions} workspace directory/ies under ${homeShort(sessionsRoot)}`);

  const dir = join(stateRoot(), HARNESS);
  const hasState = existsPath(dir);
  if (!hasState) {
    // The queue and the log live outside this directory; a missing state dir
    // does not mean nothing is pending or nothing failed.
    report.info(`no plugin state yet at ${homeShort(dir)} — no hook has captured anything here`);
  }
  const files = hasState ? readStateFiles(dir, await listStateFiles(dir)) : {};
  for (const [name, value] of Object.entries(files)) {
    if (!value?.exists || value.data) continue;
    report.warn(
      `state file ${name} cannot be parsed`,
      value.error || "unknown error",
      "a corrupt state means lost cursors; the plugin quarantines it and resumes from the file end",
    );
  }
  const states = Object.entries(files).filter(([, value]) => value?.data);
  if (hasState) report.info(`${states.length} session state file(s) under ${homeShort(dir)}`);

  let blocked = 0;
  let ended = 0;
  for (const [name, value] of states) {
    const state = value.data;
    const sessionId = name.replace(/\.json$/, "");
    for (const [agentId, agent] of Object.entries(state.agents || {})) {
      if (captureFailureBlocks(agent, cfg)) {
        blocked += 1;
        report.warn(
          `${sessionId}/${agentId} has an unresolved capture failure (${agent.captureFailed.errorKind || "?"})`,
          `recorded ${fmtAge(agent.captureFailed.at)}; the commit is blocked until it is retried or accepted`,
          `node ${join(PLUGIN_ROOT, "scripts", "recover.mjs")} accept-loss --session ${sessionId} --agent ${agentId} --yes`,
        );
      }
      if (agent.commitPending) {
        blocked += 1;
        report.warn(
          `${sessionId}/${agentId} has a queued commit`,
          "it settles only when the replay reports the commit as sent",
          "start a new session (or /plugins reload) so the queue is replayed",
        );
      }
      const lost = (agent.pendingLost || []).filter((item) => !item.accepted);
      if (lost.length) {
        blocked += 1;
        report.warn(
          `${sessionId}/${agentId} lost ${lost.length} queued operation(s)`,
          lost.map((item) => `${item.type} (${item.errorKind || "?"})`).join(", "),
          `node ${join(PLUGIN_ROOT, "scripts", "recover.mjs")} accept-loss --session ${sessionId} --agent ${agentId} --yes`,
        );
      }
      if (agent.incompatibleWire) {
        report.warn(
          `${sessionId}/${agentId} has a wire newer than this plugin understands`,
          `min_protocol_version ${agent.minProtocolVersion || "?"}; capture is stopped and the cursor kept`,
          "upgrade the plugin; nothing is lost meanwhile",
        );
      }
      if (agent.legacyWire) {
        report.info(`${sessionId}/${agentId} has a legacy wire (no metadata line); this plugin does not guess 1.5 semantics for it`);
      }
    }
    const nonLocal = state.diagnostics?.nonLocalEnvironment;
    if (nonLocal) {
      report.info(
        `${sessionId} lives in environment ${nonLocal.environmentId}; its wire is not on this machine`,
        `recorded ${fmtAge(nonLocal.at)}; capture is skipped by design, not failing`,
      );
    }
    if (isSweepCandidate(state)) {
      ended += 1;
      report.info(`${sessionId} ended ${fmtAge(state.endedMarker.at)} and is waiting for the next start's sweep`);
    }
    const breakerUntil = Number(state.recallFailureUntil) || 0;
    if (breakerUntil > Date.now()) {
      report.warn(`${sessionId} recall is paused after repeated failures`, `until ${new Date(breakerUntil).toISOString()}`, "check the server, then it resumes on its own");
    }
  }
  if (!blocked) report.ok("no blocked agents");
  if (ended) report.info(`${ended} ended session(s) will be swept at the next startup`);

  const pendingDir = process.env.OPENVIKING_PENDING_DIR || join(homedir(), ".openviking", "pending");
  // A diagnostic run must not repair anything: listing read-only keeps the
  // doctor from moving a stale `.processing` file out from under a live hook.
  const pending = await listPendingReadOnly();
  if (pending.length === 0) report.ok("offline queue empty");
  else {
    const byType = pending.reduce((out, item) => {
      const type = item.entry?.type || "?";
      out[type] = (out[type] || 0) + 1;
      return out;
    }, {});
    report.warn(
      `${pending.length} operation(s) waiting in ${homeShort(pendingDir)}`,
      Object.entries(byType).map(([type, count]) => `${type}: ${count}`).join(", "),
      "they replay at the next session start, after the server answers /health",
    );
  }

  const log = scanDebugLog(cfg.debugLogPath);
  if (!log.exists) {
    report.info(`no hook log at ${homeShort(cfg.debugLogPath)}${cfg.debug ? " — debug is on but no hook has run since" : ""}`);
  } else {
    report.info(`hook log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}`);
    if (log.recentErrors.length) {
      report.warn(`recent hook errors (${log.recentErrors.length})`, log.recentErrors.map((error) => `${error.ts} ${error.hook}/${error.stage}: ${error.message}`).join("\n"));
    }
  }
}

async function listStateFiles(dir) {
  const { readdir } = await import("node:fs/promises");
  try {
    return (await readdir(dir)).filter((name) => name.endsWith(".json"));
  } catch {
    return [];
  }
}

function fileSize(path) {
  try {
    return statSync(path).size;
  } catch {
    return 0;
  }
}

const HOST = {
  harness: HARNESS,
  pluginRoot: PLUGIN_ROOT,
  cliName: "kimi",
  launcherHint: "Kimi Code",
  nodePathFix: "make sure the environment that launches Kimi Code Desktop has node 22.19+ on PATH; a GUI launch does not read your shell profile",
  onCliFound(report, cli) {
    report.ok(cli.stdout.split("\n")[0]);
    const version = String(cli.stdout).trim();
    if (version && !version.startsWith("2.")) {
      report.warn(`host version ${version} is not the verified 2.6.2`, "hook payloads, MCP naming and the wire protocol were verified against 2.6.2", "upgrade Kimi Code, or expect differences");
    }
  },
  loadConfig: () => loadConfig(process.cwd()),
  checkInstall,
  checkConfig,
  checkActivity: (report, cfg) => checkActivity(report, cfg),
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ captureEnabled: isCaptureEnabled(cfg), pluginVersion: readManifest().version }),
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
  // `runDoctor` parses argv itself, so `--offline` reaches the network gates.
  runDoctor(HOST).catch((error) => {
    console.error("doctor failed:", error?.stack || error?.message || error);
    process.exit(2);
  });
}

export { HOST };
