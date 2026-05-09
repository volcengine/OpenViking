#!/usr/bin/env node

/**
 * Status report for the OpenViking memory plugin — invoked by the `/ov`
 * slash command. Prints a tight human-readable summary covering:
 *   - Server URL + /health probe
 *   - Resolved identity (account/user/agent)
 *   - Last session-start injection (size, age, audit path)
 *   - Last auto-recall (item count, top score, token budget use)
 *   - Toggle state for the three injection paths
 *   - Auth source — which file/env actually drove url + api_key, mirroring
 *     config.mjs's priority chain (env → ovcli.conf → ov.conf → default)
 *
 * Reads the same state files the statusline uses (~/.openviking/state/)
 * plus the audit file written by session-start.mjs.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { makeFetchJSON } from "./lib/ov-session.mjs";
import { readJsonState } from "./lib/state.mjs";

function expandHome(p) {
  return p ? resolvePath(p.replace(/^~(?=$|\/)/, homedir())) : p;
}

function tryReadJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function fmtBytes(n) {
  if (n == null) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtAge(ts) {
  if (!ts) return "never";
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 0) return "just now";
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function homeShort(path) {
  const h = homedir();
  return path && path.startsWith(h) ? "~" + path.slice(h.length) : path;
}

async function main() {
  if (!isPluginEnabled()) {
    console.log("OpenViking plugin: DISABLED (OPENVIKING_MEMORY_ENABLED=0 or no config found)");
    return;
  }

  const cfg = loadConfig();
  const fetchJSON = makeFetchJSON(cfg, "timeoutMs");

  // 1. Server / identity
  const t0 = Date.now();
  const health = await fetchJSON("/health");
  const latency = Date.now() - t0;
  console.log(`OpenViking — ${cfg.baseUrl}  (${health.ok ? "✓" : "✗"} /health ${latency}ms)`);
  console.log(
    `Identity: account=${cfg.accountId || "(unset)"}  ` +
    `user=${cfg.userId || "(server-resolved)"}  agent=${cfg.agentId}`,
  );
  console.log("");

  // 2. Last session-start injection
  const lastInjectPath = join(homedir(), ".openviking", "last_inject.md");
  let injSize = null, injMtime = null;
  try {
    const st = statSync(lastInjectPath);
    injSize = st.size; injMtime = st.mtimeMs;
  } catch { /* none yet */ }
  if (injMtime) {
    console.log(`Last session-start injection: ${fmtAge(injMtime)}, ${fmtBytes(injSize)}`);
    console.log(`  audit: ${homeShort(lastInjectPath)}`);
  } else {
    console.log("Last session-start injection: (none yet)");
  }

  // 3. Last auto-recall
  const recall = readJsonState("last-recall.json");
  if (recall) {
    const top = typeof recall.top_score === "number" ? recall.top_score.toFixed(2) : "?";
    const used = recall.tokens_used ?? 0;
    const budget = recall.tokens_budget ?? 0;
    console.log(
      `Last auto-recall: ${fmtAge(recall.ts)} — ${recall.count ?? 0} items, ` +
      `top ${top}, ${used}/${budget} tokens (${recall.reason || "ok"})`,
    );
  } else {
    console.log("Last auto-recall: (none yet)");
  }
  console.log("");

  // 4. Toggles
  console.log("Toggles:");
  console.log(`  auto-inject:  ${cfg.noAutoInject ? "OFF" : "on"}  (profile budget=${cfg.profileTokenBudget})`);
  console.log(`  auto-recall:  ${cfg.autoRecall ? "on" : "OFF"}  (recall budget=${cfg.recallTokenBudget})`);
  console.log(`  auto-capture: ${cfg.autoCapture ? "on" : "OFF"}`);
  if (cfg.bypassSession) console.log(`  ⚠ session bypass: GLOBAL ON`);
  if (cfg.bypassSessionPatterns?.length) {
    console.log(`  bypass patterns: ${cfg.bypassSessionPatterns.join(", ")}`);
  }
  console.log("");

  // 5. Auth source — which file/env actually drove each value, mirroring
  // config.mjs's priority chain (env → ovcli.conf → ov.conf → default).
  // Computed rather than file-listed so we never advertise a source that
  // isn't actually in play.
  const cliConfPath = expandHome(process.env.OPENVIKING_CLI_CONFIG_FILE
    || join(homedir(), ".openviking", "ovcli.conf"));
  const ovConfPath = expandHome(process.env.OPENVIKING_CONFIG_FILE
    || join(homedir(), ".openviking", "ov.conf"));
  const cliConf = tryReadJson(cliConfPath);
  const ovConf = tryReadJson(ovConfPath);
  const cliShort = homeShort(cliConfPath);
  const ovShort = homeShort(ovConfPath);

  const urlSrc = (process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL) ? "env"
    : (cliConf?.url) ? cliShort
    : (ovConf?.server?.url) ? ovShort
    : "default";
  const keySrc = (process.env.OPENVIKING_API_KEY || process.env.OPENVIKING_BEARER_TOKEN) ? "env"
    : (cliConf?.api_key) ? cliShort
    : (ovConf?.claude_code?.apiKey) ? ovShort
    : (ovConf?.server?.root_api_key) ? ovShort
    : "(none)";
  console.log(`Auth: url from ${urlSrc}, api_key from ${keySrc}`);
}

main().catch((err) => {
  console.error("ov-status failed:", err?.message || err);
  process.exit(1);
});
