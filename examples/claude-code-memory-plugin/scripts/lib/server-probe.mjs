/**
 * Cached server-state probe for the statusline.
 *
 * The statusline command runs once per conversation update, fresh process,
 * across potentially many concurrent CC sessions. Each call could hit the
 * server; with N tabs that's N stampedes per turn. We cache the response
 * in a shared file under STATE_DIR and let any session within `ttlMs` reuse
 * it.
 *
 * Hard 250 ms request timeout — the statusline has its own outer budget and
 * we never want a slow server to make the user's terminal feel laggy.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { STATE_DIR } from "./state.mjs";

const CACHE_FILE = join(STATE_DIR, "server-probe.json");
const DEFAULT_TTL_MS = 5000;
const REQUEST_TIMEOUT_MS = 250;

function readCache() {
  try {
    const raw = readFileSync(CACHE_FILE, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function writeCache(payload) {
  try {
    writeFileSync(CACHE_FILE, JSON.stringify(payload));
  } catch { /* best effort */ }
}

async function fetchWithTimeout(url, headers, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    const res = await fetch(url, { headers, signal: controller.signal });
    return { ok: res.ok, status: res.status };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Probe the server for liveness and (best-effort) queue health. Result is
 * cached for `ttlMs`. Always resolves; never throws — failures map to
 * `{healthy: false, error: ...}`.
 */
export async function probeServer(cfg, { ttlMs = DEFAULT_TTL_MS } = {}) {
  const cached = readCache();
  if (cached && typeof cached.ts === "number" && Date.now() - cached.ts < ttlMs) {
    if (cached.base_url === cfg.baseUrl) return cached;
  }

  const headers = { "Content-Type": "application/json" };
  if (cfg.apiKey) headers["Authorization"] = `Bearer ${cfg.apiKey}`;
  if (cfg.accountId) headers["X-OpenViking-Account"] = cfg.accountId;
  if (cfg.userId) headers["X-OpenViking-User"] = cfg.userId;
  if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;

  const t0 = Date.now();
  let healthy = false;
  let queueHealthy = null;
  let error;

  try {
    const health = await fetchWithTimeout(`${cfg.baseUrl}/health`, headers, REQUEST_TIMEOUT_MS);
    healthy = health.ok;
    if (!healthy) error = `health_${health.status}`;
  } catch (err) {
    error = err?.name === "AbortError" ? "timeout" : (err?.message || "unreachable");
  }

  // Queue badge: independent 250 ms budget. Sharing with /health caused
  // visible flapping on remote servers — slow /health left no budget for
  // the queue probe, so the badge appeared only when /health was unusually
  // fast. Worst case here is ~500 ms total (250 + 250); typical is ~200 ms
  // because both endpoints respond in tens of ms when colocated.
  if (healthy) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      try {
        const res = await fetch(`${cfg.baseUrl}/api/v1/observer/queue`, {
          headers,
          signal: controller.signal,
        });
        if (res.ok) {
          const body = await res.json().catch(() => ({}));
          if (body?.status === "ok" && typeof body.result?.is_healthy === "boolean") {
            queueHealthy = body.result.is_healthy;
          }
        }
      } finally {
        clearTimeout(timer);
      }
    } catch { /* leave queueHealthy null */ }
  }

  const payload = {
    healthy,
    queue_healthy: queueHealthy,
    latency_ms: Date.now() - t0,
    base_url: cfg.baseUrl,
    error,
    ts: Date.now(),
  };
  writeCache(payload);
  return payload;
}
