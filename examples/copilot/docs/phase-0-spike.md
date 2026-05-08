# Phase 0 spike: Copilot extensibility assumptions

Issue: <https://github.com/jwayong/OpenViking/issues/2>

Date: 2026-05-08

## Findings

| Question | Finding | Scope impact |
| --- | --- | --- |
| Does the LM-tool description heuristic reliably trigger `openviking_recall` in VS Code Copilot Chat? | The extension can expose `openviking_recall` as a VS Code Language Model Tool with a tuned description, and the repo now carries a 30-prompt seed fixture that records the selected description's relevant/irrelevant rates. This is the right automated gate for description drift, but real auto-invocation still depends on Copilot's model/tool planner and should be manually smoke-tested during Marketplace release. | Phase 2 recall is viable. Treat auto-invocation as model-discretion, not a deterministic guarantee. |
| Does `vscode.chat.onDidReceiveChatResponse` (or current equivalent) fire for default `@workspace` chat? | No stable public VS Code 1.99 API exposes a global default-chat response event to third-party extensions. Public docs cover extension-owned `vscode.chat.createChatParticipant(...)`; they do not expose `onDidReceiveChatResponse` for native/default `@workspace` turns. | Issue #25's fallback was correct: VS Code capture remains `@openviking` participant-scoped until VS Code ships a public turn-level observer. |
| Does VS Code's MCP loader honour custom HTTP headers? | Yes for the current documented MCP configuration shape: HTTP/SSE servers support a `headers` object and inputs such as `${input:api-token}` for sensitive values. This repo's VS Code target still prefers the extension's own HTTP client/config path instead of relying on VS Code's MCP loader for OpenViking auth. | Direct extension config remains the safer primary path. If a future VS Code MCP-only install is added, use `servers.<name>.headers` and input variables. |
| What is the exact Copilot CLI MCP config-file path and schema today? | GitHub's docs place user-level config at `${COPILOT_HOME:-$HOME/.copilot}/mcp-config.json`. The schema uses top-level `mcpServers`, and a local stdio server should include `type`, `command`, `args`, optional `env`, and `tools`. Project-level `.mcp.json` / `.github/mcp.json` may also be loaded, but the installer should target user-level `mcp-config.json`. | The setup helper should default to `~/.copilot/mcp-config.json`, not platform-specific VS Code-style paths. |
| Does the Copilot CLI have any in-process hook API? | There is no stable public plugin/in-process turn hook equivalent to Claude Code's `Stop` hook in the docs reviewed. GitHub CLI docs do expose user/repo hook script directories, but those are CLI customization hooks, not a documented programmatic turn transcript stream for npm plugins. | Phase 3 CLI capture remains MCP-tool based, with the shell wrapper only forcing a final commit for captures the model already recorded. |
| What is the canonical session-id field the CLI exposes per invocation? | Copilot CLI stores session state under `~/.copilot/session-state/` and interactive `/session` shows a session ID, but the docs do not expose a stable environment variable or public per-invocation field for MCP servers. | The current wrapper-generated `OPENVIKING_CLI_SESSION_ID` is the safest deterministic session source. Without it, the CLI plugin should keep its current fallback behavior. |

## Follow-up changes made from the spike

- Corrected the setup helper's Copilot CLI MCP default path to `${COPILOT_HOME:-$HOME/.copilot}/mcp-config.json`.
- Corrected the setup helper's MCP merge schema to include `type: "local"` and `tools: ["*"]`.
- Updated docs/help text from generic `mcp.json` wording to `mcp-config.json` where the user-level Copilot CLI config is meant.

## Sources

- GitHub Docs — Copilot CLI configuration directory: <https://docs.github.com/copilot/reference/copilot-cli-reference/cli-config-dir-reference>
- GitHub Docs — adding MCP servers for Copilot CLI: <https://docs.github.com/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers>
- VS Code Docs — MCP configuration reference: <https://code.visualstudio.com/docs/copilot/reference/mcp-configuration>
- VS Code Docs — Chat Participant API: <https://code.visualstudio.com/api/extension-guides/chat>
- Existing seed gate: `examples/copilot/packages/shared/src/__tests__/tool-description.test.ts`
