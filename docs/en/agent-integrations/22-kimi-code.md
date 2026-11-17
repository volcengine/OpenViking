# Kimi Code Memory Plugin, Standalone Distribution (Community)

> **Community implementation.** This plugin is community-maintained, not a
> first-party OpenViking integration. The legacy `agent-hook-plugin` Kimi
> adapter has the same positioning.

[Kimi Code](https://www.kimi.com/coding) is Moonshot AI's coding agent. This
plugin gives it long-term memory through OpenViking: it captures what the model
saw, recalls relevant memories when you ask, and exposes the server's own tools
over MCP.

This page documents only the standalone plugin in
`examples/kimi-code-memory-plugin`. The separate
`agent-hook-plugin/hosts/kimicode` integration targets Kimi Code CLI 0.43.x and
is not covered or changed here.

It is a Kimi Code plugin distributed as a self-contained directory or ZIP
installed with `/plugins install`, with its hooks and MCP server declared inline
in `kimi.plugin.json`. There is no separate installer, and the plugin never
writes the host's registry itself.

Both this plugin and the legacy `agent-hook-plugin` adapter are Kimi *native*
plugins: Kimi loads either from its managed plugin directory. The difference is
distribution, not plugin form. The legacy manifest points at
`./agent-integrations/kimicode/...`, which exists only after OpenViking's
installer assembles it, so its source directory is not directly installable.
This one is self-contained: the checked-in directory is the runnable artifact.

The hook payloads, MCP tool naming and wire protocol were checked against the
embedded source of **Kimi Code 2.6.2** — POSIX detached spawn and process-group
signals, Windows `taskkill /T`, the `Stop`/`PreCompact`/`SessionEnd` event shapes,
fork `base.{branch,line}` / `legacyUndoLine`, and session `environmentId`. That is
source evidence, not a runtime test. On this Windows machine the plugin was
verified with a real OpenViking 0.5.5 server, including hook-driven capture and
recall; POSIX, remote/Web and a real turn-sliced fork remain unverified, as do
older and newer hosts. See the plugin README for the detailed evidence.

## What it does

| Event | Behaviour | Output |
| --- | --- | --- |
| `SessionStart` | replays queued writes; sweeps sessions that ended earlier | none |
| `UserPromptSubmit` | delivers the previous turn, then injects profile and recall | one text block |
| `PreToolUse` | refuses `viking://` paths given to local file tools | deny JSON or none |
| `Stop` | delivers the closed turn and commits past the threshold | none |
| `PreCompact` | delivers, then commits regardless of thresholds | none |
| `SessionEnd` | writes an end marker, delivers, commits | none |

**Capture** reads each agent's `wire.jsonl` incrementally and projects the
*active branch*: the wire is a tree, and a branch switch only keeps the base
prefix plus the new segment. User text becomes `user_query` only when the host
would have shown it as a user message, so task prompts, injections and hook
results do not become memories. Assistant text, tool calls and their results are
kept in the order the model saw them; thinking, media and compaction summaries
are not.

**Delivery and handover are distinct.** Only a server-confirmed message counts as
delivered. A durable queued message may advance the handover cursor, but does not
count as delivered and holds back newer sends. A permanently rejected capture
blocks the commit until the configuration changes or a human accepts the loss —
archiving a transcript with a hole is worse than waiting.

**Recall** injects your profile and the server's skill catalogue on the first
successful prompt, then recall on later ones. `noAutoInject`, `autoRecall` and
`minQueryLength` are independent switches, and none affects explicit MCP calls.
Recall fails open: no server, a timeout or no results produces no text at all,
never an error in your prompt.

## Install

Install a local checkout:

```text
/plugins install <path to examples/kimi-code-memory-plugin>
```

Once a release is published on the main repository, its ZIP installs with one
command (tags follow the repository's `<package>@<version>` convention):

```text
/plugins install https://github.com/volcengine/OpenViking/releases/download/kimi-code-memory-plugin@<version>/kimi-code-memory-plugin-<version>.zip
```

Then `/plugins info openviking-memory` to confirm. The hooks run `node` from
`PATH`, so Node.js 22.19+ must be on the `PATH` the host inherits — a desktop
launch does not read your shell profile.

The plugin shares its configuration chain with the other OpenViking plugins and
resolves it against the **session's** directory, so a workspace
`.openviking/config.json` applies to the project you are in, not to the plugin's
own folder. Credentials always come from `ovcli.conf` or the environment, never
from a workspace file.

## Tools and Skills

The MCP server is `openviking`, shown as
`mcp__plugin-openviking-memory_openviking__<tool>`. The tool list is whatever the
server reports; the plugin keeps no copy of it.

| Skill | Purpose |
| --- | --- |
| `openviking-memory` | retrieve, remember, forget |
| `openviking-skills` | find, read, create and install server Skills |
| `openviking-rule-search` | retrieve applicable Rules |
| `ov-experience-memory` | retrieve and apply Experience |

Kimi Code displays them as `openviking-memory:<skill>`. The Skills name the tools
the server actually exposes and explain the fallback when a server version lacks
`rule_search` or `add_skill`.

## Diagnostics

```bash
node ./scripts/doctor.mjs             # full report
node ./scripts/doctor.mjs --offline   # no network requests
```

The doctor checks the manifest and hook registration, the host's plugin registry,
leftover hand-written hooks, resolved configuration and credentials, every
session's blocked state, the offline queue, and the server's health. Blocked
sessions come with the exact `scripts/recover.mjs` command that clears them.

## Importing existing history

Hooks remember only from the moment they first see a session. To import the
sessions that predate the plugin:

```bash
node ./scripts/backfill.mjs --dry-run        # plan only; no request, no state
node ./scripts/backfill.mjs                  # every eligible session
node ./scripts/backfill.mjs --session <id>   # one Kimi session
```

A session with no plugin state is read from its first record and committed at
the end; one that already has state is only caught up from its cursor, because
the server has no message dedup. The command honors `autoCapture` and
`bypassSession`, checks `/health` before writing, and skips non-local
environments, legacy wires and a fork's inherited prefix. A live session's open
tail is left for the next hook or a later run, and its commit stays blocked
until that turn ends.

## Upgrade and removal

Re-run the same command to upgrade; the host replaces the managed copy under the
same plugin name:

```text
/plugins install <path to examples/kimi-code-memory-plugin>
```

To remove:

```text
/plugins remove openviking-memory
```

`remove` drops the host's registration and leaves the plugin's files in place.
Memories already written to OpenViking, the pending queue and other plugins are
untouched. Delete the managed directory yourself if you also want the files
gone.

## Limits

- **At-least-once delivery.** A send that succeeded while the state write failed
  is replayed; stable wire ids suppress most duplicates, and the id set is
  bounded.
- **No rollback of delivered turns.** Undo, clear and branch switches only affect
  what has not been delivered yet.
- **No automatic history import.** A session the plugin has not seen starts at
  the current end of its wire; importing the past needs an explicit
  `scripts/backfill.mjs` run.
- **No media.** Only text is captured; media files are never read.
- **Local sessions only.** A non-local `environmentId` is skipped with a
  diagnosis, because its wire is not on this machine.
- **Crash tails need the sweep.** An open turn is closed only with an end marker,
  the session lock and a wire that is quiet across two reads.

The plugin's own [README](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/README.md)
covers the full setting table, the recovery commands and the development
workflow.
