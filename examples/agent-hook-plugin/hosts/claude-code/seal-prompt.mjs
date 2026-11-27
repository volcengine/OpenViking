#!/usr/bin/env node

/**
 * The setting helper the hooks module in `mods/seal/` runs once per session. A
 * hooks module has no Node, so the plugin's config chain is read through this
 * one-shot process; every seal and resolve call then goes from the module to
 * the server directly. It prints one SealSetting (prompt-seal.d.mts):
 * with sealing on, `server` carries the URL and headers every other hook sends
 * (credential included) and `support` the server's support for sealing, from
 * one `GET /health`.
 *
 * Any failure to answer exits non-zero. Nothing here logs a credential.
 */

import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { loadAgentHookConfig } from "../../lib/agent-hook-runtime.mjs";
import { parseExtraHeaders } from "../../lib/mcp-proxy-config.mjs";
import { buildOvHeaders } from "../../lib/ov-http.mjs";
import { featureStatus, healthVersion } from "../../lib/server-features.mjs";
import host from "./host.mjs";

const loadConfig = (cwd, { env } = {}) => loadAgentHookConfig("claude-code", cwd, { ...host.config, env });

// The mod gives this whole process 5 s.
const HEALTH_TIMEOUT_MS = 3000;

/** How the module reaches the server: the URL and headers of every other hook. */
export function serverOf(cfg, env = process.env) {
  const extraHeaders = parseExtraHeaders(env.OPENVIKING_EXTRA_HEADERS, { onWarn: () => {} });
  return { baseUrl: String(cfg.baseUrl || "").replace(/\/+$/, ""), headers: buildOvHeaders(cfg, { extraHeaders }) };
}

/** `fetchJSON` for `healthVersion`: one GET over Node's fetch, `{ ok, result }`, never throws. */
function healthFetch(server) {
  return async (path, _init, { timeoutMs = HEALTH_TIMEOUT_MS } = {}) => {
    try {
      const res = await fetch(`${server.baseUrl}${path}`, {
        headers: server.headers,
        signal: AbortSignal.timeout(timeoutMs),
      });
      const body = await res.json().catch(() => null);
      return { ok: res.ok, status: res.status, result: body?.result ?? body };
    } catch {
      return { ok: false, status: 0, result: null };
    }
  };
}

export async function readSetting({ env = process.env, load = loadConfig, fetchFor = healthFetch } = {}) {
  const cfg = load(process.cwd(), { env });
  if (!cfg.sealSecrets) return { sealing: "off" };
  const server = serverOf(cfg, env);
  const version = server.baseUrl ? await healthVersion(fetchFor(server), { timeoutMs: HEALTH_TIMEOUT_MS }) : null;
  return {
    sealing: "on",
    enrollToolSecrets: Boolean(cfg.enrollToolSecrets),
    server,
    support: featureStatus("privacy_seal", version),
  };
}

async function main() {
  process.stdout.write(`${JSON.stringify(await readSetting())}\n`);
}

function isDirectRun() {
  try {
    return Boolean(process.argv[1]) && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;
  } catch {
    return false;
  }
}

if (isDirectRun()) {
  main().catch((error) => {
    process.stderr.write(`seal-prompt: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
