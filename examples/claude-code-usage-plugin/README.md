# OV-Usage

English / [中文](./README_CN.md)

OV-Usage adds a card under each Claude Code answer that lists the OpenViking sources the answer used. That covers what auto-recall added to the prompt, plus what Claude searched for and read on its own.

The card is collapsed by default, so it takes one line:

```
OV · 14 sources · 6 past events · 5 work memories · 2 team docs · 1 skill · 1 read in full  [Expand]
```

Expand it to see every source and Claude's own lookups:

```
OV · 14 sources · … · 1 read in full  [Collapse]
  ◷ 10/3 发版检查清单补充 · found by Claude · read in full
  ⚙ openviking-release · auto-recalled
  ▤ lark-daemon · auto-recalled
  …
Claude's own lookups
  ⌕ Searched “lark-daemon release” · 6 results
  ▤ Read 10/3 发版检查清单补充
```

- Groups: ★ preferences, ◷ past events, ◆ work memories (notes, lessons, your agents' memories), ▤ team docs, ⚙ skills.
- **read in full**: Claude opened the file with `read`, either through the MCP tool or `ov read`. A search hit doesn't count.
- An answer that used nothing from OpenViking gets no card.

Expand or collapse a card with its `[Expand]` / `[Collapse]` button. You can also expand or collapse every card with `/openviking-usage expand` or `/openviking-usage collapse`.

Labels are in English only.

## Requirements

- Claude Code 2.1.286 or newer. The plugin uses Claude Code's plugin hooks (`hooks.json` with `modules`), which are still early access. Without them, the plugin installs but shows nothing.
- The OpenViking memory plugin (`openviking-memory@openviking`).

## Install

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install ov-usage@openviking
```

If you already added the marketplace, run `claude plugin marketplace update openviking` first. Then start a new session.

## What it reads and stores

- **Auto-recall**: the `<openviking-context>` block that openviking-memory's `UserPromptSubmit` hook adds to the prompt. OV-Usage reads the `uri` and `score` of each `<memory>` item, or the `viking://` URI on each line of the digest form. It reads no openviking-memory files or config. If openviking-memory changes this block's format, auto-recalled items stop showing.
- **Claude's lookups**: OpenViking MCP tool calls (`read`, `search`, `find`, `grep`, `glob`, `list`, `tree`) and `ov` CLI calls through Bash (`read`, `cat`, `abstract`, `overview`, `find`, `search`, `grep`, `glob`, `ls`, `tree`). Writes and other commands are ignored.
- **Stored** in Claude Code's plugin store, for the last 20 sessions: each answer's source URIs and scores, search queries (redacted, first 80 characters), and the transcript row id each card belongs to. That keeps cards in place after `claude --continue`. Shell commands and prompt text are not stored.

The plugin makes no network calls and writes nothing to OpenViking. The card isn't sent to Claude, so it costs no tokens.

## Development

- `hooks/register.tsx`: the hooks and the card.
- `hooks/sources.ts`: pure parsing: recall blocks, URIs in tool output, which tool calls are lookups, and what an answer consulted.
- `tests/`: `claude plugin test examples/claude-code-usage-plugin`. These run on Claude Code's plugin test runner, not in the repository's Node CI.
- `claude plugin validate examples/claude-code-usage-plugin` checks the plugin. After `claude --plugin-dir examples/claude-code-usage-plugin` has generated `.claude-plugin/types/`, `tsc -p examples/claude-code-usage-plugin` type-checks it.
