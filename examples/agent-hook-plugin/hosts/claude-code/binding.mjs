/**
 * Which Claude Code session the MCP proxy is serving right now.
 *
 * Claude Code starts the proxy once per process and gives it the session id of
 * that moment (CLAUDE_CODE_SESSION_ID); after /clear or /resume the
 * conversation continues under another id while the proxy lives on. So every
 * SessionStart records the current id under the Claude Code process id: hooks
 * see it as CLAUDE_PID, the proxy as its parent pid.
 *
 * The proxy trusts a record only when it is from its own lifetime: it names the
 * session the proxy was started in, or it was written after the proxy started.
 * A file left by an earlier process with the same pid reads as "cannot tell".
 * Records untouched for a week are removed whenever a new one is written.
 */

import { readdirSync, statSync, unlinkSync } from "node:fs";
import { join } from "node:path";

import { readJsonState, STATE_DIR, writeJsonState } from "./state.mjs";

const BINDING_FILE = /^cc-session-\d+\.json$/;
const STALE_AFTER_MS = 7 * 24 * 60 * 60 * 1000;

const bindingName = (pid) => `cc-session-${pid}.json`;

function removeStaleBindings(now = Date.now()) {
  let names;
  try {
    names = readdirSync(STATE_DIR);
  } catch {
    return;
  }
  for (const name of names) {
    if (!BINDING_FILE.test(name)) continue;
    try {
      const path = join(STATE_DIR, name);
      if (now - statSync(path).mtimeMs > STALE_AFTER_MS) unlinkSync(path);
    } catch { /* gone already */ }
  }
}

function validPid(value) {
  const pid = String(value ?? "").trim();
  return /^[1-9]\d*$/.test(pid) ? pid : "";
}

/** Record `sessionId` as the current session of this Claude Code process. */
export function bindCurrentSession(sessionId, env = process.env) {
  const pid = validPid(env.CLAUDE_PID);
  const id = String(sessionId || "").trim();
  if (!pid || !id) return false;
  writeJsonState(bindingName(pid), { session_id: id });
  removeStaleBindings();
  return true;
}

/** The native id of Claude Code process `pid`'s current session, or "" when it cannot be told. */
export function currentSessionId({ pid, startedAt, env = process.env } = {}) {
  const key = validPid(pid);
  if (!key) return "";
  const binding = readJsonState(bindingName(key));
  const id = typeof binding?.session_id === "string" ? binding.session_id.trim() : "";
  if (!id) return "";
  const startSession = String(env.CLAUDE_CODE_SESSION_ID || "").trim();
  const fresh = typeof binding.ts === "number" && binding.ts >= startedAt;
  return id === startSession || fresh ? id : "";
}
