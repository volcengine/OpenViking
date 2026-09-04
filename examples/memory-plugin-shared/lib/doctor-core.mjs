/**
 * Harness-agnostic building blocks for the memory plugin doctor scripts.
 *
 * Each harness ships a thin `ov-memory-doctor.mjs` entrypoint that knows its
 * own install layout (where the plugin is registered, which hooks file, which
 * state directory) and delegates everything that is the same everywhere to
 * this module: config-file inspection, API key display, base URL linting,
 * environment sweep, server probes and their interpretation, state/log
 * inspection, and report rendering.
 *
 * Nothing here reads harness config; callers pass a resolved connection
 * `{ baseUrl, apiKey, account, user, peerId, userAgent }`.
 */

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { resolveWorkspaceSettings } from "./plugin-config.mjs";
import { peerScopeMemoPath } from "./recall-core.mjs";
import { CONFIG_DIR_NAME, LOCAL_FILE, TEAM_FILE, workspaceConfigPaths } from "./workspace-config.mjs";
import { findWorkspaceRoot, resolveWorkspaceIdentity } from "./workspace-identity.mjs";
import { entryPath } from "./workspace-registry.mjs";

/**
 * The snippet every surface quotes verbatim — this report, the docs and the
 * skills an agent reads — so whoever follows any of them writes the same file.
 * One line so it fits a table cell and a report line unchanged.
 */
export const WORKSPACE_PEER_HINT = '{"version": 1, "peer": {"id": "my-project"}}';

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function homeShort(path) {
  const home = homedir();
  if (!path) return path;
  return path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}

export function fmtAge(ts) {
  if (!ts) return "never";
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 0) return "just now";
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function fmtBytes(n) {
  if (n == null) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function str(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

const LEVEL_ORDER = { fail: 0, warn: 1, info: 2, ok: 3 };
const LEVEL_MARK = { ok: "✓", info: "·", warn: "⚠", fail: "✗" };
const LEVEL_COLOR = { ok: "32", info: "2", warn: "33", fail: "31" };

export function createReport({ color = false } = {}) {
  const sections = [];
  let current = null;

  function paint(code, text) {
    return color ? `\x1b[${code}m${text}\x1b[0m` : text;
  }

  function section(title) {
    current = { title, findings: [] };
    sections.push(current);
    return current;
  }

  function add(level, title, detail, fix) {
    if (!current) section("General");
    const finding = { level, title };
    if (detail) finding.detail = String(detail);
    if (fix) finding.fix = String(fix);
    current.findings.push(finding);
    return finding;
  }

  const report = {
    sections,
    section,
    add,
    ok: (title, detail) => add("ok", title, detail),
    info: (title, detail) => add("info", title, detail),
    warn: (title, detail, fix) => add("warn", title, detail, fix),
    fail: (title, detail, fix) => add("fail", title, detail, fix),
    problems() {
      return sections.flatMap((s) => s.findings.filter((f) => f.level === "fail" || f.level === "warn"));
    },
    exitCode() {
      return sections.some((s) => s.findings.some((f) => f.level === "fail")) ? 1 : 0;
    },
    render() {
      const lines = [];
      for (const s of sections) {
        lines.push(paint("1", `== ${s.title} ==`));
        for (const f of s.findings) {
          lines.push(`  ${paint(LEVEL_COLOR[f.level], LEVEL_MARK[f.level])} ${f.title}`);
          if (f.detail) for (const l of String(f.detail).split("\n")) lines.push(`      ${l}`);
          if (f.fix && (f.level === "warn" || f.level === "fail")) lines.push(`      ${paint("36", "fix:")} ${f.fix}`);
        }
        lines.push("");
      }
      const problems = report.problems().sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level]);
      lines.push(paint("1", "== Summary =="));
      if (!problems.length) {
        lines.push(`  ${paint("32", "✓")} no problems found`);
      } else {
        const fails = problems.filter((p) => p.level === "fail").length;
        lines.push(`  ${fails} failure(s), ${problems.length - fails} warning(s)`);
        for (const p of problems) {
          lines.push(`  ${paint(LEVEL_COLOR[p.level], LEVEL_MARK[p.level])} ${p.title}${p.fix ? ` → ${p.fix}` : ""}`);
        }
      }
      return lines.join("\n");
    },
    toJSON() {
      return { sections };
    },
  };
  return report;
}

// ---------------------------------------------------------------------------
// API key display
// ---------------------------------------------------------------------------

export function decodeBase64Url(segment) {
  if (typeof segment !== "string" || !segment || /[^A-Za-z0-9_-]/.test(segment)) return null;
  try {
    const padded = segment + "=".repeat((4 - (segment.length % 4)) % 4);
    const text = Buffer.from(padded, "base64url").toString("utf-8");
    // Reject decoded text that is not printable — a random string that happens
    // to be valid base64 decodes to binary garbage.
    if (!text || /[^\x20-\x7e]/.test(text)) return null;
    return text;
  } catch {
    return null;
  }
}

function headTail(value, keep = 4) {
  if (!value) return "";
  if (value.length <= keep * 2 + 2) return "*".repeat(Math.max(4, value.length));
  return `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

/**
 * Describe an API key for display without revealing it.
 *
 *   three-segment key  →  account=<decoded> user=<decoded> secret=abcd…wxyz
 *   single-segment key →  abcd…wxyz (64 chars, legacy format)
 *
 * The three-segment format is base64url(account).base64url(user).base64url(secret);
 * the first two segments carry no secret and are decoded so the operator can see
 * which identity the key claims. Also reports shape problems that commonly come
 * from copy/paste mistakes.
 */
export function describeApiKey(rawKey) {
  const info = { present: false, format: "none", display: "(none)", account: "", user: "", length: 0, problems: [] };
  if (typeof rawKey !== "string" || !rawKey) return info;

  info.present = true;
  info.length = rawKey.length;
  if (rawKey !== rawKey.trim()) info.problems.push("has leading/trailing whitespace");
  if (/[\r\n]/.test(rawKey)) info.problems.push("contains a line break");
  if (/^["']|["']$/.test(rawKey.trim())) info.problems.push("wrapped in quotes");
  if (/^bearer\s+/i.test(rawKey.trim())) info.problems.push("starts with 'Bearer ' — store only the key itself");

  const key = rawKey.trim().replace(/^bearer\s+/i, "").replace(/^["']|["']$/g, "");
  const parts = key.split(".");
  if (parts.length === 3) {
    info.format = "v2";
    const account = decodeBase64Url(parts[0]);
    const user = decodeBase64Url(parts[1]);
    info.account = account || "";
    info.user = user || "";
    const accountText = account ?? `${headTail(parts[0])} (not base64url)`;
    const userText = user ?? `${headTail(parts[1])} (not base64url)`;
    if (account === null || user === null) info.problems.push("account/user segment is not decodable base64url — key may be truncated or mangled");
    if (!parts[2]) info.problems.push("secret segment is empty");
    info.display = `account=${accountText} user=${userText} secret=${headTail(parts[2])}`;
  } else {
    info.format = "legacy";
    if (parts.length !== 1) info.problems.push(`unexpected shape: ${parts.length} dot-separated segments (expected 1 or 3)`);
    info.display = `${headTail(key)} (${key.length} chars, ${parts.length === 1 ? "legacy single-segment" : "unrecognised"} format)`;
  }
  return info;
}

/** Mask any secret-looking env value for display. */
export function maskSecret(value) {
  return headTail(String(value ?? ""), 4);
}

// ---------------------------------------------------------------------------
// Base URL lint
// ---------------------------------------------------------------------------

export function lintBaseUrl(url) {
  const problems = [];
  const value = String(url ?? "");
  if (!value.trim()) {
    problems.push({ level: "fail", message: "base URL is empty", fix: "set url in ovcli.conf or OPENVIKING_URL" });
    return problems;
  }
  if (value !== value.trim() || /\s/.test(value)) {
    problems.push({ level: "fail", message: "base URL contains whitespace", fix: "remove stray spaces/newlines around the url value" });
  }
  if (!/^https?:\/\//i.test(value.trim())) {
    problems.push({ level: "fail", message: "base URL has no http:// or https:// scheme — fetch() rejects it as 'Failed to parse URL'", fix: "prefix the url with http:// or https://" });
    return problems;
  }
  let parsed;
  try {
    parsed = new URL(value.trim());
  } catch {
    problems.push({ level: "fail", message: "base URL does not parse", fix: "check the url value for typos" });
    return problems;
  }
  const path = parsed.pathname.replace(/\/+$/, "");
  if (/\/api(\/v1)?$/i.test(path)) {
    problems.push({ level: "fail", message: `base URL ends with ${path} — every request becomes ${path}/api/v1/... and 404s`, fix: "drop the /api/v1 suffix; the plugin appends API paths itself" });
  }
  if (/\/mcp$/i.test(path)) {
    problems.push({ level: "fail", message: "base URL ends with /mcp — the plugin appends /mcp itself", fix: "use the server root URL, not the MCP endpoint" });
  }
  if (parsed.hostname === "0.0.0.0") {
    problems.push({ level: "warn", message: "host 0.0.0.0 is a bind address, not a destination", fix: "use 127.0.0.1 or the real hostname" });
  }
  if (parsed.search || parsed.hash) {
    problems.push({ level: "warn", message: "base URL carries a query string or fragment", fix: "remove everything after the path" });
  }
  return problems;
}

export function isLoopbackUrl(url) {
  try {
    const host = new URL(url).hostname;
    return host === "127.0.0.1" || host === "localhost" || host === "::1" || host === "[::1]";
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Files and environment
// ---------------------------------------------------------------------------

export function inspectJsonFile(path) {
  const out = { path, exists: false, ok: false, error: "", data: null, mode: "", size: 0, mtimeMs: 0 };
  if (!path) return out;
  let st;
  try {
    st = statSync(path);
  } catch {
    return out;
  }
  out.exists = true;
  out.size = st.size;
  out.mtimeMs = st.mtimeMs;
  out.mode = (st.mode & 0o777).toString(8);
  let raw;
  try {
    raw = readFileSync(path, "utf-8");
  } catch (err) {
    out.error = `unreadable: ${err?.message || err}`;
    return out;
  }
  try {
    out.data = JSON.parse(raw);
    out.ok = out.data !== null && typeof out.data === "object";
    if (!out.ok) out.error = "top level is not a JSON object";
  } catch (err) {
    out.error = `invalid JSON: ${err?.message || err}`;
  }
  return out;
}

const KNOWN_OVCLI_KEYS = new Set([
  "url", "api_key", "root_api_key", "account", "user", "account_id", "user_id",
  "actor_peer_id", "peer_id", "agent_id", "timeout", "output", "echo_command",
  "extra_headers", "gateway_token", "auth_mode", "ldap_username", "ldap_password", "plugin",
]);

/** Keys in ovcli.conf that neither the CLI nor the plugins read — usually typos. */
export function unknownOvcliKeys(data) {
  if (!data || typeof data !== "object") return [];
  return Object.keys(data).filter((k) => !KNOWN_OVCLI_KEYS.has(k));
}

/**
 * Every knob a harness loader reads out of `ovcli.conf`'s `plugin` section.
 *
 * `plugin` is on the allowlist above, which used to mean everything inside it
 * went unchecked — so `peerSorce` sat there doing nothing with no complaint.
 * `plugin-known-keys.test.mjs` derives this set from the two loaders and fails
 * when they diverge, so it cannot rot into a list that rejects a real knob.
 */
export const KNOWN_PLUGIN_KEYS = new Set([
  "apiKey", "accountId", "userId", "auth_mode", "authMode", "enabled", "debug", "timeoutMs",
  "peerId", "peer_id", "peerSource", "workspacePeer",
  "autoRecall", "autoCapture", "noAutoInject", "writePathAsync", "minQueryLength",
  "bypassSessionPatterns",
  "recallLimit", "recallMaxTokens", "recallMaxContentChars", "recallTokenBudget",
  "recallPeerScope", "recallDedupTurns", "recallQueryExpansion", "recallPreferAbstract",
  "recallRewrite", "recallContextTimeoutMs", "recallTimeoutMs", "scoreThreshold",
  "recallCompress", "recallCompressMaxBullets", "recallCompressMaxInputChars",
  "recallCompressMinInputChars", "recallCompressBaseUrl", "recallCompressModel",
  "recallCompressThinking", "recallCompressReasoningEffort", "recallCompressTimeoutMs",
  "recallCompressDetectOnStartup", "recallCompressDetectTimeoutMs", "recallCompressDetectTtlMs",
  "captureMode", "captureMaxLength", "captureTimeoutMs", "captureToolMaxChars",
  "captureAssistantTurns", "captureLastAssistantOnStop", "logRankingDetails",
  "commitTokenThreshold", "commitKeepRecentCount", "autoCommitOnCompact",
  "profileTokenBudget", "resumeContextBudget", "resumeArchiveInject",
  "resumeArchiveMaxChars", "resumeArchiveTokenBudget",
  "skillExperience", "skillExperienceLimit",
]);

/** Nested objects in `plugin` are per-harness overrides, not knobs. */
const PLUGIN_HARNESS_KEYS = new Set(["claude_code", "codex"]);

function nearestKnownKey(key) {
  // Levenshtein would be overkill; a typo that matters is almost always one
  // edit away, and case-folding alone catches the most common one.
  const folded = key.toLowerCase();
  for (const known of KNOWN_PLUGIN_KEYS) {
    if (known.toLowerCase() === folded) return known;
  }
  for (const known of KNOWN_PLUGIN_KEYS) {
    if (known.length >= 4 && (known.toLowerCase().startsWith(folded.slice(0, 5)) || folded.startsWith(known.toLowerCase().slice(0, 5)))) {
      return known;
    }
  }
  return "";
}

/**
 * Misspelled knobs inside `plugin`, and inside each per-harness override.
 * Each result carries the closest real key so the fix is one glance away.
 */
export function unknownPluginKeys(plugin) {
  if (!plugin || typeof plugin !== "object" || Array.isArray(plugin)) return [];
  const found = [];
  const walk = (object, prefix) => {
    for (const [key, value] of Object.entries(object)) {
      if (!prefix && PLUGIN_HARNESS_KEYS.has(key)) {
        if (value && typeof value === "object" && !Array.isArray(value)) walk(value, `${key}.`);
        continue;
      }
      if (KNOWN_PLUGIN_KEYS.has(key)) continue;
      found.push({ key: `plugin.${prefix}${key}`, suggestion: nearestKnownKey(key) });
    }
  };
  walk(plugin, "");
  return found;
}

const SECRET_ENV = /KEY|TOKEN|SECRET|PASSWORD/i;

export function collectEnv(env = process.env) {
  const openviking = [];
  for (const name of Object.keys(env).sort()) {
    if (!name.startsWith("OPENVIKING_")) continue;
    const value = env[name] ?? "";
    openviking.push({ name, value: SECRET_ENV.test(name) ? maskSecret(value) : value });
  }
  const proxy = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
    .filter((name) => str(env[name]))
    .map((name) => ({ name, value: env[name] }));
  const node = ["NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED", "NODE_USE_ENV_PROXY", "NODE_OPTIONS"]
    .filter((name) => str(env[name]))
    .map((name) => ({ name, value: env[name] }));
  return { openviking, proxy, node };
}

/** Legacy rc-file wrapper blocks written by pre-stdio-proxy installers. */
export function scanRcFiles(markers, home = homedir()) {
  const hits = [];
  for (const file of [".zshrc", ".bashrc", ".zprofile", ".bash_profile", ".profile"]) {
    const path = `${home}/${file}`;
    let text;
    try {
      text = readFileSync(path, "utf-8");
    } catch {
      continue;
    }
    for (const marker of markers) {
      if (text.includes(marker)) hits.push({ file: homeShort(path), kind: "block", detail: marker });
    }
    const exported = [...new Set([...text.matchAll(/^\s*export\s+(OPENVIKING_[A-Z0-9_]+)=/gm)].map((m) => m[1]))];
    if (exported.length) hits.push({ file: homeShort(path), kind: "export", detail: exported.join(", "), vars: exported });
  }
  return hits;
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

export function runCommand(command, args = [], { timeoutMs = 10000, env = process.env } = {}) {
  try {
    const stdout = execFileSync(command, args, {
      encoding: "utf-8",
      timeout: timeoutMs,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { ok: true, stdout: String(stdout).trim(), stderr: "" };
  } catch (err) {
    return {
      ok: false,
      stdout: String(err?.stdout ?? "").trim(),
      stderr: String(err?.stderr ?? "").trim(),
      error: err?.code === "ENOENT" ? "not found" : (err?.killed ? `timed out after ${timeoutMs}ms` : (err?.message || String(err))),
    };
  }
}

export function whichCommand(name) {
  const result = runCommand(process.platform === "win32" ? "where" : "which", [name], { timeoutMs: 5000 });
  return result.ok ? result.stdout.split("\n")[0] : "";
}

export function parseNodeMajor(version) {
  const m = /^v?(\d+)/.exec(String(version || ""));
  return m ? Number(m[1]) : NaN;
}

// ---------------------------------------------------------------------------
// HTTP probes
// ---------------------------------------------------------------------------

export function classifyFetchError(err) {
  const cause = err?.cause || err;
  const code = cause?.code || "";
  const message = String(cause?.message || err?.message || err || "");
  if (err?.name === "AbortError" || /aborted/i.test(message)) {
    return { kind: "timeout", hint: "no response within the timeout — wrong host/port, firewall, or a very slow server" };
  }
  if (code === "ECONNREFUSED") return { kind: "refused", hint: "connection refused — nothing is listening on that host:port (server down or wrong port)" };
  if (code === "ENOTFOUND" || code === "EAI_AGAIN") return { kind: "dns", hint: "hostname does not resolve — typo in the url, or DNS/VPN not available" };
  if (code === "ECONNRESET" || code === "EPIPE") return { kind: "reset", hint: "connection reset — a proxy or TLS terminator closed the connection" };
  if (/certificate|CERT_|SELF_SIGNED|UNABLE_TO_VERIFY|ssl|tls/i.test(`${code} ${message}`)) {
    return { kind: "tls", hint: "TLS verification failed — private CA or self-signed cert; Node ignores the OS keychain, set NODE_EXTRA_CA_CERTS for the harness process" };
  }
  if (/Failed to parse URL|Invalid URL/i.test(message)) return { kind: "url", hint: "the url does not parse — check scheme and host" };
  if (/wrong version number|EPROTO/i.test(`${code} ${message}`)) return { kind: "tls", hint: "protocol mismatch — https:// against a plain-HTTP port (or vice versa)" };
  return { kind: "other", hint: message };
}

export async function httpProbe({ url, method = "GET", headers = {}, body, timeoutMs = 5000 }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const t0 = Date.now();
  const out = { url, method, ok: false, status: 0, latencyMs: 0, text: "", json: null, headers: {}, error: "", errorKind: "", errorHint: "" };
  try {
    const res = await fetch(url, { method, headers, body, signal: controller.signal, redirect: "manual" });
    out.status = res.status;
    out.ok = res.ok;
    out.latencyMs = Date.now() - t0;
    for (const [k, v] of res.headers.entries()) out.headers[k.toLowerCase()] = v;
    const text = await res.text().catch(() => "");
    out.text = text.length > 4000 ? `${text.slice(0, 4000)}…` : text;
    const contentType = out.headers["content-type"] || "";
    if (contentType.includes("text/event-stream")) {
      // Streamable HTTP frames: take the first data: line.
      const line = text.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        try { out.json = JSON.parse(line.slice(5).trim()); } catch { /* keep text */ }
      }
    } else {
      try { out.json = JSON.parse(text); } catch { /* not JSON */ }
    }
  } catch (err) {
    out.latencyMs = Date.now() - t0;
    const cls = classifyFetchError(err);
    out.error = err?.cause?.message || err?.message || String(err);
    out.errorKind = cls.kind;
    out.errorHint = cls.hint;
  } finally {
    clearTimeout(timer);
  }
  return out;
}

function authHeaders(conn, { identity = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (conn.apiKey) headers["Authorization"] = `Bearer ${conn.apiKey}`;
  if (identity && conn.account) headers["X-OpenViking-Account"] = conn.account;
  if (identity && conn.user) headers["X-OpenViking-User"] = conn.user;
  if (conn.peerId) headers["X-OpenViking-Actor-Peer"] = conn.peerId;
  if (conn.userAgent) headers["User-Agent"] = conn.userAgent;
  return headers;
}

export function serverErrorMessage(probe) {
  const body = probe?.json;
  if (!body) return probe?.text ? probe.text.replace(/\s+/g, " ").slice(0, 200) : "";
  return body?.error?.message || body?.error?.code || body?.message || body?.detail || "";
}

/**
 * Run the standard probe ladder against an OpenViking server.
 *
 *   health        GET /health without credentials  — reachability, version, auth_mode
 *   healthAuth    GET /health with credentials     — identity echo (account/user/role)
 *   systemStatus  GET /api/v1/system/status        — first authenticated call the hooks make; real 401/403
 *   fsLs          GET /api/v1/fs/ls?uri=viking://~/memories — tenant-data authorization + resolved user space
 *   mcp           POST /mcp tools/list             — what the MCP proxy forwards
 */
export async function probeOpenViking(conn, { timeoutMs = 5000, mcp = true } = {}) {
  const base = String(conn.baseUrl || "").replace(/\/+$/, "");
  const out = {};
  out.health = await httpProbe({ url: `${base}/health`, timeoutMs, headers: conn.userAgent ? { "User-Agent": conn.userAgent } : {} });
  if (out.health.error && out.health.errorKind !== "other") {
    // Hard network failure: the rest of the ladder would fail identically.
    return out;
  }
  if (conn.apiKey || conn.account || conn.user) {
    out.healthAuth = await httpProbe({ url: `${base}/health`, timeoutMs, headers: authHeaders(conn) });
  }
  out.systemStatus = await httpProbe({ url: `${base}/api/v1/system/status`, timeoutMs, headers: authHeaders(conn) });
  out.fsLs = await httpProbe({
    url: `${base}/api/v1/fs/ls?uri=${encodeURIComponent("viking://~/memories")}&output=original`,
    timeoutMs,
    headers: authHeaders(conn),
  });
  if (mcp) {
    out.mcp = await httpProbe({
      url: `${base}/mcp`,
      method: "POST",
      timeoutMs: Math.max(timeoutMs, 8000),
      headers: { ...authHeaders(conn), Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }),
    });
  }
  return out;
}

/**
 * Turn probe results into findings. `keyInfo` is describeApiKey(conn.apiKey).
 * Returns { authMode, version, identity } for callers that want to cross-check.
 */
export function assessProbes(report, probes, conn, keyInfo) {
  const summary = { authMode: "", version: "", identity: null, reachable: false, authOk: false };
  const health = probes.health;
  if (!health) return summary;

  if (health.error) {
    report.fail(`server unreachable: ${conn.baseUrl}`, `${health.errorKind || "error"}: ${health.error}\n${health.errorHint}`,
      "check that the server is running and the url/port are right; compare with `curl -sS <url>/health`");
    return summary;
  }
  summary.reachable = true;
  if (health.status === 404) {
    report.fail(`GET /health → 404 at ${conn.baseUrl}`, "the url points at a web server, but not at an OpenViking API root",
      "check for a missing path prefix (OpenViking Cloud needs /openviking) or a reverse proxy that does not forward /health");
    return summary;
  }
  if (health.status >= 300 && health.status < 400) {
    report.fail(`GET /health → ${health.status} redirect`, `location: ${health.headers.location || "?"}`,
      "the url is being redirected (http→https, or an SSO/login gateway); use the final url");
    return summary;
  }
  if (health.status === 503 && health.json?.status === "pending_initialization") {
    const fixes = Array.isArray(health.json.fix) ? health.json.fix.map((f) => `- ${f}`).join("\n") : "";
    report.fail("the OpenViking docker container is up but has no ov.conf — every request answers 503 until it does",
      `${health.json.error || ""}${health.json.config_file ? ` (expected at ${health.json.config_file} inside the container)` : ""}${fixes ? `\n${fixes}` : ""}`,
      "mount ~/.openviking (holding ov.conf) at /app/.openviking, or run `docker exec -it openviking openviking-server init`");
    return summary;
  }
  if (!health.ok || !health.json) {
    report.fail(`GET /health → ${health.status}`, serverErrorMessage(health) || "(non-JSON body)",
      "the server answered but not like OpenViking — verify the url reaches the OpenViking API, not a proxy error page");
    return summary;
  }
  summary.authMode = String(health.json.auth_mode || "");
  summary.version = String(health.json.version || "");
  report.ok(`server reachable — ${health.latencyMs}ms, version ${summary.version || "?"}, auth_mode=${summary.authMode || "?"}`);
  if (health.latencyMs > 1000) {
    report.warn(`/health took ${health.latencyMs}ms`, "the statusline probe has a 1s budget and will flap between 'slow' and 'offline'",
      "check network latency to the server; raise OPENVIKING_TIMEOUT_MS if hooks time out");
  }

  const identity = probes.healthAuth?.json;
  if (identity && identity.role) {
    summary.identity = { account: identity.account_id, user: identity.user_id, role: identity.role };
    summary.authOk = true;
    report.ok(`credentials accepted — account=${identity.account_id} user=${identity.user_id} role=${identity.role}`);
    if (identity.role === "root") {
      report.warn("using the ROOT api key", "root keys are rejected on tenant-data APIs and /mcp in api_key mode (403 'ROOT API keys cannot access tenant-scoped data APIs')",
        "create a user key (POST /api/v1/admin/accounts/<account>/users with the root key) and put that in ovcli.conf");
    }
    if (summary.authMode === "api_key" && keyInfo?.format === "v2") {
      if (keyInfo.account && keyInfo.account !== identity.account_id) {
        report.warn(`key claims account=${keyInfo.account} but server resolved account=${identity.account_id}`);
      }
    }
    if (summary.authMode === "api_key" && (conn.account || conn.user)) {
      const mismatch = (conn.account && conn.account !== identity.account_id) || (conn.user && conn.user !== identity.user_id);
      if (mismatch) {
        report.warn(`configured account/user (${conn.account || "-"}/${conn.user || "-"}) differ from the key's identity (${identity.account_id}/${identity.user_id})`,
          "in api_key mode the server ignores X-OpenViking-Account/User headers; the key decides where data lands",
          "remove account/user from the config, or use a key issued for that account/user");
      } else {
        report.info("account/user in config are redundant in api_key mode (identity comes from the key)");
      }
    }
  } else if (summary.authMode === "dev") {
    summary.authOk = true;
    report.info("server runs in dev auth mode — any key is accepted; identity comes from X-OpenViking-Account/User headers (default 'default')");
    if (identity?.account_id) summary.identity = { account: identity.account_id, user: identity.user_id, role: identity.role };
  } else if (conn.apiKey) {
    report.fail("api key rejected", `/health answered 200 but returned no identity for the key (${keyInfo?.display || "?"}) — the key is invalid, revoked, or belongs to another deployment`,
      "check the key in ovcli.conf / OPENVIKING_API_KEY against the one issued by the server admin");
  } else if (summary.authMode === "api_key") {
    report.fail("no api key configured but the server requires one (auth_mode=api_key)", "",
      "set api_key in ~/.openviking/ovcli.conf or OPENVIKING_API_KEY");
  } else if (summary.authMode === "trusted") {
    if (!conn.account || !conn.user) {
      report.fail("server is in trusted mode and needs account + user", "requests without X-OpenViking-Account/User are rejected with HTTP 400",
        "set account and user in ovcli.conf (or OPENVIKING_ACCOUNT / OPENVIKING_USER)");
    } else {
      summary.authOk = true;
      report.ok(`trusted mode — identity from headers account=${conn.account} user=${conn.user}`);
    }
  }

  const status = probes.systemStatus;
  if (status) {
    if (status.ok) {
      const user = status.json?.result?.user;
      report.ok(`GET /api/v1/system/status → 200${user ? ` (user space: ${user})` : ""}`);
    } else if (status.error) {
      report.fail("GET /api/v1/system/status failed", `${status.errorKind}: ${status.error}`);
    } else {
      const msg = serverErrorMessage(status);
      const level = status.status === 401 || status.status === 403 ? "fail" : "warn";
      report[level](`GET /api/v1/system/status → ${status.status}`, msg,
        status.status === 401 ? "the server rejected the credentials — this is what every hook hits after the /health gate passes"
          : status.status === 403 ? "the key is valid but not allowed here — wrong role or root key" : "");
    }
  }

  const fs = probes.fsLs;
  if (fs) {
    if (fs.ok) {
      const first = Array.isArray(fs.json?.result) ? fs.json.result.find((e) => e?.uri) : null;
      const space = first?.uri ? String(first.uri).replace(/\/memories\/.*$/, "/memories") : "";
      report.ok(`GET /api/v1/fs/ls viking://~/memories → 200${space ? ` (${space})` : ""}`);
    } else if (!fs.error) {
      const msg = serverErrorMessage(fs);
      if (fs.status === 403 && /ROOT/i.test(msg)) {
        report.fail("tenant data API rejects this key (root key)", msg, "use a user/admin key, not server.root_api_key");
      } else if (fs.status === 400) {
        report.warn(`GET /api/v1/fs/ls viking://~/memories → 400`, msg,
          /Trusted mode/i.test(msg) ? "set account/user for trusted mode" : "the server may be too old for the viking://~ home alias — upgrade the server");
      } else if (fs.status === 404) {
        report.info("viking://~/memories does not exist yet (nothing captured for this user so far)");
      } else if (fs.status !== 401) {
        report.warn(`GET /api/v1/fs/ls viking://~/memories → ${fs.status}`, msg);
      }
    }
  }

  const mcp = probes.mcp;
  if (mcp) {
    if (mcp.ok && mcp.json?.result?.tools) {
      report.ok(`POST /mcp tools/list → ${mcp.json.result.tools.length} tools`);
    } else if (mcp.error) {
      report.fail("POST /mcp failed", `${mcp.errorKind}: ${mcp.error}`);
    } else {
      const msg = serverErrorMessage(mcp) || mcp.text.replace(/\s+/g, " ").slice(0, 160);
      const hints = {
        401: "credentials rejected on /mcp",
        403: "key valid but not allowed on /mcp (root key in api_key mode)",
        404: "no MCP endpoint at <url>/mcp — missing path prefix, old server, or a reverse proxy that does not forward /mcp",
        406: "server did not accept the Accept header — something between client and server rewrites headers",
        502: "reverse proxy cannot reach the upstream",
        504: "reverse proxy timed out (SSE needs proxy_buffering off)",
      };
      report.fail(`POST /mcp tools/list → ${mcp.status}`, msg, hints[mcp.status] || "");
    }
  }
  return summary;
}

// ---------------------------------------------------------------------------
// State and logs
// ---------------------------------------------------------------------------

export function readStateFiles(stateDir, names) {
  const out = {};
  for (const name of names) {
    const path = `${stateDir}/${name}`;
    const entry = { path, exists: false, ageMs: null, data: null, error: "" };
    try {
      const st = statSync(path);
      entry.exists = true;
      entry.mtimeMs = st.mtimeMs;
      const data = JSON.parse(readFileSync(path, "utf-8"));
      entry.data = data;
      entry.ageMs = typeof data?.ts === "number" ? Date.now() - data.ts : Date.now() - st.mtimeMs;
    } catch (err) {
      if (entry.exists) entry.error = err?.message || String(err);
    }
    out[name] = entry;
  }
  return out;
}

/**
 * `peer_scope: "actor"` narrows recall to this workspace's own peer. A server
 * that predates the field rejects it, and the plugin retries without it — so
 * recall quietly runs against every peer under the user instead. `postRecall`
 * records that in a state file; without this the widening is invisible.
 */
export function lintPeerScopeDowngrade(path = peerScopeMemoPath(), now = Date.now()) {
  let data;
  try {
    data = JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return [];
  }
  if (!data?.legacyUntil || Number(data.legacyUntil) <= now) return [];
  return [{
    level: "warn",
    message: `recall peer_scope "${str(data.scope, "actor")}" was rejected by the server (HTTP ${Number(data.status) || 0})`,
    detail: "recall runs against every peer under this user, not just this workspace's",
    fix: 'upgrade the OpenViking server, or set recallPeerScope to "all" so the wider search is deliberate',
  }];
}

/**
 * `.openviking/` is also the parser's scratch directory, and the reflex is to
 * ignore the whole thing — which silently stops a team's `config.json` from
 * ever being committed. Catch the bare rule; narrower ones are fine.
 */
export function gitignoreHidesWorkspaceConfig(root) {
  for (const name of [".gitignore", join(".git", "info", "exclude")]) {
    let text;
    try {
      text = readFileSync(join(root, name), "utf-8");
    } catch {
      continue;
    }
    for (const line of text.split("\n")) {
      const rule = line.trim().replace(/\/$/, "");
      if (rule === CONFIG_DIR_NAME || rule === `/${CONFIG_DIR_NAME}` || rule === `**/${CONFIG_DIR_NAME}`) {
        return name;
      }
    }
  }
  return "";
}

/**
 * Where every workspace-scoped setting came from, and what it covered up.
 *
 * Three languages read this configuration and each could drift; per-key
 * provenance is how a user finds out which layer actually won, the way
 * `git config --show-origin --show-scope` does.
 */
export function checkWorkspace(report, { cwd = process.cwd(), env = process.env } = {}) {
  report.section("Workspace");

  const { root, rootKind, git } = findWorkspaceRoot(cwd, env);
  const summary = { root, rootKind, kind: git?.kind || "", files: [], settings: {}, provenance: {} };
  if (!root) {
    report.info(`not a workspace: ${homeShort(cwd)} is in no git repository and has no ${CONFIG_DIR_NAME}/${TEAM_FILE} above it`);
    report.info("memories here carry no workspace peer and go to your user-level space; ovcli.conf and env still apply");
    report.info(`to give this directory its own memory, create ${CONFIG_DIR_NAME}/${TEAM_FILE} here with ${WORKSPACE_PEER_HINT}`);
    return summary;
  }
  const foundBy = rootKind === "git" ? git.kind : `${TEAM_FILE}${git ? ` inside ${git.kind}` : ""}`;
  report.ok(`workspace  ${homeShort(root)}  ← ${foundBy}`);

  const resolved = resolveWorkspaceSettings(cwd, env);
  summary.settings = resolved.settings;
  summary.provenance = resolved.provenance;
  const identity = resolveWorkspaceIdentity({ cwd, env });
  const registryPath = entryPath(root, env, identity);

  for (const { layer, path } of [
    ...workspaceConfigPaths(root),
    { layer: "registry", path: registryPath },
  ]) {
    const info = fileInfo(path);
    summary.files.push({ layer, path, exists: info.exists });
    report.info(`${layer.padEnd(26)} ${homeShort(path)}${info.exists ? "" : "  (absent)"}`);
  }

  const keys = Object.keys(resolved.provenance || {}).sort();
  if (!keys.length) {
    report.info("no workspace layer sets anything — every value comes from ovcli.conf, ov.conf or the environment");
  }
  for (const key of keys) {
    const entry = resolved.provenance[key];
    const shadowed = entry.shadowed.map((s) => `${JSON.stringify(s.value)} from ${s.source}`).join(", ");
    report.info(
      `${key} = ${JSON.stringify(entry.value)}  ← ${entry.source}`,
      shadowed ? `shadows ${shadowed}` : "",
    );
  }

  for (const warning of resolved.warnings || []) report.warn(warning);
  for (const { key, value, source } of resolved.announced || []) {
    report.warn(
      `${source} sets ${key} = ${JSON.stringify(value)}`,
      "a committed workspace file is trusted without a prompt, so what it changes is announced",
      `override it in ${LOCAL_FILE} or in ${homeShort(registryPath)}`,
    );
  }

  const ignoredBy = gitignoreHidesWorkspaceConfig(root);
  if (ignoredBy) {
    report.warn(
      `${ignoredBy} ignores all of ${CONFIG_DIR_NAME}/`,
      `${TEAM_FILE} is meant to be committed; the blanket rule stops it from ever being added`,
      `narrow the rule to ${CONFIG_DIR_NAME}/media/ and ${CONFIG_DIR_NAME}/downloads/, and ignore ${CONFIG_DIR_NAME}/${LOCAL_FILE}`,
    );
  }
  return summary;
}

export function countDirEntries(dir, filter = () => true) {
  try {
    return readdirSync(dir).filter(filter).length;
  } catch {
    return null;
  }
}

export function fileInfo(path) {
  try {
    const st = statSync(path);
    return { exists: true, size: st.size, mtimeMs: st.mtimeMs };
  } catch {
    return { exists: false, size: 0, mtimeMs: 0 };
  }
}

/**
 * Scan a JSONL hook log. Returns the last `maxErrors` error entries, the last
 * mcp-proxy start record, and which hooks have written anything.
 */
export function scanDebugLog(path, { maxErrors = 5, maxBytes = 512 * 1024 } = {}) {
  const out = { path, exists: false, size: 0, mtimeMs: 0, lines: 0, errors: [], proxyStart: null, hooks: [] };
  const info = fileInfo(path);
  if (!info.exists) return out;
  Object.assign(out, { exists: true, size: info.size, mtimeMs: info.mtimeMs });
  let text;
  try {
    text = readFileSync(path, "utf-8");
  } catch {
    return out;
  }
  if (text.length > maxBytes) text = text.slice(-maxBytes);
  const hooks = new Set();
  const errors = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    out.lines += 1;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    if (entry.hook) hooks.add(entry.hook);
    if (entry.hook === "mcp-proxy" && entry.stage === "start") out.proxyStart = entry;
    if (entry.error) {
      errors.push({ ts: entry.ts, hook: entry.hook, stage: entry.stage, message: entry.error?.message || String(entry.error) });
    }
  }
  // Only errors from the last day of logging are "recent"; older ones are
  // history that predates whatever the user is debugging now.
  const cutoff = out.mtimeMs - 24 * 60 * 60 * 1000;
  out.errors = errors.slice(-maxErrors);
  out.recentErrors = errors.filter((e) => Date.parse(e.ts || "") >= cutoff).slice(-maxErrors);
  out.hooks = [...hooks].sort();
  return out;
}

export function existsPath(path) {
  return Boolean(path) && existsSync(path);
}

// ---------------------------------------------------------------------------
// Server health
//
// `GET /ready` is the server's own verdict on its subsystems and works
// against any deployment. The port and ov.conf checks only apply when the
// server runs on this machine (loopback url). Everything else server-side —
// config validation, live embedding probe, native engine, disk — is
// `openviking-server doctor`, which runs in the server's Python environment.
// ---------------------------------------------------------------------------

const PLUGIN_ONLY_OV_CONF_KEYS = { claude_code: "ovcli.conf plugin.claude_code", codex: "ovcli.conf plugin.codex" };
const READY_OK_VALUES = new Set(["ok", "not_configured", "not_supported"]);
const READY_FIXES = {
  embedding: "the server cannot embed with the configured provider — check embedding.dense.{provider,model,api_key,api_base} in ov.conf; `openviking-server doctor` shows the provider's reply",
  vectordb: "the vector index is unhealthy — check <storage.workspace>/vectordb and the server log; stop duplicate servers on the same workspace",
  agfs: "the content filesystem failed — check <storage.workspace>/viking, free disk space and the server log",
  api_key_manager: "the api key store failed to load — check <storage.workspace>/viking/_system and the server log",
  ollama: "start Ollama (or fix its host/port in ov.conf) — the server cannot reach it",
};

function isObject(v) {
  return Boolean(v) && typeof v === "object" && !Array.isArray(v);
}

/**
 * ov.conf keys that only the plugins read. The server validates its config
 * strictly and refuses to start on them, which shows up as "the server is
 * gone since I edited ov.conf". Returns [{ level, message, detail, fix }].
 */
export function lintServerConf(data) {
  const out = [];
  if (!isObject(data)) return out;
  for (const [key, dest] of Object.entries(PLUGIN_ONLY_OV_CONF_KEYS)) {
    if (!(key in data)) continue;
    out.push({
      level: "warn",
      message: `ov.conf has a top-level '${key}' block, which the server rejects at startup ("Unknown config field '${key}'")`,
      detail: "the plugin reads it, openviking-server does not; a server that is already running is unaffected until its next restart",
      fix: `move those settings to ${dest} (or OPENVIKING_* env) and delete the block; if this ov.conf never starts a server, ignore`,
    });
  }
  if (isObject(data.server) && "url" in data.server) {
    out.push({
      level: "warn",
      message: "ov.conf server.url is rejected by the server at startup (\"Extra inputs are not permitted\")",
      detail: "only the plugin and the CLI read server.url; the server validates its section strictly",
      fix: "put the url in ovcli.conf (url) and remove server.url; if this ov.conf never starts a server, ignore",
    });
  }
  return out;
}

/** Who listens on a TCP port (lsof on macOS/Linux, ss on Linux). */
export function findPortListener(port) {
  const out = { tool: "", pid: null, command: "", error: "" };
  if (process.platform === "win32") {
    out.error = "not supported on Windows";
    return out;
  }
  if (whichCommand("lsof")) {
    out.tool = "lsof";
    const r = runCommand("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-F", "pc"], { timeoutMs: 8000 });
    for (const line of r.stdout.split("\n")) {
      if (line.startsWith("p") && out.pid === null) out.pid = Number(line.slice(1));
      else if (line.startsWith("c") && !out.command) out.command = line.slice(1);
    }
    return out;
  }
  if (whichCommand("ss")) {
    out.tool = "ss";
    const r = runCommand("ss", ["-ltnpH", `sport = :${port}`], { timeoutMs: 8000 });
    const m = r.stdout.match(/users:\(\("([^"]+)",pid=(\d+)/);
    if (m) {
      out.command = m[1];
      out.pid = Number(m[2]);
    } else if (r.stdout.trim()) {
      out.command = "(owner not visible without root)";
    }
    return out;
  }
  out.error = "neither lsof nor ss is available";
  return out;
}

export async function probeReady(baseUrl, { timeoutMs = 15000 } = {}) {
  const base = String(baseUrl || "").replace(/\/+$/, "");
  return httpProbe({ url: `${base}/ready`, timeoutMs: Math.max(timeoutMs, 15000) });
}

/** Flatten one `/ready` check (string or {status, checks}) into { ok, text }. */
export function readyCheckState(value) {
  if (isObject(value)) {
    const status = String(value.status ?? "");
    const nested = isObject(value.checks) ? Object.entries(value.checks).map(([k, v]) => [k, readyCheckState(v)]) : [];
    const failed = nested.filter(([, s]) => !s.ok);
    return {
      ok: READY_OK_VALUES.has(status) && failed.length === 0,
      text: failed.length ? `${status} (${failed.map(([k, s]) => `${k}: ${s.text}`).join(", ")})` : status,
    };
  }
  const text = String(value ?? "");
  return { ok: READY_OK_VALUES.has(text), text };
}

/** Turn the `/ready` probe into findings. Returns { ready, checks } (ready null when unknown). */
export function assessReady(report, probe) {
  const out = { ready: null, checks: {} };
  if (!probe) return out;
  if (probe.error) {
    if (probe.errorKind === "timeout") report.warn(`GET /ready timed out after ${probe.latencyMs}ms`, "the readiness check embeds one token with the configured provider (10s cap) — a timeout usually means that provider hangs", "run `openviking-server doctor` in the server's environment to see the provider's reply");
    else report.info(`GET /ready failed: ${probe.error}`);
    return out;
  }
  if (probe.status === 404) {
    report.info("server has no /ready endpoint (older version) — subsystem status unavailable over HTTP", "`openviking-server doctor` in the server's environment covers embedding/VLM/storage");
    return out;
  }
  const json = probe.json;
  if (!isObject(json)) {
    report.info(`GET /ready → ${probe.status} without a JSON body`);
    return out;
  }
  if (probe.status === 503 && json.status === "not_ready" && json.reason === "initializing" && !isObject(json.checks)) {
    out.ready = false;
    report.warn("server is still initializing (503 /ready)", "storage and the embedder are being set up; the first start of a local embedding model downloads it", "wait and rerun; if it never finishes, read the server log");
    return out;
  }
  if (!isObject(json.checks)) {
    report.info(`GET /ready → ${probe.status}: ${JSON.stringify(json).slice(0, 200)}`);
    return out;
  }
  const states = Object.entries(json.checks).map(([k, v]) => [k, readyCheckState(v)]);
  out.checks = Object.fromEntries(states.map(([k, s]) => [k, s.text]));
  const failed = states.filter(([, s]) => !s.ok);
  out.ready = failed.length === 0 && probe.ok;
  const summary = states.map(([k, s]) => `${k} ${s.text}`).join(", ");
  if (out.ready) {
    report.ok(`/ready: ${summary}`);
  } else {
    for (const [key, s] of failed) report.fail(`/ready: ${key} → ${s.text}`, "", READY_FIXES[key] || "see the server log");
    if (!failed.length) report.warn(`/ready → ${probe.status}: ${summary}`, "", "read the server log");
  }
  return out;
}

function urlHostPort(baseUrl) {
  try {
    const u = new URL(baseUrl);
    return { host: u.host, port: Number(u.port || (u.protocol === "https:" ? 443 : 80)) };
  } catch {
    return null;
  }
}

/**
 * The "Server health" section. `health` is the /health probe already taken
 * for the Connection section; `ovConf` is inspectJsonFile(<ov.conf path>).
 */
export async function checkServerHealth(report, { baseUrl, ovConf, health, offline = false, timeoutMs = 5000 } = {}) {
  report.section("Server health");
  const answered = Boolean(health && !health.error && health.ok && isObject(health.json));
  const summary = { local: isLoopbackUrl(baseUrl), reachable: answered, ready: null, listener: null };
  const target = urlHostPort(baseUrl);

  if (!summary.local) {
    report.info(`server at ${target?.host || baseUrl} is not on this machine — only /ready is probed; \`openviking-server doctor\` on the server host covers the rest`);
  } else {
    if (ovConf?.ok) {
      for (const f of lintServerConf(ovConf.data)) report[f.level](f.message, f.detail, f.fix);
    }
    const listener = findPortListener(target.port);
    if (listener.pid) {
      summary.listener = { pid: listener.pid, command: listener.command };
      report.info(`port ${target.port} is served by pid ${listener.pid} (${listener.command})`);
    } else if (listener.error) {
      report.info(`could not determine who listens on port ${target.port} (${listener.error})`);
    } else if (!answered) {
      report.fail(`nothing listens on port ${target.port} — the server is not running`, "",
        "start it: `openviking-server` (first time: `openviking-server init`), or `docker start openviking` for a container install; a startup error is printed right there");
    }
    report.info("provider-level checks (embedding probe, VLM key, native engine, disk): `openviking-server doctor` in the server's own environment");
  }

  if (offline) report.info("skipped /ready (--offline)");
  else if (health?.json?.status === "pending_initialization") report.info("skipped /ready — the container has no config yet");
  else if (!answered) report.info("skipped /ready — the server did not answer /health");
  else summary.ready = assessReady(report, await probeReady(baseUrl, { timeoutMs })).ready;
  return summary;
}
