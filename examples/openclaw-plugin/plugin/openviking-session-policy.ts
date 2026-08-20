import type {
  OpenVikingClient,
  OVAutoCommitPolicy,
  SessionConfigResult,
} from "../client.js";

const MAX_IDLE_TIMEOUT_SECONDS = 7 * 24 * 60 * 60;
const LEGACY_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const legacyUntilByClient = new WeakMap<OpenVikingClient, number>();

export type SessionPolicyApplyResult = {
  ensured: boolean;
  applied: boolean;
  idleActive: boolean | null;
  method: "disabled" | "cached-legacy" | "create" | "create-legacy" | "patch" | "patch-legacy" | "error";
};

export function normalizeIdleTimeoutSeconds(value: unknown, fallback = 3600): number {
  const normalizedFallback = Number.isFinite(Number(fallback))
    ? Math.min(MAX_IDLE_TIMEOUT_SECONDS, Math.max(0, Math.floor(Number(fallback))))
    : 3600;
  if (
    typeof value === "string"
    && ["off", "false", "no"].includes(value.trim().toLowerCase())
  ) {
    return 0;
  }
  if (value == null || value === "") return normalizedFallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return normalizedFallback;
  return Math.min(MAX_IDLE_TIMEOUT_SECONDS, Math.max(0, Math.floor(numeric)));
}

export function buildIdleAutoCommitPolicy(seconds: number): OVAutoCommitPolicy | null {
  const idleTimeoutSeconds = normalizeIdleTimeoutSeconds(seconds, 0);
  if (idleTimeoutSeconds <= 0) return null;
  return {
    idle_timeout_seconds: idleTimeoutSeconds,
    pending_token_threshold: 0,
    message_count_threshold: 0,
  };
}

function readIdleActive(result: SessionConfigResult): boolean | null {
  return typeof result.auto_commit_idle_enabled === "boolean"
    ? result.auto_commit_idle_enabled
    : null;
}

function supportsPolicy(result: SessionConfigResult): boolean {
  return Object.prototype.hasOwnProperty.call(result, "auto_commit_policy");
}

function isAlreadyExists(error: unknown): boolean {
  return String(error).includes("[ALREADY_EXISTS]");
}

function isLegacyCreateError(error: unknown): boolean {
  return /HTTP 422|\[(VALIDATION_ERROR|INVALID_ARGUMENT)\]/.test(String(error));
}

function isLegacyPatchError(error: unknown): boolean {
  return /HTTP (404|405|422)|\[(NOT_FOUND|VALIDATION_ERROR|INVALID_ARGUMENT)\]/.test(
    String(error),
  );
}

async function ensureLegacySession(client: OpenVikingClient, sessionId: string): Promise<boolean> {
  try {
    await client.createSession(sessionId);
    return true;
  } catch (error) {
    return isAlreadyExists(error);
  }
}

export async function applyOpenVikingSessionPolicy(
  client: OpenVikingClient,
  sessionId: string,
  idleTimeoutSeconds: number,
  now = Date.now(),
): Promise<SessionPolicyApplyResult> {
  const policy = buildIdleAutoCommitPolicy(idleTimeoutSeconds);
  if (!policy) {
    return { ensured: false, applied: false, idleActive: null, method: "disabled" };
  }

  if ((legacyUntilByClient.get(client) ?? 0) > now) {
    const ensured = await ensureLegacySession(client, sessionId);
    return { ensured, applied: false, idleActive: null, method: "cached-legacy" };
  }

  try {
    const created = await client.createSession(sessionId, { autoCommitPolicy: policy });
    if (supportsPolicy(created)) {
      return {
        ensured: true,
        applied: true,
        idleActive: readIdleActive(created),
        method: "create",
      };
    }
    legacyUntilByClient.set(client, now + LEGACY_CACHE_TTL_MS);
    return { ensured: true, applied: false, idleActive: null, method: "create-legacy" };
  } catch (error) {
    if (isLegacyCreateError(error)) {
      const ensured = await ensureLegacySession(client, sessionId);
      if (ensured) legacyUntilByClient.set(client, now + LEGACY_CACHE_TTL_MS);
      return {
        ensured,
        applied: false,
        idleActive: null,
        method: ensured ? "create-legacy" : "error",
      };
    }
    if (!isAlreadyExists(error)) {
      return { ensured: false, applied: false, idleActive: null, method: "error" };
    }
  }

  try {
    const updated = await client.updateSessionConfig(sessionId, policy);
    if (supportsPolicy(updated)) {
      return {
        ensured: true,
        applied: true,
        idleActive: readIdleActive(updated),
        method: "patch",
      };
    }
    legacyUntilByClient.set(client, now + LEGACY_CACHE_TTL_MS);
    return { ensured: true, applied: false, idleActive: null, method: "patch-legacy" };
  } catch (error) {
    if (isLegacyPatchError(error)) {
      legacyUntilByClient.set(client, now + LEGACY_CACHE_TTL_MS);
      return { ensured: true, applied: false, idleActive: null, method: "patch-legacy" };
    }
    return { ensured: true, applied: false, idleActive: null, method: "error" };
  }
}
