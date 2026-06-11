import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";

export interface OVConfig {
  enabled: boolean;
  endpoint: string;
  apiKey: string;
  account: string;
  user: string;
  agentId: string;
  syncTurns: boolean;
  recallBudget: number;
  recallMaxContentChars: number;
  recallPreferAbstract: boolean;
  recallLimit: number;
  recallScoreThreshold: number;
  recallMinQueryLength: number;
  profileBudget: number;
  resumeContextBudget: number;
  indexBudget: number;
  commitTokenThreshold: number;
  commitOnShutdown: boolean;
  captureToolResults: boolean;
  captureMode: "semantic" | "keyword";
  captureMaxLength: number;
  captureAssistantTurns: boolean;
  mirrorMemoryWrites: boolean;
  writeQueueFlushInterval: number;
  writeQueueFlushThreshold: number;
  bypassPatterns: string[];
  logLevel: "silent" | "error" | "info";
}

const DEFAULT_CONFIG: OVConfig = {
  enabled: true,
  endpoint: "http://127.0.0.1:1933",
  apiKey: "",
  account: "",
  user: "",
  agentId: "pi",
  syncTurns: true,
  recallBudget: 2000,
  recallMaxContentChars: 500,
  recallPreferAbstract: true,
  recallLimit: 6,
  recallScoreThreshold: 0.35,
  recallMinQueryLength: 3,
  profileBudget: 10000,
  resumeContextBudget: 2000,
  indexBudget: 2000,
  commitTokenThreshold: 20000,
  commitOnShutdown: true,
  captureToolResults: false,
  captureMode: "semantic",
  captureMaxLength: 24000,
  captureAssistantTurns: true,
  mirrorMemoryWrites: true,
  writeQueueFlushInterval: 5000,
  writeQueueFlushThreshold: 5,
  bypassPatterns: [],
  logLevel: "error",
};

export function loadConfig(extensionDir: string): OVConfig {
  const configPath = join(extensionDir, "config.json");
  if (!existsSync(configPath)) return { ...DEFAULT_CONFIG };

  try {
    const file = JSON.parse(readFileSync(configPath, "utf8"));
    return { ...DEFAULT_CONFIG, ...file };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}
