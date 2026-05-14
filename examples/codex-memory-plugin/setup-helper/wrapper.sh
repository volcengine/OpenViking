# OpenViking codex memory plugin shell wrapper.
#
# Installed by examples/codex-memory-plugin/setup-helper/install.sh to
# ${OPENVIKING_CODEX_WRAPPER_RC:-~/.openviking/codex-plugin.rc.sh}. The
# installer copies this file verbatim — re-run the installer to update.
#
# This wrapper exists because Codex's MCP runtime reads OPENVIKING_API_KEY
# (and OPENVIKING_ACCOUNT / _USER / _AGENT_ID) from the process env at
# codex launch. Rather than asking users to `export` secrets globally, we
# wrap `codex` in a shell function that:
#
#   1. Reads the user's ovcli.conf (env > $OPENVIKING_CLI_CONFIG_FILE >
#      ~/.openviking/ovcli.conf) and resolves URL / API key / identity.
#
#   2. Rewrites the cached .mcp.json's URL and bearer_token_env_var to
#      match the resolved state. Required because Codex 0.130 hard-fails
#      with "Environment variable ... is empty" when bearer_token_env_var
#      points at an empty env var; and because the cached URL is otherwise
#      install-time-baked, so swapping OPENVIKING_CLI_CONFIG_FILE between
#      configs targeting different OV servers would silently keep hitting
#      the install-time URL.
#
#   3. Exec's codex with a dynamically built env prefix that omits any
#      OPENVIKING_* whose resolved value is empty (so empty values never
#      reach codex as empty strings).

codex() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if ! command -v node >/dev/null 2>&1; then
    command codex "$@"
    return
  fi

  # Resolve OV connection settings: existing env > ovcli.conf > nothing.
  local _ov_url _ov_key _ov_account _ov_user
  if [ -f "$_ov_conf" ]; then
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
  fi
  _ov_url="${OPENVIKING_URL:-${OV_URL:-}}"
  _ov_key="${OPENVIKING_API_KEY:-${OV_KEY:-}}"
  _ov_account="${OPENVIKING_ACCOUNT:-${OV_ACCOUNT:-}}"
  _ov_user="${OPENVIKING_USER:-${OV_USER:-}}"
  unset OV_URL OV_KEY OV_ACCOUNT OV_USER

  # Sync cache .mcp.json to current OV connection state: rewrite both the
  # URL (so OPENVIKING_CLI_CONFIG_FILE swaps actually change the target)
  # and the bearer_token_env_var field (Codex 0.130 hard-fails on empty
  # bearer env vars, so the field must be absent in no-auth mode). The
  # node script writes only when something actually changes — idempotent
  # fast-path so we don't bump file mtime on every codex launch.
  local _has_key _mcp_url_from_conf
  if [ -n "$_ov_key" ]; then _has_key=1; else _has_key=0; fi
  if [ -n "$_ov_url" ]; then
    if [ -n "${OPENVIKING_MCP_URL:-}" ]; then
      _mcp_url_from_conf="$OPENVIKING_MCP_URL"
    else
      _mcp_url_from_conf="${_ov_url%/}/mcp"
    fi
  else
    _mcp_url_from_conf=""
  fi
  local _cache_mcp
  for _cache_mcp in "$HOME"/.codex/plugins/cache/openviking-plugins-local/openviking-memory/*/.mcp.json; do
    [ -f "$_cache_mcp" ] || continue
    node -e '
      const fs = require("node:fs");
      // node -e: argv is [node, file, hasKey, url] — no [eval] placeholder.
      const file = process.argv[1];
      const hasKey = process.argv[2];
      const url = process.argv[3] || "";
      const j = JSON.parse(fs.readFileSync(file, "utf8"));
      const s = j.mcpServers && j.mcpServers["openviking-memory"];
      if (s) {
        let changed = false;
        if (url && s.url !== url) {
          s.url = url;
          changed = true;
        }
        const cur = s.bearer_token_env_var || "";
        if (hasKey === "1" && cur !== "OPENVIKING_API_KEY") {
          s.bearer_token_env_var = "OPENVIKING_API_KEY";
          changed = true;
        } else if (hasKey !== "1" && cur) {
          delete s.bearer_token_env_var;
          changed = true;
        }
        if (changed) {
          fs.writeFileSync(file, JSON.stringify(j, null, 2) + "\n");
        }
      }
    ' "$_cache_mcp" "$_has_key" "$_mcp_url_from_conf" 2>/dev/null || true
  done

  # Build env-prefix dynamically so empty values are NOT exported as empty
  # strings — Codex hard-fails on empty bearer_token_env_var targets.
  local -a _env_args=()
  [ -n "$_ov_url" ]     && _env_args+=("OPENVIKING_URL=$_ov_url")
  [ -n "$_ov_key" ]     && _env_args+=("OPENVIKING_API_KEY=$_ov_key")
  [ -n "$_ov_account" ] && _env_args+=("OPENVIKING_ACCOUNT=$_ov_account")
  [ -n "$_ov_user" ]    && _env_args+=("OPENVIKING_USER=$_ov_user")
  _env_args+=("OPENVIKING_AGENT_ID=${OPENVIKING_AGENT_ID:-codex}")

  env "${_env_args[@]}" codex "$@"
}
