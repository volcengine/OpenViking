import { createHash } from "node:crypto";
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

function normalizeAuthMode(val) {
  const mode = str(val, "").toLowerCase();
  return ["trusted", "api_key"].includes(mode) ? mode : "";
}

function resolveAuthMode(creds, env = process.env) {
  const cx = creds.ovFile.codex || {};
  const server = creds.ovFile.server || {};
  return (
    normalizeAuthMode(env.OPENVIKING_AUTH_MODE) ||
    normalizeAuthMode(cx.authMode) ||
    normalizeAuthMode(cx.auth_mode) ||
    normalizeAuthMode(server.auth_mode) ||
    ((creds.account || creds.user) ? "trusted" : "api_key")
  );
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

function shellQuote(value) {
  return `'${String(value ?? "").replace(/'/g, "'\\''")}'`;
}

function runtimeCliConfigObject(creds) {
  const config = {};
  if (creds.baseUrl) config.url = creds.baseUrl;
  if (creds.apiKey) config.api_key = creds.apiKey;
  if (creds.account) config.account = creds.account;
  if (creds.user) config.user = creds.user;
  if (creds.peerId) config.actor_peer_id = creds.peerId;
  return config;
}

function ensureRuntimeCliConfig(creds) {
  if (creds.credentialSource === "ovcli") {
    return creds.cliPath || creds.cliPathCandidate || "";
  }

  const content = JSON.stringify(runtimeCliConfigObject(creds), null, 2) + "\n";
  const digest = createHash("sha256").update(content).digest("hex").slice(0, 16);
  const dir = join(homedir(), ".openviking", "codex-plugin-state");
  const path = join(dir, `runtime-ovcli.${digest}.conf`);

  mkdirSync(dir, { recursive: true, mode: 0o700 });
  writeFileSync(path, content, { mode: 0o600 });
  try {
    chmodSync(path, 0o600);
  } catch { /* best effort */ }
  return path;
}

function printShellEnv() {
  const creds = resolveOpenVikingCredentials();
  const cliConfigPath = ensureRuntimeCliConfig(creds);
  const assignments = {
    OV_RESOLVED_SOURCE: creds.credentialSource,
    OV_RESOLVED_URL: creds.baseUrl,
    OV_RESOLVED_MCP_URL: creds.mcpUrl,
    OV_RESOLVED_API_KEY: creds.apiKey,
    OV_RESOLVED_ACCOUNT: creds.account,
    OV_RESOLVED_USER: creds.user,
    OV_RESOLVED_PEER_ID: creds.peerId,
    OV_RESOLVED_CLI_CONFIG_FILE: cliConfigPath,
    OV_RESOLVED_HAS_API_KEY: creds.hasApiKey ? "1" : "0",
  };
  for (const [key, value] of Object.entries(assignments)) {
    process.stdout.write(`${key}=${shellQuote(value)}\n`);
  }
}

export function syncMcpConfig(file, env = process.env) {
  const creds = resolveOpenVikingCredentials(env);
  const authMode = resolveAuthMode(creds, env);
  const j = JSON.parse(readFileSync(file, "utf-8"));
  const s = j.mcpServers && j.mcpServers["openviking-memory"];
  if (!s) return false;

  let changed = false;
  if (creds.mcpUrl && s.url !== creds.mcpUrl) {
    s.url = creds.mcpUrl;
    changed = true;
  }

  const curBearer = s.bearer_token_env_var || "";
  if (creds.hasApiKey && curBearer !== "OPENVIKING_API_KEY") {
    s.bearer_token_env_var = "OPENVIKING_API_KEY";
    changed = true;
  } else if (!creds.hasApiKey && curBearer) {
    delete s.bearer_token_env_var;
    changed = true;
  }

  const headers = s.env_http_headers || {};
  const expectedHeaders = {};
  if (authMode === "trusted" && creds.account) {
    expectedHeaders["X-OpenViking-Account"] = "OPENVIKING_ACCOUNT";
  } else if (headers["X-OpenViking-Account"]) {
    delete headers["X-OpenViking-Account"];
    changed = true;
  }
  if (authMode === "trusted" && creds.user) {
    expectedHeaders["X-OpenViking-User"] = "OPENVIKING_USER";
  } else if (headers["X-OpenViking-User"]) {
    delete headers["X-OpenViking-User"];
    changed = true;
  }
  // Actor-peer is only mapped when a peer is actually configured. Codex's MCP
  // runtime treats unset env vars in env_http_headers as empty-string headers,
  // which the OV side then has to disambiguate from "no peer scope". Match
  // the bearer_token_env_var pattern: present only when there's something to
  // send. The wrapper.sh strips empty OPENVIKING_PEER_ID before exec'ing
  // codex, so without this guard the header would silently flip to "".
  if (creds.peerId) {
    expectedHeaders["X-OpenViking-Actor-Peer"] = "OPENVIKING_PEER_ID";
  } else if (headers["X-OpenViking-Actor-Peer"]) {
    delete headers["X-OpenViking-Actor-Peer"];
    changed = true;
  }
  for (const [header, envName] of Object.entries(expectedHeaders)) {
    if (headers[header] !== envName) {
      headers[header] = envName;
      changed = true;
    }
  }
  if (s.env_http_headers !== headers) {
    s.env_http_headers = headers;
    changed = true;
  }

  if (changed) {
    writeFileSync(file, JSON.stringify(j, null, 2) + "\n");
  }
  return changed;
}

function main() {
  const cmd = process.argv[2] || "";
  if (cmd === "shell-env") {
    printShellEnv();
    return;
  }
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
  if (cmd === "sync-mcp") {
    const file = process.argv[3];
    if (!file) {
      process.stderr.write("usage: ov-credentials.mjs sync-mcp <mcp.json>\n");
      process.exitCode = 2;
      return;
    }
    syncMcpConfig(file);
    return;
  }
  process.stderr.write("usage: ov-credentials.mjs <shell-env|mcp-url|has-api-key|has-peer-id|sync-mcp>\n");
  process.exitCode = 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  main();
}
