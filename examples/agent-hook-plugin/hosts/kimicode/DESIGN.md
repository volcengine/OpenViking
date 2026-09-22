# Kimi Code host contract

Verified against Kimi Code CLI 0.43.1 and Node.js 18+.

- Kimi loads a native plugin from its managed plugin directory. The installer
  assembles that directory; this source directory is not itself a complete
  runnable plugin.
- Hook stdin is snake-case JSON. The adapter consumes the verified
  `session_id`, `cwd`, `input`, `tool_name`, and `tool_input` fields only;
  `UserPromptSubmit.input` may be a string or an array of content parts.
- `UserPromptSubmit` accepts raw text on stdout. Other lifecycle hooks are
  silent; `PreToolUse` returns Kimi's `permissionDecision` JSON shape.
- The stable transcript is the session's `agents/main/wire.jsonl`, located via
  `session_index.jsonl`. `turn.ended` closes interrupted or tool-only turns.
  If log rotation removes the acknowledged cursor, capture restarts from the
  current file and the bounded acknowledgement set suppresses duplicates.
- Stop, PreCompact, and SessionEnd capture may detach. Interrupt stays
  synchronous and all of its OpenViking requests share a two-second budget.
- The native plugin manifest owns hooks and MCP. Installation does not edit
  legacy `config.toml` or `mcp.json` files.

Shared configuration, recall, capture filtering, retry queues, HTTP, and MCP
transport remain in `memory-plugin-shared`; `kimicode.mjs` owns only Kimi's
event mapping, input/output shape, transcript decoding, and commit policy.

Versioned host references:

- [native plugin manifest and manager](https://github.com/MoonshotAI/kimi-cli/tree/0.43.1/src/kimi_cli/plugin)
- [hook event models](https://github.com/MoonshotAI/kimi-cli/tree/0.43.1/src/kimi_cli/hooks)
