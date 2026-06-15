#!/usr/bin/env bash
#
# OpenViking Memory Plugin for Codex — interactive installer.
#
# One-liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
#
# UX mirrors the claude-code installer (colored step output + interactive
# ovcli.conf setup). When stdin is not a TTY (e.g. `curl | bash`) the
# interactive prompts are skipped and existing config / env vars are used.
#
# Env overrides:
#   OPENVIKING_HOME, OPENVIKING_REPO_DIR, OPENVIKING_REPO_URL,
#   OPENVIKING_REPO_REF / OPENVIKING_REPO_BRANCH, OPENVIKING_CLI_CONFIG_FILE,
#   OPENVIKING_CODEX_WRAP_EXTRA (extra launch commands to wrap).
#   OPENVIKING_REPO_ARCHIVE_URL  when set, fetch the source from this zip instead
#                                of git clone (used by the TOS bootstrap for users
#                                who can't reach GitHub). Requires `unzip`.

set -euo pipefail

OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$OV_HOME/openviking-repo}"
# Accept both OPENVIKING_REPO_REF and OPENVIKING_REPO_BRANCH so users can
# reuse the same env var across the claude-code and codex installers.
REPO_REF="${OPENVIKING_REPO_REF:-${OPENVIKING_REPO_BRANCH:-main}}"
REPO_ARCHIVE_URL="${OPENVIKING_REPO_ARCHIVE_URL:-}"
# Marks a $REPO_DIR populated from an archive (no .git). Lets re-runs refresh it
# safely while refusing to clobber a git checkout or unrelated user data.
ARCHIVE_MARKER='.openviking-archive-source'
MARKETPLACE_NAME="${OPENVIKING_CODEX_MARKETPLACE_NAME:-openviking-plugins-local}"
MARKETPLACE_ROOT="${OPENVIKING_CODEX_MARKETPLACE_ROOT:-$HOME/.codex/${MARKETPLACE_NAME}-marketplace}"
PLUGIN_NAME="openviking-memory"
PLUGIN_ID="${PLUGIN_NAME}@${MARKETPLACE_NAME}"
CODEX_CONFIG="${CODEX_CONFIG_FILE:-$HOME/.codex/config.toml}"
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"
DEFAULT_MCP_URL="http://127.0.0.1:1933/mcp"
WRAPPER_MARKER_BEGIN="# >>> openviking-codex-plugin >>>"
WRAPPER_MARKER_END="# <<< openviking-codex-plugin <<<"

if [ -t 1 ]; then
  CYAN=$'\033[0;36m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  CYAN=''; GREEN=''; YELLOW=''; RED=''; BOLD=''; RESET=''
fi
info()    { printf '%s==>%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()    { printf '%s!!%s  %s\n' "$YELLOW" "$RESET" "$*"; }
err()     { printf '%sxx%s  %s\n' "$RED" "$RESET" "$*" >&2; }
ask()     { printf '%s??%s  %s' "$CYAN" "$RESET" "$*"; }
heading() { printf '\n%s%s%s\n' "$BOLD" "$*" "$RESET"; }

# Download a source zip and lay it out at $REPO_DIR (used for the GitHub-free
# TOS install path). The archive is `git archive` output: a single top-level
# OpenViking-<ref>/ dir, identical to a checkout minus .git.
fetch_archive() {
  local url="$1" dest="$2" tmp_zip tmp_dir top
  command -v unzip >/dev/null 2>&1 || { err 'unzip not found; required to install from an archive.'; exit 1; }
  tmp_zip=$(mktemp "${TMPDIR:-/tmp}/ov-src.XXXXXX") || { err 'mktemp failed'; exit 1; }
  tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ov-src.XXXXXX") || { err 'mktemp failed'; rm -f "$tmp_zip"; exit 1; }
  info "Downloading source archive"
  info "  $url"
  curl -fsSL -o "$tmp_zip" "$url" || { err "download failed: $url"; rm -rf "$tmp_zip" "$tmp_dir"; exit 1; }
  unzip -q "$tmp_zip" -d "$tmp_dir" || { err 'unzip failed (corrupt download?)'; rm -rf "$tmp_zip" "$tmp_dir"; exit 1; }
  top=$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ -z "$top" ] || [ ! -d "$top/examples" ]; then
    err 'unexpected archive layout (no top-level dir containing examples/)'
    rm -rf "$tmp_zip" "$tmp_dir"; exit 1
  fi
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  mv "$top" "$dest"
  : > "$dest/$ARCHIVE_MARKER"
  rm -rf "$tmp_zip" "$tmp_dir"
  info "Source ready at $dest"
}

# ----- 1. Environment check -----

heading '1. Environment check'

need() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing required command: $1"; exit 1; }
}
need codex
need git
need node

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
if [ "$NODE_MAJOR" -lt 22 ]; then
  err "Node.js 22+ is required; found $(node --version)."
  exit 1
fi
info "codex: $(codex --version 2>/dev/null || echo unknown)"
info "node:  $(node --version)"

# ----- 2. OpenViking client config -----

heading "2. OpenViking client config ($OVCLI_CONF)"

mkdir -p "$OV_HOME"
chmod 700 "$OV_HOME" 2>/dev/null || true

# Read a field from ovcli.conf via node (codex's stack — no jq dependency).
ov_read_conf() {
  [ -f "$OVCLI_CONF" ] || return 0
  node -e '
    try {
      const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
      const v = c[process.argv[2]];
      if (v) process.stdout.write(String(v));
    } catch {}
  ' "$OVCLI_CONF" "$1" 2>/dev/null || true
}

CURRENT_URL="$(ov_read_conf url)"
CURRENT_KEY="$(ov_read_conf api_key)"

if [ -t 0 ]; then
  # A url with an empty api_key is a valid unauthenticated config, so offer
  # reuse whenever a url exists — don't force the prompt (which would default
  # the url back to localhost and clobber a custom server).
  if [ -n "$CURRENT_URL" ]; then
    info "Existing config found:"
    info "  url     = $CURRENT_URL"
    if [ -n "$CURRENT_KEY" ]; then
      info "  api_key = $(printf '%s' "$CURRENT_KEY" | cut -c1-8)…"
    else
      info "  api_key = (none, unauthenticated)"
    fi
    ask 'Reuse these values? [Y/n] '
    read -r reply || reply=""
    case "$reply" in
      n|N|no|No|NO) CURRENT_URL=""; CURRENT_KEY="" ;;
    esac
  fi

  if [ -z "$CURRENT_URL" ]; then
    printf '%sChoose where you'\''ll connect to OpenViking:%s\n' "$BOLD" "$RESET"
    printf '  1) Self-hosted / local                          [default: http://127.0.0.1:1933]\n'
    printf '  2) Volcengine OpenViking Cloud                  [https://api.vikingdb.cn-beijing.volces.com/openviking]\n'
    ask '[1/2, default 1]: '
    read -r MODE_INPUT || MODE_INPUT=""
    case "$MODE_INPUT" in
      2)
        CURRENT_URL="https://api.vikingdb.cn-beijing.volces.com/openviking"
        info "Using Volcengine OpenViking Cloud: $CURRENT_URL"
        KEY_PROMPT="API key (required for Volcengine OpenViking Cloud): "
        ;;
      *)
        DEFAULT_URL="http://127.0.0.1:1933"
        ask "OpenViking server URL [$DEFAULT_URL]: "
        read -r URL_INPUT || URL_INPUT=""
        CURRENT_URL="${URL_INPUT:-$DEFAULT_URL}"
        KEY_PROMPT="API key (leave empty for unauthenticated local mode): "
        ;;
    esac

    ask "$KEY_PROMPT"
    # -s: don't echo (hide secret); fall back if -s unsupported
    if read -rs API_INPUT 2>/dev/null; then
      printf '\n'
    else
      read -r API_INPUT || API_INPUT=""
    fi
    CURRENT_KEY="$API_INPUT"

    if [ -f "$OVCLI_CONF" ]; then
      backup="$OVCLI_CONF.bak.$(date +%s)"
      cp "$OVCLI_CONF" "$backup"
      info "Backed up existing config → $backup"
    fi
    # Merge url + api_key into any existing config so extra fields (account,
    # user, …) the codex wrapper reads are preserved.
    node -e '
      const fs = require("node:fs");
      const [, file, url, key] = process.argv;
      let c = {};
      try { c = JSON.parse(fs.readFileSync(file, "utf8")); } catch {}
      c.url = url;
      c.api_key = key;
      fs.writeFileSync(file, JSON.stringify(c, null, 2) + "\n");
    ' "$OVCLI_CONF" "$CURRENT_URL" "$CURRENT_KEY"
    chmod 600 "$OVCLI_CONF"
    info "Wrote $OVCLI_CONF (mode 0600)"
  else
    info "Reusing existing config."
  fi
else
  if [ -n "$CURRENT_URL" ]; then
    info "Non-interactive: using existing $OVCLI_CONF"
  else
    warn "Non-interactive and no $OVCLI_CONF — proceeding in unauthenticated mode."
    warn 'Set OPENVIKING_URL / OPENVIKING_API_KEY, or re-run in a terminal, to configure auth.'
  fi
fi

# ----- 3. OpenViking source repository -----

heading "3. OpenViking source repository ($REPO_DIR)"

mkdir -p "$(dirname "$REPO_DIR")" "$HOME/.codex"

if [ -n "$REPO_ARCHIVE_URL" ]; then
  # Archive mode (GitHub-free): refuse to overwrite anything we didn't create.
  if [ -e "$REPO_DIR" ] && [ ! -f "$REPO_DIR/$ARCHIVE_MARKER" ]; then
    err "$REPO_DIR exists and was not created from an archive. Move it aside or set OPENVIKING_REPO_DIR."
    exit 1
  fi
  fetch_archive "$REPO_ARCHIVE_URL" "$REPO_DIR"
elif [ ! -e "$REPO_DIR/.git" ]; then
  if [ -e "$REPO_DIR" ]; then
    err "$REPO_DIR exists but is not a git checkout."
    exit 1
  fi
  info "Cloning $REPO_URL (branch $REPO_REF, depth 1)"
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$REPO_DIR"
else
  info "Refreshing existing checkout ($REPO_REF)"
  git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_REF"
  git -C "$REPO_DIR" reset --hard FETCH_HEAD
fi

PLUGIN_DIR="$REPO_DIR/examples/codex-memory-plugin"
if [ ! -d "$PLUGIN_DIR/.codex-plugin" ]; then
  err "Codex plugin not found at $PLUGIN_DIR"
  exit 1
fi

PLUGIN_VERSION="$(node -e 'const p=require(process.argv[1]); console.log(p.version || "0.0.0")' "$PLUGIN_DIR/.codex-plugin/plugin.json")"

# ----- 4. Plugin install -----

heading "4. Plugin install ($PLUGIN_ID, version $PLUGIN_VERSION)"

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
info "MCP endpoint: $MCP_URL"

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
info "Marketplace registered: $MARKETPLACE_ROOT"

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
info "Enabled plugin + features.plugin_hooks in $CODEX_CONFIG"

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
info "Plugin cache: $CACHE_DIR"
info "MCP auth: $([ "$HAS_API_KEY" = "1" ] && echo "Bearer (OPENVIKING_API_KEY)" || echo "none (unauthenticated)")"

# ----- 5. Shell rc wrapper -----
#
# The MCP server reads OPENVIKING_API_KEY (and OPENVIKING_ACCOUNT / _USER /
# _AGENT_ID) from the process env at codex launch. Install a `codex` shell
# function that pulls these from ovcli.conf at invocation time, so the user
# doesn't have to `export` secrets globally.
#
# Source of truth: setup-helper/wrapper.sh in the plugin checkout. The user's
# shell rc just sources that file directly — no copy step, so any updates land
# via the next `git fetch + reset --hard` the installer runs at the top.

heading '5. Shell rc — codex function wrapper'

WRAPPER_SRC="$PLUGIN_DIR/setup-helper/wrapper.sh"
if [ ! -f "$WRAPPER_SRC" ]; then
  err "Wrapper source not found at $WRAPPER_SRC"
  exit 1
fi

case "${SHELL:-}" in
  */zsh)  RC="$HOME/.zshrc" ;;
  */bash) RC="$HOME/.bashrc" ;;
  *)
    if   [ -f "$HOME/.zshrc" ];  then RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then RC="$HOME/.bashrc"
    else RC=""; fi
    ;;
esac

# Extra launch commands to wrap besides `codex` — e.g. a custom wrapper
# `codex-custom`, or a multi-word launcher matched on its sub-command.
# Persisted in the rc marker block as OPENVIKING_CODEX_WRAP_EXTRA; the wrapper
# reads it and injects credentials into matching invocations only.
# Seed from this run's env var (automation), else the rc value (re-run). The
# interactive prompt below (TTY only) can override.
WRAP_EXTRA="${OPENVIKING_CODEX_WRAP_EXTRA:-}"
if [ -z "$WRAP_EXTRA" ] && [ -n "$RC" ] && [ -f "$RC" ]; then
  WRAP_EXTRA=$(awk -F"'" '/^OPENVIKING_CODEX_WRAP_EXTRA=/{print $2; exit}' "$RC" 2>/dev/null || true)
fi
if [ -z "${OPENVIKING_CODEX_WRAP_EXTRA:-}" ] && [ -t 0 ]; then
  info 'Inject OpenViking creds into other launch commands too? e.g. a custom'
  info 'wrapper `codex-custom`. A multi-word launcher (a base command plus a'
  info 'sub-command) is matched on that sub-command; other uses of the command'
  info 'pass through untouched.'
  if [ -n "$WRAP_EXTRA" ]; then
    info "Currently: $WRAP_EXTRA"
    ask 'Commands (;-separated; empty = keep, "-" = clear): '
  else
    ask 'Commands (;-separated, e.g. "codex-custom"; empty to skip): '
  fi
  read -r WRAP_INPUT || WRAP_INPUT=""
  case "$WRAP_INPUT" in
    "") : ;;
    -)  WRAP_EXTRA="" ;;
    *)  WRAP_EXTRA="$WRAP_INPUT" ;;
  esac
fi
# Normalize each ';'-entry: strip single quotes (keep the rc line safely
# single-quotable), trim, collapse internal whitespace, drop empties.
if [ -n "$WRAP_EXTRA" ]; then
  WRAP_EXTRA=$(printf '%s' "$WRAP_EXTRA" | awk -F';' '{
    out="";
    for (i = 1; i <= NF; i++) {
      s = $i; gsub(/\047/, "", s); gsub(/^[ \t]+|[ \t]+$/, "", s); gsub(/[ \t]+/, " ", s);
      if (s != "") out = (out == "" ? s : out ";" s);
    }
    print out;
  }')
  [ -n "$WRAP_EXTRA" ] && info "Will wrap: $WRAP_EXTRA"
fi

# The hook content stays stable across installs (only the absolute path
# matters), so the marker-replacement logic only triggers the legacy cleanup
# path once when upgrading from a pre-rc-split install that inlined the full
# wrapper into the rc.
SOURCE_HOOK="[ -f \"$WRAPPER_SRC\" ] && . \"$WRAPPER_SRC\""
if [ -n "$WRAP_EXTRA" ]; then
  SOURCE_BLOCK="$WRAPPER_MARKER_BEGIN
OPENVIKING_CODEX_WRAP_EXTRA='$WRAP_EXTRA'
$SOURCE_HOOK
$WRAPPER_MARKER_END"
else
  SOURCE_BLOCK="$WRAPPER_MARKER_BEGIN
$SOURCE_HOOK
$WRAPPER_MARKER_END"
fi

if [ -z "$RC" ]; then
  warn 'Could not detect a shell rc. Add this snippet to your rc manually:'
  warn ''
  while IFS= read -r line; do warn "  $line"; done <<EOF
$SOURCE_BLOCK
EOF
else
  touch "$RC"
  if grep -qF "$WRAPPER_MARKER_BEGIN" "$RC"; then
    # Strip the existing marker block (whether it's the new one-liner or an
    # old inline-wrapper block from a previous version). Both markers must be
    # present — refuse the in-place rewrite otherwise.
    if grep -qF "$WRAPPER_MARKER_END" "$RC"; then
      info "Replacing openviking source hook in $RC"
      awk -v b="$WRAPPER_MARKER_BEGIN" -v e="$WRAPPER_MARKER_END" '
        $0 == b {skip=1; next}
        $0 == e {skip=0; next}
        !skip
      ' "$RC" > "$RC.tmp" && mv "$RC.tmp" "$RC"
    else
      warn "$WRAPPER_MARKER_BEGIN found in $RC but $WRAPPER_MARKER_END is missing."
      warn 'Refusing to in-place rewrite; appending a fresh source hook instead.'
      warn 'Please remove the stray begin marker manually.'
    fi
  else
    info "Appending openviking source hook to $RC"
  fi
  printf '\n%s\n' "$SOURCE_BLOCK" >> "$RC"
fi

if [ ! -f "$OVCLI_CONF" ] && [ "$HAS_API_KEY" != "1" ]; then
  warn "$OVCLI_CONF was not found and no OPENVIKING_API_KEY in env."
  warn "Installed in unauthenticated mode targeting $MCP_URL."
  warn 'To enable Bearer auth later, create ovcli.conf with an api_key and re-run.'
fi

# ----- Done -----

heading 'Done!'
info "Plugin:    $PLUGIN_ID (version $PLUGIN_VERSION)"
info "Config:    $OVCLI_CONF"
info "MCP:       $MCP_URL ($([ "$HAS_API_KEY" = "1" ] && echo "Bearer auth" || echo "unauthenticated"))"
[ -n "$RC" ] && info "Shell rc:  $RC"
printf '\n'
if [ -n "$RC" ]; then
  printf '%s%sNext — run this in your shell to pick up the codex() wrapper:%s\n' "$BOLD" "$YELLOW" "$RESET"
  printf '    %s%ssource %s%s\n' "$BOLD" "$CYAN" "$RC" "$RESET"
  printf '  (or just open a new terminal window)\n\n'
else
  printf '  (paste the snippet printed above into your shell rc, then restart your shell)\n\n'
fi
info 'Then:'
info '  codex               # start Codex; review /hooks if prompted'
