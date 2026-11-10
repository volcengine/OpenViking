import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { buildUserAgent, resolveOpenVikingCredentials } from "./shared/credentials.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

/** Hand-maintained: this extension ships no manifest to read a version from. */
export const EXTENSION_VERSION = "0.1.0";

/**
 * Agent-driven context windows. Every number is clamped on load: these bound
 * a blocking tool call and the size of the frozen window header, so an absurd
 * value in `config.json` must not be able to hang a reset or blow the header
 * past the budget the model reads it under.
 */
export interface OVContextWindowConfig {
  /** Total budget for one reset: sync, barrier, commit and archive wait. */
  resetDeadlineMs: number;
  /** Delay between `.overview.md` polls while Phase 2 runs. */
  archivePollMs: number;
  /** Non-blocking retries at `turn_end` when a window opened without its overview. */
  overviewRefreshMaxAttempts: number;
  /** Token budget for the Working Memory block in the window header. */
  overviewBudget: number;
  /** Token budget for the agent's handoff notes in the window header. */
  notesBudget: number;
  /** Token budget for the last user message carried into the new window. */
  pendingRequestBudget: number;
  /** Context usage that earns one soft "checkpoint soon" reminder. */
  softPercent: number;
  /** Context usage that earns one hard "reset now" reminder; never below `softPercent`. */
  hardPercent: number;
  /** Idle gap after which the status line suggests considering a new window. */
  idleGapMinutes: number;
  /** Append a one-line context status after every user prompt. */
  statusEveryTurn: boolean;
  /** Per-item cap for `history` reads out of an archived `messages.jsonl`. */
  historyItemMaxChars: number;
  /**
   * How long after a reset `session_before_compact` refuses to archive again.
   * A provider hiccup can make pi estimate from the untransformed list and
   * compact a window that just opened; inside this guard the current header is
   * returned as the summary instead.
   */
  recentResetGuardMs: number;
}

export interface OVConfig {
  enabled: boolean;
  endpoint: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  userAgent: string;
  harness: string;
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
  scoreThreshold: number;
  minQueryLength: number;
  profileTokenBudget: number;
  commitKeepRecentCount: number;
  /**
   * Fixed on in this fork: the archive is the model's only way back to a
   * closed context window, so capture stays faithful to what was said.
   */
  faithfulCapture: true;
  captureToolResults: boolean;
  captureMaxLength: number;
  captureToolMaxChars: number;
  captureAssistantTurns: boolean;
  bypassPatterns: string[];
  logLevel: "silent" | "error" | "info";
  debugLogPath: string;
  contextWindow: OVContextWindowConfig;
}

const DEFAULT_CONFIG: OVConfig = {
  enabled: true,
  endpoint: "http://127.0.0.1:1933",
  apiKey: "",
  account: "",
  user: "",
  peerId: "",
  userAgent: "",
  harness: "pi",
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
  scoreThreshold: 0.35,
  minQueryLength: 3,
  profileTokenBudget: 10000,
  commitKeepRecentCount: 10,
  faithfulCapture: true,
  // The `history` tool promises the archive can be read back, so tool output
  // has to reach OpenViking — unlike the non-experimental extension.
  captureToolResults: true,
  captureMaxLength: 24000,
  captureToolMaxChars: 1000000,
  captureAssistantTurns: true,
  bypassPatterns: [],
  logLevel: "error",
  debugLogPath: "",
  contextWindow: {
    resetDeadlineMs: 60000,
    archivePollMs: 2000,
    overviewRefreshMaxAttempts: 20,
    overviewBudget: 3000,
    notesBudget: 1500,
    pendingRequestBudget: 400,
    softPercent: 70,
    hardPercent: 85,
    idleGapMinutes: 30,
    statusEveryTurn: true,
    historyItemMaxChars: 8000,
    recentResetGuardMs: 60000,
  },
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

  const creds = resolveOpenVikingCredentials();
  // Nested block: keys are picked one by one, so an unknown key in
  // `contextWindow` is ignored instead of landing in the config.
  const cw = file.contextWindow && typeof file.contextWindow === "object" ? file.contextWindow : {};
  const cwDefaults = DEFAULT_CONFIG.contextWindow;
  const config: OVConfig = {
    ...DEFAULT_CONFIG,
    ...file,
    endpoint: creds.baseUrl,
    apiKey: creds.apiKey,
    account: creds.account,
    user: creds.user,
    peerId: creds.peerId || (typeof file.peerId === "string" ? file.peerId : DEFAULT_CONFIG.peerId),
    userAgent: buildUserAgent("pi", EXTENSION_VERSION),
    harness: "pi",
    recallLimitConfigured: Object.prototype.hasOwnProperty.call(file, "recallLimit"),
    recallQueryExpansionConfigured: Object.prototype.hasOwnProperty.call(file, "recallQueryExpansion"),
    recallTokenBudget: file.recallTokenBudget ?? file.recallBudget ?? DEFAULT_CONFIG.recallTokenBudget,
    scoreThreshold: file.scoreThreshold ?? file.recallScoreThreshold ?? DEFAULT_CONFIG.scoreThreshold,
    minQueryLength: file.minQueryLength ?? file.recallMinQueryLength ?? DEFAULT_CONFIG.minQueryLength,
    profileTokenBudget: file.profileTokenBudget ?? file.profileBudget ?? DEFAULT_CONFIG.profileTokenBudget,
    faithfulCapture: true,
    contextWindow: {
      resetDeadlineMs: cw.resetDeadlineMs ?? cwDefaults.resetDeadlineMs,
      archivePollMs: cw.archivePollMs ?? cwDefaults.archivePollMs,
      overviewRefreshMaxAttempts: cw.overviewRefreshMaxAttempts ?? cwDefaults.overviewRefreshMaxAttempts,
      overviewBudget: cw.overviewBudget ?? cwDefaults.overviewBudget,
      notesBudget: cw.notesBudget ?? cwDefaults.notesBudget,
      pendingRequestBudget: cw.pendingRequestBudget ?? cwDefaults.pendingRequestBudget,
      softPercent: cw.softPercent ?? cwDefaults.softPercent,
      hardPercent: cw.hardPercent ?? cwDefaults.hardPercent,
      idleGapMinutes: cw.idleGapMinutes ?? cwDefaults.idleGapMinutes,
      statusEveryTurn: cw.statusEveryTurn ?? cwDefaults.statusEveryTurn,
      historyItemMaxChars: cw.historyItemMaxChars ?? cwDefaults.historyItemMaxChars,
      recentResetGuardMs: cw.recentResetGuardMs ?? cwDefaults.recentResetGuardMs,
    },
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
  // OPENVIKING_DEBUG_LOG is the shared spelling; OV_DEBUG_LOG is pi's older
  // name, kept working so existing setups still log.
  const debugLogEnv = process.env.OPENVIKING_DEBUG_LOG || process.env.OV_DEBUG_LOG;
  if (debugLogEnv) config.debugLogPath = debugLogEnv;
  if (process.env.OPENVIKING_CONTEXT_STATUS_EVERY_TURN !== undefined) {
    config.contextWindow.statusEveryTurn = envBool(
      process.env.OPENVIKING_CONTEXT_STATUS_EVERY_TURN,
      config.contextWindow.statusEveryTurn,
    );
  }
  if (process.env.OPENVIKING_CONTEXT_RESET_DEADLINE_MS) {
    config.contextWindow.resetDeadlineMs = Number(process.env.OPENVIKING_CONTEXT_RESET_DEADLINE_MS);
  }

  config.recallLimit = clampInt(config.recallLimit, 1, 50, DEFAULT_CONFIG.recallLimit);
  config.recallMaxContentChars = clampInt(config.recallMaxContentChars, 100, 5000, DEFAULT_CONFIG.recallMaxContentChars);
  config.recallTokenBudget = clampInt(config.recallTokenBudget, 200, 50000, DEFAULT_CONFIG.recallTokenBudget);
  config.scoreThreshold = clampNumber(config.scoreThreshold, 0, 1, DEFAULT_CONFIG.scoreThreshold);
  config.minQueryLength = clampInt(config.minQueryLength, 1, 64, DEFAULT_CONFIG.minQueryLength);
  config.profileTokenBudget = clampInt(config.profileTokenBudget, 500, 50000, DEFAULT_CONFIG.profileTokenBudget);
  config.commitKeepRecentCount = clampInt(config.commitKeepRecentCount, 0, 1000, DEFAULT_CONFIG.commitKeepRecentCount);
  config.captureToolResults = config.captureToolResults !== false;
  config.captureMaxLength = clampInt(config.captureMaxLength, 200, 100000, DEFAULT_CONFIG.captureMaxLength);
  config.captureToolMaxChars = clampInt(config.captureToolMaxChars, 200, 1000000, DEFAULT_CONFIG.captureToolMaxChars);
  const window = config.contextWindow;
  window.resetDeadlineMs = clampInt(window.resetDeadlineMs, 5000, 600000, cwDefaults.resetDeadlineMs);
  window.archivePollMs = clampInt(window.archivePollMs, 250, 30000, cwDefaults.archivePollMs);
  window.overviewRefreshMaxAttempts = clampInt(window.overviewRefreshMaxAttempts, 0, 200, cwDefaults.overviewRefreshMaxAttempts);
  window.overviewBudget = clampInt(window.overviewBudget, 100, 50000, cwDefaults.overviewBudget);
  window.notesBudget = clampInt(window.notesBudget, 100, 20000, cwDefaults.notesBudget);
  window.pendingRequestBudget = clampInt(window.pendingRequestBudget, 0, 8000, cwDefaults.pendingRequestBudget);
  window.softPercent = clampInt(window.softPercent, 10, 99, cwDefaults.softPercent);
  window.hardPercent = clampInt(window.hardPercent, 10, 99, cwDefaults.hardPercent);
  // The hard reminder is the last one before the harness compacts on its own,
  // so it can never fire before the soft one.
  if (window.hardPercent < window.softPercent) window.hardPercent = window.softPercent;
  window.idleGapMinutes = clampInt(window.idleGapMinutes, 0, 1440, cwDefaults.idleGapMinutes);
  window.historyItemMaxChars = clampInt(window.historyItemMaxChars, 500, 100000, cwDefaults.historyItemMaxChars);
  window.recentResetGuardMs = clampInt(window.recentResetGuardMs, 0, 600000, cwDefaults.recentResetGuardMs);
  window.statusEveryTurn = envBool(String(window.statusEveryTurn), cwDefaults.statusEveryTurn);
  config.recallPeerScope = config.recallPeerScope === "actor" ? "actor" : "all";
  config.recallQueryExpansion = config.recallQueryExpansion === "off" ? "off" : "auto";
  if (!Array.isArray(config.bypassPatterns)) config.bypassPatterns = [];
  config.debugLogPath = typeof config.debugLogPath === "string" ? config.debugLogPath.trim() : "";
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
