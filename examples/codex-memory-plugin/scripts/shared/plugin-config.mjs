// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Per-harness plugin settings from ovcli.conf.
 *
 * ovcli.conf carried connection fields only, so every harness had to keep its
 * tuning knobs in ov.conf's harness section — a server-side file that a
 * client-side plugin has no business editing. The `plugin` section fixes that:
 * shared keys apply to every harness that reads them, and a per-harness object
 * overrides them.
 *
 *   {
 *     "url": "...", "api_key": "...",
 *     "plugin": {
 *       "recallQueryExpansion": "off",
 *       "recallCompress": "auto"
 *     }
 *   }
 *
 * Resolution stays env → ovcli.conf plugin.<harness> → ovcli.conf plugin →
 * ov.conf harness section (legacy) → defaults.
 *
 * Every harness resolves through `resolveSettings()` below, and assembles its
 * whole configuration through `buildPluginConfig()`, so one `plugin` section
 * configures all of them and `ov config switch` moves behaviour along with
 * credentials. The knobs themselves are declared once in `config-schema.mjs`;
 * this module decides which files supply them and what is derived from them.
 */

import { homedir } from "node:os";
import { join } from "node:path";

import { HARNESS_KEYS, KNOBS, harnessKey, resolveKnobs } from "./config-schema.mjs";
import {
  buildUserAgent,
  loadCredentialFiles,
  readManifestVersion,
  resolveAuthMode,
  resolveOpenVikingCredentials,
} from "./credentials.mjs";
import {
  announcedOverrides,
  loadWorkspaceLayers,
  mergeConfigLayers,
  normalizeWorkspaceConfig,
  projectWorkspaceSettings,
} from "./workspace-config.mjs";
import { findWorkspaceRoot, resolveWorkspaceIdentity } from "./workspace-identity.mjs";
import { resolveEffectivePeerId, resolvePluginPeerId } from "./workspace-peer.mjs";
import { readEntry } from "./workspace-registry.mjs";

const OVCLI_LAYER = "ovcli.conf";
const OV_CONF_LAYER = "ov.conf";

export { HARNESS_KEYS, harnessKey };

/**
 * Merge the shared plugin section with the harness-specific override.
 * Returns a flat settings object; unknown keys pass through untouched so a
 * harness can add its own knobs without touching this module.
 *
 * `cliFile` is ovcli.conf already parsed. Callers that have it hand it over so
 * the file is read once per build and, more importantly, so the `plugin`
 * section comes out of the same file the credential chain read — an
 * `OPENVIKING_CONFIG_FILE` that is ovcli-shaped is ovcli.conf, and only
 * `loadCredentialFiles` knows that.
 */
export function loadPluginSettings(harness, env = process.env, options = {}) {
  const file = options.cliFile || loadCredentialFiles(env).cliFile;
  const plugin = file && typeof file.plugin === "object" && file.plugin ? file.plugin : {};

  const shared = {};
  for (const [key, value] of Object.entries(plugin)) {
    if (value && typeof value === "object" && !Array.isArray(value)) continue;
    shared[key] = value;
  }
  // Both spellings of the harness name find the same override, so copying
  // `claude-code` out of the installer is not a silent no-op.
  const key = harnessKey(harness);
  const scoped = {};
  for (const candidate of [key, key.replace(/_/g, "-")]) {
    const value = candidate && plugin[candidate];
    if (value && typeof value === "object" && !Array.isArray(value)) Object.assign(scoped, value);
  }

  const settings = { ...shared, ...scoped };
  const cwd = String(options.cwd || "").trim();
  if (!cwd) return settings;
  return { ...settings, ...resolveWorkspaceSettings(cwd, env, options).settings };
}

/**
 * Every knob for one harness, resolved through the whole layer stack.
 *
 * `legacy` is that harness's section in ov.conf, which is where tuning used to
 * live: it stays the lowest configured layer so an existing deployment keeps
 * working, and everything above it comes from files a client may edit.
 *
 * Returns the settings, the names some layer actually supplied (what the
 * `*Configured` flags report), and the raw ovcli.conf merge — a couple of
 * callers still need to know a value came from ovcli.conf rather than ov.conf.
 */
export function resolveSettings(harness, options = {}) {
  const { env = process.env, cwd = "", legacy = {}, clientVersion = "", cliFile = null } = options;
  const key = harnessKey(harness);
  const plugin = loadPluginSettings(key, env, { cwd, clientVersion, cliFile });
  const { settings, configured, sources } = resolveKnobs({
    harness: key,
    layers: [
      { name: OV_CONF_LAYER, data: legacy },
      { name: OVCLI_LAYER, data: plugin },
    ],
    env,
  });
  return { settings, configured, sources, plugin };
}

/**
 * The workspace layers for one cwd, flattened into harness knobs.
 *
 * Takes the cwd explicitly because a hook's `loadConfig()` runs at module top
 * level, before the payload on stdin has said which directory the session is
 * actually in — the caller resolves this again once it knows.
 */
export function resolveWorkspaceSettings(cwd, env = process.env, { clientVersion = "" } = {}) {
  const empty = { settings: {}, root: "", provenance: {}, warnings: [], announced: [] };
  try {
    const { root } = findWorkspaceRoot(cwd, env);
    if (!root) return empty;

    const { layers, warnings } = loadWorkspaceLayers(root, { clientVersion });
    // The identity is what makes the registry's negative evidence work: without
    // it a directory reused by a different repository inherits the old peer.
    const identity = resolveWorkspaceIdentity({ cwd, env });
    const registry = readEntry(root, { identity, env });
    warnings.push(...registry.warnings);
    if (registry.entry?.settings) layers.push({ layer: "registry", data: registry.entry.settings });
    if (registry.entry?.peer) layers.push({ layer: "registry", data: { peer: registry.entry.peer } });
    if (!layers.length) return { ...empty, root, warnings };

    const { value, provenance } = mergeConfigLayers(layers, warnings);
    normalizeWorkspaceConfig(value, warnings);
    return {
      settings: projectWorkspaceSettings(value),
      root,
      provenance,
      warnings,
      announced: announcedOverrides(provenance),
      value,
    };
  } catch {
    // A hook must never die over a config file. An unreadable layer is no layer.
    return empty;
  }
}

const SEND_ONLY_WHEN_CONFIGURED = KNOBS.filter((knob) => knob.sendOnlyWhenConfigured);

function str(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

/** The ov.conf block named after this harness, the lowest layer of every chain. */
function ovConfSection(ovFile, key) {
  const section = key ? ovFile?.[key] : null;
  return section && typeof section === "object" && !Array.isArray(section) ? section : {};
}

/**
 * A connection value ovcli.conf's `plugin` section supplied, as opposed to
 * ov.conf's harness block or the environment — the credential chain ranks
 * those two itself, and would rank a `plugin` value wrong.
 */
function fromPluginSection(settings, sources, name) {
  return sources[name] === OVCLI_LAYER ? str(settings[name]) : "";
}

/**
 * One harness's whole configuration: the files, the knobs, the peer, and the
 * handful of fields derived from them.
 *
 * Every loader used to write this sequence out by hand and each wrote a
 * slightly different subset, which is how ov.conf's `<harness>.authMode` came
 * to apply on three harnesses only, and why two of the four knobs the server
 * defaults for itself were reported as configured on two harnesses and nowhere
 * else. What is left in a loader after this call is what only that harness
 * knows.
 *
 * `legacy` replaces the ov.conf block this harness would otherwise get (dsh's
 * host hands it one of its own); `hostInput` is what an embedding host named
 * for this process, which is the more specific answer and outranks every file;
 * `logFile` is the log basename this harness has always used, and a harness
 * that never had a default log path does not get one invented for it;
 * `rootKeyFallback` is for the harnesses whose chain predates the ovcli.conf
 * pinning and has always ended at `server.root_api_key`.
 */
export function buildPluginConfig(harness, {
  cwd = "",
  env = process.env,
  legacy = null,
  manifestUrl = "",
  version = "",
  hostInput = {},
  logFile = "",
  rootKeyFallback = false,
  deriveEffectivePeer = false,
} = {}) {
  const name = String(harness || "");
  const key = harnessKey(name);
  const workspaceCwd = str(cwd) || process.cwd();
  const files = loadCredentialFiles(env);
  // The version answers two questions: what the User-Agent reports, and whether
  // a workspace file asking for a newer client gets to say so.
  const clientVersion = str(version) || (manifestUrl ? readManifestVersion(manifestUrl) : "");
  const { settings, configured, sources } = resolveSettings(key, {
    env,
    cwd: workspaceCwd,
    legacy: legacy || ovConfSection(files.ovFile, key),
    cliFile: files.cliFile,
    clientVersion,
  });
  const credentials = resolveOpenVikingCredentials(env, key, {
    apiKey: fromPluginSection(settings, sources, "apiKey"),
    accountId: fromPluginSection(settings, sources, "accountId"),
    userId: fromPluginSection(settings, sources, "userId"),
  });

  // Pinned to ovcli.conf, the credential chain stops at that file. The layers
  // under it still answer: ov.conf's harness block is where tuning used to
  // live, and `server.root_api_key` is where a harness that predates the
  // pinning has always ended its chain.
  let apiKey = credentials.apiKey;
  let apiKeySource = credentials.apiKeySource;
  let credentialPath = credentials.credentialPath;
  if (!apiKey) {
    apiKey = sources.apiKey === OV_CONF_LAYER
      ? str(settings.apiKey)
      : (rootKeyFallback ? str(credentials.ovFile?.server?.root_api_key) : "");
    if (apiKey) {
      apiKeySource = "ov";
      credentialPath = credentials.ovPath;
    }
  }
  const hostApiKey = str(hostInput.apiKey);
  if (hostApiKey) {
    apiKey = hostApiKey;
    apiKeySource = "host";
    credentialPath = "";
  }

  const hostBaseUrl = str(hostInput.baseUrl).replace(/\/+$/, "");
  const baseUrl = hostBaseUrl || credentials.baseUrl;
  const account = str(hostInput.account) || credentials.account;
  const user = str(hostInput.user) || credentials.user;
  const timeoutMs = settings.timeoutMs;
  const peerId = resolvePluginPeerId({
    settings,
    configured,
    sources,
    credentials,
    hostInput: hostInput.peerId,
    env,
  });

  const config = {
    ...settings,
    harness: name,
    clientVersion,
    userAgent: buildUserAgent(name, clientVersion),

    baseUrl,
    mcpUrl: hostBaseUrl ? `${hostBaseUrl}/mcp` : credentials.mcpUrl,
    apiKey,
    account,
    user,
    ...resolveAuthMode({ settings, ovFile: credentials.ovFile, account, user }),

    credentialSource: credentials.credentialSource,
    apiKeySource,
    credentialPath,
    hasApiKey: Boolean(apiKey),
    configPath: credentials.cliPath || credentials.ovPath || null,
    cliConfigPath: credentials.cliPath,
    ovConfigPath: credentials.ovPath,
    cliPath: credentials.cliPath,
    ovPath: credentials.ovPath,

    peerId,
    explicitPeerId: peerId,

    // A write gets a longer budget than a read, and a digest has to finish
    // inside the recall request it belongs to, so both fall back to a knob.
    captureTimeoutMs: settings.captureTimeoutMs || Math.max(timeoutMs * 2, 30000),
    recallCompressTimeoutMs: settings.recallCompressTimeoutMs
      || Math.max(1000, settings.recallTimeoutMs - 10000),
    debugLogPath: settings.debugLogPath
      || (logFile ? join(homedir(), ".openviking", "logs", logFile) : ""),

    // The spellings these fields carried before every harness shared a loader.
    endpoint: baseUrl,
    accountId: account,
    userId: user,
    requestTimeoutMs: timeoutMs,
  };
  for (const knob of SEND_ONLY_WHEN_CONFIGURED) {
    config[`${knob.name}Configured`] = configured.has(knob.name);
  }

  // Only for the harnesses that resolve the peer once at load. The rest derive
  // it per session, against the directory the payload names, and an MCP proxy
  // must not derive one from wherever it happened to be launched.
  if (deriveEffectivePeer) {
    const effectivePeer = resolveEffectivePeerId({ cfg: config, cwd: workspaceCwd });
    config.effectivePeer = effectivePeer;
    config.legacyPeerId = effectivePeer.legacyPeerId;
  }
  return config;
}

const REWRITE_MODES = new Set(["off", "client", "server", "auto"]);

/**
 * Normalize the tri-state rewrite knob.
 *
 * off    — never compress
 * client — always compress locally through the host CLI
 * server — always ask the server for a digest
 * auto   — prefer local (cost lands on the user's own subscription), fall back
 *          to the server when no healthy host CLI is available
 */
export function normalizeRewriteMode(value, fallback = "off") {
  const raw = String(value ?? "").trim().toLowerCase();
  if (REWRITE_MODES.has(raw)) return raw;
  if (raw === "1" || raw === "true" || raw === "yes") return "auto";
  if (raw === "0" || raw === "false" || raw === "no") return "off";
  return fallback;
}
