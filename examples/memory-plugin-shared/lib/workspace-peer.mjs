import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { realpathSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// Git invocation guard: never throw, never hang. Returns "" on any failure
// (git missing, not a git repo, no remote, timeout, ...). The plugin must
// degrade gracefully to a path-derived peer rather than crash the host.
const GIT_TIMEOUT_MS = 2000;

function gitOutput(args, cwd) {
  try {
    const out = execFileSync("git", args, {
      cwd: String(cwd || "."),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: GIT_TIMEOUT_MS,
    });
    return String(out || "").trim();
  } catch {
    return "";
  }
}

/**
 * Strip trailing slash, ".git" suffix, query, fragment, and surrounding
 * whitespace from a remote URL path component.
 */
function stripRemoteSuffix(p) {
  let s = String(p || "").trim();
  s = s.split(/[?#]/)[0];
  s = s.replace(/\.git$/, "");
  s = s.replace(/\/+$/, "");
  return s;
}

// Scheme default ports. Only these are stripped from the authority; any other
// explicit port is significant and distinguishes distinct repositories served
// from the same host/path (issue #3516 requires different repos not collide).
const DEFAULT_PORTS = { ssh: 22, http: 80, https: 443, git: 9418, rsync: 873 };

/**
 * Strip the scheme-default port from a lowercased "host" or "host:port"
 * authority. Non-default ports are preserved so two services at the same
 * host/path on different non-default ports produce different peer ids.
 */
function stripDefaultPort(hostPort, scheme) {
  const m = hostPort.match(/^(.+):(\d+)$/);
  if (!m) return hostPort;
  const def = DEFAULT_PORTS[scheme];
  return def !== undefined && Number(m[2]) === def ? m[1] : hostPort;
}

/**
 * Normalize a git remote URL to a canonical "host/owner/repo" string.
 *
 * Handles SSH (scp-like and ssh://), HTTPS, and git:// forms. Strips
 * credentials, ports, query, fragment, ".git" suffix, and trailing slashes.
 * SSH and HTTPS URLs for the same repository normalize to the same string.
 *
 * Returns "" for local paths (file://, /abs/path, relative) or anything that
 * does not look like a remote host/path pair — callers fall back to a local
 * repo identity or the absolute path slug in that case.
 */
export function normalizeGitRemoteUrl(raw) {
  if (typeof raw !== "string") return "";
  let url = raw.trim();
  if (!url) return "";

  // Scheme URL: ssh://, https://, http://, git://, file://, ...
  const schemeMatch = url.match(/^([A-Za-z][A-Za-z0-9+.\-]*):\/\/(.+)$/);
  if (schemeMatch) {
    const scheme = schemeMatch[1].toLowerCase();
    let rest = schemeMatch[2];
    // Strip query / fragment first (they apply to the whole URL).
    rest = rest.split(/[?#]/)[0];
    const slash = rest.indexOf("/");
    const authority = slash === -1 ? rest : rest.slice(0, slash);
    const path = slash === -1 ? "" : rest.slice(slash + 1);
    // Strip userinfo ONLY within the authority segment — a "@" later in the path
    // (e.g. ".../org/repo/@v1.0" or ".../@scope/repo") is NOT a credential marker
    // and must not corrupt host detection.
    const at = authority.lastIndexOf("@");
    const hostPort = (at === -1 ? authority : authority.slice(at + 1)).toLowerCase();
    if (!hostPort) return "";
    // Strip ONLY the scheme-default port. Non-default ports are significant:
    // distinct services on the same host/path (e.g. an https host on :8443 vs
    // :9443) are different repositories and must NOT collide (issue #3516).
    const host = stripDefaultPort(hostPort, scheme);
    const cleanPath = stripRemoteSuffix(path);
    if (!host || !cleanPath) return "";
    return `${host}/${cleanPath}`;
  }

  // SSH scp-like: [user@]host.x:path
  // Must have a host with a dot (or be a known short host) and a colon
  // followed by a path, and not look like a Windows drive ("C:\...").
  if (!url.startsWith("file:") && !/^[A-Za-z]:[\\/]/.test(url)) {
    const scp = url.match(/^([^/@\s]+@)?([A-Za-z0-9][\w.\-]+):(.+)$/);
    if (scp) {
      const host = scp[2].toLowerCase();
      const cleanPath = stripRemoteSuffix(scp[3]);
      if (!cleanPath) return "";
      return `${host}/${cleanPath}`;
    }
  }

  // Local path or unrecognized form — no canonical remote identity.
  return "";
}

/**
 * Build a single-segment OpenViking peer id from a canonical remote string.
 *
 * Format: `git-<slug>-<hash8>` where slug is the lowercased canonical form
 * (non-[a-z0-9] → "-", capped to 48 chars) and hash8 is the first 8 hex chars
 * of sha256(canonical). Stable across SSH/HTTPS, never contains credentials
 * (creds are stripped before canonicalization). Only contains [a-z0-9-].
 */
export function peerIdFromCanonical(canonical) {
  const c = String(canonical || "").trim().toLowerCase();
  if (!c) return "";
  const hash = createHash("sha256").update(c).digest("hex").slice(0, 8);
  let slug = c.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (slug.length > 48) slug = slug.slice(0, 48).replace(/-+$/, "");
  if (!slug) return `git-${hash}`;
  return `git-${slug}-${hash}`;
}

/**
 * Stable peer id for a local-only git clone (no usable remote). Hashes the
 * absolute git common dir, which is shared by the main checkout and all of
 * its linked worktrees, so the same clone resolves to one peer id regardless
 * of which worktree cwd is passed.
 */
function peerIdFromLocalRepo(commonDir) {
  const hash = createHash("sha256").update(String(commonDir)).digest("hex").slice(0, 12);
  return `git-local-${hash}`;
}

function deriveGitCanonical(cwd) {
  const url = gitOutput(["-C", cwd, "config", "--get", "remote.origin.url"], cwd);
  if (!url) return "";
  return normalizeGitRemoteUrl(url);
}

function deriveGitCommonDir(cwd) {
  const cd = gitOutput(["-C", cwd, "rev-parse", "--git-common-dir"], cwd);
  if (!cd) return "";
  // git-common-dir may be relative (e.g. ".git" in the main checkout) or
  // absolute (in a linked worktree). Modern git also canonicalizes symlinks
  // in the worktree case (e.g. /tmp -> /private/tmp on macOS), so we resolve
  // both the cwd-relative join AND symlinks via realpath to guarantee the
  // main checkout and every worktree hash to the same local-repo identity.
  const resolved = resolvePath(String(cwd), cd);
  try {
    return realpathSync(resolved);
  } catch {
    return resolved;
  }
}

/**
 * Legacy fallback for non-git directories: slug the absolute path exactly like
 * the previous implementation (every non-letter-or-digit char becomes "-").
 */
function pathSlug(p) {
  return String(p || "").replace(/[^A-Za-z0-9]/g, "-");
}

/**
 * Derive a default workspace peer id for the given cwd.
 *
 * Resolution order:
 *   1. Canonical remote URL of the enclosing git repo (origin), so the main
 *      checkout and every linked worktree of the same remote resolve to one
 *      peer. SSH and HTTPS forms of the same remote collapse together.
 *   2. In a git repo with no usable remote: stable local-repo identity
 *      derived from the git common dir (still shared across linked worktrees).
 *   3. Otherwise (non-git directory): the absolute path slug.
 *
 * Returns "" only when cwd is empty.
 */
export function deriveWorkspacePeerId(cwd) {
  const path = String(cwd || "");
  if (!path) return "";

  const canonical = deriveGitCanonical(path);
  if (canonical) return peerIdFromCanonical(canonical);

  const commonDir = deriveGitCommonDir(path);
  if (commonDir) return peerIdFromLocalRepo(commonDir);

  return pathSlug(path);
}

export function resolveEffectivePeerId({ cfg = {}, cwd = "" } = {}) {
  const explicit = String(cfg.peerId || "").trim();
  if (explicit) return { peerId: explicit, source: "explicit" };

  if (cfg.workspacePeer !== false) {
    const peerId = deriveWorkspacePeerId(cwd);
    if (peerId) return { peerId, source: "workspace" };
  }

  return { peerId: "", source: "none" };
}
