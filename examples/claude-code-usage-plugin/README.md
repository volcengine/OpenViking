# OV-Usage

English / [中文](./README_CN.md)

Shows which OpenViking sources each answer in Claude Code consulted: what OpenViking recalled automatically, and what Claude searched for and read on its own. It also shows totals for the session and since install.

The details can appear in a sidebar, or as cards in the conversation itself.

## Sidebar or conversation

**Sidebar** (the default): a pane beside the conversation, opened when a session starts.

```
All sessions
  [▸ Settings]  Auto-recall on · auto-capture on
  Memories auto-added to 38 prompts
  12 memory updates saved from 5 conversations
  Startup: 167 memories indexed · your profile: Product manager

This conversation
  This session
    9 OpenViking sources consulted this session
    1 saved to OpenViking
  Earlier answers
    [#5] 15:02 “what about access controls?” ✓2
    [#4] 14:51 “what is openviking?”

  This answer  [Details]
  “what is the status of the permission model”

  ✓ 2 OpenViking sources consulted · 2 work memories · 1 read in full
    ◆ Work note · found by Claude
      permission model design
    ◆ Work note · found by Claude
      permission model open questions

  Claude's own lookups
    ⌕ Searched “permission model” · 2 results
    ▤ Read permission model open questions
```

**In conversation**: no pane. A card under each answer shows what that answer drew on, and a card above the conversation's first prompt shows the totals for all sessions and this session. An answer that had nothing recalled and looked nothing up gets no card.

```
⏺ …Claude's answer…
  ╭──────────────────────────────────────────────────────────────╮
  │ OpenViking · this answer                                     │
  │ ✓ 12 OpenViking sources consulted · 1 preference · 1 past event
  │   · 6 work memories · 3 team docs · 1 skill · 2 read in full │
  │   ◆ permission model design · found by Claude                │
  │   ◆ permission model open questions · found by Claude        │
  │   ▤ project rules ×5 · found by Claude                       │
  │   …                                                          │
  │   [+2 more]                                                  │
  │ Claude's own lookups                                         │
  │   ⌕ Searched “permission model” · 8 results                  │
  │   ▤ Read permission model open questions                     │
  ╰──────────────────────────────────────────────────────────────╯
```

Cards show the 10 most relevant sources. Files Claude opened rank first, then search results and recalled memories by their score. The rest, including less relevant recalled items, wait behind `[+N more]`. Copies of the same document (one name, several URIs) share a row marked `×N`.

**Card detail** has two levels: **Low** (the default) shows titles only, and **High** adds each source's full `viking://` URI. Set it with `/openviking-usage card low|high` or **Card detail** under Settings.

Switch views with `/openviking-usage show conversation` or `/openviking-usage show sidebar`, or with **Show in** under Settings in the sidebar. The choice is remembered across sessions. Either way, `/openviking-usage` opens the sidebar whenever you want it.

**Status line**, in both views:

```
OV · this answer ✓12 (★1 ◷1 ◆6 ▤3 ⚙1) · 45 this session · 1 saved
```

This answer's sources by group, then unique sources consulted this session, then successful saves to OpenViking. Before the first answer it reads `OV · startup: 167 memories indexed`. When an answer drew on nothing: `OV · no sources for this answer`.

None of this is sent to Claude, so none of it adds tokens.

## What each part means

- **✓ N OpenViking sources consulted**: every memory and document OpenViking put in front of Claude for this answer. That's what auto-recall added for the prompt plus what Claude's own reads and searches returned. It's a record of what was retrieved, not a guess from the answer's wording, so it works whether or not the answer names its sources. Sources from earlier answers aren't counted again.
- **The groups after it** (only those above 0 are shown):

  | Group | Icon | What it covers |
  |---|---|---|
  | Preferences | ★ | Your profile, identity and preferences |
  | Past events | ◷ | Dated events from your history |
  | Work memories | ◆ ⇄ | People, projects, lessons, cases, and memories from your agents |
  | Team docs | ▤ | Documents, code and pages under `viking://resources` |
  | Skills | ⚙ | Personal or shared Skills |
  | Read in full | | Sources Claude opened in full, not just saw as a search hit or recalled summary |

- **Labels**: "auto-recalled" (with its relevance score) or "found by Claude". Less relevant recalled items count too, but stay folded: behind `[Show]` in the sidebar, behind `[+N more]` in cards.
- **Claude's own lookups**: each OpenViking search, read or save Claude made while answering, with the query and the number of results or the files opened. Only real `ov` CLI calls count from the shell, not commands that merely mention a `viking://` path.
- **[Details]** (sidebar): recall diagnostics. That covers why recall came back empty, items already in context, skipped empty documents, how many memories were about you, and how many Claude read in full.
- **Earlier answers** (sidebar): any earlier answer in this conversation. `✓3` means 3 OpenViking sources were consulted for it.
- **All sessions**: how many prompts auto-recall added memories to, and how many memory updates OpenViking saved, from how many conversations. OV-Usage counts these itself from the day you install it, because openviking-memory keeps no history of its own. `/openviking-usage clear` resets them.
- **Startup**: how many memory files the startup context indexed, and your role if your profile has a line labeled as one (职业, 角色, Role, Job title, Occupation and similar).
- **Settings** (sidebar): read-only. Shows auto-recall and its threshold, auto-capture and how much conversation it collects before saving to memory (openviking-memory's `commitTokenThreshold`, 20,000 tokens by default), the startup profile, the server, and whether an API key is set (never the key itself). This is also where you choose the layout, card detail and language.

If auto-recall is off in your OpenViking config (`"autoRecall": false` in `~/.openviking/ovcli.conf`), OV-Usage hides everything about it, including under [details]: recalled files, recall counts and recall timing. Turning it back on brings them back.

Memories are labeled by kind: ★ your preferences, ◷ your history, ◆ your notes and lessons, ⇄ from your agents, ⚙ skills, ▤ docs. Labels are in English or Chinese (`/openviking-usage lang en|zh|system`).

## Requirements

- Claude Code 2.1.286 or newer. OV-Usage is built on Claude Code's plugin hooks (`hooks.json` with `modules`), which are still early access. On older versions, or where those hooks aren't available, the plugin installs but shows nothing.
- The OpenViking memory plugin (`openviking-memory@openviking`), installed through Claude Code and signed in. OV-Usage finds it through Claude Code's plugin registry. The "Claude's own lookups" section needs its MCP tools to be enabled.
- `node` on your PATH. OV-Usage uses it to run openviking-memory's config loader and read your settings. If that fails, Settings says why and names the openviking-memory version it tried.

OV-Usage is optional. The OpenViking installer doesn't install it, and it changes nothing about how openviking-memory recalls or captures, except for memories you mute yourself (see below).

## Install

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install ov-usage@openviking
```

If you already added that marketplace, run `claude plugin marketplace update openviking` first.

Then start a new Claude Code session, or quit and reopen the desktop app. In a terminal at least 144 columns wide, the sidebar opens on its own. In a narrower terminal, run `/openviking-usage` to open it, or switch to cards with `/openviking-usage show conversation`.

## Commands

| Command | What it does |
|---|---|
| `/openviking-usage` | Open the pane |
| `/openviking-usage history` | Show all earlier answers, or only the last three |
| `/openviking-usage answer 12` | Open answer #12 |
| `/openviking-usage latest` | Go back to the newest answer |
| `/openviking-usage details` | Show score, layer, tokens and URI under each memory, and the tool and URIs under each lookup |
| `/openviking-usage weak` | Show or hide less relevant items |
| `/openviking-usage settings` | Open or close Settings |
| `/openviking-usage show conversation` | Show details as cards in the conversation, and stop opening the sidebar on its own |
| `/openviking-usage card low` | Cards show source titles only (the default) |
| `/openviking-usage card high` | Cards also show each source's `viking://` URI |
| `/openviking-usage show sidebar` | Show details in the sidebar (the default) |
| `/openviking-usage unmute` | Clear this session's muted memories |
| `/openviking-usage clear` | Delete all saved answer history, session data and the All sessions totals |
| `/openviking-usage lang en` | Set the pane language: `en`, `zh` or `system` |

The buttons in the pane do the same things. If a click does nothing (this can happen in a pane restored after a restart), use the command instead.

## What it reads and stores

Nothing leaves your machine. The plugin makes no network calls.

**Reads**
- openviking-memory's own files: the startup context it injected (`~/.openviking/last_inject.md`), and its snapshots of the latest recall and capture (`last-recall.json` and `last-capture.json` under `$OPENVIKING_HOME/state`, by default `~/.openviking/state`). A snapshot counts only if it belongs to the current session, and a recall snapshot only if it was written after you sent the prompt.
- Your OpenViking settings, through the config loader of the openviking-memory install Claude Code actually runs, as listed in `~/.claude/plugins/installed_plugins.json`. An install scoped to the current project wins over the user-wide one. The API key is never read into the pane. The pane only shows whether one is set.
- The results of OpenViking tool calls Claude makes (search, read, write). It only collects the `viking://` URIs they name.

**Stores**, in Claude Code's plugin store (`~/.claude/plugins/store/`), for your last 30 sessions:
- The first 120 characters of each prompt, with keys and tokens redacted.
- For each answer: the titles, summaries and URIs of the memories OpenViking recalled, and for each OpenViking lookup Claude made, the tool name, the query (first 80 characters, redacted) and the URIs. Shell commands are never stored. For `ov` commands run in a shell, only the `viking://` URIs they name are kept.
- Which memories Claude opened, and which ones you muted.
- For cards: the transcript row ids of the first prompt and of each answer's last text block, plus short fingerprints (hashes) of the end of their text at a few lengths, used to find the right row. The words themselves are never stored.
- For searches: the relevance score each result came back with, used to rank sources in cards.

**Stores**, across sessions, for the **All sessions** totals: how many prompts recall added memories to, and the commit count of each session's last capture, keyed by session id. openviking-memory keeps no history, so these totals start when you install ov-usage.

Run `/openviking-usage clear` to delete all of it. Muting a memory hides it from Claude for the current session. It doesn't change anything in OpenViking.

## Update

```bash
claude plugin marketplace update openviking
claude plugin update ov-usage@openviking
```

## Uninstall

```bash
claude plugin uninstall ov-usage@openviking
```

Uninstalling leaves openviking-memory and everything in OpenViking unchanged. To delete what OV-Usage saved first, run `/openviking-usage clear`.

## Limitations

- OV-Usage reads files openviking-memory writes for its own use (`last_inject.md`, `last-recall.json`, `last-capture.json`) and runs its `scripts/config.mjs`. These aren't a published interface. If a future openviking-memory version changes them, the affected numbers disappear or Settings shows an error. Answers and memory are not affected.
- **All sessions** totals start when you install OV-Usage. openviking-memory keeps no history to count from.
- Muting removes a memory from the recall context openviking-memory adds to your prompt, for the current session only. It doesn't change or delete anything in OpenViking.
- The pane, the cards and the status line need Claude Code's plugin hooks. There's no fallback for hosts without them.

## Development

- `hooks/register.tsx`: the hooks, and everything that uses Claude Code's engine handle `$`: session state, reading openviking-memory's files, the `/openviking-usage` command, the sidebar and the cards. Claude Code requires these in the module `hooks.json` names, since it doesn't follow `$` into imported files.
- `hooks/parse.ts`: pure parsing of recall blocks, the startup context, `viking://` URIs and redaction, plus the text fingerprints that match an answer to its row in the transcript.
- `hooks/sources.ts`: which tool calls are OpenViking lookups or writes (`classifyToolCall`), what a source is (its kind, group and relevance), and what one answer consulted.
- `hooks/openviking.ts`: the formats of openviking-memory's settings and registry record, and the totals across sessions.
- `hooks/strings.ts`: English and Chinese labels.
- `types/index.d.ts`: the shape of the values the plugin keeps.
- `tests/`: run them with `claude plugin test examples/claude-code-usage-plugin`. Check the plugin with `claude plugin validate examples/claude-code-usage-plugin`.

To try changes without installing, start Claude Code with `claude --plugin-dir examples/claude-code-usage-plugin`. Loading the plugin also makes Claude Code write its API types to `.claude-plugin/types/`. That folder is generated and ignored by Git. After it appears, `tsc -p examples/claude-code-usage-plugin` type-checks the plugin.

These tests run on Claude Code's plugin test runner, not `node --test`, so the repository's Node CI doesn't run them. From this folder, `npm test` runs them, `npm run check` validates and type-checks the plugin, and `npm run format` applies the code style (Prettier defaults, 100 columns).
