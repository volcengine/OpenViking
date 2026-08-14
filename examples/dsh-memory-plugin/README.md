# OpenViking Memory for DeepSeek Harness

An installable DeepSeek Harness bundle that adds OpenViking auto-recall, session capture, `viking://` URI protection, and model-invocable memory tools.

## Requirements

- `@deepseek-ai/dsh` `0.1.0-rc.6`
- Node.js `^22.19.0` or `>=24`
- A reachable OpenViking server

The bundle has no runtime npm dependencies. Its tool and message structures
come from the DSH constructors (`defineTool` from `@deepseek-ai/dsh-tools`,
`createUserMessage` from `@deepseek-ai/dsh-llm`) behind exact-pinned
peerDependencies that DSH exposes through its profile fallback at boot time, so
the definitions track DSH's contracts instead of hand-built object shapes.
It is tested against `0.1.0-rc.6`; install that exact DSH release because
prerelease package dist-tags are not synchronized across the package family.

## Why injection uses pre-step user messages, not the system prompt

Recall and profile context enter through the `agent/pre-step` waterfall as
durable, source-attributed user messages (`source: { kind: 'plugin', … }`).
They are deliberately **not** added to the system prompt: a DSH preset whose
persona declares `complete: true` (the stock `minimal` preset does) restores
that persona as the sole prompt section after assembly, silently discarding
every other contribution — a system-prompt-based memory plugin loses its
context under such presets with no error. Pre-step injection also makes each
injection a session event that replays, is visible to compaction, and never
reaches `request/header`.

## Install

From the OpenViking repository:

```bash
dsh plugin --profile default add ./examples/dsh-memory-plugin
```

Or install the published package:

```bash
dsh plugin --profile default add @openviking/dsh-memory-plugin
```

Confirm that the profile includes the bundle:

```bash
dsh --profile default --dump-config
```

The package patch mounts the runtime inside a Cordis group with an isolated `openvikingMemory` service.

## Configuration

OpenViking credentials use the same resolution order as the other memory plugins:

1. `OPENVIKING_*` environment variables
2. `~/.openviking/ovcli.conf`
3. `~/.openviking/ov.conf`

Common environment variables:

| Variable | Purpose |
| --- | --- |
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | OpenViking server endpoint |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | Bearer credential |
| `OPENVIKING_ACCOUNT` | Trusted-mode account |
| `OPENVIKING_USER` | Trusted-mode user |
| `OPENVIKING_PEER_ID` | Explicit actor peer |
| `OPENVIKING_WORKSPACE_PEER` | Derive a peer from each DSH session workspace by default |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` for cross-workspace recall or `actor` for isolation |

The patch can also carry plugin config:

```yaml
- insert:
    - id: openviking-memory
      name: '@deepseek-ai/cordis-plugin-group'
      group: true
      isolate:
        openvikingMemory: true
      config:
        - id: openviking-memory-runtime
          name: '@openviking/dsh-memory-plugin'
          config:
            endpoint: http://127.0.0.1:1933
            recallTokenBudget: 2000
            scoreThreshold: 0.35
            captureToolResults: false
            commitTokenThreshold: 20000
```

## Behavior

- `agent/session-start` injects the OpenViking profile and available-memory index through `agent.inject()`.
- `agent/pre-step` retrieves with the current step input and appends a durable plugin message to that same step.
- `session/event` captures user, assistant, and optionally tool-result messages without scraping a transcript.
- `turn/end` checks the OpenViking pending-token threshold and commits when required.
- Failed writes enter the shared OpenViking pending queue for replay at the next session start.
- `tools/pre-execute` blocks DSH filesystem and shell tools from treating `viking://` URIs as local paths.

Each DSH session maps to `dsh-<session-id>` in OpenViking. Workspace-derived actor peers are resolved per session and sent on every session-specific request.

## Tools

The bundle registers:

- `viking_search`
- `viking_read`
- `viking_browse`
- `viking_remember`
- `viking_forget`
- `viking_add_resource`
- `viking_archive_expand`

`viking_forget` performs permanent deletion. The calling model should use it only when the user explicitly requests deletion.

## Testing

```bash
npm ci          # installs the exact-pinned dsh devDependencies the tests exercise
npm test        # node --test *.test.mjs — runs in the repo's PR workflow
```

`live-recall.test.mjs` is an opt-in end-to-end gate against a real OpenViking
server: it stores a sentinel memory through a session commit, waits for
extraction, and asserts recall returns that sentinel — the property no stub
can certify. Enable it with `OPENVIKING_E2E=1` plus the normal credential
chain; it skips otherwise (including in CI until a server secret exists).
