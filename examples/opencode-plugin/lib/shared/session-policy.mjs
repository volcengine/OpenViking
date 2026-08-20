// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const LEGACY_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const MAX_IDLE_TIMEOUT_SECONDS = 7 * 24 * 60 * 60;
const DISABLED_VALUES = new Set(["off", "false", "no"]);

function stateFile(cacheKey) {
  const root = String(process.env.OPENVIKING_STATE_DIR || "").trim()
    || join(homedir(), ".openviking", "state");
  const digest = createHash("sha256").update(String(cacheKey || "default")).digest("hex").slice(0, 20);
  return join(root, `session-policy-${digest}.json`);
}

async function readLegacyUntil(path) {
  try {
    const parsed = JSON.parse(await readFile(path, "utf8"));
    return Number(parsed?.legacyUntil || 0);
  } catch {
    return 0;
  }
}

async function markLegacy(path, now) {
  try {
    await mkdir(dirname(path), { recursive: true });
    const tmp = `${path}.${process.pid}.tmp`;
    await writeFile(tmp, JSON.stringify({ legacyUntil: now + LEGACY_CACHE_TTL_MS }), "utf8");
    await rename(tmp, path);
  } catch {
    // Best effort: compatibility detection must never block a harness hook.
  }
}

function isAlreadyExists(result) {
  return Number(result?.status || 0) === 409
    && result?.error?.code === "ALREADY_EXISTS";
}

function isRetryable(result) {
  if (!result || result.ok) return false;
  const status = Number(result.status || 0);
  if (!status || status === 408 || status === 429 || status >= 500) return true;
  return status === 409 && result.error?.details?.retryable === true;
}

function hasPolicyEcho(result) {
  return Boolean(
    result
    && typeof result === "object"
    && Object.prototype.hasOwnProperty.call(result, "auto_commit_policy"),
  );
}

// Only a body that actually looks like a session result proves the server is
// old. Plugin fetch helpers turn an unparseable 200 (truncated stream, proxy
// interstitial) into {ok:true, result:{}} or a raw string, and caching that as
// "legacy" would silently disable the idle backstop for the whole TTL.
function looksLikeSessionResult(result) {
  return Boolean(
    result
    && typeof result === "object"
    && Object.prototype.hasOwnProperty.call(result, "session_id"),
  );
}

async function callFetch(fetchJSON, path, init, options) {
  try {
    const result = await fetchJSON(path, init, options);
    return result && typeof result === "object"
      ? result
      : { ok: false, status: 0, error: { message: "empty response" } };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: { message: error instanceof Error ? error.message : String(error) },
    };
  }
}

function outcome({
  ensured = false,
  applied = false,
  idleActive = null,
  idleTimeoutSeconds = 0,
  method,
  raw = null,
}) {
  return {
    ensured,
    applied,
    idleActive,
    idleTimeoutSeconds,
    method,
    retryable: isRetryable(raw),
    status: Number(raw?.status || (raw?.ok ? 200 : 0)),
    raw,
  };
}

export function normalizeIdleTimeoutSeconds(value, fallback = 3600) {
  const normalizedFallback = Number.isFinite(Number(fallback))
    ? Math.min(MAX_IDLE_TIMEOUT_SECONDS, Math.max(0, Math.floor(Number(fallback))))
    : 3600;
  if (typeof value === "string" && DISABLED_VALUES.has(value.trim().toLowerCase())) return 0;
  if (value == null || value === "") return normalizedFallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return normalizedFallback;
  return Math.min(MAX_IDLE_TIMEOUT_SECONDS, Math.max(0, Math.floor(numeric)));
}

export function buildIdleAutoCommitPolicy(seconds) {
  const idleTimeoutSeconds = normalizeIdleTimeoutSeconds(seconds, 0);
  if (idleTimeoutSeconds <= 0) return null;
  return {
    idle_timeout_seconds: idleTimeoutSeconds,
    pending_token_threshold: 0,
    message_count_threshold: 0,
  };
}

export function readIdleActive(result) {
  return typeof result?.auto_commit_idle_enabled === "boolean"
    ? result.auto_commit_idle_enabled
    : null;
}

export async function applySessionAutoCommitPolicy(
  fetchJSON,
  sessionId,
  policy,
  {
    cacheKey = "default",
    actorPeerId = "",
    log = () => {},
    ensureOnLegacy = true,
    legacyCachePath,
    timeoutMs = 0,
    now = Date.now(),
  } = {},
) {
  const idleTimeoutSeconds = Number(policy?.idle_timeout_seconds || 0);
  if (!policy || idleTimeoutSeconds <= 0) {
    return outcome({ method: "disabled", idleTimeoutSeconds: 0 });
  }

  const encodedSessionId = encodeURIComponent(sessionId);
  const createPath = "/api/v1/sessions";
  const patchPath = `/api/v1/sessions/${encodedSessionId}/config`;
  const fetchOptions = {
    ...(actorPeerId ? { actorPeerId } : {}),
    ...(Number(timeoutMs) > 0 ? { timeoutMs: Number(timeoutMs) } : {}),
  };
  const cachePath = legacyCachePath || stateFile(cacheKey);
  const legacyUntil = await readLegacyUntil(cachePath);

  if (legacyUntil > now) {
    if (!ensureOnLegacy) {
      return outcome({ method: "cached-legacy", idleTimeoutSeconds });
    }
    const raw = await callFetch(fetchJSON, createPath, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }, fetchOptions);
    const ensured = raw.ok || isAlreadyExists(raw);
    return outcome({ ensured, method: "cached-legacy", idleTimeoutSeconds, raw });
  }

  const create = await callFetch(fetchJSON, createPath, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, auto_commit_policy: policy }),
  }, fetchOptions);

  if (create.ok) {
    if (hasPolicyEcho(create.result)) {
      const result = outcome({
        ensured: true,
        applied: true,
        idleActive: readIdleActive(create.result),
        idleTimeoutSeconds,
        method: "create",
        raw: create,
      });
      log("session_policy", { sessionId, method: result.method, idleActive: result.idleActive });
      return result;
    }
    if (looksLikeSessionResult(create.result)) await markLegacy(cachePath, now);
    return outcome({
      ensured: true,
      method: "create-legacy",
      idleTimeoutSeconds,
      raw: create,
    });
  }

  if (Number(create.status || 0) === 422) {
    const stripped = await callFetch(fetchJSON, createPath, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }, fetchOptions);
    const ensured = stripped.ok || isAlreadyExists(stripped);
    if (ensured) await markLegacy(cachePath, now);
    return outcome({
      ensured,
      method: ensured ? "create-legacy" : "error",
      idleTimeoutSeconds,
      raw: stripped,
    });
  }

  if (!isAlreadyExists(create)) {
    return outcome({ method: "error", idleTimeoutSeconds, raw: create });
  }

  const patch = await callFetch(fetchJSON, patchPath, {
    method: "PATCH",
    body: JSON.stringify({ auto_commit_policy: policy }),
  }, fetchOptions);
  if (patch.ok && hasPolicyEcho(patch.result)) {
    const result = outcome({
      ensured: true,
      applied: true,
      idleActive: readIdleActive(patch.result),
      idleTimeoutSeconds,
      method: "patch",
      raw: patch,
    });
    log("session_policy", { sessionId, method: result.method, idleActive: result.idleActive });
    return result;
  }

  if (
    (patch.ok && !hasPolicyEcho(patch.result))
    || [404, 405, 422].includes(Number(patch.status || 0))
  ) {
    await markLegacy(cachePath, now);
    return outcome({
      ensured: true,
      method: "patch-legacy",
      idleTimeoutSeconds,
      raw: patch,
    });
  }

  return outcome({
    ensured: true,
    method: "error",
    idleTimeoutSeconds,
    raw: patch,
  });
}
