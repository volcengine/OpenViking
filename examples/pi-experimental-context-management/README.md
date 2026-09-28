# pi × OpenViking — experimental context management

> **EXPERIMENTAL.** A demo extension, not a supported product. It changes what
> the model sees on every provider request. Do not point it at a session you
> cannot afford to lose, and do not load it next to the `openviking` extension.

## What this is

A fork of [`examples/pi-coding-agent-extension`](../pi-coding-agent-extension)
in which takeover mode is replaced by **agent-managed context windows** — a
pi-side take on Codex's `features.context_management.experimental_mode`.

The model decides when the current window is done and calls `new_context`. The
extension archives the conversation to OpenViking, waits for the server to write
a Working Memory of it, and then cuts everything up to that tool call out of the
next provider request, putting a frozen window header in front: the model's own
handoff notes, the reason it gave, the user's last request and the Working
Memory. Nothing is summarized by an LLM, and nothing is deleted — closed windows
are readable again through the `history` tool.

Everything else the upstream extension does — turn sync, recall before every
prompt, profile injection, the `viking_*` tools — still works.

Full design, exact prompt texts, failure matrix and the e2e gate:
[CONTEXT-WINDOW.md](./CONTEXT-WINDOW.md).

## Install

```bash
pi install /abs/path/to/examples/pi-experimental-context-management
```

**Disable the `openviking` extension first.** Both register the same `viking_*`
tools and both sync the same OpenViking session, so loading them together
duplicates every tool and gives one session two writers. Set
`"enabled": false` in `~/.pi/agent/extensions/openviking/config.json`, or drop
it from `settings.json`'s `packages`. As a backstop, this extension looks for a
`viking_search` registered from a directory other than its own — at startup and
again on every event boundary, because the other extension registers its tools
only after an awaited health check — and stands down with a warning when it
finds one. Once a window is open the stand-down is skipped (dropping the cut
would hand the model back a conversation it was told is archived); it warns
instead.

Credentials resolve exactly as upstream: `OPENVIKING_*` environment variables →
`~/.openviking/ovcli.conf` → `ov.conf`. No new config file.

## Quick config

`config.json` keeps the upstream fields (minus `captureMode` — capture here is
always faithful — and minus `commitTokenThreshold` / `resumeContextBudget`,
which nothing in this fork reads any more) and adds one nested `contextWindow`
block:

```json
{
  "captureToolResults": true,
  "contextWindow": {
    "resetDeadlineMs": 60000,
    "softPercent": 70,
    "hardPercent": 85,
    "idleGapMinutes": 30,
    "statusEveryTurn": true
  }
}
```

`resetDeadlineMs` bounds the blocking `new_context` call end to end (sync,
barrier, commit, and the wait for the server-side Working Memory — measured at
25–55s on the reference server, so 60s is the default). `softPercent` /
`hardPercent` are the one-shot reminder thresholds, clamped at runtime under
pi's own auto-compaction line. `statusEveryTurn` emits the one-line context
status after every user prompt. Two environment variables override the block
for one run: `OPENVIKING_CONTEXT_STATUS_EVERY_TURN` and
`OPENVIKING_CONTEXT_RESET_DEADLINE_MS`.

The full table, with ranges and clamping rules, is in
[CONTEXT-WINDOW.md §6](./CONTEXT-WINDOW.md#6-configuration-contextwindow-block).

## Tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `new_context` | `reason`, `notes`, `next_steps?` | Archives the current window to OpenViking and opens a fresh one. Blocks until the archive exists. Call it alone |
| `history` | `action`, `window?`, `item?`, `query?`, `offset?`, `limit?` | Reads closed windows: `list_windows`, `list_items`, `read_item`, `search_contents` |
| `get_context_remaining` | — | Tokens left, window age, turns, idle gaps, archive readiness, one advice line |
| `viking_search` | `query`, `scope?`, `limit?` | Semantic search over the OpenViking knowledge base |
| `viking_read` | `uri`, `level` | Read a `viking://` URI at `abstract` / `overview` / `full` detail |
| `viking_browse` | `action`, `uri?` | `list` or `stat` the store like a filesystem |
| `viking_remember` | `content`, `category?` | Store a fact in the session for memory extraction |
| `viking_forget` | `uri?`, `query?` | Delete a memory by URI or strongest match |
| `viking_add_resource` | `url` | Ingest an HTTP URL into OpenViking |

`viking_archive_expand` is gone — it read `viking://session/{id}`, a namespace
the server does not serve, and `history` replaces it.

## What differs from the upstream extension

- **Agent-managed windows instead of takeover**: no `takeover.ts`, no boundary
  state machine, no threshold-driven commit inside `syncBranch`. Archives are
  produced only by `new_context` and by the pi-compaction fallback.
- **Tool output reaches the archive.** `normalizeRole` recognises pi's
  `toolResult` messages and captures them as `[tool-result <toolName>] …`,
  bounded by `captureToolMaxChars`; `captureToolResults` defaults to true. The
  upstream adapter dropped them, which would have made `history` a false
  promise.
- **Capture is always faithful** — the archive is the only way back to a closed
  window, so nothing is filtered but plugin chatter and slash commands.
- **No recall injection ledger**: replaying historical recall blocks needed
  `SessionManager.buildContextEntries()`, which pi 0.80.3 does not expose.
  `injectRecall` prepends this turn's block to the newest user message only and
  skips any message that already carries an `<openviking-context` block — which
  is also what keeps it away from the frozen window header.
- **`sync.flushForTakeover()` is now `sync.flushBarrier({budgetMs})`**, so a
  reset can cap how long it waits for the pending queue to drain. The sync
  manager also counts messages OpenViking rejected for good; a window opened
  while that count is non-zero says so in its header, because those messages are
  missing from the archive that `history` reads.

## Layout

| Path | What it is |
| --- | --- |
| `index.ts` | Extension entry: event handlers, coexistence guard, static guidance, `/viking` command |
| `client.ts` | OpenViking HTTP client, including the archive read/list/grep helpers |
| `sync.ts` | Session sync, disk pending queue, `flushBarrier`, `commit` |
| `recall.ts` | Per-prompt recall search and injection |
| `config.ts`, `config.json` | Config, the `contextWindow` block, credential resolution |
| `tools.ts` | Six `viking_*` tools plus `new_context` / `history` / `get_context_remaining` |
| `context-window.ts` | Adapter binding the core to pi, the client and the sync manager |
| `lib/context-window-core.mjs` | Pure, harness-agnostic window state machine |
| `lib/text-budget.mjs` | Token estimation / truncation helpers |
| `lib/capture-adapter.mjs` | Branch entries → OpenViking message payloads |
| `lib/uri-guard-adapter.mjs` | Blocks builtin file tools on `viking://` URIs |
| `lib/pi-settings.mjs` | Reads pi's `compaction.reserveTokens` |
| `shared/` | Copied from `examples/memory-plugin-shared/lib` — do not edit (see "Not wired into the repo" below) |
| `scripts/` | The manual e2e gate (`e2e-window.mjs` with the `e2e-window.sh` wrapper), the `e2e-probe.ts` payload recorder and the setup wizard |
| `demo-evidence/` | A redacted snapshot of two real runs: transcripts, provider payloads either side of each reset, and the OpenViking archives pulled back off the server |

## Tests

```bash
node --test examples/pi-experimental-context-management/tests/*.test.mjs
```

These are not in the CI glob yet (see below), so run them locally. The
end-to-end gate is manual and needs live credentials — see
[CONTEXT-WINDOW.md §13](./CONTEXT-WINDOW.md#13-end-to-end-gate).

## Not wired into the repo

This directory is deliberately self-contained: nothing outside it changes. Two
repo-level hooks are therefore missing, and a maintainer who wants to promote
this out of experimental status has to add them:

- **CI does not run these tests.** Add
  `examples/pi-experimental-context-management/tests/*.test.mjs` to the
  `plugin-tests` glob list in `.github/workflows/pr.yml`.
- **`shared/` is not refreshed by the sync script.** Add a `TARGETS` entry for
  `examples/pi-experimental-context-management/shared` (with `PI_SHARED_FILES`)
  in `examples/memory-plugin-shared/sync.mjs`. Until then the copies here are
  frozen at the commit that added them and will drift as the shared modules
  change.

The root `.gitignore` ignores every `lib/` directory, so this extension carries
its own `.gitignore` with `!lib/` instead of adding another exception there.

## Links

- [CONTEXT-WINDOW.md](./CONTEXT-WINDOW.md) — design, prompts, failure matrix
- [examples/pi-coding-agent-extension](../pi-coding-agent-extension) — the
  stable extension this forks
- [docs/en/agent-integrations/11-pi.md](../../docs/en/agent-integrations/11-pi.md)
  · [docs/zh/agent-integrations/11-pi.md](../../docs/zh/agent-integrations/11-pi.md)
  — the pi integration docs for the stable extension; they do not cover this one
