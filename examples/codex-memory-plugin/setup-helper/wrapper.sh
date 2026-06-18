# OpenViking codex memory plugin shell wrapper.
#
# Sourced from the user's shell rc via a `[ -f ... ] && . ...` hook that the
# installer writes once. Updates land via the installer's `git fetch +
# reset --hard` of the plugin checkout — no need to re-run it to refresh.
#
# This wrapper exists because Codex's MCP runtime reads OPENVIKING_API_KEY
# (and OPENVIKING_ACCOUNT / _USER / _PEER_ID) from the process env at
# codex launch. Rather than asking users to `export` secrets globally, we
# wrap `codex` in a shell function that:
#
#   1. Resolves credentials through the plugin's shared resolver. By default
#      active ovcli.conf wins, so `ov config switch` controls hooks, MCP, and
#      in-process `ov` commands together. Set OPENVIKING_CREDENTIAL_SOURCE=env
#      to force env-var credentials.
#
#   2. Rewrites the cached .mcp.json's URL and bearer_token_env_var to
#      match the resolved state. Required because Codex 0.130 hard-fails
#      with "Environment variable ... is empty" when bearer_token_env_var
#      points at an empty env var; and because the cached URL is otherwise
#      install-time-baked, so swapping OPENVIKING_CLI_CONFIG_FILE between
#      configs targeting different OV servers would silently keep hitting
#      the install-time URL.
#
#   3. Exec's the launcher after stripping stale credential env vars and
#      adding only the resolved OPENVIKING_* values (so empty values never
#      reach codex as empty strings).
#
# Besides `codex`, any extra launch commands listed in
# $OPENVIKING_CODEX_WRAP_EXTRA (set by the installer in your shell rc, one
# command per ';'-separated entry — e.g. "codex-custom") get the same
# treatment. For a multi-word entry, only invocations whose leading arguments
# match the configured sub-command are wrapped; every other invocation of
# that command passes through untouched.
#
# Targets bash and zsh (the only shells that source ~/.bashrc / ~/.zshrc).

_openviking_codex_plugin_dir() {
  local _ov_src
  if [ -n "${BASH_SOURCE[0]-}" ]; then
    _ov_src="${BASH_SOURCE[0]}"
  else
    _ov_src="${(%):-%x}"
  fi
  cd "$(dirname "$_ov_src")/.." >/dev/null 2>&1 && pwd -P
}

# Resolve OpenViking credentials, sync the cached .mcp.json, then exec the
# given launch command with an OPENVIKING_* env prefix. $@ is the command to
# run (e.g. "codex", or a custom launcher like "codex-custom").
_openviking_codex_exec() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if ! command -v node >/dev/null 2>&1; then
    command "$@"
    return
  fi

  local _ov_plugin_dir _ov_creds_script
  _ov_plugin_dir="$(_openviking_codex_plugin_dir 2>/dev/null || true)"
  _ov_creds_script="$_ov_plugin_dir/scripts/ov-credentials.mjs"
  if [ ! -f "$_ov_creds_script" ]; then
    command "$@"
    return
  fi

  local _ov_env
  _ov_env=$(OPENVIKING_CLI_CONFIG_FILE="$_ov_conf" node "$_ov_creds_script" shell-env 2>/dev/null) || _ov_env=""
  if [ -z "$_ov_env" ]; then
    command "$@"
    return
  fi
  eval "$_ov_env"

  # Sync cache .mcp.json to current OV connection state: rewrite both the
  # URL (so OPENVIKING_CLI_CONFIG_FILE swaps actually change the target)
  # and the bearer_token_env_var field (Codex 0.130 hard-fails on empty
  # bearer env vars, so the field must be absent in no-auth mode). The
  # node script writes only when something actually changes — idempotent
  # fast-path so we don't bump file mtime on every codex launch.
  local _cache_root _cache_mcp
  _cache_root="$HOME/.codex/plugins/cache/openviking-plugins-local/openviking-memory"
  if [ -d "$_cache_root" ]; then
    while IFS= read -r _cache_mcp; do
      [ -f "$_cache_mcp" ] || continue
      OPENVIKING_CLI_CONFIG_FILE="${OV_RESOLVED_CLI_CONFIG_FILE:-$_ov_conf}" \
        node "$_ov_creds_script" sync-mcp "$_cache_mcp" 2>/dev/null || true
    done < <(find "$_cache_root" -mindepth 2 -maxdepth 2 -name .mcp.json -type f 2>/dev/null)
  fi

  # Build env-prefix dynamically so empty values are NOT exported as empty
  # strings — Codex hard-fails on empty bearer_token_env_var targets.
  local -a _env_args=()
  [ -n "${OV_RESOLVED_CLI_CONFIG_FILE:-}" ] && _env_args+=("OPENVIKING_CLI_CONFIG_FILE=$OV_RESOLVED_CLI_CONFIG_FILE")
  [ -n "${OV_RESOLVED_URL:-}" ]             && _env_args+=("OPENVIKING_URL=$OV_RESOLVED_URL")
  [ -n "${OV_RESOLVED_API_KEY:-}" ]         && _env_args+=("OPENVIKING_API_KEY=$OV_RESOLVED_API_KEY")
  [ -n "${OV_RESOLVED_ACCOUNT:-}" ]         && _env_args+=("OPENVIKING_ACCOUNT=$OV_RESOLVED_ACCOUNT")
  [ -n "${OV_RESOLVED_USER:-}" ]            && _env_args+=("OPENVIKING_USER=$OV_RESOLVED_USER")
  [ -n "${OV_RESOLVED_PEER_ID:-}" ]         && _env_args+=("OPENVIKING_PEER_ID=$OV_RESOLVED_PEER_ID")

  env \
    -u OPENVIKING_URL \
    -u OPENVIKING_BASE_URL \
    -u OPENVIKING_MCP_URL \
    -u OPENVIKING_API_KEY \
    -u OPENVIKING_BEARER_TOKEN \
    -u OPENVIKING_ACCOUNT \
    -u OPENVIKING_USER \
    -u OPENVIKING_PEER_ID \
    "${_env_args[@]}" "$@"
}

# Runtime guard for a wrapped command. $1 = exec helper, $2 = command name,
# $3 = the configured sub-command prefix (empty for single-word commands),
# rest = the actual args. Inject only when the leading args match $3.
_openviking_dispatch() {
  local _ov_helper="$1" _ov_cmd="$2" _ov_want="$3"; shift 3
  if [ -n "$_ov_want" ]; then
    local _ov_rest="$_ov_want" _ov_word _ov_actual _ov_i=1 _ov_ok=1
    while [ -n "$_ov_rest" ]; do
      case "$_ov_rest" in
        *' '*) _ov_word="${_ov_rest%% *}"; _ov_rest="${_ov_rest#* }" ;;
        *)     _ov_word="$_ov_rest"; _ov_rest="" ;;
      esac
      eval "_ov_actual=\${$_ov_i-}"
      if [ "$_ov_actual" != "$_ov_word" ]; then _ov_ok=0; break; fi
      _ov_i=$((_ov_i + 1))
    done
    if [ "$_ov_ok" != 1 ]; then
      command "$_ov_cmd" "$@"
      return $?
    fi
  fi
  "$_ov_helper" "$_ov_cmd" "$@"
}

# Define a wrapper function for each ';'-separated entry in $2, routing it
# through the exec helper $1. No subshell (functions must persist in the
# current shell) and no unquoted word splitting (zsh-safe).
_openviking_define_wrappers() {
  local _ov_helper="$1" _ov_list="$2" _ov_entry _ov_head _ov_sub
  [ -n "$_ov_list" ] || return 0
  while [ -n "$_ov_list" ]; do
    case "$_ov_list" in
      *';'*) _ov_entry="${_ov_list%%;*}"; _ov_list="${_ov_list#*;}" ;;
      *)     _ov_entry="$_ov_list"; _ov_list="" ;;
    esac
    _ov_entry="${_ov_entry#"${_ov_entry%%[![:space:]]*}"}"
    _ov_entry="${_ov_entry%"${_ov_entry##*[![:space:]]}"}"
    [ -n "$_ov_entry" ] || continue
    # Allow only word-like characters so a value can never smuggle shell
    # metacharacters (" $ ` \ ;, …) into the eval below.
    case "$_ov_entry" in
      *[!A-Za-z0-9\ ._-]*) continue ;;
    esac
    _ov_head="${_ov_entry%% *}"
    if [ "$_ov_head" = "$_ov_entry" ]; then _ov_sub=""; else _ov_sub="${_ov_entry#* }"; fi
    # Reject empty, leading-`-` (would be misread as an option by `alias`
    # below and `command` in the dispatcher), or any non-word-char head.
    case "$_ov_head" in
      ''|-*|*[!A-Za-z0-9_-]*) continue ;;
    esac
    # A same-named shell alias (e.g. `alias cx=codex`) already routes through
    # the base `codex` wrapper once it expands, so it needs no function here —
    # and defining one is actively harmful: bash expands the alias mid-eval and
    # clobbers the real wrapper, while zsh aborts with a parse error on every
    # shell start. Skip alias names; they're covered for free.
    if alias "$_ov_head" >/dev/null 2>&1; then
      continue
    fi
    eval "${_ov_head}() { _openviking_dispatch \"$_ov_helper\" \"$_ov_head\" \"$_ov_sub\" \"\$@\"; }"
  done
}

codex() { _openviking_codex_exec codex "$@"; }

_openviking_define_wrappers _openviking_codex_exec "${OPENVIKING_CODEX_WRAP_EXTRA:-}"
