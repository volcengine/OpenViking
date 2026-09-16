# OpenClaw Plugin

Add long-term memory to [OpenClaw](https://github.com/openclaw/openclaw). After installation, OpenClaw automatically remembers important facts from conversations and recalls relevant context before every reply.

Source: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## Prerequisites

| Component | Required Version |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.5.27 |

The plugin connects to a running OpenViking server — see the [Deployment Guide](../guides/03-deployment.md) if you need one.

<details>
<summary><b>Upgrading from the legacy <code>memory-openviking</code> plugin?</b></summary>

The old plugin is not compatible. Run the cleanup script first:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

</details>

## Install

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --json
openclaw gateway restart
```

The `setup` wizard writes configuration and activates the plugin. After install, start a conversation — OpenClaw will begin remembering and recalling automatically.

<details>
<summary><b>Alternative: install via <code>ov-install</code></b></summary>

If ClawHub is unavailable:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install --base-url http://your-server:1933
```

Key parameters:

| Parameter | Meaning |
| --- | --- |
| `--workdir PATH` | OpenClaw data directory (default `~/.openclaw`) |
| `--plugin-version=VER` | Plugin version: npm version, dist-tag, or Git ref |
| `--base-url URL` | OpenViking server URL |
| `--api-key KEY` | OpenViking API key |
| `--peer-role ROLE` | Memory scope: `none`, `assistant`, or `sender` (`person` is a legacy alias) |
| `--uninstall` | Uninstall the plugin |

Full parameter list in the [install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md).

</details>

## Choose the Memory Scope

`peer_role` decides whether long-term memory is shared at the OpenViking user level or attributed to a concrete peer:

| Value | Memory layout | Use case |
| --- | --- | --- |
| `none` (default) | Shared memory at `viking://user/<user_id>/memories/...`; no peer-specific memory subtree is used | General-purpose setup where all conversations for this OpenViking user share user-level memory |
| `assistant` | Assistant-attributed peer memory at `viking://user/<user_id>/peers/<assistant_id>/memories/...` | **Human as OpenViking user**: separate the peer memories of assistants such as `main` and `research` |
| `sender` | Sender-attributed peer memory at `viking://user/<user_id>/peers/<sender_id>/memories/...` | **Agent as OpenViking user**: separate the peer memories of senders such as `customer-42` and `customer-99` |

For example:

```bash
# Alice is the OpenViking user; separate memories by OpenClaw assistant.
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --peer-role assistant --json

# support-agent is the OpenViking user; separate memories by human sender.
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --peer-role sender --json
```

New configuration should use `sender`; existing `peer_role=person` configurations remain compatible and are treated as `sender`. OpenViking initializes the managed `peers/` container for every user, so `none` means that no concrete `peers/<peer_id>/memories` subtree is used. Actor-peer recall includes shared user memory plus the current peer memory, and changing the scope does not move existing memories.

## How assemble builds context

The plugin occupies OpenClaw's `contextEngine` slot. It handles session history, long-term memory recall, and the pending user input separately; `assemble()` returns context for the current model request. It does not persist assembled summaries or recalled context to the OpenClaw session transcript, nor does it append messages to the OV session through this call. The host may update the current turn's in-memory messages with the result; that is separate from writing persistent conversation history.

Main assemble prepares history at the start of each new turn. The plugin identifies this call by the presence of at least one of `prompt`, `availableTools`, or `citationsMode`.

### Main assemble: history and pending input

The main branch calls `getSessionContext(tokenBudget)` and builds:

```text
summaryMessage = { role: "user", content: "[Session History Summary]\n" + latest_archive_overview }
messages = [summaryMessage] + OV active messages
systemPromptAddition = Session Context Guide (when archives exist) + recalled context (when available)
```

`latest_archive_overview` is the summary text returned by the server; `[Session History Summary]` is the literal heading prepended by the plugin. This synthetic user message is inserted only when the overview is nonempty. Active messages provide recent uncompressed conversation. The host adds the pending `prompt` to the turn. The plugin uses it for recall without appending a second copy to the returned history. Recalled context belongs to this request and is not directly captured as new conversation in OV.

OV generates the overview through its server-side working-memory flow; the plugin reads the result. The server budgets active messages first and omits the overview if the remaining space is insufficient. `pre_archive_abstracts` is currently an empty array, so the response is not a complete archive index. Use `ov_archive_search` for original details.

The plugin reserves output headroom, subtracts estimated guide and summary tokens, trims active messages from the oldest end, and normalizes provider message formats such as tool calls and results. It does not hard-truncate the summary to the calculated archive budget, so the partitions are not strict per-layer limits. A new recall block is omitted if it would push the total estimate above `tokenBudget`.

The history branch falls back to host messages when OV has no data, has fewer messages than the host without an archive, produces an empty converted history, or fails to load. Main-branch recall can still run with a valid `prompt` and `autoRecall` enabled even when history passes through. Missing recall results or recall failures do not stop the conversation.

### transformContext

`transformContext` runs before each LLM call, whether the last message is a user message or a tool response. The best use of this hook in the OV integration has not yet been determined.

### Capture and compaction

- `ingest()` / `ingestBatch()` do not write messages. Regular capture uses `afterTurn`. For stable OpenClaw versions from 2026.9.3 onward, the plugin also captures completed turns delivered through `commitTurn`; older hosts and standalone runners use `afterTurn`. If the host version cannot be classified, `commitTurn` rejects acknowledgement to avoid confirming uncaptured data.
- Capture removes injected context, converts text and tool messages, and writes them to the OV session. When `pending_tokens >= tokenBudget × commitTokenThresholdRatio`, it starts an asynchronous session commit. The default ratio is `0.5`; retention defaults to the most recent `10` messages, with `turn_budget` available as another policy. `pending_tokens` counts messages eligible for archiving under the server retention policy, not the entire model request.
- `ownsCompaction: true` assigns compaction to the plugin. Normal `compact()` commits the OV session with `wait=true` and retention `0`, then reads the overview as its summary. The next main assemble rebuilds history from that summary and active messages. Bypassed sessions attempt to delegate compaction to the host; if the host bridge is unavailable, the plugin returns a skip result.

A **session commit** archives conversation and processes memory. It is separate from a [snapshot commit](../guides/15-snapshot.md), which versions resource files.

## Verify

```bash
openclaw openviking status
```

This checks plugin registration, server connectivity, and version compatibility in one command. Append `--json` for machine-readable output.

<details>
<summary><b>Manual verification</b></summary>

Check the plugin owns the `contextEngine` slot:

```bash
openclaw config get plugins.slots.contextEngine
# expect: openviking
```

For an end-to-end pipeline test:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

See [HEALTHCHECK.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/health_check_tools/HEALTHCHECK.md) for details.

</details>

<details>
<summary><b>Configuration</b></summary>

Plugin config lives under `plugins.entries.openviking.config`. Setup usually writes this for you.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking server endpoint |
| `apiKey` | empty | OpenViking API key |
| `peer_role` | `none` | `none`, `assistant`, or `sender`; legacy `person` is accepted as `sender` |
| `peer_prefix` | empty | Optional prefix for assistant peer identity when `peer_role=assistant` |
| `autoRecallTimeoutMs` | `5000` | Outer timeout (ms) for the whole auto-recall flow; increase for slow local embedding hardware (clamped 1000–300000) |

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
```

</details>

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

## See also

- [Capability Reference](./16-capability-reference.md)
- [Full install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — every install path and parameter
- [Plugin design notes](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — architecture, identity & routing, hook lifecycle
- [Agent operator guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — for agents driving installation on behalf of a user
