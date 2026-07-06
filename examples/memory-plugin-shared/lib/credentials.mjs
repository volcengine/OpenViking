import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_BASE_URL = "http://127.0.0.1:1933";

function str(val, fallback = "") {
  if (typeof val === "string" && val.trim()) return val.trim();
  return fallback;
}

function normalizePath(value) {
  const raw = str(value, "");
  if (!raw) return "";
  if (raw === "~") return homedir();
  if (raw.startsWith("~/")) return resolvePath(join(homedir(), raw.slice(2)));
  return resolvePath(raw);
}

function tryLoadJson(path) {
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function looksLikeOvcli(obj) {
  if (!obj || typeof obj !== "object") return false;
  if (obj.server && typeof obj.server === "object") return false;
  return Boolean(
    typeof obj.url === "string" ||
    typeof obj.api_key === "string" ||
    typeof obj.account === "string" ||
    typeof obj.account_id === "string" ||
    typeof obj.user === "string" ||
    typeof obj.user_id === "string" ||
    typeof obj.actor_peer_id === "string",
  );
}

function hasCredentialFields(obj) {
  if (!obj || typeof obj !== "object") return false;
  return [
    "url",
    "api_key",
    "account",
    "account_id",
    "user",
    "user_id",
    "actor_peer_id",
    "peer_id",
  ].some((key) => typeof obj[key] === "string");
}

export function loadCredentialFiles(env = process.env) {
  const cliPathCandidate = normalizePath(env.OPENVIKING_CLI_CONFIG_FILE) || DEFAULT_OVCLI_CONF_PATH;
  const ovPathCandidate = normalizePath(env.OPENVIKING_CONFIG_FILE) || DEFAULT_OV_CONF_PATH;
  const cliPathEnv = Boolean(str(env.OPENVIKING_CLI_CONFIG_FILE, ""));
  const ovPathEnv = Boolean(str(env.OPENVIKING_CONFIG_FILE, ""));

  let cliFile = tryLoadJson(cliPathCandidate);
  let cliPath = cliFile ? cliPathCandidate : "";
  let ovFile = tryLoadJson(ovPathCandidate);
  let ovPath = ovFile ? ovPathCandidate : "";

  // Backward compat: older plugin installs used OPENVIKING_CONFIG_FILE for
  // both ov.conf and ovcli.conf. Preserve that when the file is ovcli-shaped.
  if (ovPathEnv && !cliPathEnv && looksLikeOvcli(ovFile)) {
    cliFile = ovFile;
    cliPath = ovPath;
    ovFile = null;
    ovPath = "";
  }

  return {
    cliFile: cliFile || {},
    cliPath,
    cliPathCandidate,
    ovFile: ovFile || {},
    ovPath,
  };
}

function sourceMode(env) {
  const raw = str(env.OPENVIKING_CREDENTIAL_SOURCE, str(env.OPENVIKING_CREDENTIALS_SOURCE, "auto"))
    .toLowerCase();
  if (raw === "env" || raw === "environment") return "env";
  if (raw === "cli" || raw === "ovcli" || raw === "file" || raw === "config") return "cli";
  return "auto";
}

function deriveBaseUrl({ env, cliFile, ovFile, useCli }) {
  const envUrl = str(env.OPENVIKING_URL, str(env.OPENVIKING_BASE_URL, ""));
  const cliUrl = str(cliFile.url, "");

  if (useCli && cliUrl) return cliUrl.replace(/\/+$/, "");
  if (!useCli && envUrl) return envUrl.replace(/\/+$/, "");
  if (!useCli && cliUrl) return cliUrl.replace(/\/+$/, "");

  const server = ovFile.server || {};
  const ovUrl = str(server.url, "");
  if (ovUrl) return ovUrl.replace(/\/+$/, "");

  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Number.isFinite(Number(server.port)) ? Math.floor(Number(server.port)) : 1933;
  return `http://${host}:${port}`;
}

export function resolveOpenVikingCredentials(env = process.env) {
  const files = loadCredentialFiles(env);
  const mode = sourceMode(env);
  const useCli = mode === "cli" || (mode === "auto" && files.cliPath && hasCredentialFields(files.cliFile));
  const cx = files.ovFile.codex || {};
  const server = files.ovFile.server || {};

  const baseUrl = deriveBaseUrl({ env, ...files, useCli });

  const apiKey = useCli
    ? str(files.cliFile.api_key, "")
    : (
        str(env.OPENVIKING_BEARER_TOKEN, "") ||
        str(env.OPENVIKING_API_KEY, "") ||
        str(files.cliFile.api_key, "") ||
        str(cx.apiKey, "") ||
        str(server.root_api_key, "")
      );

  const account = useCli
    ? str(files.cliFile.account, str(files.cliFile.account_id, ""))
    : (
        str(env.OPENVIKING_ACCOUNT, "") ||
        str(files.cliFile.account, str(files.cliFile.account_id, "")) ||
        str(cx.accountId, "")
      );

  const user = useCli
    ? str(files.cliFile.user, str(files.cliFile.user_id, ""))
    : (
        str(env.OPENVIKING_USER, "") ||
        str(files.cliFile.user, str(files.cliFile.user_id, "")) ||
        str(cx.userId, "")
      );

  const peerId = useCli
    ? str(files.cliFile.actor_peer_id, str(files.cliFile.peer_id, ""))
    : (
        str(env.OPENVIKING_PEER_ID, "") ||
        str(files.cliFile.actor_peer_id, str(files.cliFile.peer_id, "")) ||
        str(cx.peerId, str(cx.peer_id, ""))
      );

  const explicitMcpUrl = str(env.OPENVIKING_MCP_URL, "");
  const mcpUrl = (!useCli && explicitMcpUrl) ? explicitMcpUrl : `${baseUrl.replace(/\/+$/, "")}/mcp`;

  return {
    ...files,
    credentialSource: useCli ? "ovcli" : (mode === "env" ? "env" : "auto"),
    baseUrl,
    mcpUrl,
    apiKey,
    account,
    user,
    peerId,
    hasApiKey: Boolean(apiKey),
  };
}

function main() {
  const cmd = process.argv[2] || "";
  if (cmd === "mcp-url") {
    process.stdout.write(resolveOpenVikingCredentials().mcpUrl);
    return;
  }
  if (cmd === "has-api-key") {
    process.stdout.write(resolveOpenVikingCredentials().hasApiKey ? "1" : "0");
    return;
  }
  if (cmd === "has-peer-id") {
    process.stdout.write(resolveOpenVikingCredentials().peerId ? "1" : "0");
    return;
  }
  process.stderr.write("usage: ov-credentials.mjs <mcp-url|has-api-key|has-peer-id>\n");
  process.exitCode = 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  main();
}
