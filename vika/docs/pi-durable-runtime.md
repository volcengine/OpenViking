# Durable runtime

`durable` runs the agent on [`@earendil-works/pi-durable`](https://github.com/earendil-works/pi).
The daemon supervises one host child process,
`packages/daemon/src/durable-host.mjs`, which owns a pi-durable Harness over
SQLite at `<dataDir>/durable/session.sqlite`. Conversation entries, model turns
and tool calls are committed to that store before they are shown, so a host that
dies mid-turn resumes the run from the last checkpoint when it starts again.

The daemon keeps every guarantee it owns for the other runtimes: the inbound
ledger, outbox idempotency, decisions and the cognition journal stay in
`vika.sqlite3`. The durable store only holds the conversation.

## Choosing it

- `vika setup` lists **Durable (built in)**; it can be chosen once at least one
  model has credentials (see below). It ships with the release like Pi; there is
  no CLI to install or log in to.
- `/runtime durable` (also `pi-durable`, `pidurable`) switches a running
  instance.
- In `config.json` the agent's `model` is `durable`, or `durable:<provider>/<model>`
  to pick the model of a fresh session.

## Models

The host loads models the way Pi does, through pi-coding-agent's `ModelRuntime`,
offline at startup:

- pi-ai's built-in providers, using their API key environment variables;
- Pi's agent directory (`~/.pi/agent`, or `PI_CODING_AGENT_DIR`): custom
  providers in `models.json` (for example an OpenAI-compatible Volcengine Ark
  endpoint), credentials saved by `/login` in `auth.json`, and the default model
  in `settings.json`.

A model that works in `vika pi` therefore works in durable without a second
configuration. Run `vika pi` and `/login`, or edit `~/.pi/agent/models.json`, to
add one.

Provider keys from the environment reach the host only through the daemon's
secret fields: `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`,
`GOOGLE_GENERATIVE_AI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY` and `ARK_API_KEY`. Set them
in the operator environment or `~/.vika/secrets.env`, never in `config.json`.
`ARK_API_KEY` is kept for the same reason as for Pi: a `models.json` provider may
name it as `"apiKey": "$ARK_API_KEY"`.

The model of a turn is, first match wins:

1. the model saved by `/model` for this runtime;
2. the `durable:<provider>/<model>` selector in `config.json`, on a fresh session;
3. `VIKA_DURABLE_MODEL`;
4. Pi's default model, when it has credentials;
5. the first available chat model.

`/model` lists the available chat models and `/effort` the levels of the current
model (`off` … `max` for reasoning models, `off` otherwise; default from
`VIKA_DURABLE_EFFORT`, else `medium`). Both apply from the next turn.

## Tools

- **Core agent tools.** The host reads `GET /v1/tools` from `<dataDir>/actions.sock`
  (`PERSONAL_AGENT_SOCKET` / `PERSONAL_AGENT_AUTHORITY`) and registers each one;
  a call goes to `POST /v1/call`, through the same authority, freshness and
  outbox checks as the MCP adapter of the other runtimes. Image media from a
  tool result (`get_message_media`) reaches the model as an image block, up to
  10 MB. A host without the socket refuses to start.
- **Coding tools.** pi-durable's `CodingTools` (`bash`, `read`, `write`, `edit`)
  run in the instance workspace.

The child environment is sanitized like the other local runtimes: `LARK_*`,
`FEISHU_*`, `LARKSUITE_*`, `VIKA_*`, `ARK_*` (except `ARK_API_KEY`) and
`VOLCENGINE_*` are removed, a Feishu instance's `LARKSUITE_CLI_PROFILE` is fixed
to its profile, and the configured `lark-cli` and Slack CLI directories come first on
`PATH`.

## Sessions and recovery

Vika mints the logical session id that every runtime is told. The durable
conversation id is the runtime's native handle: the daemon stores it in the
`global_session_native` meta row together with the session it belongs to, and
reads it back only while that session is still the global one.

- **Start.** `hello` names the conversation to open. When the stored handle is
  stale the host creates a new conversation and reports its id in `ready`; the
  daemon adopts it. A fresh session gets the OpenViking context prefix on its
  first input, as for the other runtimes.
- **Input.** Input during a run is submitted with `whenBusy: "steer"`, so it
  joins the running turn.
- **Crash.** The daemon restarts the host with backoff (1 s doubling to 30 s).
  The new host calls `harness.resume()` and reports an unfinished run with
  `turn_start`. If the host fails to start three times while a turn is active,
  that turn is failed instead of staying active forever.
- **New session.** `/new` sends `rotate`. The new conversation id stays inside
  the prepared plan until the daemon's session CAS commits it; if preparation
  fails or is cancelled, `open` restores the previous conversation.

## Protocol (version 2)

JSON lines over the host's stdin and stdout; stderr carries JSON log lines.
A command may carry an `id`; its reply is `{ "id", "ok": true, ...result }` or
`{ "id", "ok": false, "error" }`. A failing command without an `id` is reported
as a `fatal` event.

| Command | Fields | Result |
|---|---|---|
| `hello` | `dataDir`, `workspace`, `policy`, `sessionKey`, `nativeSessionId`, `model`, `effort` | `ready` event, then the reply |
| `submit` | `text`, `requestId` | `submissionId` |
| `abort` | | |
| `configure` | `model`, `effort` | |
| `models` | | `models: [{ id, efforts, isDefault? }]` |
| `rotate` | `nextSessionKey` | `conversationId` |
| `open` | `sessionKey`, `nativeSessionId`, `create?` | `conversationId`, `busy` |
| `stop` | | reply, then a `stopped` event; the host exits |

| Event | Fields |
|---|---|
| `ready` | `protocol`, `version` (pi-durable), `conversationId`, `model`, `resolvedModel`, `effort` |
| `turn_start` | `turn` |
| `note` | `text` (assistant text) |
| `thinking` | `text` |
| `tool_start` | `toolId`, `toolName`, `input` |
| `tool_end` | `toolId`, `toolName`, `isError`, `output` |
| `usage` | `inputTokens`, `cachedInputTokens`, `outputTokens`, `costUsd` |
| `settled` | `turn`, `status` (`done` or `failed`), `error?` |
| `fatal` | `error` |
| `stopped` | |

`sessionKey` and `nextSessionKey` match `[A-Za-z0-9._:-]{1,128}`; a
`nativeSessionId` is an integer or null.

## Versions and checks

- `@earendil-works/pi-durable` is pinned exactly in `packages/daemon/package.json`.
  The host reports the version it loaded in `ready`, and the daemon stops a host
  whose version differs.
- `packages/daemon/scripts/check-durable.mjs` (`npm run check:durable`) starts
  the host the way the daemon does, with a fake provider and no network, and
  waits for `ready`. The release build runs it.
- `VIKA_DURABLE_HOST` points the daemon at another host script, for development.
  `DURABLE_FAUX`, `DURABLE_FAUX_RESPONSES` and `DURABLE_FAUX_DELAY_MS` drive a
  scripted fake model in tests.
