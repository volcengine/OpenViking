# Antigravity CLI (agy) Memory Integration

Give the Antigravity CLI (`agy`) long-term memory across projects and sessions. OpenViking lifecycle hooks load relevant context before each model call and capture the session transcript on `Stop`, committing it for memory extraction. The built-in `/mcp` endpoint remains available for explicit memory search, reading, and management.

## Prerequisites

- Linux or macOS with the Antigravity CLI (`agy`).
- Node.js 18+ (the hooks are plain Node scripts run via `sh -c`).
- A running OpenViking server; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).

## Install

Installer support is not wired yet, so register the hooks manually:

```bash
# 1. copy the adapter into a stable location
mkdir -p "$HOME/.openviking/agent-integrations/agy"
rsync -a --delete ./examples/agy-memory-hooks/ "$HOME/.openviking/agent-integrations/agy/"
```

Then edit `~/.gemini/config/hooks.json`: it must be valid JSON with a single
`openviking` key, and the two commands must point at absolute paths:

```json
{
  "openviking": {
    "PreInvocation": [
      { "type": "command", "command": "node $HOME/.openviking/agent-integrations/agy/scripts/auto-recall.mjs", "timeout": 20 }
    ],
    "Stop": [
      { "type": "command", "command": "node $HOME/.openviking/agent-integrations/agy/scripts/auto-capture.mjs", "timeout": 30 }
    ]
  }
}
```

`~` is expanded and commands run with the hooks.json directory as the working
directory; `node` must be on `PATH`. Quit and restart the CLI afterwards.

### Bypassing sensitive projects

Add an `agy` section to `~/.openviking/ov.conf`; sessions whose conversation id
or workspace path matches a pattern are fully inert (no recall, no capture):

```json
{
  "agy": {
    "bypassSessionPatterns": ["**sensitive-project**"]
  }
}
```

`OPENVIKING_BYPASS_SESSION_PATTERNS` (comma-separated) overrides the array. The
same section also accepts `enabled`, `autoRecall`, `autoCapture`,
`workspacePeer`, `scoreThreshold` and `recallLimit`.

## What gets installed

- `PreInvocation` replays pending writes, loads the actor profile once per
  session, then recalls relevant memory for the current user turn and injects it
  via `injectSteps[].ephemeralMessage`.
- `Stop` parses the session transcript
  (`~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`),
  captures new user/assistant turns and immediately commits the session,
  including short sessions.
- Only conversational records become turns. agy emits tool results under the
  model's own source (`MODEL/VIEW_FILE`, `MODEL/RUN_COMMAND`, …) and IDE edit
  notices under the user's (`USER_EXPLICIT/CODE_ACTION`), so the adapter selects
  on the record type as well: `USER_INPUT` for user turns and `PLANNER_RESPONSE`
  for assistant turns. Raw command output and file dumps never reach memory.
- Prompts are stored as written: agy wraps them in `<USER_REQUEST>` and appends
  an `<ADDITIONAL_METADATA>` block with the local time, and both are stripped.
- Turns are ordered by `step_index`, not by their position in the file: agy
  flushes the transcript asynchronously, so records can land out of order around
  `Stop`.
- Deduplication is by content hash (not a step cursor), so a re-scan is
  idempotent and no turn is silently dropped.

## Verify

1. Restart `agy` and start a new session in a project directory.
2. Ask about an existing project or preference and confirm the response uses stored memory.
3. Tell the Agent a temporary preference, wait for the response to finish, then start a new session and ask for it again — the value should come back from memory.

For Hook diagnostics, run with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/agy-hooks.log`.

## See also

- [TRAE, TRAE CN and TRAE CLI Memory Integration](./13-trae.md)
- [Authentication](../guides/04-authentication.md)
- [Agent Integrations Overview](./01-overview.md)
