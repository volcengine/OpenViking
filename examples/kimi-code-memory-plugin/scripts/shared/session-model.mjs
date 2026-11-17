// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Shared OpenViking session-id helpers for memory plugin harnesses.
 */

/**
 * Glob -> RegExp. Minimal implementation: supports `*`, `**`, and literals.
 */
function globToRe(glob) {
  // A trailing `/**` means "this directory and everything under it", which is
  // what the documented bypass examples rely on: `/tmp/**` must match `/tmp`
  // itself, or a user standing in the directory they asked to bypass is let
  // through.
  const source = String(glob);
  const trailingAny = source.endsWith("/**");
  const body = trailingAny ? source.slice(0, -3) : source;
  let re = "^";
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (c === "*") {
      if (body[i + 1] === "*") { re += ".*"; i++; }
      else re += "[^/]*";
    } else if (/[.+?^${}()|[\]\\]/.test(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  if (trailingAny) re += "(?:/.*)?";
  re += "$";
  return new RegExp(re);
}

/** Does one bypass pattern match one directory? Exposed for the tests. */
export function globMatchesCwd(pattern, cwd) {
  return globToRe(pattern).test(String(cwd || ""));
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
