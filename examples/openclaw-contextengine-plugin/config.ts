import type { ContextEngineOpenVikingPluginConfig } from "./types.js";

type Input = {
  mode?: unknown;
  connection?: unknown;
  retrieval?: unknown;
  profileInjection?: unknown;
  ingestion?: unknown;
};

type ConnectionInput = {
  baseUrl?: unknown;
  timeoutMs?: unknown;
  apiKey?: unknown;
  agentId?: unknown;
};

type RetrievalInput = {
  enabled?: unknown;
  lastNUserMessages?: unknown;
  skipGreeting?: unknown;
  minQueryChars?: unknown;
  targetUri?: unknown;
  injectMode?: unknown;
  scoreThreshold?: unknown;
};

type IngestionInput = {
  writeMode?: unknown;
  maxBatchMessages?: unknown;
};

type ProfileInjectionInput = {
  enabled?: unknown;
  qualityGateMinScore?: unknown;
  maxChars?: unknown;
};

const TOP_LEVEL_KEYS = new Set(["mode", "connection", "retrieval", "profileInjection", "ingestion"]);
const CONNECTION_KEYS = new Set(["baseUrl", "timeoutMs", "apiKey", "agentId"]);
const RETRIEVAL_KEYS = new Set([
  "enabled",
  "lastNUserMessages",
  "skipGreeting",
  "minQueryChars",
  "targetUri",
  "injectMode",
  "scoreThreshold",
]);
const INGESTION_KEYS = new Set(["writeMode", "maxBatchMessages"]);
const PROFILE_INJECTION_KEYS = new Set(["enabled", "qualityGateMinScore", "maxChars"]);
const DEFAULT_BASE_URL = "http://127.0.0.1:1933";
const DEFAULT_TIMEOUT_MS = 15000;
const DEFAULT_AGENT_ID = "default";
const DEFAULT_TARGET_URI = "viking://user/memories";

function assertKnownKeys(value: Record<string, unknown>, allowed: Set<string>, scope: string): void {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length > 0) {
    throw new Error(`${scope} has unknown keys: ${unknown.join(", ")}`);
  }
}

function asObject(value: unknown, scope: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${scope} must be an object`);
  }
  return value as Record<string, unknown>;
}

function clamp(value: unknown, min: number, max: number, fallback: number): number {
  const n = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.max(min, Math.min(max, n));
}

function normalizeOptionalString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function resolveDefaultBaseUrl(): string {
  const fromEnv = process.env.OPENVIKING_BASE_URL ?? process.env.OPENVIKING_URL;
  if (typeof fromEnv === "string" && fromEnv.trim().length > 0) {
    return fromEnv.trim();
  }
  return DEFAULT_BASE_URL;
}

export function parseConfig(value: unknown): ContextEngineOpenVikingPluginConfig {
  if (typeof value !== "undefined" && (!value || typeof value !== "object" || Array.isArray(value))) {
    throw new Error("contextengine-openviking config must be an object");
  }

  const input: Input =
    value && typeof value === "object" && !Array.isArray(value) ? (value as Input) : {};

  const topLevel = input as unknown as Record<string, unknown>;
  assertKnownKeys(topLevel, TOP_LEVEL_KEYS, "contextengine-openviking config");

  const connectionInput: ConnectionInput =
    typeof input.connection === "undefined"
      ? {}
      : (asObject(input.connection, "connection") as ConnectionInput);
  const retrievalInput: RetrievalInput =
    typeof input.retrieval === "undefined" ? {} : (asObject(input.retrieval, "retrieval") as RetrievalInput);
  const profileInjectionInput: ProfileInjectionInput =
    typeof input.profileInjection === "undefined"
      ? {}
      : (asObject(input.profileInjection, "profileInjection") as ProfileInjectionInput);
  const ingestionInput: IngestionInput =
    typeof input.ingestion === "undefined" ? {} : (asObject(input.ingestion, "ingestion") as IngestionInput);

  assertKnownKeys(retrievalInput as Record<string, unknown>, RETRIEVAL_KEYS, "retrieval");
  assertKnownKeys(
    profileInjectionInput as Record<string, unknown>,
    PROFILE_INJECTION_KEYS,
    "profileInjection",
  );
  assertKnownKeys(connectionInput as Record<string, unknown>, CONNECTION_KEYS, "connection");
  assertKnownKeys(ingestionInput as Record<string, unknown>, INGESTION_KEYS, "ingestion");

  const mode = input.mode ?? "local";
  if (mode !== "local" && mode !== "remote") {
    throw new Error('mode must be "local" or "remote"');
  }

  const injectMode = retrievalInput.injectMode ?? "simulated_tool_result";
  if (injectMode !== "simulated_tool_result" && injectMode !== "text") {
    throw new Error('retrieval.injectMode must be "simulated_tool_result" or "text"');
  }

  const writeMode = ingestionInput.writeMode ?? "compact_batch";
  if (writeMode !== "compact_batch" && writeMode !== "after_turn_batch") {
    throw new Error('ingestion.writeMode must be "compact_batch" or "after_turn_batch"');
  }

  if (typeof retrievalInput.enabled !== "undefined" && typeof retrievalInput.enabled !== "boolean") {
    throw new Error("retrieval.enabled must be a boolean");
  }
  if (
    typeof retrievalInput.skipGreeting !== "undefined" &&
    typeof retrievalInput.skipGreeting !== "boolean"
  ) {
    throw new Error("retrieval.skipGreeting must be a boolean");
  }
  if (
    typeof profileInjectionInput.enabled !== "undefined" &&
    typeof profileInjectionInput.enabled !== "boolean"
  ) {
    throw new Error("profileInjection.enabled must be a boolean");
  }
  if (typeof connectionInput.baseUrl !== "undefined" && typeof connectionInput.baseUrl !== "string") {
    throw new Error("connection.baseUrl must be a string");
  }
  if (
    typeof connectionInput.timeoutMs !== "undefined" &&
    (typeof connectionInput.timeoutMs !== "number" || !Number.isFinite(connectionInput.timeoutMs))
  ) {
    throw new Error("connection.timeoutMs must be a number");
  }
  if (typeof connectionInput.apiKey !== "undefined" && typeof connectionInput.apiKey !== "string") {
    throw new Error("connection.apiKey must be a string");
  }
  if (typeof connectionInput.agentId !== "undefined" && typeof connectionInput.agentId !== "string") {
    throw new Error("connection.agentId must be a string");
  }
  if (typeof retrievalInput.targetUri !== "undefined" && typeof retrievalInput.targetUri !== "string") {
    throw new Error("retrieval.targetUri must be a string");
  }

  const baseUrl = (
    normalizeOptionalString(connectionInput.baseUrl) ?? resolveDefaultBaseUrl()
  ).replace(/\/+$/, "");
  const apiKey = normalizeOptionalString(connectionInput.apiKey) ?? process.env.OPENVIKING_API_KEY ?? "";
  const agentId = normalizeOptionalString(connectionInput.agentId) ?? DEFAULT_AGENT_ID;
  const targetUri = normalizeOptionalString(retrievalInput.targetUri) ?? DEFAULT_TARGET_URI;

  return {
    mode,
    connection: {
      baseUrl,
      timeoutMs: Math.floor(clamp(connectionInput.timeoutMs, 1000, 120000, DEFAULT_TIMEOUT_MS)),
      apiKey,
      agentId,
    },
    retrieval: {
      enabled: retrievalInput.enabled ?? true,
      lastNUserMessages: Math.floor(clamp(retrievalInput.lastNUserMessages, 1, 50, 5)),
      skipGreeting: retrievalInput.skipGreeting ?? true,
      minQueryChars: Math.floor(clamp(retrievalInput.minQueryChars, 1, 200, 4)),
      targetUri,
      injectMode,
      scoreThreshold: clamp(retrievalInput.scoreThreshold, 0, 1, 0.15),
    },
    profileInjection: {
      enabled: profileInjectionInput.enabled ?? true,
      qualityGateMinScore: clamp(profileInjectionInput.qualityGateMinScore, 0, 1, 0.7),
      maxChars: Math.floor(clamp(profileInjectionInput.maxChars, 200, 10000, 1200)),
    },
    ingestion: {
      writeMode,
      maxBatchMessages: Math.floor(clamp(ingestionInput.maxBatchMessages, 1, 2000, 200)),
    },
  };
}
