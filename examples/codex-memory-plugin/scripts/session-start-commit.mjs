#!/usr/bin/env node

/**
 * SessionStart hook for Codex.
 *
 * The ONLY action this script takes is on `source === "clear"`:
 *   /clear in Codex creates a new session (with the new session_id in this
 *   payload) and orphans the previous in-memory transcript. We treat that
 *   as a deterministic "context is about to disappear" signal and commit
 *   any pending OpenViking sessions for *other* codex session_ids.
 *
 * For `source === "startup"` and `source === "resume"`, this hook is a
 * no-op. Codex re-fires SessionStart on short reconnects and resume, and
 * we don't want to commit during a still-active session.
 *
 * SessionStart output schema accepts {} as a no-op.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { clearState, listStates } from "./session-state.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("session-start");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
    if (cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return null;
    if (!res.ok || body.status === "error") return null;
    return body.result ?? body;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function countExtracted(commit) {
  if (!commit?.memories_extracted) return 0;
  if (typeof commit.memories_extracted === "number") return commit.memories_extracted;
  if (typeof commit.memories_extracted === "object") {
    return Object.values(commit.memories_extracted).reduce(
      (a, b) => a + (typeof b === "number" ? b : 0),
      0,
    );
  }
  return 0;
}

async function commitOvSession(ovSessionId) {
  if (!ovSessionId) return null;
  return fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

async function main() {
  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    noop();
    return;
  }

  const source = input.source || "unknown";
  const newSessionId = input.session_id || "unknown";
  log("start", { source, newSessionId });

  if (source !== "clear") {
    log("skip", { stage: "source_check", reason: `source=${source} (only 'clear' triggers commit)` });
    noop();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable; cannot commit");
    noop();
    return;
  }

  const states = await listStates();
  let committedCount = 0;
  let totalExtracted = 0;

  for (const s of states) {
    if (!s?.codexSessionId || s.codexSessionId === newSessionId) continue;
    if (s.ovSessionId) {
      const commit = await commitOvSession(s.ovSessionId);
      const extracted = countExtracted(commit);
      log("commit_orphan", {
        codexSessionId: s.codexSessionId,
        ovSessionId: s.ovSessionId,
        extracted,
      });
      totalExtracted += extracted;
    } else {
      log("clear_orphan_no_ov", { codexSessionId: s.codexSessionId });
    }
    await clearState(s.codexSessionId);
    committedCount += 1;
  }

  if (committedCount > 0) {
    log("done", { committedCount, totalExtracted });
    noop(
      `/clear: committed ${committedCount} prior OpenViking session(s), ${totalExtracted} memory item(s) extracted`,
    );
  } else {
    log("done", { committedCount: 0, totalExtracted: 0 });
    noop();
  }
}

main().catch((err) => { logError("uncaught", err); noop(); });
