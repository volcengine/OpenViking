import fs from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { readJSON, AdapterError } from "./files.mjs";
import { validateParameters } from "./parameters.mjs";

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
  const serverUrl = normalizeURL(config.serverUrl || cli.url || "http://127.0.0.1:1933");
  // Root credentials are intentionally separate from ordinary memory access.
  let apiKey = env.OPENVIKING_ROOT_API_KEY || cli.root_api_key || "";
  if (config.rootApiKeyFile) apiKey = (await fs.readFile(expand(config.rootApiKeyFile), "utf8")).trim();
  const botMode = config.botMode ?? "preserve";
  if (!["preserve", "follow", "independent"].includes(botMode)) throw new AdapterError("invalid_bot_mode");
  const manageVlm = config.manageVlm ?? true;
  if (typeof manageVlm !== "boolean" || (!manageVlm && botMode === "follow")) throw new AdapterError("invalid_model_roles");
  if (config.botModel != null && (typeof config.botModel !== "string" || !config.botModel.trim() || botMode !== "independent")) throw new AdapterError("invalid_bot_model");
  const pollMs = config.pollMs ?? 2000;
  if (!Number.isSafeInteger(pollMs) || pollMs < 500 || pollMs > 60000) throw new AdapterError("invalid_poll_interval");
  return { directory, selectionFile: join(directory, "selection.json"), stateFile: join(directory, "state.json"),
    statusFile: join(directory, "status.json"), serverUrl, apiKey, manageVlm, botMode, botModel: config.botModel,
    vlmParameters: validateParameters(config.vlmParameters), botParameters: validateParameters(config.botParameters),
    pollMs, timeoutMs: 10000 };
}
