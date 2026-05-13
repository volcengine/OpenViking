#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$HOME/.openviking/openviking-repo}"
# Accept both OPENVIKING_REPO_REF and OPENVIKING_REPO_BRANCH so users can
# reuse the same env var across the claude-code and codex installers.
REPO_REF="${OPENVIKING_REPO_REF:-${OPENVIKING_REPO_BRANCH:-main}}"
MARKETPLACE_NAME="${OPENVIKING_CODEX_MARKETPLACE_NAME:-openviking-plugins-local}"
MARKETPLACE_ROOT="${OPENVIKING_CODEX_MARKETPLACE_ROOT:-$HOME/.codex/${MARKETPLACE_NAME}-marketplace}"
PLUGIN_NAME="openviking-memory"
PLUGIN_ID="${PLUGIN_NAME}@${MARKETPLACE_NAME}"
CODEX_CONFIG="${CODEX_CONFIG_FILE:-$HOME/.codex/config.toml}"
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
DEFAULT_MCP_URL="http://127.0.0.1:1933/mcp"
WRAPPER_MARKER_BEGIN="# >>> openviking-codex-plugin >>>"
WRAPPER_MARKER_END="# <<< openviking-codex-plugin <<<"

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
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$REPO_DIR"
else
  echo "Refreshing existing OpenViking checkout at $REPO_DIR ($REPO_REF)..."
  git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_REF"
  git -C "$REPO_DIR" reset --hard FETCH_HEAD
fi

PLUGIN_DIR="$REPO_DIR/examples/codex-memory-plugin"
if [ ! -d "$PLUGIN_DIR/.codex-plugin" ]; then
  echo "Codex plugin not found at $PLUGIN_DIR" >&2
  exit 1
fi

PLUGIN_VERSION="$(node -e 'const p=require(process.argv[1]); console.log(p.version || "0.0.0")' "$PLUGIN_DIR/.codex-plugin/plugin.json")"

# Resolve the OpenViking /mcp endpoint at install time. Priority:
#   OPENVIKING_MCP_URL (env, full /mcp URL) > OPENVIKING_URL (env, base URL) >
#   ovcli.conf .url > default localhost.
resolve_mcp_url() {
  if [ -n "${OPENVIKING_MCP_URL:-}" ]; then
    printf '%s' "$OPENVIKING_MCP_URL"
    return
  fi
  if [ -n "${OPENVIKING_URL:-}" ]; then
    printf '%s/mcp' "${OPENVIKING_URL%/}"
    return
  fi
  if [ -f "$OVCLI_CONF" ] && command -v node >/dev/null 2>&1; then
    local from_conf
    from_conf="$(node -e '
      try {
        const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
        if (typeof c.url === "string" && c.url) {
          process.stdout.write(c.url.replace(/\/+$/, "") + "/mcp");
        }
      } catch {}
    ' "$OVCLI_CONF" 2>/dev/null || true)"
    if [ -n "$from_conf" ]; then
      printf '%s' "$from_conf"
      return
    fi
  fi
  printf '%s' "$DEFAULT_MCP_URL"
}

MCP_URL="$(resolve_mcp_url)"

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

node - "$CODEX_CONFIG" "$PLUGIN_ID" <<'NODE'
const fs = require("node:fs");
const path = process.argv[2];
const pluginId = process.argv[3];

let text = "";
try {
  text = fs.readFileSync(path, "utf8");
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

text = ensurePluginEnabled(text, pluginId);
text = ensureSectionLine(text, "features", "plugin_hooks", "true");

fs.mkdirSync(require("node:path").dirname(path), { recursive: true });
fs.writeFileSync(path, text);
NODE

CACHE_DIR="$HOME/.codex/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME/$PLUGIN_VERSION"
mkdir -p "$(dirname "$CACHE_DIR")"
rm -rf "$CACHE_DIR"
cp -R "$PLUGIN_DIR" "$CACHE_DIR"

# Codex 0.130 does not inject CODEX_PLUGIN_ROOT into hook subprocess env and
# does not let hooks.json declare a cwd, so relative paths in hooks.json
# resolve against the user's cwd (typically ~). Render the placeholder
# __OPENVIKING_PLUGIN_ROOT__ into the cache copy's absolute path. The repo's
# checked-in hooks.json keeps the placeholder; only the cached copy is
# rewritten at install time.
HOOKS_JSON="$CACHE_DIR/hooks/hooks.json"
if [ -f "$HOOKS_JSON" ]; then
  CACHE_ESC="$(printf '%s' "$CACHE_DIR" | sed -e 's/[\\/&]/\\&/g')"
  sed -i.bak -e "s/__OPENVIKING_PLUGIN_ROOT__/$CACHE_ESC/g" "$HOOKS_JSON"
  rm -f "${HOOKS_JSON}.bak"
fi

# Detect whether the user has an OpenViking API key configured anywhere.
# When they don't (typical for a local unauth OV), we render .mcp.json
# WITHOUT bearer_token_env_var, so Codex doesn't see an empty
# OPENVIKING_API_KEY at MCP launch and trigger its OAuth fallback for
# what should be an unauthenticated server.
detect_api_key() {
  if [ -n "${OPENVIKING_API_KEY:-}" ] || [ -n "${OPENVIKING_BEARER_TOKEN:-}" ]; then
    echo "1"
    return
  fi
  if [ -f "$OVCLI_CONF" ]; then
    node -e '
      try {
        const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
        process.stdout.write(c.api_key ? "1" : "0");
      } catch { process.stdout.write("0"); }
    ' "$OVCLI_CONF" 2>/dev/null || echo "0"
    return
  fi
  echo "0"
}
HAS_API_KEY="$(detect_api_key)"

# Render the OpenViking /mcp URL into the cached .mcp.json (and drop the
# bearer_token_env_var line in no-auth mode). The repo's checked-in
# .mcp.json keeps the placeholder + always-present bearer field; the cache
# copy is what Codex actually loads.
MCP_JSON="$CACHE_DIR/.mcp.json"
if [ -f "$MCP_JSON" ]; then
  node - "$MCP_JSON" "$MCP_URL" "$HAS_API_KEY" <<'NODE'
const fs = require("node:fs");
const [, , file, url, hasKey] = process.argv;
const j = JSON.parse(fs.readFileSync(file, "utf8"));
const s = j.mcpServers["openviking-memory"];
s.url = url;
if (hasKey !== "1") {
  delete s.bearer_token_env_var;
}
fs.writeFileSync(file, JSON.stringify(j, null, 2) + "\n");
NODE
fi

# ----- Shell rc wrapper -----
#
# The MCP server reads OPENVIKING_API_KEY (and OPENVIKING_ACCOUNT / _USER /
# _AGENT_ID) from the process env at codex launch. Add a `codex` shell function
# that pulls these from ovcli.conf at invocation time so the user doesn't have
# to `export` secrets globally.

case "${SHELL:-}" in
  */zsh)  RC="$HOME/.zshrc" ;;
  */bash) RC="$HOME/.bashrc" ;;
  *)
    if   [ -f "$HOME/.zshrc" ];  then RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then RC="$HOME/.bashrc"
    else RC=""; fi
    ;;
esac

# The wrapper uses `node` (already a hard requirement of this installer) to
# parse ovcli.conf instead of `jq`. This avoids a silent-auth-loss failure
# mode where `jq` is missing on the user's machine, the wrapper's
# `command -v jq` check fails, and codex starts with no Bearer token →
# OpenViking returns 401 → Codex falls back to OAuth.
read -r -d '' WRAPPER_BODY <<'WRAPPER' || true
codex() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if [ -f "$_ov_conf" ] && command -v node >/dev/null 2>&1; then
    local _ov_env
    _ov_env=$(node -e '
      try {
        const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
        const out = (k, v) => v ? `${k}=${JSON.stringify(String(v))}\n` : "";
        process.stdout.write(
          out("OV_URL", c.url) +
          out("OV_KEY", c.api_key) +
          out("OV_ACCOUNT", c.account) +
          out("OV_USER", c.user)
        );
      } catch {}
    ' "$_ov_conf" 2>/dev/null)
    eval "$_ov_env"
    OPENVIKING_URL="${OPENVIKING_URL:-${OV_URL:-}}" \
    OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-${OV_KEY:-}}" \
    OPENVIKING_ACCOUNT="${OPENVIKING_ACCOUNT:-${OV_ACCOUNT:-}}" \
    OPENVIKING_USER="${OPENVIKING_USER:-${OV_USER:-}}" \
    OPENVIKING_AGENT_ID="${OPENVIKING_AGENT_ID:-codex}" \
      command codex "$@"
    unset OV_URL OV_KEY OV_ACCOUNT OV_USER
  else
    command codex "$@"
  fi
}
WRAPPER

# Wrap the function body with marker lines.
WRAPPER_BLOCK="$WRAPPER_MARKER_BEGIN
$WRAPPER_BODY
$WRAPPER_MARKER_END"

if [ -z "$RC" ]; then
  cat >&2 <<EOF

Note: could not detect a shell rc to install the codex() wrapper into.
Add this snippet to your rc manually so OPENVIKING_API_KEY reaches codex:

$WRAPPER_BLOCK
EOF
else
  touch "$RC"
  if grep -qF "$WRAPPER_MARKER_BEGIN" "$RC"; then
    # Replace existing block in place — only if BOTH markers are present, so
    # a corrupted rc (manual edit that lost the END marker) cannot cause us
    # to drop everything from the BEGIN marker to EOF. Otherwise leave the
    # file untouched and append a fresh block, so the user can inspect what
    # they have and clean up themselves.
    if grep -qF "$WRAPPER_MARKER_END" "$RC"; then
      echo "Replacing existing openviking codex() wrapper in $RC"
      awk -v b="$WRAPPER_MARKER_BEGIN" -v e="$WRAPPER_MARKER_END" '
        $0 == b {skip=1; next}
        $0 == e {skip=0; next}
        !skip
      ' "$RC" > "$RC.tmp" && mv "$RC.tmp" "$RC"
    else
      cat >&2 <<EOF
Warning: $WRAPPER_MARKER_BEGIN found in $RC but $WRAPPER_MARKER_END is missing.
Refusing to in-place rewrite; appending a fresh block instead. Please
remove the stray begin marker manually.
EOF
    fi
  else
    echo "Appending codex() wrapper to $RC"
  fi
  printf '\n%s\n' "$WRAPPER_BLOCK" >> "$RC"
fi

if [ ! -f "$OVCLI_CONF" ] && [ "$HAS_API_KEY" != "1" ]; then
  cat >&2 <<EOF

Note: $OVCLI_CONF was not found and no OPENVIKING_API_KEY in env.
The plugin is installed in unauthenticated mode targeting $MCP_URL.
To enable Bearer auth later, create ovcli.conf with an api_key (see
https://docs.openviking.ai/zh/guides/03-deployment#cli) and re-run this
installer — the bearer_token_env_var field will be re-added to .mcp.json.
EOF
fi

cat <<EOF

Installed $PLUGIN_ID (version $PLUGIN_VERSION).
Marketplace: $MARKETPLACE_ROOT
Plugin cache: $CACHE_DIR
MCP endpoint: $MCP_URL
MCP auth: $([ "$HAS_API_KEY" = "1" ] && echo "Bearer (OPENVIKING_API_KEY)" || echo "none (unauthenticated)")

Next:
EOF
if [ -n "$RC" ]; then
  echo "  source $RC      # pick up the codex() wrapper"
else
  echo "  (paste the codex() snippet printed above into your shell rc, then restart your shell)"
fi
echo "  codex           # restart codex; review /hooks if prompted"
