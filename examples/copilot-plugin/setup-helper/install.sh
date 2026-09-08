#!/usr/bin/env bash
#
# OpenViking Memory Plugin for GitHub Copilot — installer.
#
# One-liner (from a repo checkout):
#   bash examples/copilot-plugin/setup-helper/install.sh
#
# What it does:
#   1. Resolves the absolute path to this examples/copilot-plugin checkout.
#   2. Asks for the OpenViking server URL + API key (or accepts env vars).
#   3. Writes one of three Copilot MCP configs, depending on --target:
#        --target vscode  -> .vscode/mcp.json in the current workspace (uses "servers")
#        --target cli     -> ~/.copilot/mcp-config.json (uses "mcpServers", type http)
#        --target repo    -> prints a JSON snippet to paste into GitHub Settings
#                            (Settings -> Copilot -> MCP servers); never written to disk
#      Default: --target cli (matches `gh copilot`).
#   4. Optionally also installs the Agent Skill (openviking-memory) into the
#      Copilot CLI skills directory when --with-skill is passed.
#   5. Runs the plugin's pure-function tests as a smoke test.
#
# Env overrides:
#   OPENVIKING_HOME          OpenViking home (default ~/.openviking)
#   OPENVIKING_CLI_CONFIG_FILE   Override ~/.openviking/ovcli.conf path
#   OPENVIKING_URL           OpenViking server URL (also written to ovcli.conf)
#   OPENVIKING_API_KEY       OpenViking API key (blank for local unauthenticated)
#   COPILOT_CLI_CONFIG_DIR   Copilot CLI config dir (default ~/.copilot)
#   COPILOT_VSCODE_DIR       VSCode workspace dir (default ./.vscode)
#   COPILOT_SKILLS_DIR       Copilot CLI skills dir (default ~/.copilot/skills)
#
# Non-interactive: set OPENVIKING_URL / OPENVIKING_API_KEY and pass --yes.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"
COPILOT_CLI_CONFIG_DIR="${COPILOT_CLI_CONFIG_DIR:-$HOME/.copilot}"
COPILOT_VSCODE_DIR="${COPILOT_VSCODE_DIR:-./.vscode}"
COPILOT_SKILLS_DIR="${COPILOT_SKILLS_DIR:-$HOME/.copilot/skills}"

TARGET="cli"
INTERACTIVE=1
WITH_SKILL=0
for arg in "$@"; do
  case "$arg" in
    --target=*) TARGET="${arg#--target=}" ;;
    --target)   TARGET="cli" ;;  # set later by next arg if value form
    --vscode)   TARGET="vscode" ;;
    --cli)      TARGET="cli" ;;
    --repo)     TARGET="repo" ;;
    --with-skill) WITH_SKILL=1 ;;
    --yes)      INTERACTIVE=0 ;;
    -h|--help)
      sed -n '2,40p' "$0"; exit 0 ;;
    *) ;;
  esac
done

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
step()   { printf '\n%s %s\n' "$(color '1;36' '▶')" "$*"; }
note()   { printf '  %s %s\n' "$(color '0;37' '·')" "$*"; }
warn()   { printf '  %s %s\n' "$(color '0;33' '!')" "$*" >&2; }

# --- 1. Connection settings -------------------------------------------------
step "Resolving OpenViking connection"
if [ -f "$OVCLI_CONF" ] && [ "$INTERACTIVE" -eq 1 ] && [ -t 0 ]; then
  note "found $OVCLI_CONF — using its url/api_key"
fi

if [ "$INTERACTIVE" -eq 1 ] && [ -t 0 ]; then
  DEFAULT_URL="http://127.0.0.1:1933"
  printf '  %s OpenViking server URL [%s]: ' "$(color '0;37' '?')" "$DEFAULT_URL"
  read -r OV_URL
  OV_URL="${OV_URL:-$DEFAULT_URL}"
  printf '  %s OpenViking API key (blank for local unauthenticated): ' "$(color '0;37' '?')"
  read -r OV_KEY
else
  OV_URL="${OPENVIKING_URL:-http://127.0.0.1:1933}"
  OV_KEY="${OPENVIKING_API_KEY:-}"
fi
OV_BASE="${OV_URL%/}"          # base URL for ovcli.conf (shared runtime appends paths)
OV_MCP_URL="${OV_BASE}/mcp"    # full /mcp URL for Copilot MCP configs
[ -z "$OV_KEY" ] && warn "no API key — only works against an unauthenticated local server"

# Mirror into ovcli.conf if not present, so the stdio proxy (if used later)
# picks up the same credentials as the HTTP config we are about to write.
if [ ! -f "$OVCLI_CONF" ]; then
  mkdir -p "$OV_HOME"
  cat > "$OVCLI_CONF" <<EOF
{
  "url": "${OV_BASE}",
  "api_key": "${OV_KEY}"
}
EOF
  chmod 600 "$OVCLI_CONF"
  note "wrote $OVCLI_CONF"
fi

# --- 2. Render config via the same pure helpers the tests exercise ----------
step "Rendering Copilot MCP config (target: $TARGET)"
case "$TARGET" in
  vscode)
    DST="$COPILOT_VSCODE_DIR/mcp.json"
    node --input-type=module - "$PLUGIN_DIR" "$DST" "$OV_MCP_URL" "$OV_KEY" <<'NODE'
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
const pluginRoot = process.argv[2];
const dst = process.argv[3];
const url = process.argv[4];
const apiKey = process.argv[5];
const { buildVscodeConfig } = await import(join(pluginRoot, "scripts", "build-configs.mjs"));
await mkdir(dirname(dst), { recursive: true });
let existing = {};
try { existing = JSON.parse(await readFile(dst, "utf8")); } catch {}
const rendered = buildVscodeConfig({ url, apiKey });
existing.servers = Object.assign({}, existing.servers || {}, rendered.servers);
await writeFile(dst, `${JSON.stringify(existing, null, 2)}\n`);
process.stdout.write(`  · wrote ${dst}\n`);
NODE
    ;;
  cli)
    DST="$COPILOT_CLI_CONFIG_DIR/mcp-config.json"
    node --input-type=module - "$PLUGIN_DIR" "$DST" "$OV_MCP_URL" "$OV_KEY" <<'NODE'
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
const pluginRoot = process.argv[2];
const dst = process.argv[3];
const url = process.argv[4];
const apiKey = process.argv[5];
const { buildCopilotCliConfig } = await import(join(pluginRoot, "scripts", "build-configs.mjs"));
await mkdir(dirname(dst), { recursive: true });
let existing = {};
try { existing = JSON.parse(await readFile(dst, "utf8")); } catch {}
const rendered = buildCopilotCliConfig({ url, apiKey });
existing.mcpServers = Object.assign({}, existing.mcpServers || {}, rendered.mcpServers);
await writeFile(dst, `${JSON.stringify(existing, null, 2)}\n`);
process.stdout.write(`  · wrote ${dst}\n`);
NODE
    ;;
  repo)
    node --input-type=module - "$PLUGIN_DIR" "$OV_MCP_URL" <<'NODE'
import { join } from "node:path";
const pluginRoot = process.argv[2];
const url = process.argv[3];
const { buildGithubRepoConfig } = await import(join(pluginRoot, "scripts", "build-configs.mjs"));
const cfg = buildGithubRepoConfig({ url });
process.stdout.write(
  "\n  Paste this into: GitHub repo → Settings → Copilot → MCP servers\n" +
  "  (create an Agents secret named COPILOT_MCP_OPENVIKING_API_KEY first):\n\n" +
  JSON.stringify(cfg, null, 2) + "\n\n"
);
NODE
    ;;
  *)
    warn "unknown --target=$TARGET; expected vscode|cli|repo"; exit 2 ;;
esac

# --- 3. Optional Agent Skill -----------------------------------------------
if [ "$WITH_SKILL" -eq 1 ]; then
  step "Installing openviking-memory Agent Skill"
  mkdir -p "$COPILOT_SKILLS_DIR"
  cp -R "$PLUGIN_DIR/skills/openviking-memory" "$COPILOT_SKILLS_DIR/"
  note "wrote $COPILOT_SKILLS_DIR/openviking-memory/SKILL.md"
  note "Copilot CLI auto-discovers skills under $COPILOT_SKILLS_DIR"
fi

# --- 4. Smoke test ----------------------------------------------------------
step "Smoke test"
if node --test "$PLUGIN_DIR/scripts/build-configs.test.mjs" >/dev/null 2>&1; then
  note "config-builder tests passed"
else
  warn "tests did not report success — run: node --test $PLUGIN_DIR/scripts/build-configs.test.mjs"
fi

# --- 5. Next steps ----------------------------------------------------------
cat <<EOF

$(color '1;32' 'Done.') OpenViking MCP is configured for GitHub Copilot ($TARGET).

Important: GitHub Copilot has NO lifecycle hooks (no SessionStart/Stop).
Memory is NOT auto-injected or auto-captured — the model calls the OpenViking
MCP tools itself. To approximate the auto-recall experience from Claude Code /
Codex / Cursor, install the bundled Agent Skill:

  bash $PLUGIN_DIR/setup-helper/install.sh --cli --with-skill

Next:
  1. Restart the target Copilot surface so it picks up the new MCP config.
  2. Verify the connection (VSCode: MCP panel; CLI: \`copilot mcp list\`).
  3. Try: "use OpenViking to recall what we decided about X" or just
     "remember that I prefer tabs over spaces".

Uninstall:
  - VSCode:   remove the "openviking" entry from $COPILOT_VSCODE_DIR/mcp.json
  - CLI:      \`copilot mcp remove openviking\` or edit $COPILOT_CLI_CONFIG_DIR/mcp-config.json
  - Repo:     remove the block from the GitHub repo MCP settings UI
  - Skill:    rm -rf $COPILOT_SKILLS_DIR/openviking-memory

EOF
