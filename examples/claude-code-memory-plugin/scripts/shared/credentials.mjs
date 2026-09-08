// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_BASE_URL = "http://127.0.0.1:1933";
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
 * The calling harness's own section of ov.conf.
 *
 * ov.conf spells its sections snake_case while a host may call itself
 * `claude-code` or `trae-cn`, so both spellings land on the same block. The
 * normalization stays inline rather than importing `config-schema.mjs`, which
 * the portable agent-plugins bundle would then have to ship as well.
 */
function harnessSection(ovFile, harness) {
  const key = String(harness || "").trim().toLowerCase().replace(/-/g, "_");
  const section = key ? ovFile[key] : null;
  return section && typeof section === "object" && !Array.isArray(section) ? section : {};
}

const AUTH_MODES = ["trusted", "api_key"];

function normalizeAuthMode(value) {
  const mode = str(value, "").toLowerCase();
  return AUTH_MODES.includes(mode) ? mode : "";
}

/**
 * Which auth mode the server is in, and with it whether the identity headers
 * may go on the wire.
 *
 * An `api_key` server reads the account and the user out of the key and ignores
 * `X-OpenViking-Account` / `X-OpenViking-User`, so sending them there tells
 * every proxy on the path who the operator is and buys nothing. A named mode
 * wins; ov.conf's server block answers next; failing both, a credential layer
 * that supplied an identity at all means the deployment expects one.
 */
export function resolveAuthMode({ settings = {}, ovFile = {}, account = "", user = "" } = {}) {
  const server = ovFile.server || {};
  const authMode = normalizeAuthMode(settings.authMode)
    || normalizeAuthMode(server.auth_mode)
    || ((account || user) ? "trusted" : "api_key");
  return { authMode, sendIdentityHeaders: authMode === "trusted" };
}

function sourceMode(env) {
  const raw = str(env.OPENVIKING_CREDENTIAL_SOURCE, str(env.OPENVIKING_CREDENTIALS_SOURCE, "auto"))
    .toLowerCase();
  if (raw === "env" || raw === "environment") return "env";
  if (raw === "cli" || raw === "ovcli" || raw === "file" || raw === "config") return "cli";
  return "auto";
}

function hasEnvCredentialFields(env) {
  return Boolean(
    str(env.OPENVIKING_URL, str(env.OPENVIKING_BASE_URL, "")) ||
    str(env.OPENVIKING_MCP_URL, "") ||
    str(env.OPENVIKING_BEARER_TOKEN, str(env.OPENVIKING_API_KEY, "")) ||
    str(env.OPENVIKING_ACCOUNT, "") ||
    str(env.OPENVIKING_USER, "") ||
    str(env.OPENVIKING_PEER_ID, ""),
  );
}

function deriveBaseUrl({ env, cliFile, ovFile, mode, useCli }) {
  const envUrl = str(env.OPENVIKING_URL, str(env.OPENVIKING_BASE_URL, ""));
  const cliUrl = str(cliFile.url, "");

  if (mode !== "cli" && envUrl) return envUrl.replace(/\/+$/, "");
  if (useCli && cliUrl) return cliUrl.replace(/\/+$/, "");
  if (mode !== "env" && cliUrl) return cliUrl.replace(/\/+$/, "");

  const server = ovFile.server || {};
  const ovUrl = str(server.url, "");
  if (ovUrl) return ovUrl.replace(/\/+$/, "");

  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Number.isFinite(Number(server.port)) ? Math.floor(Number(server.port)) : 1933;
  return `http://${host}:${port}`;
}

/**
 * `harness` names whose ov.conf section supplies the legacy fallback. It sits
 * where it always did — after ovcli.conf, before `server.root_api_key` — but a
 * harness now reads its own section instead of every one of them reading
 * codex's. The default keeps codex, the only caller that never passes one.
 *
 * `plugin` carries the `apiKey`, `accountId` and `userId` ovcli.conf's `plugin`
 * section named, which this module cannot read for itself: resolving that
 * section needs the knob schema, and the portable bundles would then have to
 * ship it. All three rank where the file they come from ranks — under
 * ovcli.conf's own fields, over ov.conf.
 */
export function resolveOpenVikingCredentials(env = process.env, harness = "codex", plugin = {}) {
  const files = loadCredentialFiles(env);
  const mode = sourceMode(env);
  const envHasCredentials = hasEnvCredentialFields(env);
  const useCli = mode === "cli" ||
    (mode === "auto" && !envHasCredentials && files.cliPath && hasCredentialFields(files.cliFile));
  const cx = harnessSection(files.ovFile, harness);
  const server = files.ovFile.server || {};

  const baseUrl = deriveBaseUrl({ env, ...files, mode, useCli });

  const pluginApiKey = str(plugin?.apiKey, "");
  const apiKey = useCli
    ? (str(files.cliFile.api_key, "") || pluginApiKey)
    : (
        str(env.OPENVIKING_BEARER_TOKEN, "") ||
        str(env.OPENVIKING_API_KEY, "") ||
        str(files.cliFile.api_key, "") ||
        pluginApiKey ||
        str(cx.apiKey, "") ||
        str(server.root_api_key, "")
      );

  const pluginAccount = str(plugin?.accountId, "");
  const account = useCli
    ? (str(files.cliFile.account, str(files.cliFile.account_id, "")) || pluginAccount)
    : (
        str(env.OPENVIKING_ACCOUNT, "") ||
        str(files.cliFile.account, str(files.cliFile.account_id, "")) ||
        pluginAccount ||
        str(cx.accountId, "")
      );

  const pluginUser = str(plugin?.userId, "");
  const user = useCli
    ? (str(files.cliFile.user, str(files.cliFile.user_id, "")) || pluginUser)
    : (
        str(env.OPENVIKING_USER, "") ||
        str(files.cliFile.user, str(files.cliFile.user_id, "")) ||
        pluginUser ||
        str(cx.userId, "")
      );

  const peerId = useCli
    ? str(files.cliFile.actor_peer_id, str(files.cliFile.peer_id, ""))
    : (
        str(env.OPENVIKING_PEER_ID, "") ||
        str(files.cliFile.actor_peer_id, str(files.cliFile.peer_id, "")) ||
        str(cx.peerId, str(cx.peer_id, ""))
      );

  const explicitMcpUrl = str(env.OPENVIKING_MCP_URL, "");
  const mcpUrl = (mode !== "cli" && explicitMcpUrl) ? explicitMcpUrl : `${baseUrl.replace(/\/+$/, "")}/mcp`;

  // Which layer actually supplied the api_key, following the same chain, and
  // the file behind it — empty when the key came from the environment or was
  // never found. `apiKeySource` is what a doctor and a 401 hint report; the
  // credential *source* beside it is the mode the chain ran in, not a file.
  let apiKeySource = "none";
  let credentialPath = "";
  if (apiKey) {
    if (useCli) {
      apiKeySource = "ovcli";
      credentialPath = files.cliPath;
    } else if (str(env.OPENVIKING_BEARER_TOKEN, str(env.OPENVIKING_API_KEY, ""))) {
      apiKeySource = "env";
    } else if (str(files.cliFile.api_key, "") || pluginApiKey) {
      apiKeySource = "ovcli";
      credentialPath = files.cliPath;
    } else {
      apiKeySource = "ov";
      credentialPath = files.ovPath;
    }
  }

  return {
    ...files,
    credentialSource: useCli ? "ovcli" : ((mode === "env" || envHasCredentials) ? "env" : "auto"),
    apiKeySource,
    credentialPath,
    baseUrl,
    mcpUrl,
    apiKey,
    account,
    user,
    peerId,
    hasApiKey: Boolean(apiKey),
  };
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
 * This is the connection half alone: the credential chain, the auth mode that
 * decides whether the identity headers may go on the wire, the User-Agent, the
 * request timeout, and the debug log.
 *
 * The api_key ends at `server.root_api_key` even when ovcli.conf pinned the
 * chain to itself. A portable package has no installer to migrate anyone, so
 * an install that names only a `url` there keeps the key it has always used.
 */
export function buildProxyConnection(harness, { env = process.env, manifestUrl = "", version = "" } = {}) {
  const name = str(harness);
  const credentials = resolveOpenVikingCredentials(env, name);

  let { apiKey, apiKeySource, credentialPath } = credentials;
  if (!apiKey) {
    apiKey = str(credentials.ovFile?.server?.root_api_key);
    if (apiKey) {
      apiKeySource = "ov";
      credentialPath = credentials.ovPath;
    }
  }

  return {
    harness: name,
    userAgent: buildUserAgent(name, str(version) || (manifestUrl ? readManifestVersion(manifestUrl) : "")),
    baseUrl: credentials.baseUrl,
    mcpUrl: credentials.mcpUrl,
    apiKey,
    account: credentials.account,
    user: credentials.user,
    peerId: credentials.peerId,
    ...resolveAuthMode({
      ovFile: credentials.ovFile,
      account: credentials.account,
      user: credentials.user,
    }),
    credentialSource: credentials.credentialSource,
    apiKeySource,
    credentialPath,
    hasApiKey: Boolean(apiKey),
    watchedPaths: [credentials.cliPath, credentials.ovPath, credentials.cliPathCandidate],
    timeoutMs: clampTimeout(env.OPENVIKING_TIMEOUT_MS),
    debug: envFlag(env, "OPENVIKING_DEBUG"),
    debugLogPath: str(env.OPENVIKING_DEBUG_LOG)
      || join(homedir(), ".openviking", "logs", `${name}.log`),
  };
}

function main() {
  const cmd = process.argv[2] || "";
  if (cmd === "mcp-url") {
    process.stdout.write(resolveOpenVikingCredentials().mcpUrl);
    return;
  }
  if (cmd === "has-api-key") {
    process.stdout.write(resolveOpenVikingCredentials().hasApiKey ? "1" : "0");
    return;
  }
  if (cmd === "has-peer-id") {
    process.stdout.write(resolveOpenVikingCredentials().peerId ? "1" : "0");
    return;
  }
  process.stderr.write("usage: credentials.mjs <mcp-url|has-api-key|has-peer-id>\n");
  process.exitCode = 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  main();
}
