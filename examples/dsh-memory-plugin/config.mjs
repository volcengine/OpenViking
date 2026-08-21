import { buildUserAgent, resolveOpenVikingCredentials } from "./shared/credentials.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

export const PLUGIN_VERSION = "0.2.1";

/**
 * Namespace for the bridged OpenViking MCP tools. DSH publishes every MCP tool
 * as `mcp__<serverName>__<rawName>`, so this string is part of the
 * model-facing contract: changing it renames all of them.
 */
export const MCP_SERVER_NAME = "openviking";

const DEFAULT_CONFIG = Object.freeze({
  endpoint: "http://127.0.0.1:1933",
  apiKey: "",
  account: "",
  user: "",
  peerId: "",
  userAgent: "",
  workspacePeer: true,
  recallPeerScope: "all",
  recallQueryExpansion: "auto",
  recallQueryExpansionConfigured: false,
  syncTurns: true,
  recallTokenBudget: 2000,
  recallMaxContentChars: 500,
  recallPreferAbstract: true,
  recallLimit: 10,
  recallLimitConfigured: false,
  scoreThreshold: 0.35,
  minQueryLength: 3,
  profileTokenBudget: 10000,
  commitTokenThreshold: 20000,
  commitKeepRecentCount: 10,
  captureToolResults: false,
  captureMode: "semantic",
  captureMaxLength: 24000,
  captureToolMaxChars: 1000000,
  captureAssistantTurns: true,
  requestTimeoutMs: 10000,
  mcpToolCallTimeoutMs: 60000,
});

export function resolveConfig(input = {}, env = process.env, cwd = process.cwd()) {
  const credentials = resolveOpenVikingCredentials(env);
  const explicitPeerId = input.peerId || credentials.peerId;
  const config = {
    ...DEFAULT_CONFIG,
    ...input,
    endpoint: input.endpoint || credentials.baseUrl || DEFAULT_CONFIG.endpoint,
    apiKey: input.apiKey || credentials.apiKey,
    account: input.account || credentials.account,
    user: input.user || credentials.user,
    peerId: explicitPeerId,
    explicitPeerId,
    userAgent: buildUserAgent("dsh", PLUGIN_VERSION),
    recallLimitConfigured: Object.prototype.hasOwnProperty.call(input, "recallLimit"),
    recallQueryExpansionConfigured: Object.prototype.hasOwnProperty.call(input, "recallQueryExpansion"),
  };

  if (env.OPENVIKING_WORKSPACE_PEER !== undefined) {
    config.workspacePeer = environmentBoolean(
      env.OPENVIKING_WORKSPACE_PEER,
      config.workspacePeer,
    );
  }
  if (env.OPENVIKING_RECALL_PEER_SCOPE) {
    config.recallPeerScope = env.OPENVIKING_RECALL_PEER_SCOPE;
  }
  if (env.OPENVIKING_RECALL_QUERY_EXPANSION) {
    config.recallQueryExpansion = env.OPENVIKING_RECALL_QUERY_EXPANSION;
    config.recallQueryExpansionConfigured = true;
  }
  if (env.OPENVIKING_RECALL_LIMIT) {
    config.recallLimit = env.OPENVIKING_RECALL_LIMIT;
    config.recallLimitConfigured = true;
  }

  config.endpoint = String(config.endpoint || DEFAULT_CONFIG.endpoint).replace(/\/+$/, "");
  config.workspacePeer = config.workspacePeer !== false;
  config.peerId = resolveEffectivePeerId({ cfg: config, cwd }).peerId;
  config.recallPeerScope = config.recallPeerScope === "actor" ? "actor" : "all";
  config.recallQueryExpansion = config.recallQueryExpansion === "off" ? "off" : "auto";
  config.recallLimit = clampInteger(config.recallLimit, 1, 50, DEFAULT_CONFIG.recallLimit);
  config.recallMaxContentChars = clampInteger(
    config.recallMaxContentChars,
    100,
    5000,
    DEFAULT_CONFIG.recallMaxContentChars,
  );
  config.recallTokenBudget = clampInteger(
    config.recallTokenBudget,
    200,
    50000,
    DEFAULT_CONFIG.recallTokenBudget,
  );
  config.scoreThreshold = clampNumber(
    config.scoreThreshold,
    0,
    1,
    DEFAULT_CONFIG.scoreThreshold,
  );
  config.minQueryLength = clampInteger(
    config.minQueryLength,
    1,
    64,
    DEFAULT_CONFIG.minQueryLength,
  );
  config.profileTokenBudget = clampInteger(
    config.profileTokenBudget,
    500,
    50000,
    DEFAULT_CONFIG.profileTokenBudget,
  );
  config.commitTokenThreshold = clampInteger(
    config.commitTokenThreshold,
    1000,
    1000000,
    DEFAULT_CONFIG.commitTokenThreshold,
  );
  config.commitKeepRecentCount = clampInteger(
    config.commitKeepRecentCount,
    0,
    1000,
    DEFAULT_CONFIG.commitKeepRecentCount,
  );
  config.captureMaxLength = clampInteger(
    config.captureMaxLength,
    200,
    100000,
    DEFAULT_CONFIG.captureMaxLength,
  );
  config.captureToolMaxChars = clampInteger(
    config.captureToolMaxChars,
    200,
    1000000,
    DEFAULT_CONFIG.captureToolMaxChars,
  );
  config.requestTimeoutMs = clampInteger(
    config.requestTimeoutMs,
    1000,
    120000,
    DEFAULT_CONFIG.requestTimeoutMs,
  );
  config.mcpToolCallTimeoutMs = clampInteger(
    config.mcpToolCallTimeoutMs,
    1000,
    600000,
    DEFAULT_CONFIG.mcpToolCallTimeoutMs,
  );
  config.captureMode = config.captureMode === "keyword" ? "keyword" : "semantic";
  config.syncTurns = config.syncTurns !== false;
  config.captureAssistantTurns = config.captureAssistantTurns !== false;
  config.captureToolResults = config.captureToolResults === true;
  return config;
}

function environmentBoolean(value, fallback) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  return fallback;
}

function clampInteger(value, minimum, maximum, fallback) {
  const number = Math.round(Number(value));
  if (!Number.isFinite(number)) return fallback;
  return Math.max(minimum, Math.min(maximum, number));
}

function clampNumber(value, minimum, maximum, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(minimum, Math.min(maximum, number));
}
