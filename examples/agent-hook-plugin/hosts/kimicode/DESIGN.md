# Kimi Code host contract

Verified against Kimi Code CLI 0.43.1 and, for capture and the hook events below, a live
2.8.1 session; Node.js 18+.

- Kimi loads a native plugin from its managed plugin directory. The installer
  assembles that directory; this source directory is not itself a complete
  runnable plugin.
- Hook stdin is snake-case JSON. The adapter consumes the verified
  `session_id`, `cwd`, `prompt`, `tool_name`, and `tool_input` fields only;
  `UserPromptSubmit.prompt` may be a string or an array of content parts.
- `UserPromptSubmit` accepts raw text on stdout. Other lifecycle hooks are
  silent; `PreToolUse` returns Kimi's `permissionDecision` JSON shape.
- The stable transcript is the session's `agents/main/wire.jsonl`, located via
  `session_index.jsonl`. `turn.ended` closes interrupted or tool-only turns.
  If log rotation removes the acknowledged cursor, capture restarts from the
  current file and the bounded acknowledgement set suppresses duplicates.
- The user's words are the text of a `user` message whose origin kind is
  `user`, without the parts Kimi tags `meta.source: "user prompt submit hook"`
  (a hook's output) or `"skill activation"` (an activated skill's body), so a
  skill invoked with `/skill:<name> <args>` is captured as the command typed.
  Messages of origin kind `injection` are host reminders and are skipped.
- Capture reads `context.append_loop_event` records: `content.part` text,
  `tool.call` (`toolCallId`, `name`, `args`, `turnId`; the `stepUuid` of a step
  that carries a `turnId` stands in when it is missing) and `tool.result`
  (`toolCallId`, `result.output` as a string or content parts,
  `result.isError`). A result has no `turnId` and joins the turn of the call it
  answers; one whose call was never seen is dropped, as is any record missing
  those fields. Each turn sends one assistant message: its text, then each call
  as a `running` tool part with `tool_input` and each result as a `completed`
  or `error` tool part with the output's text, paired by `tool_id` and
  `tool_name`, in wire order. In a recorded 2.8.1 session (wire protocol 1.5)
  every `tool.call` carried `turnId`, every output was a string, a cancelled
  tool's result was the error `Interrupted by user`, and the plugin's MCP tools
  were named `mcp__plugin-openviking-memory_openviking__<tool>`; calls and
  results go out as `mcp__openviking__<tool>`, the name the server recognises.
  A compaction appends `context.apply_compaction` and leaves earlier records in
  place; it can fire in the middle of a turn, so PreCompact leaves the last
  assistant message to the next capture, which sends it whole.
- A Stop sends the new turns, then reads the session and commits once its
  `pending_tokens` reach `commitTokenThreshold`, unless `serverAutoCommit`
  leaves that commit to OpenViking. Interrupt, SessionEnd and PreCompact
  (unless `autoCommitOnCompact` is off) commit whatever is uncommitted without
  reading the session.
- Stop, PreCompact, and SessionEnd capture may detach, and their OpenViking requests share a 25-second budget. Interrupt stays
  synchronous and all of its OpenViking requests share a two-second budget; a
  commit that no longer fits is queued and replayed later.
- Kimi fires Stop only after a completed turn and Interrupt only after a
  cancelled one; `kimi -p` fires neither Stop nor SessionEnd, so a prompt-mode
  run is not captured.
- The native plugin manifest owns hooks and MCP. Installation does not edit
  legacy `config.toml` or `mcp.json` files.

Shared configuration, recall, capture filtering, retry queues, HTTP, and MCP
transport remain in `memory-plugin-shared`; `kimicode.mjs` owns only Kimi's
event mapping, input/output shape, transcript decoding, and commit policy.

Versioned host references:

- [native plugin manifest and manager](https://github.com/MoonshotAI/kimi-code/tree/%40moonshot-ai/kimi-code%400.43.1/packages/agent-core-v2/src/app/plugin)
- [hook event models](https://github.com/MoonshotAI/kimi-code/tree/%40moonshot-ai/kimi-code%400.43.1/packages/agent-core-v2/src/features/externalHooks)
