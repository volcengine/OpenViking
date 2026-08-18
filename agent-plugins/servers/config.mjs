/**
 * Connection config loader for the OpenViking Agent Plugins 1.0 package.
 *
 * VENDORED (adapted) from examples/claude-code-memory-plugin/scripts/config.mjs —
 * keep the resolution semantics in sync with that file. This copy is trimmed to
 * the fields consumed by servers/mcp-proxy.mjs: Agent Plugins 1.0 has no hooks,
 * so all of the hook-tuning knobs (recall/capture/commit/profile) are dropped,
 * as is the Claude-Code-specific `claude_code` section of ov.conf.
 *
 * Resolution priority (highest → lowest):
 *   1. Environment variables (OPENVIKING_*)
 *   2. ovcli.conf (CLI client config: url, api_key, account, user)
 *   3. ov.conf `server` section (local server config; url/host/port/root_api_key)
 *   4. Built-in defaults (http://127.0.0.1:1933)
 *
 * Env vars covered:
 *   Connection / identity:
 *     OPENVIKING_URL / OPENVIKING_BASE_URL, OPENVIKING_API_KEY / OPENVIKING_BEARER_TOKEN,
 *     OPENVIKING_ACCOUNT, OPENVIKING_USER, OPENVIKING_PEER_ID, OPENVIKING_WORKSPACE_PEER
 *   Config file overrides:
 *     OPENVIKING_CONFIG_FILE (ov.conf), OPENVIKING_CLI_CONFIG_FILE (ovcli.conf)
 *   Misc:
 *     OPENVIKING_TIMEOUT_MS, OPENVIKING_DEBUG, OPENVIKING_DEBUG_LOG
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { resolve as resolvePath, join } from "node:path";

import { buildUserAgent, readManifestVersion } from "./shared/credentials.mjs";

const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const USER_AGENT = buildUserAgent(
  "agent-plugins",
  readManifestVersion(new URL("../plugin.json", import.meta.url)),
);

function num(val, fallback) {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val === "string" && val.trim()) {
    const n = Number(val);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

function str(val, fallback) {
  if (typeof val === "string" && val.trim()) return val.trim();
  return fallback;
}

function envBool(name) {
  const v = process.env[name];
  if (v == null || v === "") return undefined;
  const lower = v.trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no") return false;
  if (lower === "1" || lower === "true" || lower === "yes") return true;
  return undefined;
}

/**
 * Try to load and parse a JSON config file. Returns { configPath, file } or null.
 */
function tryLoadJsonFile(envVar, defaultPath) {
  const configPath = resolvePath(
    (process.env[envVar] || defaultPath).replace(/^~/, homedir()),
  );

  let raw;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch {
    return null;
  }

  try {
    return { configPath, file: JSON.parse(raw) };
  } catch {
    return null;
  }
}

/**
 * Load the connection configuration for the stdio MCP proxy.
 *
 * Resolution: env vars → ovcli.conf → ov.conf → defaults.
 */
export function loadConfig() {
  const ovConf = tryLoadJsonFile("OPENVIKING_CONFIG_FILE", DEFAULT_OV_CONF_PATH);
  const cliConf = tryLoadJsonFile("OPENVIKING_CLI_CONFIG_FILE", DEFAULT_OVCLI_CONF_PATH);

  const ovFile = ovConf?.file || {};
  const cliFile = cliConf?.file || {};
  const configPath = cliConf?.configPath || ovConf?.configPath || null;

  const server = ovFile.server || {};

  // baseUrl: env → ovcli.url → ov.server.url → http://{host}:{port}
  const envUrl = str(process.env.OPENVIKING_URL, null) || str(process.env.OPENVIKING_BASE_URL, null);
  let baseUrl;
  if (envUrl) {
    baseUrl = envUrl.replace(/\/+$/, "");
  } else if (cliFile.url) {
    baseUrl = str(cliFile.url, "").replace(/\/+$/, "");
  } else if (server.url) {
    baseUrl = str(server.url, "").replace(/\/+$/, "");
  } else {
    const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
    const port = Math.floor(num(server.port, 1933));
    baseUrl = `http://${host}:${port}`;
  }

  // apiKey: env → ovcli.api_key → ov.server.root_api_key
  // Accepts OPENVIKING_BEARER_TOKEN or OPENVIKING_API_KEY (sent as Bearer either way).
  const apiKey = str(process.env.OPENVIKING_BEARER_TOKEN, null)
    || str(process.env.OPENVIKING_API_KEY, null)
    || str(cliFile.api_key, null)
    || str(server.root_api_key, "");

  const accountId = str(process.env.OPENVIKING_ACCOUNT, null)
    || str(cliFile.account, "");

  const userId = str(process.env.OPENVIKING_USER, null)
    || str(cliFile.user, "");

  const peerId = str(process.env.OPENVIKING_PEER_ID, "");
  const workspacePeer = envBool("OPENVIKING_WORKSPACE_PEER") ?? true;

  const timeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_TIMEOUT_MS,
    15000,
  )));

  const debug = envBool("OPENVIKING_DEBUG") ?? false;
  const defaultLogPath = join(homedir(), ".openviking", "logs", "agent-plugins.log");
  const debugLogPath = str(process.env.OPENVIKING_DEBUG_LOG, defaultLogPath);

  return {
    configPath,
    baseUrl,
    apiKey,
    accountId,
    userId,
    peerId,
    workspacePeer,
    timeoutMs,
    userAgent: USER_AGENT,
    debug,
    debugLogPath,
  };
}
