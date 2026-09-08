# Copilot Extension Surface — Research Notes

Researched 2026-07-28 against official GitHub Copilot, VSCode, and MCP documentation. All facts below are sourced; this file is the bibliography and evidence backing [DESIGN.md](./DESIGN.md).

## Bottom line

GitHub Copilot's primary extension mechanism in 2026 is the **Model Context Protocol (MCP)**. It is supported across all major Copilot surfaces. Copilot does **not** expose Claude-Code-style lifecycle hooks (`SessionStart` / `UserPromptSubmit` / `PreToolUse` / `Stop`); the only "policy" injection point is the open-standard **Agent Skills** mechanism, which is a markdown skill the model reads rather than an event a plugin can subscribe to.

## Surface 1 — VSCode Copilot Chat / agent mode

- **Status**: GA.
- **Config file**: `.vscode/mcp.json` in the workspace (committed to source control to share with the team) OR a user-profile `mcp.json` opened via the `MCP: Open User Configuration` command.
- **Top-level key**: `"servers"` (NOT `"mcpServers"` — this is the VSCode-specific quirk; the CLI and repo-level surfaces do use `mcpServers`).
- **HTTP entry shape**: `{ "type": "http", "url": "...", "headers": {...} }`. Stdio entry shape: `{ "command": "...", "args": [...] }` (no `type` field).
- **Tool filtering**: handled in the VSCode UI (Configure Tools), not in the JSON.
- **Auto-discovery**: with `chat.mcp.discovery.enabled`, VSCode can reuse Claude Desktop's MCP config automatically.
- **Source**: <https://code.visualstudio.com/docs/agent-customization/mcp-servers> (titled "Add and manage MCP servers in VS Code", fetched 2026-07-28).

## Surface 2 — `gh copilot` CLI

- **Status**: GA. The built-in GitHub MCP server is available with no config; third-party servers are user-configured.
- **Config file**: `~/.copilot/mcp-config.json`.
- **Top-level key**: `"mcpServers"`.
- **HTTP entry shape**: `{ "type": "http", "url": "...", "headers": {...}, "tools": ["*"] | ["tool1", ...] }`. Local/stdio entry shape: `{ "type": "local" | "stdio", "command": "...", "args": [...], "env": {...}, "tools": [...] }`.
- **CLI subcommands**: `copilot mcp add SERVER-NAME -- COMMAND [ARGS...]` (stdio) and `copilot mcp add --transport http SERVER-NAME URL` (HTTP), with `--env`, `--header`, `--tools`, `--timeout` options. Interactive: `/mcp add` and `/mcp search` (the latter experimental, hits the GitHub MCP Registry).
- **Source**: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers> (fetched 2026-07-28).

## Surface 3 — GitHub.com Copilot cloud agent + Copilot code review

- **Status**: **public preview** for agent skills + MCP servers with code review.
- **Config**: NOT a committed file. The repo admin pastes a JSON blob into the repo's Settings → Copilot → MCP servers page.
- **Top-level key**: `"mcpServers"`.
- **Required keys**: `type` (`"local" | "stdio" | "http" | "sse"`) and `tools` (string[]). The docs "strongly recommend" allowlisting specific read-only tools because the cloud agent invokes tools autonomously without approval.
- **Secrets**: MUST be referenced as `${COPILOT_MCP_*}` and configured as Agents secrets (Settings → Secrets and variables → Actions / Agents). Hard-coding a key in the pasted JSON would leak it.
- **Two caveats that affect OpenViking integration**:
  1. The cloud agent supports MCP **tools only**. MCP resources and prompts are ignored. (So OpenViking's resource-based context injection does not work here; the `recall` tool does.)
  2. The cloud agent does **not** support OAuth-protected remote MCP servers. Use API-key (`Bearer`) auth, which is what OpenViking uses by default.
- **Source**: <https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers> (fetched 2026-07-28).

## Surface 4 — GitHub Copilot app

- Reads MCP servers configured in the repository OR via Copilot CLI; additional MCP servers can be added in app settings. Same `mcpServers` shape.
- **Source**: <https://docs.github.com/en/copilot/how-tos/github-copilot-app/customize-github-copilot-app>.

## Surface 5 — JetBrains / Xcode / Eclipse / Cursor / Windsurf IDEs

- Broad support for local (stdio) MCP servers; remote (HTTP) MCP server support is growing, with OAuth or PAT auth. OpenViking uses API-key auth, which works wherever HTTP MCP is supported.
- **Source**: <https://docs.github.com/en/copilot/concepts/context/mcp#availability>.

## What Copilot does NOT have

- **No `SessionStart` / `Stop` / `UserPromptSubmit` / `PreToolUse` hook surface.** Searched all four official docs pages above and the Copilot Chat extensions documentation. The closest equivalent is "the model calls MCP tools automatically when relevant" — there is no event an external plugin can subscribe to that fires at turn boundaries.
- **No system-prompt / `CLAUDE.md`-equivalent file** that an external plugin can install to be loaded as a system instruction. The Agent Skills mechanism is the closest analogue, and it is loaded based on `description` matching rather than unconditionally.
- **No plugin marketplace/catalog** that this plugin could register in. The GitHub MCP Registry (public preview) lists MCP servers, not memory plugins.

## The closest equivalent to hooks: Agent Skills

- **Open standard**: <https://github.com/agentskills/agentskills>.
- **Discovery locations**: `.github/skills`, `.claude/skills`, `~/.copilot/skills`, `~/.agents/skills`. (Project-level skills are committed to the repo; personal skills live under the user's home.)
- **Format**: a directory containing a `SKILL.md` with YAML frontmatter (`name`, `description`, etc.) followed by markdown instructions. The `description` field controls when the skill is loaded into context.
- **Supported by**: Copilot cloud agent, Copilot code review, Copilot CLI, Copilot app, and VSCode Copilot Chat agent mode.
- **Source**: <https://docs.github.com/en/copilot/concepts/agents/about-agent-skills>.

This plugin ships an `openviking-memory` skill that supplies the recall/remember policy the missing hooks would have supplied. It is not a perfect substitute (the model may or may not call `recall` proactively depending on the strength of the `description`), but it is the only mechanism Copilot offers.

## Decision summary

| Path | Viable? | Shipped? | Why |
|---|---|---|---|
| Mirror ZCode/Cursor hook adapter | ❌ | no | Copilot has no hook events to subscribe to. A `hooks.json` would be inert. |
| MCP server wiring (3 surfaces) | ✅ GA / preview | yes | The official, documented extension path; works across VSCode / CLI / cloud. |
| Agent Skill (recall/remember policy) | ✅ GA | yes | Closest analogue to hook-supplied policy; only mechanism Copilot offers. |
| Stdio MCP proxy (optional) | ✅ | yes (opt-in) | For credential auto-resolution / local unauthenticated servers. Mirrors ZCode/Codex. |
| VSCode Chat `@participant` extension | ✅ possible | no (future) | Would need a compiled TypeScript VSCode extension with build/publish pipeline; documented as future work. |
| GitHub MCP Registry listing | ✅ preview | no (future) | Registry is for MCP servers generally; will list OpenViking's HTTP endpoint when feature is GA. |

## Caveats to flag to users

1. **Repo-level cloud agent / code review is public preview** — features may change.
2. **Enterprise MCP policy is disabled by default** for Copilot Business / Enterprise subscriptions where the org configured the policy. Copilot Free / Pro / Pro+ / Max are NOT affected.
3. **Auto-recall is best-effort**: it depends on the model deciding to call `recall`. The Agent Skill's job is to make that decision reliable, but it cannot guarantee hook-level determinism.
4. **No auto-capture**: there is no `Stop` event, so completed turns are NOT automatically written to OpenViking. The model must call `remember` (or the user must invoke it explicitly) to persist anything.

## Sources

- VSCode Copilot Chat MCP servers — <https://code.visualstudio.com/docs/agent-customization/mcp-servers>
- `gh copilot` CLI MCP servers — <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers>
- GitHub.com repo-level MCP servers (cloud agent + code review) — <https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers>
- "About Model Context Protocol (MCP)" (overview + availability matrix) — <https://docs.github.com/en/copilot/concepts/context/mcp>
- "About agent skills" — <https://docs.github.com/en/copilot/concepts/agents/about-agent-skills>
- Agent Skills open standard — <https://github.com/agentskills/agentskills>
- GitHub MCP Registry (public preview) — <https://github.com/mcp>
- MCP specification — <https://modelcontextprotocol.io/introduction>
