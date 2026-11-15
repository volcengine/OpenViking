#!/usr/bin/env node

/**
 * Backfill: replay Kimi Code sessions that predate the plugin into OpenViking.
 *
 * The hooks deliberately start at the file end when they first see a session,
 * so nothing before installation is remembered (DESIGN §6.1). This command is
 * the explicit exception the design reserves for history: a session with no
 * plugin state is read from its first record, every closed turn goes through
 * the same capture pipeline the hooks use, and the resulting cursors are
 * persisted so normal capture continues where the backfill stopped.
 *
 * It never re-imports a session that already has plugin state. OpenViking has
 * no server-side message dedup, so replaying a captured prefix would create a
 * second copy of the same memory; an existing state is only caught up from its
 * recorded cursor. A corrupt or unreadable state is skipped and left for
 * `doctor`/`recover`, never overwritten.
 *
 * Usage:
 *   node ./scripts/backfill.mjs [--dry-run] [--session <id>[,<id>...]]
 *                               [--limit <n>] [--timeout <seconds>] [--json]
 */

import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";

import {
  makeAgentFetchJSON,
  readHookStateDetailed,
  withAgentHookLock,
} from "./shared/agent-hook-runtime.mjs";
import { captureSession } from "./capture-session.mjs";
import { HARNESS, loadConfig, resolveConfigForCwd } from "./config.mjs";
import { saveState } from "./hook-stage.mjs";
import { kimiCodeHome, sessionEnvironmentId } from "./session-locator.mjs";
import { emptySessionState } from "./session-state.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { isBypassed } from "./shared/session-model.mjs";

/** One pass reads at most this many bytes, then persists and continues. */
const SCAN_WINDOW_BYTES = 1024 * 1024;
/**
 * A turn can be larger than the initial window. When a pass makes no progress
 * solely because its last turn is still open, the window doubles up to the
 * reader's own 32 MB ceiling instead of stopping forever mid-turn.
 */
const MAX_SCAN_WINDOW_BYTES = 32 * 1024 * 1024;
/** A pass that keeps finding nothing new ends the session. */
const MAX_PASSES = 500;
/** Status lines that mean "not importable here", not "broken". */
const SKIP_STATUSES = new Set([
  "non_local_environment",
  "legacy_wire",
  "incompatible_wire",
  "fork_without_marker",
  "capture_disabled",
  "bypassed",
  "missing_wire",
]);

function usage() {
  process.stdout.write(
    "Replay existing Kimi Code sessions into OpenViking.\n\n"
    + "Usage: node ./scripts/backfill.mjs [options]\n\n"
    + "  --dry-run, -n       list what would be imported; no request, no state write\n"
    + "  --session, -s <ids> only these native session ids (comma separated, repeatable)\n"
    + "  --limit <n>         import at most n sessions\n"
    + "  --timeout <seconds> per-session budget (default 300)\n"
    + "  --json              print the result as JSON\n"
    + "  --help, -h          this text\n",
  );
}

function parseArgs(argv) {
  const opts = { dryRun: false, json: false, sessionIds: [], limit: 0, timeoutMs: 300_000 };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run" || arg === "-n") opts.dryRun = true;
    else if (arg === "--json") opts.json = true;
    else if (arg === "--help" || arg === "-h") {
      usage();
      process.exit(0);
    } else if (arg === "--session" || arg === "-s") {
      const value = argv[++index];
      if (!value) throw new Error(`${arg} needs a session id`);
      opts.sessionIds.push(...value.split(",").map((id) => id.trim()).filter(Boolean));
    } else if (arg === "--limit") {
      const value = Number(argv[++index]);
      if (!Number.isFinite(value) || value < 0) throw new Error("--limit needs a non-negative number");
      opts.limit = Math.floor(value);
    } else if (arg === "--timeout") {
      const value = Number(argv[++index]);
      if (!Number.isFinite(value) || value <= 0) throw new Error("--timeout needs a positive number of seconds");
      opts.timeoutMs = Math.floor(value * 1000);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return opts;
}

function fmtBytes(value) {
  const units = ["B", "KB", "MB", "GB"];
  let size = Number(value) || 0;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${size} B` : `${size.toFixed(1)} ${units[unit]}`;
}

async function readdirSafe(dir) {
  try {
    return await readdir(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

/**
 * Sessions the host itself knows about, one level of workspace above them.
 *
 * The `state.json` must sit directly in the session directory: `notify/state.json`
 * also exists in a real home and is not a session.
 */
async function discoverSessions(home) {
  const root = join(home, "sessions");
  const found = [];
  for (const workspace of await readdirSafe(root)) {
    if (!workspace.isDirectory()) continue;
    const workspaceDir = join(root, workspace.name);
    for (const entry of await readdirSafe(workspaceDir)) {
      if (!entry.isDirectory()) continue;
      const sessionDir = join(workspaceDir, entry.name);
      let state;
      try {
        state = JSON.parse(await readFile(join(sessionDir, "state.json"), "utf8"));
      } catch {
        continue;
      }
      if (!state || typeof state !== "object" || typeof state.id !== "string") continue;
      if (!state.agents || typeof state.agents !== "object") continue;
      found.push({ id: state.id, sessionDir, state });
    }
  }
  return found.sort((left, right) => left.id.localeCompare(right.id));
}

async function wireSize(session) {
  let total = 0;
  for (const agent of Object.values(session.state.agents || {})) {
    if (!agent?.homedir) continue;
    try {
      total += (await stat(join(agent.homedir, "wire.jsonl"))).size;
    } catch {
      // A missing wire is reported by capture; sizing is only a preview.
    }
  }
  return total;
}

/** The configuration for one session's own directory. */
function cfgForSession(session, baseCfg) {
  const cwd = String(session.state.cwd || "");
  return (cwd && resolveConfigForCwd(cwd, { base: baseCfg })) || baseCfg;
}

/** What a dry run says about one session, without touching state or network. */
async function describeSession(session, baseCfg) {
  const environmentId = sessionEnvironmentId(session.state);
  const base = {
    sessionId: session.id,
    cwd: String(session.state.cwd || ""),
    agents: Object.keys(session.state.agents || {}),
    wireBytes: await wireSize(session),
  };
  if (environmentId !== "local") {
    return { ...base, action: "skip", reason: "non_local_environment", environmentId };
  }
  const cfg = cfgForSession(session, baseCfg);
  if (!isCaptureEnabled(cfg)) return { ...base, action: "skip", reason: "capture_disabled" };
  if (isBypassed(cfg, { sessionId: session.id, cwd: base.cwd })) {
    return { ...base, action: "skip", reason: "bypassed" };
  }
  const read = await readHookStateDetailed(HARNESS, session.id);
  if (read.status === "ok") return { ...base, action: "catch_up", reason: "state_exists" };
  if (read.status === "missing") return { ...base, action: "import", reason: "no_plugin_state" };
  return { ...base, action: "skip", reason: `state_${read.status}` };
}

/**
 * Import one session, pass by pass.
 *
 * Each pass is bounded by the scan window, so the state written after it is
 * also the restart point if the process is interrupted. Normal passes respect
 * the configured commit thresholds; one final forced pass commits whatever
 * remains delivered, so a short session still produces exactly one extraction.
 */
async function backfillSession(session, { baseCfg, timeoutMs, logError }) {
  const environmentId = sessionEnvironmentId(session.state);
  const result = {
    sessionId: session.id,
    action: "import",
    status: "ok",
    passes: 0,
    delivered: 0,
    queued: 0,
    committed: 0,
    open: 0,
    blocked: [],
    ovSessionIds: [],
  };
  if (environmentId !== "local") {
    return { ...result, action: "skip", status: "non_local_environment", environmentId };
  }

  const cwd = String(session.state.cwd || "");
  const cfg = cfgForSession(session, baseCfg);
  if (!isCaptureEnabled(cfg)) {
    return { ...result, action: "skip", status: "capture_disabled" };
  }
  if (isBypassed(cfg, { sessionId: session.id, cwd })) {
    return { ...result, action: "skip", status: "bypassed" };
  }
  const deadline = Date.now() + timeoutMs;
  const { fetchJSON, effectivePeer } = makeAgentFetchJSON(cfg, cwd || process.cwd(), {
    defaultTimeoutMs: Math.max(500, Math.min(30_000, timeoutMs)),
    getRemainingMs: () => deadline - Date.now(),
  });
  const peerId = effectivePeer?.peerId || "";

  const locked = await withAgentHookLock(HARNESS, session.id, async () => {
    // Re-read under the lock: a hook may have written between discovery and here.
    const read = await readHookStateDetailed(HARNESS, session.id);
    if (read.status === "corrupt" || read.status === "unreadable") {
      return { ...result, action: "skip", status: `state_${read.status}`, error: read.error };
    }
    let state = read.status === "ok"
      ? read.state
      : {
        ...emptySessionState(),
        nativeSessionId: session.id,
        // The one boundary this command exists to override: a fresh session is
        // read from its first record instead of from the file end.
        startSource: "startup",
      };
    if (read.status === "ok") {
      result.catchUp = true;
      result.action = "catch_up";
    }

    const scanSnapshot = (value) => Object.entries(value.agents || {})
      .map(([agentId, agent]) => `${agentId}:${agent.scanOffset || 0}:${agent.ackOffset || 0}`)
      .sort()
      .join("|");
    const openAgents = (summary) => Object.values(summary.agents || {})
      .reduce((count, agent) => count + (Number(agent.open) || 0), 0);

    const runPass = async (mode, windowBytes) => {
      const capture = await captureSession({
        cfg,
        input: {},
        cwd: cwd || process.cwd(),
        log: () => {},
        logError,
        deadline,
        fetchJSON,
        peerId,
        sessionId: session.id,
        state,
        mode,
        now: Date.now(),
        scanLimitBytes: windowBytes,
      });
      state = capture.state;
      await saveState(session.id, state);
      result.passes += 1;
      result.delivered += capture.summary.delivered || 0;
      result.queued += capture.summary.queued || 0;
      result.committed += capture.summary.committed || 0;
      result.open = Math.max(result.open, openAgents(capture.summary));
      for (const blocked of capture.summary.blocked || []) {
        if (!result.blocked.includes(blocked)) result.blocked.push(blocked);
      }
      if (capture.summary.status && capture.summary.status !== "ok") result.status = capture.summary.status;
      return capture.summary;
    };

    let windowBytes = SCAN_WINDOW_BYTES;
    while (result.passes < MAX_PASSES && Date.now() < deadline) {
      const before = scanSnapshot(state);
      const summary = await runPass("normal", windowBytes);
      if (summary.delivered || summary.queued) continue;
      if (scanSnapshot(state) !== before) continue;
      const open = openAgents(summary);
      if (open > 0 && windowBytes < MAX_SCAN_WINDOW_BYTES) {
        windowBytes = Math.min(MAX_SCAN_WINDOW_BYTES, windowBytes * 2);
        continue;
      }
      break;
    }
    // One forced boundary commits everything the passes delivered, including
    // terminal partials, without waiting for a hook that may never come.
    if (Date.now() < deadline) await runPass("session-end", windowBytes);

    result.ovSessionIds = Object.values(state.agents || {})
      .map((agent) => agent.ovSessionId)
      .filter(Boolean);
    if (result.status === "ok" && result.blocked.some((reason) => reason.endsWith(":open_turn") || reason === "open_turn")) {
      // Not a failure: the live session's current turn has not ended, so it is
      // deliberately left for the next hook or a later backfill run.
      result.status = "open_tail_pending";
    }
    return result;
  }, { waitMs: 2000 });

  if (locked === null) {
    return { ...result, action: "skip", status: "lock_busy" };
  }
  return locked;
}

function describeResult(result) {
  const verb = result.action === "skip" ? "skip" : "import";
  const details = [
    `agents=${(result.agents || []).join(",") || "?"}`,
    `wire=${fmtBytes(result.wireBytes || 0)}`,
  ];
  if (result.action === "skip") details.push(`reason=${result.reason}`);
  else if (result.action === "catch_up") details.push("reason=state_exists");
  return `${result.sessionId}  ${verb}  ${details.join("  ")}`;
}

function printSessionResult(result) {
  const parts = [
    `${result.sessionId}`,
    result.action === "skip"
      ? `skip (${result.reason || result.status})`
      : (result.action === "catch_up" ? "catch up" : result.action),
    `${result.passes} pass(es)`,
    `delivered ${result.delivered}`,
    `queued ${result.queued}`,
    `committed ${result.committed}`,
  ];
  if (result.open) parts.push(`open ${result.open} (left for the next boundary)`);
  if (result.blocked.length) parts.push(`blocked: ${result.blocked.join(", ")}`);
  if (result.status && result.status !== "ok") parts.push(`status: ${result.status}`);
  process.stdout.write(`${parts.join(" — ")}\n`);
  if (result.ovSessionIds?.length) process.stdout.write(`  ov sessions: ${result.ovSessionIds.join(", ")}\n`);
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const home = kimiCodeHome();
  const baseCfg = loadConfig(process.cwd(), { env: process.env });
  const only = new Set(opts.sessionIds);
  const discovered = await discoverSessions(home);
  const missing = [...only].filter((id) => !discovered.some((session) => session.id === id));

  if (opts.dryRun) {
    const rows = [];
    for (const session of discovered) {
      if (only.size && !only.has(session.id)) continue;
      if (opts.limit && rows.length >= opts.limit) break;
      rows.push(await describeSession(session, baseCfg));
    }
    if (opts.json) {
      process.stdout.write(`${JSON.stringify({ home, sessions: rows, missing }, null, 2)}\n`);
    } else {
      for (const row of rows) process.stdout.write(`${describeResult(row)}\n`);
      if (rows.length === 0) process.stdout.write("no sessions found\n");
      if (missing.length) process.stdout.write(`not found: ${missing.join(", ")}\n`);
    }
    return 0;
  }

  const results = [];
  let failures = 0;
  let serverReady = null;
  // A backfill writes every message it reads. If the server is down the sender
  // would enqueue the whole history into the bounded offline queue instead of
  // failing, which is not what an explicit import should do. The probe is lazy:
  // a run that only skips sessions must not touch the network at all.
  const ensureServer = async () => {
    if (serverReady !== null) return serverReady;
    const probe = makeAgentFetchJSON(baseCfg, process.cwd(), { defaultTimeoutMs: 5000 });
    const health = await probe.fetchJSON("/health");
    serverReady = health.ok === true;
    if (!serverReady) {
      process.stderr.write(
        `[backfill] server unreachable at ${baseCfg.baseUrl}; nothing was written. `
        + "Start OpenViking first, or use --dry-run to inspect the plan.\n",
      );
    }
    return serverReady;
  };
  for (const session of discovered) {
    if (only.size && !only.has(session.id)) continue;
    if (opts.limit && results.length >= opts.limit) break;
    const plan = await describeSession(session, baseCfg);
    if (plan.action === "skip") {
      const skipped = {
        sessionId: session.id,
        action: "skip",
        status: plan.reason,
        reason: plan.reason,
        agents: plan.agents,
        wireBytes: plan.wireBytes,
        delivered: 0,
        queued: 0,
        committed: 0,
        open: 0,
        blocked: [],
        ovSessionIds: [],
      };
      results.push(skipped);
      if (!opts.json) printSessionResult(skipped);
      continue;
    }
    if (!(await ensureServer())) return 2;
    try {
      const result = await backfillSession(session, {
        baseCfg,
        timeoutMs: opts.timeoutMs,
        logError: (stage, error) => process.stderr.write(`[backfill] ${session.id}/${stage}: ${error?.message || error}\n`),
      });
      results.push(result);
      if (!opts.json) printSessionResult(result);
      if (result.action !== "skip" && result.status && !SKIP_STATUSES.has(result.status) && result.status !== "ok" && result.status !== "open_tail_pending") {
        failures += 1;
      }
    } catch (error) {
      failures += 1;
      results.push({ sessionId: session.id, action: "error", status: "exception", error: error?.message || String(error) });
      process.stderr.write(`[backfill] ${session.id}: ${error?.message || error}\n`);
    }
  }

  if (opts.json) {
    process.stdout.write(`${JSON.stringify({ sessions: results, missing, failures }, null, 2)}\n`);
  } else {
    if (missing.length) process.stdout.write(`not found: ${missing.join(", ")}\n`);
    const imported = results.filter((result) => result.action === "import").length;
    const caughtUp = results.filter((result) => result.action === "catch_up").length;
    const skipped = results.filter((result) => result.action === "skip").length;
    process.stdout.write(`\n${imported} imported, ${caughtUp} caught up, ${skipped} skipped, ${failures} failed\n`);
  }
  return failures ? 1 : 0;
}

main().then((code) => {
  // Let the process drain stdout; process.exit() can truncate a piped report.
  process.exitCode = code;
}).catch((error) => {
  process.stderr.write(`[backfill] ${error?.message || error}\n`);
  process.exitCode = 1;
});
