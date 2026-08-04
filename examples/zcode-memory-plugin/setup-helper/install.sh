#!/usr/bin/env bash
#
# OpenViking Memory Hooks for ZCode — installer.
#
# One-liner (from a repo checkout):
#   bash examples/zcode-memory-plugin/setup-helper/install.sh
#
# What it does:
#   1. Resolves the absolute path to this examples/zcode-memory-plugin checkout.
#   2. Renders hooks/hooks.json with __OPENVIKING_ZCODE_ROOT__ -> that path,
#      and writes it into the ZCode config directory (default ~/.zcode/hooks.json).
#   3. Writes the OpenViking MCP server entry into the ZCode MCP config so
#      `node <plugin>/servers/mcp-proxy.mjs` is launched as a stdio MCP server.
#   4. If OPENVIKING_URL / OPENVIKING_API_KEY are set, mirrors them into
#      ~/.openviking/ovcli.conf (only if that file does not already exist).
#   5. Prints verify instructions.
#
# Env overrides:
#   ZCODE_CONFIG_DIR     ZCode config directory (default ~/.zcode)
#   ZCODE_HOOKS_FILE     hooks file name inside that dir (default hooks.json)
#   ZCODE_MCP_FILE       MCP config file name (default mcp.json)
#   OPENVIKING_HOME      OpenViking home (default ~/.openviking)
#   OPENVIKING_URL       OpenViking server URL
#   OPENVIKING_API_KEY   OpenViking API key
#   OPENVIKING_ACCOUNT   Trusted-mode account id (optional)
#   OPENVIKING_USER      Trusted-mode user id (optional)
#   OPENVIKING_PEER_ID   Explicit actor peer (optional; default: workspace-derived)
#
# Non-interactive: set the env vars above and run with --yes.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
ZCODE_CONFIG_DIR="${ZCODE_CONFIG_DIR:-$HOME/.zcode}"
ZCODE_HOOKS_FILE="${ZCODE_HOOKS_FILE:-hooks.json}"
ZCODE_MCP_FILE="${ZCODE_MCP_FILE:-mcp.json}"
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"
INTERACTIVE=1
[ "${1:-}" = "--yes" ] && INTERACTIVE=0

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
step()   { printf '\n%s %s\n' "$(color '1;36' '▶')" "$*"; }
note()   { printf '  %s %s\n' "$(color '0;37' '·')" "$*"; }
warn()   { printf '  %s %s\n' "$(color '0;33' '!')" "$*" >&2; }
die()    { printf '  %s %s\n' "$(color '1;31' '✗')" "$*" >&2; exit 1; }

command -v node >/dev/null 2>&1 || die "Node.js 18+ is required (could not find 'node' on PATH)."
NODE_MAJOR="$(node -e 'process.stdout.write(String((process.versions.node||"0").split(".")[0]))')"
[ "$NODE_MAJOR" -ge 18 ] || die "Node.js 18+ is required (found Node $(node -v))."

# --- 1. Render hooks.json with the absolute plugin root ---------------------
step "Rendering lifecycle hooks"
mkdir -p "$ZCODE_CONFIG_DIR"
HOOKS_DST="$ZCODE_CONFIG_DIR/$ZCODE_HOOKS_FILE"
sed "s|__OPENVIKING_ZCODE_ROOT__|$PLUGIN_DIR|g" "$PLUGIN_DIR/hooks/hooks.json" > "$HOOKS_DST"
note "wrote $HOOKS_DST"

# --- 2. Render MCP config ---------------------------------------------------
step "Wiring OpenViking MCP server"
MCP_DST="$ZCODE_CONFIG_DIR/$ZCODE_MCP_FILE"
node - "$PLUGIN_DIR" "$MCP_DST" <<'NODE'
const pluginRoot = process.argv[2];
const dst = process.argv[3];
const fs = require("fs");
const path = require("path");
const dir = path.dirname(dst);
fs.mkdirSync(dir, { recursive: true });
// Preserve any user-authored mcpServers entries, only add/replace `openviking`.
let existing = {};
try { existing = JSON.parse(fs.readFileSync(dst, "utf8")); } catch {}
if (!existing.mcpServers || typeof existing.mcpServers !== "object") existing.mcpServers = {};
existing.mcpServers.openviking = {
  command: "node",
  args: [path.join(pluginRoot, "servers", "mcp-proxy.mjs")],
};
fs.writeFileSync(dst, `${JSON.stringify(existing, null, 2)}\n`);
process.stdout.write(`  · wrote ${dst}\n`);
NODE

# --- 3. Credentials ---------------------------------------------------------
step "Checking OpenViking credentials"
if [ -f "$OVCLI_CONF" ]; then
  note "found $OVCLI_CONF — hooks and MCP will read it at runtime"
else
  if [ "$INTERACTIVE" -eq 1 ] && [ -t 0 ]; then
    printf '  %s OpenViking server URL [http://127.0.0.1:1933]: ' "$(color '0;37' '?')"
    read -r OV_URL
    OV_URL="${OV_URL:-http://127.0.0.1:1933}"
    printf '  %s OpenViking API key (blank for local unauthenticated): ' "$(color '0;37' '?')"
    read -r OV_KEY
  else
    OV_URL="${OPENVIKING_URL:-http://127.0.0.1:1933}"
    OV_KEY="${OPENVIKING_API_KEY:-}"
  fi
  mkdir -p "$OV_HOME"
  cat > "$OVCLI_CONF" <<EOF
{
  "url": "${OV_URL%/}",
  "api_key": "${OV_KEY}"
}
EOF
  chmod 600 "$OVCLI_CONF"
  note "wrote $OVCLI_CONF"
fi

# --- 4. Verify --------------------------------------------------------------
step "Smoke test"
if node "$PLUGIN_DIR/scripts/zcode-hooks.test.mjs" >/dev/null 2>&1; then
  note "hook tests passed"
else
  warn "hook test runner did not report success — run: node --test $PLUGIN_DIR/scripts/zcode-hooks.test.mjs"
fi

cat <<EOF

$(color '1;32' 'Done.') ZCode OpenViking memory hooks are installed.

Next:
  1. Quit ZCode completely and restart it so it picks up the new hooks + MCP.
  2. In a new ZCode session, ask the agent to search OpenViking memory, or
     tell it a temporary preference and (in a fresh session) ask for it back.
  3. To enable debug logging: start ZCode with OPENVIKING_DEBUG=1 and inspect
     $(color '0;37' '~/.openviking/logs/zcode-hooks.log')

Uninstall: remove the OpenViking block from $MCP_DST
and delete $HOOKS_DST, then restart ZCode.

EOF
