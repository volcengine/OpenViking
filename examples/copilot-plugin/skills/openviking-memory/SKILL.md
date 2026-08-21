---
name: openviking-memory
description: Long-term memory for GitHub Copilot via the OpenViking MCP server. Use when the user asks you to remember a preference, decision, or fact for later; when a new session/turn touches a topic that prior work might have covered; or when the user explicitly asks you to recall something. Triggers on phrases like "remember this", "as we discussed before", "what did I tell you about", "my usual preference", "across sessions", or any time long-lived context would help.
compatibility: OpenViking MCP server reachable at `https://your-openviking.example.com/mcp` (configured via MCP). Tools: `recall`, `search`, `find`, `remember`, `read`, `list`, `grep`, `glob`.
version: 0.1.0
last_updated: 2026-07-28
---

# OpenViking Memory for GitHub Copilot

GitHub Copilot does **not** expose Claude-Code-style lifecycle hooks (no `SessionStart` / `UserPromptSubmit` / `Stop`). That means memory cannot be auto-injected before each turn or auto-captured after each turn by an external plugin. Instead, **you (the model) decide when to call the OpenViking MCP tools**. This skill tells you when and how, so the experience approaches what hook-based agents get for free.

## When to call which tool

| Situation | Tool | Why |
|---|---|---|
| A new task starts, or the user references prior work / preferences | `recall` (preferred) or `search` | Pull relevant memories into context before answering |
| The user says "remember this", "for next time", "I prefer", "note that" | `remember` | Persist a durable memory |
| The user references a `viking://` path or asks to browse stored knowledge | `read`, `list`, `glob`, `grep` | Walk the OpenViking resource/filesystem namespace |
| You need a precise semantic lookup, not a digest | `find` or `search` | `recall` returns a rendered digest; `find`/`search` return ranked raw entries |

## Working rules

1. **Recall proactively at the start of substantive work.** Before answering a non-trivial question, call `recall` with the user's latest prompt as the query. This is the closest equivalent of the auto-recall hook in Claude Code / Codex / Cursor integrations, and it is what gives you cross-session memory. Skip it only for trivial or self-contained questions.
2. **Echo only what helps.** If `recall` returns relevant memories, use them silently — do not narrate "I recalled X from OpenViking" unless the user asks. If nothing relevant comes back, proceed normally; do not force a memory reference into the answer.
3. **Remember when the user commits to a fact or preference.** Durable facts worth saving: tooling preferences, project conventions, account or environment details, decisions with rationale, "from now on" instructions. Do not save trivia, transient state, or anything the user asks to forget.
4. **Never invent `viking://` paths.** If you need to read a stored resource, call `list` or `glob` first to discover real URIs, then `read`. Treat `viking://` paths returned by tools as opaque strings.
5. **Prefer one `recall` per turn, not many.** `recall` returns a token-budgeted digest of several memories at once. Batch your query rather than calling repeatedly.
6. **Do not echo full memory bodies back into `remember`.** `remember` is for new durable facts, not for re-persisting what `recall` already returned.

## Why this is a skill (and not a hook)

In Claude Code / Codex / Cursor / TRAE / ZCode / OpenCode, OpenViking's plugin fires on lifecycle hooks and injects/captures memory transparently. GitHub Copilot has no such hook surface (verified 2026-07-28; see `DESIGN.md` in this plugin). The MCP server itself is fully supported across VSCode Copilot Chat, JetBrains Copilot, `gh copilot` CLI, the GitHub Copilot app, and the GitHub.com Copilot cloud agent — so the *capabilities* are all there. This skill just supplies the *policy* that hooks would have supplied: when to recall, when to remember.

## Example

User: "Add the same retry wrapper we use on the other services."

- Call `recall` with query `"retry wrapper service pattern"`.
- If a memory returns the wrapper shape, use it.
- If the user then says "actually bump maxAttempts to 5 from now on", call `remember` with the new preference.
