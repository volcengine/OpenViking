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
#
# Besides `claude`, any extra launch commands listed in
# $OPENVIKING_CC_WRAP_EXTRA (set by the installer in your shell rc, one
# command per ';'-separated entry — e.g. "cc-custom") get the same injection.
# For a multi-word entry, only invocations whose leading arguments match the
# configured sub-command are wrapped; every other invocation of that command
# passes through untouched.
#
# Targets bash and zsh (the only shells that source ~/.bashrc / ~/.zshrc).
# Splitting is done with parameter expansion rather than unquoted word
# splitting so it behaves the same under zsh (which does not field-split
# unquoted expansions by default).

# Run a command with OpenViking credentials from ovcli.conf injected into
# its environment only — never exported globally.
_openviking_run() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if [ -f "$_ov_conf" ] && command -v jq >/dev/null 2>&1; then
    local _ov_url _ov_key
    _ov_url=$(jq -r '.url // empty'     "$_ov_conf" 2>/dev/null)
    _ov_key=$(jq -r '.api_key // empty' "$_ov_conf" 2>/dev/null)
    OPENVIKING_URL="${OPENVIKING_URL:-$_ov_url}" \
    OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-$_ov_key}" \
      command "$@"
  else
    command "$@"
  fi
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
    case "$_ov_head" in
      ''|*[!A-Za-z0-9_-]*) continue ;;
    esac
    eval "${_ov_head}() { _openviking_dispatch \"$_ov_helper\" \"$_ov_head\" \"$_ov_sub\" \"\$@\"; }"
  done
}

claude() { _openviking_run claude "$@"; }

_openviking_define_wrappers _openviking_run "${OPENVIKING_CC_WRAP_EXTRA:-}"
