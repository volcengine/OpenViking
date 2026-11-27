/**
 * Shared OpenViking session-id helpers for memory plugin harnesses.
 */

/**
 * Glob -> RegExp. Minimal implementation: supports `*`, `**`, and literals.
 */
function globToRe(glob) {
  let re = "^";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") { re += ".*"; i++; }
      else re += "[^/]*";
    } else if (/[.+?^${}()|[\]\\]/.test(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  re += "$";
  return new RegExp(re);
}

export function isBypassed(cfg, { sessionId, cwd } = {}) {
  if (cfg.bypassSession) return true;
  const patterns = cfg.bypassSessionPatterns || [];
  if (patterns.length === 0) return false;
  // Windows hands hooks `C:\work\scratch`, while directory patterns are written
  // with forward slashes.
  const slashCwd = typeof cwd === "string" && cwd.includes("\\") ? cwd.replace(/\\/g, "/") : "";
  const haystacks = [sessionId, cwd, slashCwd].filter(Boolean);
  for (const pat of patterns) {
    const re = globToRe(pat);
    if (haystacks.some((h) => re.test(h))) return true;
  }
  return false;
}

function safeId(value, replacement = "_") {
  return String(value || "unknown").replace(/[^A-Za-z0-9._-]/g, replacement);
}

export function deriveHarnessSessionId(prefix, sessionId, suffix = "") {
  if (!prefix || typeof prefix !== "string") {
    throw new Error("deriveHarnessSessionId requires a non-empty prefix");
  }
  if (!sessionId || typeof sessionId !== "string") {
    throw new Error("deriveHarnessSessionId requires a non-empty sessionId");
  }
  const base = `${prefix}${sessionId}`;
  if (!suffix) return base;
  const normalized = String(suffix).replace(/:/g, "-").replace(/[^A-Za-z0-9._-]/g, "-");
  return `${base}__${normalized}`;
}

export function deriveCodexSessionId(codexSessionId) {
  return `cx-${safeId(codexSessionId, "_")}`;
}

/** Whether a compaction commits the session first (`autoCommitOnCompact`). */
export function commitsOnCompaction(cfg) {
  return cfg?.autoCommitOnCompact !== false;
}

// The shortest idle timeout the server accepts; 0 turns the idle commit off.
const MIN_IDLE_COMMIT_SECONDS = 300;

/**
 * With `serverAutoCommit`, whether OpenViking commits the session at the token
 * threshold by itself: its auto-commit policy is the one local config asks for.
 *
 * A session without that policy is given it and the answer is still false. The
 * server checks the threshold when a message is written, not when the policy
 * arrives, so the turn just written stays the plugin's to commit. A server that
 * refuses the policy never shows it, which leaves every commit with the plugin.
 */
async function serverCommitsSession(fetchJSON, sessionId, cfg, meta) {
  if (!cfg.serverAutoCommit) return false;
  // Every field is sent: the server merges a policy onto the stored one and
  // fills a new one from its own defaults. The idle commit only runs where the
  // server has memory.session_auto_commit.enabled.
  const idle = Number(cfg.commitIdleSeconds) || 0;
  const policy = {
    pending_token_threshold: cfg.commitTokenThreshold,
    message_count_threshold: 0,
    idle_timeout_seconds: idle > 0 ? Math.max(MIN_IDLE_COMMIT_SECONDS, idle) : 0,
    keep_recent_count: 0,
    min_commit_interval_seconds: 0,
  };
  const current = meta?.auto_commit_policy;
  if (current && Object.keys(policy).every((key) => current[key] === policy[key])) return true;
  await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/config`, {
    method: "PATCH",
    body: JSON.stringify({ auto_commit_policy: policy }),
  });
  return false;
}

/**
 * Whether the plugin commits the session now: its pending tokens reached
 * `commitTokenThreshold`, and `serverAutoCommit` has not left that commit to
 * OpenViking. `meta` is the session as GET /sessions/{id} returned it.
 */
export async function thresholdCommitDue(fetchJSON, sessionId, cfg, meta) {
  if (await serverCommitsSession(fetchJSON, sessionId, cfg, meta)) return false;
  return Number(meta?.pending_tokens || 0) >= cfg.commitTokenThreshold;
}
