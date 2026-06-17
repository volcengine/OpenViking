#!/usr/bin/env node

/**
 * Detached worker for committing an OpenViking session outside Codex hook
 * timeout budgets.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("commit-session");

function usage() {
  process.stderr.write("Usage: node scripts/commit-session.mjs --ov-session-id <id> [--reason <text>]\n");
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      usage();
      process.exit(0);
    } else if (arg === "--ov-session-id") {
      out.ovSessionId = argv[++i];
    } else if (arg === "--reason") {
      out.reason = argv[++i];
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!out.ovSessionId) throw new Error("--ov-session-id is required");
  return out;
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers.Authorization = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.peerId) headers["X-OpenViking-Actor-Peer"] = cfg.peerId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return null;
    if (!res.ok || body.status === "error") {
      throw new Error(`HTTP ${res.status}: ${JSON.stringify(body)}`);
    }
    return body.result ?? body;
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  log("start", { ovSessionId: args.ovSessionId, reason: args.reason || "" });
  const commit = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(args.ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
  log("commit", {
    ovSessionId: args.ovSessionId,
    reason: args.reason || "",
    archived: commit?.archived ?? false,
    taskId: commit?.task_id,
    status: commit?.status,
  });
}

main().catch((err) => {
  logError("uncaught", err);
  process.exitCode = 1;
});
