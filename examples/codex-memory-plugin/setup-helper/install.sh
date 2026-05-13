#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$HOME/.openviking/openviking-repo}"
MARKETPLACE_NAME="${OPENVIKING_CODEX_MARKETPLACE_NAME:-openviking-plugins-local}"
MARKETPLACE_ROOT="${OPENVIKING_CODEX_MARKETPLACE_ROOT:-$HOME/.codex/${MARKETPLACE_NAME}-marketplace}"
PLUGIN_NAME="openviking-memory"
PLUGIN_ID="${PLUGIN_NAME}@${MARKETPLACE_NAME}"
CODEX_CONFIG="${CODEX_CONFIG_FILE:-$HOME/.codex/config.toml}"

ENABLE_NATIVE_MCP="${OPENVIKING_CODEX_ENABLE_MCP:-}"
if [ -z "$ENABLE_NATIVE_MCP" ]; then
  if [ -t 0 ]; then
    printf 'Enable OpenViking native MCP tools in Codex? [Y/n] '
    read -r MCP_REPLY || MCP_REPLY=""
    case "$MCP_REPLY" in
      n|N|no|No|NO) ENABLE_NATIVE_MCP=0 ;;
      *) ENABLE_NATIVE_MCP=1 ;;
    esac
  else
    ENABLE_NATIVE_MCP=1
  fi
fi

case "$ENABLE_NATIVE_MCP" in
  1|true|TRUE|yes|YES|y|Y) ENABLE_NATIVE_MCP=1 ;;
  0|false|FALSE|no|NO|n|N) ENABLE_NATIVE_MCP=0 ;;
  *)
    echo "Invalid OPENVIKING_CODEX_ENABLE_MCP=$ENABLE_NATIVE_MCP (expected 1/0 or true/false)." >&2
    exit 1
    ;;
esac

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need codex
need git
need node

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "Node.js 22+ is required; found $(node --version)." >&2
  exit 1
fi

mkdir -p "$(dirname "$REPO_DIR")" "$HOME/.codex"

if [ ! -e "$REPO_DIR/.git" ]; then
  if [ -e "$REPO_DIR" ]; then
    echo "$REPO_DIR exists but is not a git checkout." >&2
    exit 1
  fi
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

PLUGIN_DIR="$REPO_DIR/examples/codex-memory-plugin"
if [ ! -d "$PLUGIN_DIR/.codex-plugin" ]; then
  echo "Codex plugin not found at $PLUGIN_DIR" >&2
  exit 1
fi

PLUGIN_VERSION="$(node -e 'const p=require(process.argv[1]); console.log(p.version || "0.0.0")' "$PLUGIN_DIR/package.json")"

mkdir -p "$MARKETPLACE_ROOT/.claude-plugin"
rm -f "$MARKETPLACE_ROOT/$PLUGIN_NAME"
ln -s "$PLUGIN_DIR" "$MARKETPLACE_ROOT/$PLUGIN_NAME"

cat > "$MARKETPLACE_ROOT/.claude-plugin/marketplace.json" <<EOF
{
  "name": "$MARKETPLACE_NAME",
  "plugins": [
    { "name": "$PLUGIN_NAME", "source": "./$PLUGIN_NAME" }
  ]
}
EOF

codex plugin marketplace add "$MARKETPLACE_ROOT" >/dev/null 2>&1 || true

node - "$CODEX_CONFIG" "$PLUGIN_ID" "$ENABLE_NATIVE_MCP" <<'NODE'
const fs = require("node:fs");
const os = require("node:os");
const pathMod = require("node:path");
const configPath = process.argv[2];
const pluginId = process.argv[3];
const enableNativeMcp = process.argv[4] === "1";

let text = "";
try {
  text = fs.readFileSync(configPath, "utf8");
} catch {
  text = "";
}

function ensureSectionLine(src, section, key, value) {
  const lines = src.split(/\n/);
  const header = `[${section}]`;
  const start = lines.findIndex((line) => line.trim() === header);
  if (start === -1) {
    const prefix = src.trimEnd();
    return `${prefix}${prefix ? "\n\n" : ""}${header}\n${key} = ${value}\n`;
  }

  let end = lines.length;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^\s*\[/.test(lines[i])) {
      end = i;
      break;
    }
  }

  for (let i = start + 1; i < end; i += 1) {
    if (new RegExp(`^\\s*${key}\\s*=`).test(lines[i])) {
      lines[i] = `${key} = ${value}`;
      return lines.join("\n").replace(/\n*$/, "\n");
    }
  }

  lines.splice(end, 0, `${key} = ${value}`);
  return lines.join("\n").replace(/\n*$/, "\n");
}

function ensurePluginEnabled(src, pluginId) {
  const header = `[plugins."${pluginId}"]`;
  const lines = src.split(/\n/);
  const start = lines.findIndex((line) => line.trim() === header);
  if (start === -1) {
    const prefix = src.trimEnd();
    return `${prefix}${prefix ? "\n\n" : ""}${header}\nenabled = true\n`;
  }

  let end = lines.length;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^\s*\[/.test(lines[i])) {
      end = i;
      break;
    }
  }

  for (let i = start + 1; i < end; i += 1) {
    if (/^\s*enabled\s*=/.test(lines[i])) {
      lines[i] = "enabled = true";
      return lines.join("\n").replace(/\n*$/, "\n");
    }
  }

  lines.splice(end, 0, "enabled = true");
  return lines.join("\n").replace(/\n*$/, "\n");
}

function removeSection(src, section) {
  let lines = src.split(/\n/);
  const header = `[${section}]`;

  while (true) {
    const start = lines.findIndex((line) => line.trim() === header);
    if (start === -1) break;

    let end = lines.length;
    for (let i = start + 1; i < lines.length; i += 1) {
      if (/^\s*\[/.test(lines[i])) {
        end = i;
        break;
      }
    }
    lines.splice(start, end - start);
  }

  return lines.join("\n").replace(/\n{3,}/g, "\n\n").replace(/\n*$/, "\n");
}

function removeNativeMcpServer(src) {
  const withoutSections = removeSection(
    removeSection(src, "mcp_servers.openviking.http_headers"),
    "mcp_servers.openviking",
  );
  return withoutSections
    .replace(/^# OpenViking native MCP endpoint, managed by examples\/codex-memory-plugin\/setup-helper\/install\.sh\n/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/\n*$/, "\n");
}

function tomlString(value) {
  return JSON.stringify(String(value));
}

function expandHome(filePath) {
  if (!filePath) return filePath;
  if (filePath === "~") return os.homedir();
  if (filePath.startsWith("~/")) return pathMod.join(os.homedir(), filePath.slice(2));
  return filePath;
}

function readJson(filePath) {
  if (!filePath) return null;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function str(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function looksLikeOvcli(obj) {
  if (!obj || typeof obj !== "object") return false;
  if (obj.server && typeof obj.server === "object") return false;
  return typeof obj.url === "string" || typeof obj.api_key === "string";
}

function loadOpenVikingConnection() {
  const cliPath = expandHome(process.env.OPENVIKING_CLI_CONFIG_FILE)
    || pathMod.join(os.homedir(), ".openviking", "ovcli.conf");
  const ovPath = expandHome(process.env.OPENVIKING_CONFIG_FILE)
    || pathMod.join(os.homedir(), ".openviking", "ov.conf");

  let cliFile = readJson(cliPath) || {};
  let ovFile = readJson(ovPath) || {};
  if (process.env.OPENVIKING_CONFIG_FILE && !process.env.OPENVIKING_CLI_CONFIG_FILE && looksLikeOvcli(ovFile)) {
    cliFile = ovFile;
    ovFile = {};
  }

  const server = ovFile.server || {};
  const codex = ovFile.codex || {};
  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Number.isFinite(Number(server.port)) ? Number(server.port) : 1933;
  const baseUrl = str(process.env.OPENVIKING_URL, "")
    || str(process.env.OPENVIKING_BASE_URL, "")
    || str(cliFile.url, "")
    || str(server.url, "")
    || `http://${host}:${Math.trunc(port)}`;

  const bearerTokenEnvVar = process.env.OPENVIKING_BEARER_TOKEN && !process.env.OPENVIKING_API_KEY
    ? "OPENVIKING_BEARER_TOKEN"
    : "OPENVIKING_API_KEY";

  return {
    mcpUrl: `${baseUrl.replace(/\/+$/, "")}/mcp`,
    bearerTokenEnvVar,
    account: str(process.env.OPENVIKING_ACCOUNT, "") || str(cliFile.account, "") || str(codex.accountId, ""),
    user: str(process.env.OPENVIKING_USER, "") || str(cliFile.user, "") || str(codex.userId, ""),
    agentId: str(process.env.OPENVIKING_AGENT_ID, "") || str(cliFile.agent_id, "") || str(codex.agentId, "codex"),
  };
}

function ensureNativeMcpServer(src) {
  const conn = loadOpenVikingConnection();
  let next = removeNativeMcpServer(src);

  const lines = [
    "# OpenViking native MCP endpoint, managed by examples/codex-memory-plugin/setup-helper/install.sh",
    "[mcp_servers.openviking]",
    `url = ${tomlString(conn.mcpUrl)}`,
    `bearer_token_env_var = ${tomlString(conn.bearerTokenEnvVar)}`,
    "startup_timeout_sec = 30",
    "tool_timeout_sec = 120",
  ];

  const headers = [];
  if (conn.account) headers.push(["X-OpenViking-Account", conn.account]);
  if (conn.user) headers.push(["X-OpenViking-User", conn.user]);
  if (conn.agentId) headers.push(["X-OpenViking-Agent", conn.agentId]);

  if (headers.length > 0) {
    lines.push("", "[mcp_servers.openviking.http_headers]");
    for (const [key, value] of headers) {
      lines.push(`${tomlString(key)} = ${tomlString(value)}`);
    }
  }

  const prefix = next.trimEnd();
  return `${prefix}${prefix ? "\n\n" : ""}${lines.join("\n")}\n`;
}

text = ensurePluginEnabled(text, pluginId);
text = ensureSectionLine(text, "features", "plugin_hooks", "true");
if (enableNativeMcp) {
  text = ensureNativeMcpServer(text);
} else {
  text = removeNativeMcpServer(text);
}

fs.mkdirSync(pathMod.dirname(configPath), { recursive: true });
fs.writeFileSync(configPath, text);
NODE

CACHE_DIR="$HOME/.codex/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME/$PLUGIN_VERSION"
mkdir -p "$(dirname "$CACHE_DIR")"
rm -rf "$CACHE_DIR"
cp -R "$PLUGIN_DIR" "$CACHE_DIR"

if [ ! -f "$HOME/.openviking/ovcli.conf" ]; then
  cat >&2 <<'EOF'

Note: ~/.openviking/ovcli.conf was not found.
Hooks and native MCP will default to http://127.0.0.1:1933 unless OPENVIKING_URL is set.
EOF
fi

cat <<EOF
Installed $PLUGIN_ID.
Marketplace: $MARKETPLACE_ROOT
Plugin cache: $CACHE_DIR
EOF

if [ "$ENABLE_NATIVE_MCP" -eq 1 ]; then
  cat <<EOF
Native MCP: mcp_servers.openviking in $CODEX_CONFIG

Before starting Codex, make sure the bearer env var configured above is set
(usually OPENVIKING_API_KEY). Hooks can read ovcli.conf directly; Codex's
native HTTP MCP transport reads auth from the configured env var.
EOF
else
  cat <<EOF
Native MCP: disabled/removed (hooks-only install). Enable later by re-running with
OPENVIKING_CODEX_ENABLE_MCP=1.
EOF
fi

cat <<EOF

Restart Codex with:
  codex
EOF
