# OpenViking copilot() shell-wrapper fallback (issue #27).
#
# Source this from your ~/.zshrc or ~/.bashrc:
#
#     source /path/to/cli-plugin/wrapper/copilot.sh
#
# Behaviour: every `copilot ...` invocation runs the real GitHub Copilot
# CLI as usual, with two coordination steps wrapped around it:
#
#   1. Before launch: derive a stable OV session id and export it as
#      OPENVIKING_CLI_SESSION_ID. The MCP server (mounted via the user's
#      mcp-config.json) reads this env var and uses it as the default capture
#      session id. So every `openviking_capture` tool call the model
#      makes during this `copilot` run lands in the same OV session.
#
#   2. After exit: run `openviking-copilot-mcp --commit-flush
#      --session=$OPENVIKING_CLI_SESSION_ID` so any pending captures
#      that didn't cross the threshold mid-session land as archives.
#
# Why "degraded fidelity": the wrapper does NOT see the user prompts
# or the assistant's responses. It only forces a final commit. Capture
# itself still requires the model to call `openviking_capture` during
# the session — without that, there's nothing on the OV server to
# commit. The wrapper's job is to close the gap where the model called
# capture but never tripped the commit threshold (or commits queued
# async without flushing on exit).
#
# Configuration:
#   OPENVIKING_BYPASS_SESSION=1   skips the wrapper entirely (no env
#                                 var set, no post-exit commit).
#   OPENVIKING_WRAPPER_QUIET=1    suppresses post-exit commit stderr
#                                 output even when --commit-flush
#                                 fails. Default: failures are
#                                 logged to stderr but never block
#                                 the user's exit code.
#
# Diagnose: `OPENVIKING_DEBUG=1 copilot ...` writes the wrapper's
# coordination decisions plus the MCP server's hook log to
# `~/.openviking/logs/copilot-cli-hooks.log`.

copilot() {
  # Bypass: don't touch env vars, don't post-exit. Pass-through to the
  # real CLI with no coordination — same shape the user would see if
  # the wrapper weren't sourced.
  case "${OPENVIKING_BYPASS_SESSION:-}" in
    1|true|yes|TRUE|YES)
      command copilot "$@"
      return $?
      ;;
  esac

  # Derive a session id once per invocation. Stable for the duration
  # of this `copilot` call so the post-exit commit targets the same
  # session the MCP tool wrote to.
  local _ov_sid
  if command -v uuidgen >/dev/null 2>&1; then
    _ov_sid="cp-$(uuidgen | tr 'A-Z' 'a-z')"
  else
    # Fallback for systems without uuidgen.
    _ov_sid="cp-$$-$(date +%s)"
  fi

  OPENVIKING_CLI_SESSION_ID="${_ov_sid}" command copilot "$@"
  local _ov_rc=$?

  # Post-exit commit. Don't let its outcome perturb the user's exit
  # code — pure best-effort. When OPENVIKING_WRAPPER_QUIET=1, drop
  # stderr entirely.
  if command -v openviking-copilot-mcp >/dev/null 2>&1; then
    case "${OPENVIKING_WRAPPER_QUIET:-}" in
      1|true|yes|TRUE|YES)
        openviking-copilot-mcp --commit-flush --session="${_ov_sid}" >/dev/null 2>&1 || true
        ;;
      *)
        openviking-copilot-mcp --commit-flush --session="${_ov_sid}" >/dev/null || true
        ;;
    esac
  fi

  return ${_ov_rc}
}
