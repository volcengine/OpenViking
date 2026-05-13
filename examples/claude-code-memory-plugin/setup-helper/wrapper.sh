# OpenViking claude-code memory plugin shell wrapper.
#
# Sourced from the user's shell rc via a `[ -f ... ] && . ...` hook that
# the installer writes once. Updates land for free via the installer's
# `git fetch + reset --hard` of the plugin checkout — no need to re-run
# the installer just to refresh this wrapper.
#
# The MCP server URL and bearer token end up in `.mcp.json` rather than
# in the model's per-process env, so Claude Code needs the OpenViking
# credentials in the env at `claude` launch. The wrapper pulls them from
# ovcli.conf and injects them as a prefix, so the user doesn't need to
# `export OPENVIKING_API_KEY` globally and risk leaking it into other
# subprocesses.

claude() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if [ -f "$_ov_conf" ] && command -v jq >/dev/null 2>&1; then
    local _ov_url _ov_key
    _ov_url=$(jq -r '.url // empty'     "$_ov_conf" 2>/dev/null)
    _ov_key=$(jq -r '.api_key // empty' "$_ov_conf" 2>/dev/null)
    OPENVIKING_URL="${OPENVIKING_URL:-$_ov_url}" \
    OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-$_ov_key}" \
      command claude "$@"
  else
    command claude "$@"
  fi
}
