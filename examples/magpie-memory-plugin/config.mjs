import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import { resolveSettings, normalizeRewriteMode } from "./shared/plugin-config.mjs";
import { KNOBS } from "./shared/config-schema.mjs";
import { buildUserAgent, readManifestVersion, resolveConnection } from "./shared/credentials.mjs";
import { resolvePluginPeerId } from "./shared/workspace-peer.mjs";

const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const str = (value, fallback = "") => typeof value === "string" && value.trim() ? value.trim() : fallback;
const isSection = (value) => value && typeof value === "object" && !Array.isArray(value);
function normalizePath(value) {
  const path = str(value);
  return !path ? "" : path === "~" ? homedir() : resolvePath(path.startsWith("~/") ? join(homedir(), path.slice(2)) : path);
}

/** Strict client-file input for integrations that must not discover ov.conf. */
function loadClientCredentialFiles(env = process.env) {
  const path = normalizePath(env.OPENVIKING_CLI_CONFIG_FILE) || DEFAULT_OVCLI_CONF_PATH;
  const invalid = () => Object.assign(new Error("OpenViking: invalid"), { code: "invalid" });
  let file;
  try { file = JSON.parse(readFileSync(path, "utf8")); }
  catch (error) {
    if (error.code === "ENOENT") throw Object.assign(new Error("OpenViking: unconfigured"), { code: "unconfigured" });
    throw invalid();
  }
  if (!isSection(file) || !str(file.url)) throw invalid();
  try {
    const url = new URL(file.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw invalid();
  } catch { throw invalid(); }
  for (const key of ["api_key", "account", "user", "account_id", "user_id", "actor_peer_id", "peer_id", "agent_id", "ldap_username", "ldap_password", "oidc_token"]) {
    if (file[key] != null && (typeof file[key] !== "string" || /[\r\n]/.test(file[key]))) throw invalid();
  }
  if (file.actor_peer_id != null && file.agent_id != null) throw invalid();
  if (file.plugin != null && !isSection(file.plugin)) throw invalid();
  if (file.extra_headers != null && file.extra_header != null) throw invalid();
  file.extra_headers ??= file.extra_header;
  if (file.extra_headers != null) {
    if (!isSection(file.extra_headers)) throw invalid();
    for (const [key, value] of Object.entries(file.extra_headers)) {
      if (typeof value !== "string") throw invalid();
      try { new Headers({ [key]: value }); } catch { throw invalid(); }
    }
  }
  if (file.auth_mode != null && !["api_key", "ldap", "oidc"].includes(file.auth_mode)) throw invalid();
  if (file.auth_mode === "ldap" && (!(str(file.ldap_username) || str(file.user)) || !file.ldap_password)) throw invalid();
  if (file.auth_mode === "oidc" && !(str(file.oidc_token) || str(file.api_key))) throw invalid();
  return { cliFile: { ...file, actor_peer_id: file.actor_peer_id ?? file.agent_id }, cliPath: path, cliPathCandidate: path, ovFile: {}, ovPath: "" };
}

/** Global client-file switch shared by memory hooks and model management. */
export function pluginEnabled(env = process.env) {
  try { return loadClientCredentialFiles(env).cliFile.plugin?.enabled !== false; }
  catch (error) { if (error.code === "unconfigured") return true; throw error; }
}

/** Match the CLI's explicit LDAP/OIDC authorization without exposing secrets. */
function clientAuthorization(file) {
  if (file.auth_mode === "ldap") {
    return `Basic ${Buffer.from(`${str(file.ldap_username) || str(file.user)}:${file.ldap_password}`, "utf8").toString("base64")}`;
  }
  if (file.auth_mode === "oidc") return `Bearer ${str(file.oidc_token) || str(file.api_key)}`;
  return "";
}

/** Magpie pins one client snapshot; host defaults do not alter other plugins. */
export async function loadConfig(env = process.env, options = {}) {
  if (options.memoryEnabled !== undefined && typeof options.memoryEnabled !== "boolean") throw Object.assign(new Error("OpenViking: invalid"), { code: "invalid" });
  const files = loadClientCredentialFiles(env);
  const file = files.cliFile;
  const scoped = file.plugin?.magpie;
  if (scoped != null && !isSection(scoped)) {
    throw Object.assign(new Error("OpenViking: invalid"), { code: "invalid" });
  }
  const nativeAgents = options.nativeAgents ?? scoped?.nativeAgents ?? [];
  if (!Array.isArray(nativeAgents) || nativeAgents.some((agent) => typeof agent !== "string" || !agent.trim())) {
    throw Object.assign(new Error("OpenViking: invalid"), { code: "invalid" });
  }
  const captureWithoutSession = options.captureWithoutSession ?? scoped?.captureWithoutSession ?? file.plugin?.captureWithoutSession;
  if (captureWithoutSession != null && typeof captureWithoutSession !== "boolean") {
    throw Object.assign(new Error("OpenViking: invalid"), { code: "invalid" });
  }
  // Host options may tune behavior, never connection credentials or identity.
  const allowed = ["autoRecall", "autoCapture", "mcpEnabled", "recallMaxTokens", "recallContextTimeoutMs"];
  const overrides = Object.fromEntries(Object.entries(options || {}).filter(([key]) => allowed.includes(key)));
  // Preserve Magpie's strict boolean validation and nullish fallback before resolving the schema default.
  if (captureWithoutSession != null) overrides.captureWithoutSession = captureWithoutSession;
  const settingsFile = { ...file, plugin: { ...file.plugin, magpie: { ...scoped, ...overrides } } };
  const { settings, configured, sources } = resolveSettings("magpie", { env: {}, cliFile: settingsFile });
  if (!configured.has("mcpEnabled")) settings.mcpEnabled = false;
  if (!configured.has("autoCapture")) settings.autoCapture = true;
  if (!configured.has("recallContextTimeoutMs")) settings.recallContextTimeoutMs = 2000;
  const connection = resolveConnection("magpie", { env: { OPENVIKING_CREDENTIAL_SOURCE: "cli" }, files });
  const peerId = resolvePluginPeerId({ settings, configured, sources, credentials: connection, env: {} });
  const authorization = clientAuthorization(file);
  const version = readManifestVersion(new URL("./package.json", import.meta.url));
  const config = {
    ...settings,
    enabled: options.memoryEnabled !== false && file.plugin?.enabled !== false && settings.enabled,
    baseUrl: connection.baseUrl, apiKey: connection.apiKey,
    account: connection.account, user: connection.user, peerId,
    authMode: authorization ? file.auth_mode : connection.authMode,
    authorization,
    sendIdentityHeaders: !authorization && connection.sendIdentityHeaders,
    userAgent: buildUserAgent("magpie", version),
    recallRewrite: normalizeRewriteMode(settings.recallCompress),
    extraHeaders: file.extra_headers || {}, path: files.cliPath,
    explicitMcpOptIn: options.mcpEnabled === true || (options.mcpEnabled === undefined && scoped?.mcpEnabled === true),
    // Requests from the model adapter already belong to OV / VikingBot.
    // Never recall or capture their own processing through the memory gateway.
    nativeAgents: [...new Set([...nativeAgents, "openviking-models"])],
  };
  for (const knob of KNOBS.filter((knob) => knob.sendOnlyWhenConfigured)) {
    config[`${knob.name}Configured`] = configured.has(knob.name);
  }
  if (typeof file.timeout === "number" && Number.isFinite(file.timeout) && file.timeout > 0) {
    config.timeoutMs = Math.max(100, Math.min(50000, Math.floor(file.timeout * 1000)));
  }
  return config;
}
