// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
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

export const DEFAULT_PROXY_TIMEOUT_MS = 15000;
const MIN_PROXY_TIMEOUT_MS = 1000;

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
 * workspace, so their process cwd is not a reliable peer identity. Broad
 * recall intentionally spans the authenticated user's peer workspaces and
 * therefore leaves the actor header unset. Actor-scoped recall requires an
 * explicit peer so isolation never depends on the proxy launch directory.
 */
export function resolveMcpActorPeerId({
  peerId = "",
  recallPeerScope = "all",
} = {}) {
  if (recallPeerScope !== "actor") return "";

  const explicitPeerId = String(peerId || "").trim();
  if (explicitPeerId) return explicitPeerId;

  throw new Error(
    "OpenViking MCP actor-scoped recall requires an explicit peer ID. "
    + "Set actor_peer_id in ovcli.conf or OPENVIKING_PEER_ID in the MCP environment.",
  );
}

function uniq(values) {
  return [...new Set(values.filter(Boolean))];
}

/**
 * Shape a harness's loaded config into the object `createOpenVikingMcpProxy`
 * expects.
 *
 * `mcpUrl` wins over `baseUrl` when both are given, so a harness that lets the
 * user pin an explicit MCP URL keeps that behavior. `watchedPaths` are the
 * harness's own extra files; the shared defaults are appended and the result
 * deduplicated.
 */
export function buildMcpProxyConfig({
  baseUrl = "",
  mcpUrl = "",
  apiKey = "",
  account = "",
  user = "",
  peerId = "",
  userAgent = "",
  timeoutMs,
  debug = false,
  debugLogPath = "",
  credentialSource = "auto",
  credentialPath = "",
  watchedPaths = [],
  env = process.env,
} = {}) {
  return {
    mcpUrl: mcpUrl || `${trimSlash(baseUrl)}/mcp`,
    apiKey: apiKey || "",
    account: account || "",
    user: user || "",
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
  };
}
