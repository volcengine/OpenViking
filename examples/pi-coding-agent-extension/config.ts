import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { buildUserAgent, resolveOpenVikingCredentials } from "./shared/credentials.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

/** Hand-maintained: this extension ships no manifest to read a version from. */
export const EXTENSION_VERSION = "0.1.0";

export interface OVConfig {
  enabled: boolean;
  endpoint: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  userAgent: string;
  workspacePeer: boolean;
  recallPeerScope: "actor" | "all";
  recallQueryExpansion: "auto" | "off";
  recallQueryExpansionConfigured: boolean;
  syncTurns: boolean;
  recallTokenBudget: number;
  recallMaxContentChars: number;
  recallPreferAbstract: boolean;
  recallLimit: number;
  recallLimitConfigured: boolean;
  recallLedger: boolean;
  scoreThreshold: number;
  minQueryLength: number;
  profileTokenBudget: number;
  resumeContextBudget: number;
  commitTokenThreshold: number;
  commitKeepRecentCount: number;
  takeoverEnabled: boolean;
  takeoverTokenThreshold: number;
  takeoverKeepRecentTurns: number;
  takeoverOverviewBudget: number;
  takeoverOverviewPollMs: number;
  takeoverOverviewPollMax: number;
  captureToolResults: boolean;
  captureMode: "semantic" | "keyword";
  captureMaxLength: number;
  captureToolMaxChars: number;
  captureAssistantTurns: boolean;
  bypassPatterns: string[];
  logLevel: "silent" | "error" | "info";
}

const DEFAULT_CONFIG: OVConfig = {
  enabled: true,
  endpoint: "http://127.0.0.1:1933",
  apiKey: "",
  account: "",
  user: "",
  peerId: "",
  userAgent: "",
  workspacePeer: true,
  recallPeerScope: "all",
  // Server-side query expansion costs a model call before retrieval starts, so
  // it has to be switchable from the client that pays the latency.
  recallQueryExpansion: "auto",
  recallQueryExpansionConfigured: false,
  syncTurns: true,
  recallTokenBudget: 2000,
  recallMaxContentChars: 500,
  recallPreferAbstract: true,
  recallLimit: 10,
  recallLimitConfigured: false,
  // Persist injected recall blocks and re-apply them to historical user
  // messages so provider prompt-prefix caches keep hitting (#4137).
  recallLedger: true,
  scoreThreshold: 0.35,
  minQueryLength: 3,
  profileTokenBudget: 10000,
  resumeContextBudget: 32000,
  commitTokenThreshold: 20000,
  commitKeepRecentCount: 10,
  takeoverEnabled: true,
  takeoverTokenThreshold: 30000,
  takeoverKeepRecentTurns: 3,
  takeoverOverviewBudget: 3000,
  takeoverOverviewPollMs: 2000,
  takeoverOverviewPollMax: 15,
  captureToolResults: false,
  captureMode: "semantic",
  captureMaxLength: 24000,
  captureToolMaxChars: 1000000,
  captureAssistantTurns: true,
  bypassPatterns: [],
  logLevel: "error",
};

export function loadConfigFromModuleUrl(moduleUrl: string): OVConfig {
  return loadConfig(dirname(fileURLToPath(moduleUrl)));
}

export function loadConfig(extensionDir: string): OVConfig {
  const configPath = join(extensionDir, "config.json");
  let file: any = {};
  try {
    if (existsSync(configPath)) file = JSON.parse(readFileSync(configPath, "utf8"));
  } catch {
    file = {};
  }

  const takeover = file.takeover && typeof file.takeover === "object" ? file.takeover : {};
  const creds = resolveOpenVikingCredentials();
  const config: OVConfig = {
    ...DEFAULT_CONFIG,
    ...file,
    endpoint: creds.baseUrl,
    apiKey: creds.apiKey,
    account: creds.account,
    user: creds.user,
    peerId: creds.peerId,
    userAgent: buildUserAgent("pi", EXTENSION_VERSION),
    recallLimitConfigured: Object.prototype.hasOwnProperty.call(file, "recallLimit"),
    recallQueryExpansionConfigured: Object.prototype.hasOwnProperty.call(file, "recallQueryExpansion"),
    recallTokenBudget: file.recallTokenBudget ?? file.recallBudget ?? DEFAULT_CONFIG.recallTokenBudget,
    scoreThreshold: file.scoreThreshold ?? file.recallScoreThreshold ?? DEFAULT_CONFIG.scoreThreshold,
    minQueryLength: file.minQueryLength ?? file.recallMinQueryLength ?? DEFAULT_CONFIG.minQueryLength,
    profileTokenBudget: file.profileTokenBudget ?? file.profileBudget ?? DEFAULT_CONFIG.profileTokenBudget,
    takeoverEnabled: takeover.enabled ?? file.takeoverEnabled ?? DEFAULT_CONFIG.takeoverEnabled,
    takeoverTokenThreshold: takeover.tokenThreshold ?? file.takeoverTokenThreshold ?? DEFAULT_CONFIG.takeoverTokenThreshold,
    takeoverKeepRecentTurns: takeover.keepRecentTurns ?? file.takeoverKeepRecentTurns ?? DEFAULT_CONFIG.takeoverKeepRecentTurns,
    takeoverOverviewBudget: takeover.overviewBudget ?? file.takeoverOverviewBudget ?? DEFAULT_CONFIG.takeoverOverviewBudget,
    takeoverOverviewPollMs: takeover.overviewPollMs ?? file.takeoverOverviewPollMs ?? DEFAULT_CONFIG.takeoverOverviewPollMs,
    takeoverOverviewPollMax: takeover.overviewPollMax ?? file.takeoverOverviewPollMax ?? DEFAULT_CONFIG.takeoverOverviewPollMax,
  };

  if (process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL) config.endpoint = creds.baseUrl;
  if (process.env.OPENVIKING_API_KEY || process.env.OPENVIKING_BEARER_TOKEN) config.apiKey = creds.apiKey;
  if (process.env.OPENVIKING_ACCOUNT) config.account = creds.account;
  if (process.env.OPENVIKING_USER) config.user = creds.user;
  if (process.env.OPENVIKING_PEER_ID) config.peerId = creds.peerId;
  if (process.env.OPENVIKING_WORKSPACE_PEER !== undefined) {
    config.workspacePeer = envBool(process.env.OPENVIKING_WORKSPACE_PEER, config.workspacePeer);
  }
  if (process.env.OPENVIKING_RECALL_PEER_SCOPE) {
    config.recallPeerScope = process.env.OPENVIKING_RECALL_PEER_SCOPE === "actor" ? "actor" : "all";
  }
  if (process.env.OPENVIKING_RECALL_LIMIT) {
    config.recallLimit = Number(process.env.OPENVIKING_RECALL_LIMIT);
    config.recallLimitConfigured = true;
  }
  if (process.env.OPENVIKING_RECALL_QUERY_EXPANSION) {
    config.recallQueryExpansion = process.env.OPENVIKING_RECALL_QUERY_EXPANSION === "off" ? "off" : "auto";
    config.recallQueryExpansionConfigured = true;
  }
  if (process.env.OPENVIKING_RECALL_LEDGER !== undefined) {
    config.recallLedger = envBool(process.env.OPENVIKING_RECALL_LEDGER, config.recallLedger);
  }

  config.recallLimit = clampInt(config.recallLimit, 1, 50, DEFAULT_CONFIG.recallLimit);
  config.recallMaxContentChars = clampInt(config.recallMaxContentChars, 100, 5000, DEFAULT_CONFIG.recallMaxContentChars);
  config.recallTokenBudget = clampInt(config.recallTokenBudget, 200, 50000, DEFAULT_CONFIG.recallTokenBudget);
  config.scoreThreshold = clampNumber(config.scoreThreshold, 0, 1, DEFAULT_CONFIG.scoreThreshold);
  config.minQueryLength = clampInt(config.minQueryLength, 1, 64, DEFAULT_CONFIG.minQueryLength);
  config.profileTokenBudget = clampInt(config.profileTokenBudget, 500, 50000, DEFAULT_CONFIG.profileTokenBudget);
  config.resumeContextBudget = clampInt(config.resumeContextBudget, 1024, 128000, DEFAULT_CONFIG.resumeContextBudget);
  config.commitTokenThreshold = clampInt(config.commitTokenThreshold, 1000, 1000000, DEFAULT_CONFIG.commitTokenThreshold);
  config.commitKeepRecentCount = clampInt(config.commitKeepRecentCount, 0, 1000, DEFAULT_CONFIG.commitKeepRecentCount);
  config.takeoverEnabled = config.takeoverEnabled !== false;
  config.takeoverTokenThreshold = clampInt(config.takeoverTokenThreshold, 1, 1000000, DEFAULT_CONFIG.takeoverTokenThreshold);
  config.takeoverKeepRecentTurns = clampInt(config.takeoverKeepRecentTurns, 0, 100, DEFAULT_CONFIG.takeoverKeepRecentTurns);
  config.takeoverOverviewBudget = clampInt(config.takeoverOverviewBudget, 100, 50000, DEFAULT_CONFIG.takeoverOverviewBudget);
  config.takeoverOverviewPollMs = clampInt(config.takeoverOverviewPollMs, 0, 60000, DEFAULT_CONFIG.takeoverOverviewPollMs);
  config.takeoverOverviewPollMax = clampInt(config.takeoverOverviewPollMax, 1, 120, DEFAULT_CONFIG.takeoverOverviewPollMax);
  config.captureMaxLength = clampInt(config.captureMaxLength, 200, 100000, DEFAULT_CONFIG.captureMaxLength);
  config.captureToolMaxChars = clampInt(config.captureToolMaxChars, 200, 1000000, DEFAULT_CONFIG.captureToolMaxChars);
  config.captureMode = config.captureMode === "keyword" ? "keyword" : "semantic";
  config.recallPeerScope = config.recallPeerScope === "actor" ? "actor" : "all";
  config.recallQueryExpansion = config.recallQueryExpansion === "off" ? "off" : "auto";
  config.recallLedger = config.recallLedger !== false;
  if (!Array.isArray(config.bypassPatterns)) config.bypassPatterns = [];
  config.peerId = resolveEffectivePeerId({ cfg: config as any, cwd: process.cwd() }).peerId;
  return config;
}

function envBool(value: string, fallback: boolean): boolean {
  const lower = String(value || "").trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no" || lower === "off") return false;
  if (lower === "1" || lower === "true" || lower === "yes" || lower === "on") return true;
  return fallback;
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const next = Math.round(Number(value));
  if (!Number.isFinite(next)) return fallback;
  return Math.max(min, Math.min(max, next));
}

function clampNumber(value: unknown, min: number, max: number, fallback: number): number {
  const next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return Math.max(min, Math.min(max, next));
}
