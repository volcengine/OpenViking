import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_TIMEOUT_MS = 15000;
const MIN_TIMEOUT_MS = 1000;

function str(val, fallback = "") {
  if (typeof val === "string" && val.trim()) return val.trim();
  return fallback;
}

function normalizePath(value) {
  const raw = str(value, "");
  if (!raw) return "";
  if (raw === "~") return homedir();
  if (raw.startsWith("~/")) return resolvePath(join(homedir(), raw.slice(2)));
  return resolvePath(raw);
}

function tryLoadJson(path) {
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

/**
 * Build the User-Agent every harness plugin sends on OpenViking-bound requests.
 * Shape is `name/semver` so downstream stats layers can parse it as one token.
 */
export function buildUserAgent(harness, version) {
  return `openviking-memory-${harness}/${str(version, "") || "0.0.0"}`;
}

/**
 * Read a plugin manifest's `version` field. Accepts a path or a URL (so callers
 * can resolve relative to import.meta.url). Returns "" when unreadable so the
 * User-Agent falls back to 0.0.0 instead of throwing inside a short-lived hook.
 */
export function readManifestVersion(manifest) {
  try {
    return str(JSON.parse(readFileSync(manifest, "utf-8")).version, "");
  } catch {
    return "";
  }
}

function looksLikeOvcli(obj) {
  if (!obj || typeof obj !== "object") return false;
  if (obj.server && typeof obj.server === "object") return false;
  return Boolean(
    typeof obj.url === "string" ||
    typeof obj.api_key === "string" ||
    typeof obj.account === "string" ||
    typeof obj.account_id === "string" ||
    typeof obj.user === "string" ||
    typeof obj.user_id === "string" ||
    typeof obj.actor_peer_id === "string",
  );
}

function hasCredentialFields(obj) {
  if (!obj || typeof obj !== "object") return false;
  return [
    "url",
    "api_key",
    "account",
    "account_id",
    "user",
    "user_id",
    "actor_peer_id",
    "peer_id",
  ].some((key) => typeof obj[key] === "string");
}

export function loadCredentialFiles(env = process.env) {
  const cliPathCandidate = normalizePath(env.OPENVIKING_CLI_CONFIG_FILE) || DEFAULT_OVCLI_CONF_PATH;
  const ovPathCandidate = normalizePath(env.OPENVIKING_CONFIG_FILE) || DEFAULT_OV_CONF_PATH;
  const cliPathEnv = Boolean(str(env.OPENVIKING_CLI_CONFIG_FILE, ""));
  const ovPathEnv = Boolean(str(env.OPENVIKING_CONFIG_FILE, ""));

  let cliFile = tryLoadJson(cliPathCandidate);
  let cliPath = cliFile ? cliPathCandidate : "";
  let ovFile = tryLoadJson(ovPathCandidate);
  let ovPath = ovFile ? ovPathCandidate : "";

  // Backward compat: older plugin installs used OPENVIKING_CONFIG_FILE for
  // both ov.conf and ovcli.conf. Preserve that when the file is ovcli-shaped.
  if (ovPathEnv && !cliPathEnv && looksLikeOvcli(ovFile)) {
    cliFile = ovFile;
    cliPath = ovPath;
    ovFile = null;
    ovPath = "";
  }

  return {
    cliFile: cliFile || {},
    cliPath,
    cliPathCandidate,
    ovFile: ovFile || {},
    ovPath,
  };
}

/**
 * The environment variables that name part of the connection. Any one of them
 * set is what takes the chain off ovcli.conf in `auto` mode, so a variable
 * that only tunes how the chain runs must not be on this list: an
 * `OPENVIKING_AUTH_MODE` exported on its own would otherwise unpin the file.
 */
export const CREDENTIAL_ENV_VARS = [
  "OPENVIKING_URL",
  "OPENVIKING_BASE_URL",
  "OPENVIKING_MCP_URL",
  "OPENVIKING_BEARER_TOKEN",
  "OPENVIKING_API_KEY",
  "OPENVIKING_ACCOUNT",
  "OPENVIKING_USER",
  "OPENVIKING_PEER_ID",
];

/** Every environment variable `resolveConnection` reads. */
export const CONNECTION_ENV_VARS = [
  ...CREDENTIAL_ENV_VARS,
  "OPENVIKING_AUTH_MODE",
  "OPENVIKING_CREDENTIAL_SOURCE",
  "OPENVIKING_CREDENTIALS_SOURCE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_CONFIG_FILE",
];

function isSection(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/**
 * ov.conf spells its sections snake_case while a host may call itself
 * `claude-code` or `trae-cn`, so both spellings land on the same block. The
 * normalization stays inline rather than importing `config-schema.mjs`, which
 * the portable agent-plugins bundle would then have to ship as well.
 */
function sectionKey(harness) {
  return String(harness || "").trim().toLowerCase().replace(/-/g, "_");
}

/** The calling harness's own section of ov.conf. */
function harnessSection(ovFile, harness) {
  const section = ovFile[sectionKey(harness)];
  return isSection(section) ? section : {};
}

/**
 * ovcli.conf's `plugin` section as this harness sees it: the shared scalars,
 * then `plugin.<harness>` over them in both spellings, the hyphenated one last.
 * `loadPluginSettings` merges the same way for every other knob, and the two
 * must agree or a key would win in the hooks and lose here.
 */
function pluginSection(cliFile, harness) {
  const plugin = isSection(cliFile.plugin) ? cliFile.plugin : {};
  const merged = {};
  for (const [key, value] of Object.entries(plugin)) {
    if (!isSection(value)) merged[key] = value;
  }
  const key = sectionKey(harness);
  for (const candidate of [key, key.replace(/_/g, "-")]) {
    if (candidate && isSection(plugin[candidate])) Object.assign(merged, plugin[candidate]);
  }
  return merged;
}

/** A knob-file scalar as the schema coerces a string knob: `12345` is "12345". */
function scalar(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

const AUTH_MODES = ["trusted", "api_key"];

function normalizeAuthMode(value) {
  const mode = str(value, "").toLowerCase();
  return AUTH_MODES.includes(mode) ? mode : "";
}

function authModeIn(section) {
  return normalizeAuthMode(section.authMode) || normalizeAuthMode(section.auth_mode);
}

function sourceMode(env) {
  const raw = str(env.OPENVIKING_CREDENTIAL_SOURCE, str(env.OPENVIKING_CREDENTIALS_SOURCE, "auto"))
    .toLowerCase();
  if (raw === "env" || raw === "environment") return "env";
  if (raw === "cli" || raw === "ovcli" || raw === "file" || raw === "config") return "cli";
  return "auto";
}

function hasEnvCredentialFields(env) {
  return CREDENTIAL_ENV_VARS.some((name) => str(env[name], ""));
}

function trimSlash(value) {
  return value.replace(/\/+$/, "");
}

/** The first layer that holds a value, with the source label and file it names. */
function firstLayer(layers) {
  for (const [value, source, path = ""] of layers) {
    if (value) return { value, source, path };
  }
  return { value: "", source: "none", path: "" };
}

/**
 * The one connection a harness has: which server, which key, whose identity,
 * and whether that identity goes on the wire. Hooks reach it through
 * `buildPluginConfig`, every MCP proxy through the same loader or through
 * `buildProxyConnection`, so the two sides of one harness cannot disagree.
 *
 * Only three kinds of input count: what an embedding host named (`hostInput`),
 * the environment, and the two files under `~/.openviking`. The working
 * directory never does — an MCP proxy starts wherever its host launches it.
 *
 * ovcli.conf pins the chain when it names a url, key, identity or peer and the
 * environment names none of those; pinned, the environment's credentials no
 * longer apply. `OPENVIKING_CREDENTIAL_SOURCE` forces either side, and forced
 * to `env` no file is read at all — which is what lets a parent process hand a
 * resolved connection, including an empty key, to a child verbatim. Every chain
 * runs host → env (unpinned) → ovcli.conf → its `plugin.<harness>` and
 * `plugin` keys → ov.conf's harness section; the key then ends at
 * `server.root_api_key`, which a pinned chain reaches only with
 * `rootKeyFallback`, and the identity skips ov.conf's harness section when
 * pinned. The auth mode reads the environment in either mode, then the
 * `plugin` keys, the harness section and `server.auth_mode`; failing all of
 * them, an identity from any layer means the deployment expects one.
 */
export function resolveConnection(harness, {
  env = process.env,
  files = null,
  hostInput = {},
  rootKeyFallback = false,
} = {}) {
  const name = str(harness, "");
  const loaded = files || loadCredentialFiles(env);
  const { cliFile, ovFile, cliPath, ovPath } = loaded;
  const host = hostInput || {};
  const mode = sourceMode(env);
  const envHasCredentials = hasEnvCredentialFields(env);
  const pinned = mode === "cli" ||
    (mode === "auto" && !envHasCredentials && Boolean(cliPath) && hasCredentialFields(cliFile));
  const unpinned = (layer) => (pinned ? [] : [layer]);
  const fromFiles = mode !== "env";
  const filed = (...layers) => (fromFiles ? layers : []);
  const plugin = pluginSection(cliFile, name);
  const section = harnessSection(ovFile, name);
  const server = isSection(ovFile.server) ? ovFile.server : {};

  const key = firstLayer([
    [str(host.apiKey, ""), "host"],
    ...unpinned([str(env.OPENVIKING_BEARER_TOKEN, str(env.OPENVIKING_API_KEY, "")), "env"]),
    ...filed(
      [str(cliFile.api_key, ""), "ovcli", cliPath],
      [scalar(plugin.apiKey), "ovcli", cliPath],
      [scalar(section.apiKey), "ov", ovPath],
    ),
    ...((pinned && !rootKeyFallback) ? [] : filed([str(server.root_api_key, ""), "ov", ovPath])),
  ]);

  const identity = (hostValue, envName, cliValue, knob) => firstLayer([
    [str(hostValue, ""), "host"],
    ...unpinned([str(env[envName], ""), "env"]),
    ...filed([cliValue, "ovcli", cliPath], [scalar(plugin[knob]), "ovcli", cliPath]),
    ...(pinned ? [] : filed([str(section[knob], ""), "ov", ovPath])),
  ]).value;
  const account = identity(host.account, "OPENVIKING_ACCOUNT", str(cliFile.account, str(cliFile.account_id, "")), "accountId");
  const user = identity(host.user, "OPENVIKING_USER", str(cliFile.user, str(cliFile.user_id, "")), "userId");

  const peerId = firstLayer([
    ...unpinned([str(env.OPENVIKING_PEER_ID, ""), "env"]),
    ...filed([str(cliFile.actor_peer_id, str(cliFile.peer_id, "")), "ovcli"]),
    ...(pinned ? [] : filed([str(section.peerId, str(section.peer_id, "")), "ov"])),
  ]).value;

  const authMode = normalizeAuthMode(host.authMode)
    || normalizeAuthMode(env.OPENVIKING_AUTH_MODE)
    || (fromFiles && (authModeIn(plugin) || authModeIn(section) || normalizeAuthMode(server.auth_mode)))
    || ((account || user) ? "trusted" : "api_key");

  const hostBaseUrl = trimSlash(str(host.baseUrl, ""));
  const envUrl = pinned ? "" : str(env.OPENVIKING_URL, str(env.OPENVIKING_BASE_URL, ""));
  const fileUrl = fromFiles ? (str(cliFile.url, "") || str(server.url, "")) : "";
  const baseUrl = hostBaseUrl || trimSlash(envUrl || fileUrl) || serverAddress(fromFiles ? server : {});
  const envMcpUrl = pinned ? "" : str(env.OPENVIKING_MCP_URL, "");
  const mcpUrl = (!hostBaseUrl && envMcpUrl) ? envMcpUrl : `${baseUrl}/mcp`;

  return {
    ...loaded,
    harness: name,
    credentialSource: pinned ? "ovcli" : ((mode === "env" || envHasCredentials) ? "env" : "auto"),
    baseUrl,
    mcpUrl,
    apiKey: key.value,
    account,
    user,
    peerId,
    authMode,
    sendIdentityHeaders: authMode === "trusted",
    hasApiKey: Boolean(key.value),
    // Which layer supplied the key and the file behind it, empty when the key
    // came from the host or the environment. A doctor and a 401 hint report it;
    // `credentialSource` is the mode the chain ran in, not a file.
    apiKeySource: key.source,
    credentialPath: key.path,
  };
}

function serverAddress(server) {
  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Number.isFinite(Number(server.port)) ? Math.floor(Number(server.port)) : 1933;
  return `http://${host}:${port}`;
}

function envFlag(env, name) {
  const raw = str(env[name], "").toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes";
}

function clampTimeout(value) {
  const raw = str(value, "");
  const parsed = raw ? Number(raw) : NaN;
  return Math.max(MIN_TIMEOUT_MS, Math.floor(Number.isFinite(parsed) ? parsed : DEFAULT_TIMEOUT_MS));
}

/**
 * Everything a stdio MCP proxy needs, for a package that ships no hooks.
 *
 * `buildPluginConfig` answers the same question one layer up, but reaching it
 * means vendoring the knob schema and the workspace layers — a closure the
 * portable agent-plugins bundle would carry in git to send one HTTP header.
 * This is the same connection plus what a proxy adds to it: the User-Agent,
 * the request timeout, and the debug log.
 *
 * The api_key ends at `server.root_api_key` even when ovcli.conf pinned the
 * chain to itself. A portable package has no installer to migrate anyone, so
 * an install that names only a `url` there keeps the key it has always used.
 */
export function buildProxyConnection(harness, { env = process.env, manifestUrl = "", version = "" } = {}) {
  const name = str(harness);
  const connection = resolveConnection(name, { env, rootKeyFallback: true });
  return {
    harness: name,
    userAgent: buildUserAgent(name, str(version) || (manifestUrl ? readManifestVersion(manifestUrl) : "")),
    baseUrl: connection.baseUrl,
    mcpUrl: connection.mcpUrl,
    apiKey: connection.apiKey,
    account: connection.account,
    user: connection.user,
    peerId: connection.peerId,
    authMode: connection.authMode,
    sendIdentityHeaders: connection.sendIdentityHeaders,
    credentialSource: connection.credentialSource,
    apiKeySource: connection.apiKeySource,
    credentialPath: connection.credentialPath,
    hasApiKey: connection.hasApiKey,
    cliPath: connection.cliPath,
    ovPath: connection.ovPath,
    timeoutMs: clampTimeout(env.OPENVIKING_TIMEOUT_MS),
    debug: envFlag(env, "OPENVIKING_DEBUG"),
    debugLogPath: str(env.OPENVIKING_DEBUG_LOG)
      || join(homedir(), ".openviking", "logs", `${name}.log`),
  };
}
