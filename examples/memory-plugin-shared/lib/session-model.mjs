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
  const haystacks = [sessionId, cwd].filter(Boolean);
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
