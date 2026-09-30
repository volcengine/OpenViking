#!/usr/bin/env bash
# Fail when a distributed plugin changed without its version string.
#
# Claude Code resolves an installed plugin by the `version` in its manifest
# rather than by the commit the marketplace ref points at, so a frozen version
# string means users keep running the build they already have no matter how
# many fixes land on main. Other entries use their package or installer manifest;
# pi is copied wholesale and reports its manifest version on the wire. Hermes
# installs the plugin at a pinned commit, but resolves dependencies from
# pyproject.toml and reports plugin.yaml's version in its User-Agent, so the two
# files must agree and move together.
#
# Usage: check-plugin-version-bumps.sh <base-ref>
set -euo pipefail

BASE_REF="${1:-}"
if [ -z "$BASE_REF" ]; then
  echo "usage: $0 <base-ref>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# <plugin directory>:<manifest holding the version>
PLUGINS=(
  "examples/claude-code-memory-plugin:examples/claude-code-memory-plugin/.claude-plugin/plugin.json"
  "examples/codex-memory-plugin:examples/codex-memory-plugin/.codex-plugin/plugin.json"
  "examples/agent-hook-plugin:examples/agent-hook-plugin/plugin.json"
  "examples/opencode-plugin:examples/opencode-plugin/package.json"
  "examples/dsh-memory-plugin:examples/dsh-memory-plugin/package.json"
  "examples/pi-coding-agent-extension:examples/pi-coding-agent-extension/package.json"
  "examples/hermes-plugin:examples/hermes-plugin/plugin.yaml"
)

# A change to the shared library reaches every plugin, and it no longer reaches
# them through a vendored copy inside their directory: the config-driven hook
# hosts have the runtime assembled at install time, and the packaged plugins
# build their copies at pack time. So the library counts as a change to all.
SHARED_LIB="examples/memory-plugin-shared/lib"
# Plugins that do not consume the shared JS library: a change there is not a
# change to them. Hermes is a Python in-process provider.
NO_SHARED_LIB=(
  "examples/hermes-plugin"
)

uses_shared_lib() { # uses_shared_lib <plugin directory>
  local dir
  for dir in "${NO_SHARED_LIB[@]}"; do
    [ "$dir" = "$1" ] && return 1
  done
  return 0
}

read_version() { # read_version <ref-or-empty> <path>
  local ref="$1" path="$2" text
  if [ -n "$ref" ]; then
    text="$(git show "$ref:$path" 2>/dev/null)" || return 1
  else
    text="$(cat "$path")"
  fi
  case "$path" in
    *.yaml | *.yml)
      # Top-level `version:` key, optionally quoted.
      printf '%s\n' "$text" | sed -nE 's/^version:[[:space:]]*["'"'"']?([^"'"'"'[:space:]#]+).*/\1/p' | head -n 1
      return
      ;;
    *.toml)
      # `version` in the [project] table.
      printf '%s\n' "$text" | awk '
        /^\[/ { in_project = ($0 ~ /^\[project\][[:space:]]*$/) }
        in_project && /^version[[:space:]]*=/ {
          sub(/^version[[:space:]]*=[[:space:]]*/, ""); gsub(/["'"'"'[:space:]]/, ""); print; exit
        }'
      return
      ;;
  esac
  printf '%s' "$text" | node -e '
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  try { process.stdout.write(String(JSON.parse(raw).version || "")); }
  catch { process.exit(1); }
});
'
}

# Each host-facing manifest must report the distributed agent-hook version.
# Kimi also exposes kimi.plugin.json to the host and its marketplace, so keep
# that version aligned with the integration and root manifests. Hermes keeps
# its version in both plugin.yaml and pyproject.toml.
PAIRED=(
  "examples/agent-hook-plugin/plugin.json:examples/agent-hook-plugin/hosts/cursor/openviking.integration.json"
  "examples/agent-hook-plugin/plugin.json:examples/agent-hook-plugin/hosts/trae/openviking.integration.json"
  "examples/agent-hook-plugin/plugin.json:examples/agent-hook-plugin/hosts/zcode/openviking.integration.json"
  "examples/agent-hook-plugin/plugin.json:examples/agent-hook-plugin/hosts/kimicode/openviking.integration.json"
  "examples/agent-hook-plugin/plugin.json:examples/agent-hook-plugin/hosts/kimicode/kimi.plugin.json"
  "examples/hermes-plugin/plugin.yaml:examples/hermes-plugin/pyproject.toml"
)

failed=0
for entry in "${PAIRED[@]}"; do
  host="${entry%%:*}"
  integration="${entry#*:}"
  [ -f "$host" ] && [ -f "$integration" ] || continue
  host_version="$(read_version "" "$host")"
  integration_version="$(read_version "" "$integration")"
  if [ "$host_version" != "$integration_version" ]; then
    echo "::error file=$host::$host says $host_version and $integration says $integration_version."
    failed=1
  fi
done

for entry in "${PLUGINS[@]}"; do
  dir="${entry%%:*}"
  manifest="${entry#*:}"

  paths=("$dir")
  uses_shared_lib "$dir" && paths+=("$SHARED_LIB")
  changed="$(git diff --name-only "$BASE_REF...HEAD" -- "${paths[@]}" | grep -v '/node_modules/' || true)"
  [ -n "$changed" ] || continue

  # A plugin added in this branch has no baseline version to compare against.
  before="$(read_version "$BASE_REF" "$manifest")" || continue
  after="$(read_version "" "$manifest")"

  if [ "$before" = "$after" ]; then
    echo "::error file=$manifest::$dir changed but its version is still $after."
    echo "  Hosts install by this string, so users receive nothing until it moves."
    echo "  Files changed:"
    printf '    %s\n' $changed
    failed=1
  else
    echo "ok: $dir  $before -> $after"
  fi
done

exit "$failed"
