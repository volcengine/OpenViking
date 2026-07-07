import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { resolveOpenVikingCredentials } from "./shared/credentials.mjs";

export interface OVConfig {
  enabled: boolean;
  endpoint: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  syncTurns: boolean;
  recallTokenBudget: number;
  recallMaxContentChars: number;
  recallPreferAbstract: boolean;
  recallLimit: number;
  scoreThreshold: number;
  minQueryLength: number;
  profileTokenBudget: number;
  resumeContextBudget: number;
  commitTokenThreshold: number;
  commitKeepRecentCount: number;
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
  syncTurns: true,
  recallTokenBudget: 2000,
  recallMaxContentChars: 500,
  recallPreferAbstract: true,
  recallLimit: 6,
  scoreThreshold: 0.35,
  minQueryLength: 3,
  profileTokenBudget: 10000,
  resumeContextBudget: 32000,
  commitTokenThreshold: 20000,
  commitKeepRecentCount: 10,
  captureToolResults: false,
  captureMode: "semantic",
  captureMaxLength: 24000,
  captureToolMaxChars: 2000,
  captureAssistantTurns: true,
  bypassPatterns: [],
  logLevel: "error",
};

export function loadConfig(extensionDir: string): OVConfig {
  const configPath = join(extensionDir, "config.json");
  let file: any = {};
  try {
    if (existsSync(configPath)) file = JSON.parse(readFileSync(configPath, "utf8"));
  } catch {
    file = {};
  }

  const creds = resolveOpenVikingCredentials();
  const config: OVConfig = {
    ...DEFAULT_CONFIG,
    ...file,
    endpoint: creds.baseUrl,
    apiKey: creds.apiKey,
    account: creds.account,
    user: creds.user,
    peerId: creds.peerId,
    recallTokenBudget: file.recallTokenBudget ?? file.recallBudget ?? DEFAULT_CONFIG.recallTokenBudget,
    scoreThreshold: file.scoreThreshold ?? file.recallScoreThreshold ?? DEFAULT_CONFIG.scoreThreshold,
    minQueryLength: file.minQueryLength ?? file.recallMinQueryLength ?? DEFAULT_CONFIG.minQueryLength,
    profileTokenBudget: file.profileTokenBudget ?? file.profileBudget ?? DEFAULT_CONFIG.profileTokenBudget,
  };

  if (process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL) config.endpoint = creds.baseUrl;
  if (process.env.OPENVIKING_API_KEY || process.env.OPENVIKING_BEARER_TOKEN) config.apiKey = creds.apiKey;
  if (process.env.OPENVIKING_ACCOUNT) config.account = creds.account;
  if (process.env.OPENVIKING_USER) config.user = creds.user;
  if (process.env.OPENVIKING_PEER_ID) config.peerId = creds.peerId;

  config.recallLimit = clampInt(config.recallLimit, 1, 50, DEFAULT_CONFIG.recallLimit);
  config.recallMaxContentChars = clampInt(config.recallMaxContentChars, 100, 5000, DEFAULT_CONFIG.recallMaxContentChars);
  config.recallTokenBudget = clampInt(config.recallTokenBudget, 200, 50000, DEFAULT_CONFIG.recallTokenBudget);
  config.scoreThreshold = clampNumber(config.scoreThreshold, 0, 1, DEFAULT_CONFIG.scoreThreshold);
  config.minQueryLength = clampInt(config.minQueryLength, 1, 64, DEFAULT_CONFIG.minQueryLength);
  config.profileTokenBudget = clampInt(config.profileTokenBudget, 500, 50000, DEFAULT_CONFIG.profileTokenBudget);
  config.resumeContextBudget = clampInt(config.resumeContextBudget, 1024, 128000, DEFAULT_CONFIG.resumeContextBudget);
  config.commitTokenThreshold = clampInt(config.commitTokenThreshold, 1000, 1000000, DEFAULT_CONFIG.commitTokenThreshold);
  config.commitKeepRecentCount = clampInt(config.commitKeepRecentCount, 0, 1000, DEFAULT_CONFIG.commitKeepRecentCount);
  config.captureMaxLength = clampInt(config.captureMaxLength, 200, 100000, DEFAULT_CONFIG.captureMaxLength);
  config.captureToolMaxChars = clampInt(config.captureToolMaxChars, 200, 20000, DEFAULT_CONFIG.captureToolMaxChars);
  config.captureMode = config.captureMode === "keyword" ? "keyword" : "semantic";
  if (!Array.isArray(config.bypassPatterns)) config.bypassPatterns = [];
  return config;
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
