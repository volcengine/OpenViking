/**
 * Shared configuration loader for the Codex OpenViking memory plugin.
 *
 * Reads connection settings from `~/.openviking/ovcli.conf` (the canonical CLIENT
 * config that the `ov` CLI uses), and falls back to the legacy `~/.openviking/ov.conf`
 * server config when ovcli.conf is missing.
 *
 * Plugin-specific overrides go in an optional `codex` section of either file.
 *
 * Env vars:
 *   OPENVIKING_CONFIG_FILE  alternate ovcli.conf path
 *   OPENVIKING_URL          override server URL
 *   OPENVIKING_API_KEY      override API key
 *   OPENVIKING_ACCOUNT      override account
 *   OPENVIKING_USER         override user
 *   OPENVIKING_AGENT_ID     override agent identity
 *   OPENVIKING_DEBUG=1      enable debug logging
 *   OPENVIKING_DEBUG_LOG    debug log path
 */

import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_CLI_CONFIG = join(homedir(), ".openviking", "ovcli.conf");
const DEFAULT_SERVER_CONFIG = join(homedir(), ".openviking", "ov.conf");

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

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function deriveBaseUrl(file) {
  const direct = str(file?.url, "");
  if (direct) return direct.replace(/\/+$/, "");
  const server = file?.server || {};
  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Math.floor(num(server.port, 1933));
  return `http://${host}:${port}`;
}

export function loadConfig() {
  const explicitPath = process.env.OPENVIKING_CONFIG_FILE
    ? resolvePath(process.env.OPENVIKING_CONFIG_FILE.replace(/^~/, homedir()))
    : null;

  const candidates = explicitPath
    ? [explicitPath]
    : [DEFAULT_CLI_CONFIG, DEFAULT_SERVER_CONFIG];

  let configPath = null;
  let file = null;
  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      configPath = candidate;
      file = readJson(candidate) || {};
      break;
    }
  }
  if (!file) {
    file = {};
    configPath = explicitPath || DEFAULT_CLI_CONFIG;
  }

  const baseUrlFromFile = deriveBaseUrl(file);
  const baseUrl = (str(process.env.OPENVIKING_URL, baseUrlFromFile) || "http://127.0.0.1:1933").replace(/\/+$/, "");

  const apiKeyFromFile = str(file.api_key, "") || str(file?.server?.root_api_key, "");
  const apiKey = str(process.env.OPENVIKING_API_KEY, apiKeyFromFile);

  const account = str(process.env.OPENVIKING_ACCOUNT, str(file.account, ""));
  const user = str(process.env.OPENVIKING_USER, str(file.user, ""));

  const cx = file.codex || {};

  const debug = cx.debug === true || process.env.OPENVIKING_DEBUG === "1";
  const defaultLogPath = join(homedir(), ".openviking", "logs", "codex-hooks.log");
  const debugLogPath = str(process.env.OPENVIKING_DEBUG_LOG, defaultLogPath);

  const timeoutMs = Math.max(1000, Math.floor(num(cx.timeoutMs, 15000)));
  const captureTimeoutMs = Math.max(
    1000,
    Math.floor(num(cx.captureTimeoutMs, Math.max(timeoutMs * 2, 30000))),
  );

  return {
    configPath,
    baseUrl,
    apiKey,
    account,
    user,
    agentId: str(process.env.OPENVIKING_AGENT_ID, str(cx.agentId, "codex")),
    timeoutMs,

    autoRecall: cx.autoRecall !== false,
    recallLimit: Math.max(1, Math.floor(num(cx.recallLimit, 6))),
    scoreThreshold: Math.min(1, Math.max(0, num(cx.scoreThreshold, 0.01))),
    minQueryLength: Math.max(1, Math.floor(num(cx.minQueryLength, 3))),
    logRankingDetails: cx.logRankingDetails === true,

    autoCapture: cx.autoCapture !== false,
    captureMode: cx.captureMode === "keyword" ? "keyword" : "semantic",
    captureMaxLength: Math.max(200, Math.floor(num(cx.captureMaxLength, 24000))),
    captureTimeoutMs,
    captureAssistantTurns: cx.captureAssistantTurns === true,
    captureLastAssistantOnStop: cx.captureLastAssistantOnStop !== false,

    autoCommitOnCompact: cx.autoCommitOnCompact !== false,

    debug,
    debugLogPath,
  };
}
