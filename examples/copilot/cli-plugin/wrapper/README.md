# `copilot()` shell-wrapper fallback (issue #27)

Optional **degraded-fidelity** capture path for the GitHub Copilot CLI. The
[`openviking_capture` MCP tool](../README.md) is the primary capture
mechanism — but it requires the model to actually call it at end-of-turn,
which is a model-discretion concern. The wrapper closes one specific gap:
captures that the model DID record mid-session but that didn't cross
`commitTokenThreshold` to trigger an automatic commit.

## What the wrapper does

```
        ┌──────────────────────────────────────────────────────────┐
        │ shell rc (~/.zshrc, ~/.bashrc) sources copilot.sh        │
        └──────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │ user runs `copilot "fix the auth migration"`              │
        └──────────────────────────────────────────────────────────┘
                                      │
                ┌─────────────────────┴────────────────────┐
                │ wrapper derives `cp-<uuid>` session id   │
                │ exports OPENVIKING_CLI_SESSION_ID=$id     │
                │ → command copilot "..."                   │
                └─────────────────────┬────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────┐
                │ copilot CLI mounts MCP server, which     │
                │ reads OPENVIKING_CLI_SESSION_ID and uses  │
                │ it as the default capture session id.    │
                │ Model may call openviking_capture {…}    │
                │ zero or more times during the session.    │
                └─────────────────────┬────────────────────┘
                                      │ (CLI exits)
                                      ▼
                ┌──────────────────────────────────────────┐
                │ wrapper runs:                             │
                │   openviking-copilot-mcp                  │
                │     --commit-flush --session=$id          │
                │ → forces archive of any pending captures  │
                └──────────────────────────────────────────┘
```

## What the wrapper does NOT do

- **It does not see the user's prompt** or **the assistant's response**.
  The wrapper has no transcript access; it can't capture a turn the model
  forgot to call `openviking_capture` for.
- **It does not bypass the model-discretion risk** baked into the CLI
  capture path. If the model never invokes `openviking_capture` during
  a session, the OV session has no pending turns, and the post-exit
  commit-flush archives nothing.
- **It does not block your exit code**. The wrapper preserves the
  CLI's exit code and silently absorbs any commit-flush failure.

## Install

```bash
# Add to ~/.zshrc or ~/.bashrc:
source /absolute/path/to/cli-plugin/wrapper/copilot.sh

# Reload your shell:
source ~/.zshrc   # or ~/.bashrc
```

The bin must be on PATH. If you installed via `npm i -g
@openviking/copilot-cli-memory` you're done. If you're running from
source, expose `dist/mcp-server.js` as `openviking-copilot-mcp` somewhere
on PATH.

## Disable

```bash
# One-off (current invocation only):
OPENVIKING_BYPASS_SESSION=1 copilot ...

# Permanent (per-shell):
unset -f copilot   # in your rc, or run interactively
```

## Configuration

| Env var                       | Effect                                                                                |
|-------------------------------|---------------------------------------------------------------------------------------|
| `OPENVIKING_BYPASS_SESSION=1` | Skip the wrapper entirely. Real `copilot` runs as if the wrapper were never sourced. |
| `OPENVIKING_WRAPPER_QUIET=1`  | Suppress stderr output from the post-exit commit-flush even on failure.              |
| `OPENVIKING_DEBUG=1`          | Wrapper coordination + MCP-server hook log lands in `~/.openviking/logs/`.           |
| `OPENVIKING_CLI_SESSION_ID`   | Set by the wrapper; consumed by the MCP server as the default capture session id.    |

## When to NOT use the wrapper

- You're running `copilot` purely non-interactively (e.g. `copilot
  --print "..." | head`) and don't care about end-of-session memory
  hygiene. The wrapper's overhead is small but non-zero, and the MCP
  server's threshold-based commits cover most real-world capture.
- You explicitly want every capture to flush as it happens. Set
  `commitTokenThreshold: 1` in your `~/.openviking/ov.conf` instead;
  the per-tool-call commits are crisper than a final batch.

## Replace this once VS Code (and the CLI) ship a hook API

This wrapper is a pre-API stopgap. When the GitHub Copilot CLI grows a
turn-level event hook (the equivalent of Claude Code's `Stop` hook),
the post-exit commit-flush becomes a per-turn force-commit, and the
wrapper's design changes correspondingly. The `openviking_capture`
MCP tool path is forward-compatible with that change.
