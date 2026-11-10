import fs from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { readJSON, AdapterError } from "./files.mjs";
import { validateParameters } from "./parameters.mjs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execute = promisify(execFile);
export async function cliConnection(cliPath, env, executable = "ov") {
  try {
    const { stdout } = await execute(executable, ["config", "show", "-o", "json"], {
      env: { ...env, OPENVIKING_CLI_CONFIG_FILE: cliPath }, timeout: 5000, maxBuffer: 65536,
    });
    const data = JSON.parse(stdout);
    const config = data.result || data;
    // CLI display values are redacted. Never use its api_key / root_api_key
    // output as credentials; read the same selected CLI file privately below.
    return typeof config.url === "string" ? { url: config.url } : {};
  } catch { return {}; }
}

export const defaultDirectory = join(homedir(), ".config", "magpie-openviking-models");
export const expand = (value) => resolve(value.startsWith("~/") ? join(homedir(), value.slice(2)) : value);
export function normalizeURL(value) {
  let url;
  try { url = new URL(value); } catch { throw new AdapterError("invalid_url"); }
  if (url.username || url.password || url.search || url.hash
    || !["http:", "https:"].includes(url.protocol)
    || (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) {
    throw new AdapterError("https_required_except_loopback");
  }
  return url.href.replace(/\/$/, "");
}
export async function settings(options = {}, env = process.env) {
  const directory = expand(env.OPENVIKING_MAGPIE_MODELS_DIR || defaultDirectory);
  const local = await readJSON(join(directory, "adapter.json")) || {};
  const config = { ...local, ...options };
  const cliPath = expand(config.ovcliConfig || env.OPENVIKING_CLI_CONFIG_FILE || join(homedir(), ".openviking", "ovcli.conf"));
  const cli = await readJSON(cliPath) || {};
  const discovered = await cliConnection(cliPath, env);
  const serverUrl = normalizeURL(config.serverUrl || discovered.url || cli.url || "http://127.0.0.1:1933");
  // Root credentials are intentionally separate from ordinary memory access.
  let apiKey = env.OPENVIKING_ROOT_API_KEY || cli.root_api_key || "";
  if (config.rootApiKeyFile) apiKey = (await fs.readFile(expand(config.rootApiKeyFile), "utf8")).trim();
  const botMode = config.botMode ?? "preserve";
  if (!["preserve", "follow", "independent"].includes(botMode)) throw new AdapterError("invalid_bot_mode");
  const queryMode = config.queryMode ?? "preserve";
  if (!["preserve", "follow", "independent"].includes(queryMode)) throw new AdapterError("invalid_query_mode");
  const manageVlm = config.manageVlm ?? true;
  if (typeof manageVlm !== "boolean" || (!manageVlm && (botMode === "follow" || queryMode === "follow"))) throw new AdapterError("invalid_model_roles");
  // Only independent roles consume their model IDs. Empty means the Agent
  // selection; inactive IDs may remain in existing Options.
  if (botMode === "independent" && config.botModel != null && typeof config.botModel !== "string") throw new AdapterError("invalid_bot_model");
  const botModel = botMode === "independent" ? config.botModel?.trim() || undefined : undefined;
  if (queryMode === "independent" && config.queryModel != null && typeof config.queryModel !== "string") throw new AdapterError("invalid_query_model");
  const queryModel = queryMode === "independent" ? config.queryModel?.trim() || undefined : undefined;
  const pollMs = config.pollMs ?? 2000;
  if (!Number.isSafeInteger(pollMs) || pollMs < 500 || pollMs > 60000) throw new AdapterError("invalid_poll_interval");
  const restartOnChange = config.restartOnChange ?? true;
  if (typeof restartOnChange !== "boolean") throw new AdapterError("invalid_restart_policy");
  return { directory, selectionFile: join(directory, "selection.json"), stateFile: join(directory, "state.json"),
    statusFile: join(directory, "status.json"), serverUrl, apiKey, manageVlm, botMode, botModel, queryMode, queryModel,
    vlmParameters: validateParameters(config.vlmParameters), queryParameters: validateParameters(config.queryParameters), botParameters: validateParameters(config.botParameters, "bot"),
    restartOnChange, pollMs, timeoutMs: 10000 };
}
