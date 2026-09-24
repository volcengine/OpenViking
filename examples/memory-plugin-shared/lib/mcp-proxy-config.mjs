/**
 * Shared config shaping for the stdio MCP proxy entrypoints.
 *
 * Every harness loads its credentials differently, but the shape
 * `createOpenVikingMcpProxy` consumes is identical everywhere, and so is the
 * set of files whose changes must trigger a credential reload. Keeping that
 * here stops each entrypoint from re-deriving `~` expansion, URL trimming,
 * timeout clamping, and the watch list.
 */

import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

import { CONNECTION_ENV_VARS, CREDENTIAL_ENV_VARS } from "./credentials.mjs";

export const DEFAULT_PROXY_TIMEOUT_MS = 15000;
const MIN_PROXY_TIMEOUT_MS = 1000;

/**
 * Every variable that changes what an MCP proxy sends. Hooks inherit their
 * host's whole environment; a host that hands its MCP servers an allowlist
 * instead (Codex's `env_vars`) has to name each of these, or the proxy
 * resolves a different connection from the hooks beside it.
 */
export const MCP_PROXY_ENV_VARS = [
  ...CONNECTION_ENV_VARS,
  "OPENVIKING_HOME",
  "OPENVIKING_STATE_DIR",
  "OPENVIKING_RECALL_PEER_SCOPE",
  "OPENVIKING_TIMEOUT_MS",
  "OPENVIKING_DEBUG",
  "OPENVIKING_DEBUG_LOG",
  "OPENVIKING_EXTRA_HEADERS",
];

/**
 * A resolved connection as the environment of a child proxy process.
 *
 * The other way across the process boundary, for a host that cannot hand its
 * MCP servers the inputs (DSH drops credential-shaped names from what a child
 * inherits): the parent resolves once and the child reads the answer back. The
 * forced `env` source makes that exact — the child reads no file, so a key the
 * parent left empty stays empty instead of falling through to
 * `server.root_api_key`. Every credential variable is written, the empty ones
 * too, because the rest of the parent's environment still reaches the child:
 * an `OPENVIKING_ACCOUNT` the parent's pinned chain ignored must not fill the
 * gap there.
 */
export function forwardConnectionEnv(connection) {
  const env = Object.fromEntries(CREDENTIAL_ENV_VARS.map((name) => [name, ""]));
  return {
    ...env,
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_URL: connection.baseUrl,
    OPENVIKING_MCP_URL: connection.mcpUrl,
    OPENVIKING_API_KEY: connection.apiKey || "",
    OPENVIKING_ACCOUNT: connection.account || "",
    OPENVIKING_USER: connection.user || "",
    OPENVIKING_PEER_ID: connection.peerId || "",
    OPENVIKING_AUTH_MODE: connection.authMode,
  };
}

export function trimSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

/** Expand a leading `~` and resolve to an absolute path; "" stays "". */
export function normalizeConfigPath(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw === "~") return homedir();
  if (raw.startsWith("~/")) return resolvePath(join(homedir(), raw.slice(2)));
  return resolvePath(raw);
}

/**
 * The credential files every harness must watch: the two `OPENVIKING_*_FILE`
 * overrides and the two default locations under `~/.openviking`.
 */
export function defaultCredentialPaths(env = process.env) {
  return [
    normalizeConfigPath(env.OPENVIKING_CLI_CONFIG_FILE),
    normalizeConfigPath(env.OPENVIKING_CONFIG_FILE),
    join(homedir(), ".openviking", "ovcli.conf"),
    join(homedir(), ".openviking", "ov.conf"),
  ].filter(Boolean);
}

/**
 * Resolve the actor peer header for a long-lived MCP proxy process.
 *
 * MCP servers may start in the plugin directory rather than the active
 * workspace, so their process cwd is not a reliable peer identity — and unlike
 * a hook, a long-lived proxy cannot re-derive one per turn. Broad recall
 * intentionally spans the authenticated user's peer workspaces and therefore
 * leaves the actor header unset. Actor-scoped recall needs an explicit peer so
 * isolation never depends on the proxy's launch directory.
 *
 * A missing one degrades to broad recall with a warning rather than refusing to
 * start: the proxy is what carries every memory tool, and taking those away
 * because a scope preference cannot be honoured costs the user far more than
 * the wider search does. Only the harness whose parent process injects the peer
 * (dsh, `mcp-env.mjs`) can satisfy this without the user setting it.
 */
export function resolveMcpActorPeerId({
  peerId = "",
  recallPeerScope = "all",
  onWarn = null,
} = {}) {
  if (recallPeerScope !== "actor") return "";

  const explicitPeerId = String(peerId || "").trim();
  if (explicitPeerId) return explicitPeerId;

  const message = "OpenViking MCP: actor-scoped recall needs an explicit peer id, which an MCP proxy cannot "
    + "derive from its launch directory. Falling back to broad recall across every peer under this user. "
    + "Set actor_peer_id in ovcli.conf, or OPENVIKING_PEER_ID in the MCP server environment, to scope it.";
  if (typeof onWarn === "function") onWarn(message);
  else process.stderr.write(`${message}\n`);
  return "";
}

function uniq(values) {
  return [...new Set(values.filter(Boolean))];
}

/**
 * Parse `OPENVIKING_EXTRA_HEADERS` into a plain string→string map.
 *
 * Some private deployments sit behind gateways that require custom headers
 * (a tenant/vault name, a signed token, a region hint) on every request. The
 * proxy has no other seam to inject those: the credential file's own
 * `extra_headers` field is intentionally not exported to MCP callers (see
 * `workspace-config.mjs` `FORBIDDEN_KEYS`), so this env-only escape hatch keeps
 * that boundary while letting an operator unblock a strict upstream.
 *
 * The value is a JSON object; anything else is ignored with a stderr warning
 * rather than crashing the proxy — a bad env var must not take down the whole
 * memory tool surface. Reserved names (`Content-Type`, `Accept`,
 * `MCP-Protocol-Version`, `Mcp-Session-Id`, `Authorization`) are dropped: they
 * are set by the proxy itself and letting the env override them would break
 * session negotiation or expose the wrong credential.
 */
const RESERVED_HEADER_NAMES = new Set([
  "content-type",
  "accept",
  "mcp-protocol-version",
  "mcp-session-id",
  "authorization",
]);

export function parseExtraHeaders(raw, { onWarn = null } = {}) {
  const source = String(raw || "").trim();
  if (!source) return {};
  const warn = (message) => {
    if (typeof onWarn === "function") onWarn(message);
    else process.stderr.write(`${message}\n`);
  };
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (err) {
    warn(`OPENVIKING_EXTRA_HEADERS is not valid JSON; ignoring (${err.message})`);
    return {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    warn("OPENVIKING_EXTRA_HEADERS must be a JSON object of string values; ignoring");
    return {};
  }
  const out = {};
  for (const [key, value] of Object.entries(parsed)) {
    const name = String(key || "").trim();
    if (!name) continue;
    if (RESERVED_HEADER_NAMES.has(name.toLowerCase())) {
      warn(`OPENVIKING_EXTRA_HEADERS: ignoring reserved header "${name}"`);
      continue;
    }
    if (value === null || value === undefined) continue;
    if (typeof value === "object") {
      warn(`OPENVIKING_EXTRA_HEADERS: header "${name}" must be a scalar; ignoring`);
      continue;
    }
    out[name] = String(value);
  }
  return out;
}

/**
 * Shape a harness's loaded config into the object `createOpenVikingMcpProxy`
 * expects.
 *
 * `mcpUrl` wins over `baseUrl` when both are given, so a harness that lets the
 * user pin an explicit MCP URL keeps that behavior. `watchedPaths` are the
 * harness's own extra files; the shared defaults are appended and the result
 * deduplicated. `sendIdentityHeaders` carries the harness's resolved auth mode
 * so an `api_key` server, which reads the identity out of the key and ignores
 * the headers, never sees the operator's account and user on the wire.
 */
export function buildMcpProxyConfig({
  baseUrl = "",
  mcpUrl = "",
  apiKey = "",
  account = "",
  user = "",
  sendIdentityHeaders = false,
  peerId = "",
  userAgent = "",
  timeoutMs,
  debug = false,
  debugLogPath = "",
  credentialSource = "auto",
  credentialPath = "",
  watchedPaths = [],
  extraHeaders = null,
  env = process.env,
} = {}) {
  const resolvedExtraHeaders = extraHeaders && typeof extraHeaders === "object" && !Array.isArray(extraHeaders)
    ? { ...extraHeaders }
    : parseExtraHeaders(env.OPENVIKING_EXTRA_HEADERS);
  return {
    mcpUrl: mcpUrl || `${trimSlash(baseUrl)}/mcp`,
    apiKey: apiKey || "",
    account: account || "",
    user: user || "",
    sendIdentityHeaders: sendIdentityHeaders === true,
    peerId: peerId || "",
    userAgent: userAgent || "",
    timeoutMs: Math.max(
      MIN_PROXY_TIMEOUT_MS,
      Number(timeoutMs) || DEFAULT_PROXY_TIMEOUT_MS,
    ),
    debug: debug === true,
    debugLogPath,
    credentialSource: credentialSource || "auto",
    credentialPath: credentialPath || "",
    watchedPaths: uniq([...watchedPaths, ...defaultCredentialPaths(env)]),
    extraHeaders: resolvedExtraHeaders,
  };
}

/**
 * The proxy config for a resolved harness config, mapped in one place.
 *
 * Every proxy used to copy fields out of its loader by hand, and the copies
 * drifted: two dropped `mcpUrl`, so `OPENVIKING_MCP_URL` reached the hooks and
 * not the tools. `cfg` is what `buildPluginConfig` or `buildProxyConnection`
 * returned. `peerId` replaces the actor peer only for a harness whose parent
 * process resolved it (dsh); everywhere else a long-lived proxy may send one
 * only when actor-scoped recall asks for it.
 */
export function toMcpProxyConfig(cfg, {
  env = process.env,
  peerId = undefined,
  debug = cfg.debug,
  debugLogPath = cfg.debugLogPath,
} = {}) {
  return buildMcpProxyConfig({
    baseUrl: cfg.baseUrl,
    mcpUrl: cfg.mcpUrl,
    apiKey: cfg.apiKey,
    account: cfg.account,
    user: cfg.user,
    sendIdentityHeaders: cfg.sendIdentityHeaders,
    peerId: peerId === undefined ? resolveMcpActorPeerId(cfg) : peerId,
    userAgent: cfg.userAgent,
    timeoutMs: cfg.timeoutMs,
    debug,
    debugLogPath,
    credentialSource: cfg.credentialSource,
    credentialPath: cfg.credentialPath || "",
    watchedPaths: [cfg.cliPath, cfg.ovPath],
    env,
  });
}
