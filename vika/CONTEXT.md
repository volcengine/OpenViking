# Personal Agent Core

A single-user local agent pipe with provider Connector processes. These terms name the seams this repo actually owns.

**Connector**:
A child process that owns one provider's receive loop, credentials, provider state, and typed actions. It speaks the versioned NDJSON protocol in `docs/connector-protocol.md`.
_Avoid_: putting provider polling, webhooks, or long connections in Core

**Core**:
The process that owns the shared AgentRuntime session, OpenViking session, inbox pipeline, and the outbound ActionService with its outbox.
_Avoid_: naming Core after one provider (a Lark or Weixin bot) when discussing provider-neutral behavior

## Language

**Conversation policy**:
The assembled text a runtime is allowed to see: base markdown plus harness and OpenViking sections.
_Avoid_: system prompt file, kernel, instructions blob

**Harness**:
The inbound pipe from a canonical Connector event to `AgentRuntime.submit`.
All events share the `connector_events` ledger; conversation/message/reply route
are optional. Provider payload decoding belongs to the Connector. Runtime input
and OpenViking context preserve source instance, event kind/id, and subject.
_Avoid_: daemon class, orchestrator, coordinator

**OpenViking connection**:
The instance's own OV user (`ovConfig`), which Vika owns for the agent: the daemon mirrors the session journal into OV with each line's speaker in `from=` and no peer, and before a turn shows the owner's and the sender's preferences (`ov-recall.mjs`); the model reads and writes through the Core's `openviking_*` tools. Extraction keeps profile, entities and events; preferences are written only by `openviking_remember`, into the directory the Core keeps for the sender (`ov-person.mjs`). The runtimes' own OpenViking plugins stay switched off (`ov-isolation.mjs`, `docs/openviking-ownership.md`).
_Avoid_: OV client, memory service

**Meeting plugin**:
A standalone inbound source for Lark meetings the bot is invited to. Meeting events stay in their own store, and the model reads them with `read_meeting_inbox`.
_Avoid_: meeting stack

**Agent tools**:
The one tool surface every runtime uses for messaging and chat reads, served by
the Core's ActionService over `<dataDir>/actions.sock`. The MCP adapter (Codex,
TraeX, Claude, Pi, OpenCode, Antigravity) only relays; every outbound action is checked there and
recorded in the single `outbox` table before a Connector executes it.
_Avoid_: Lark tools, sidecar, delivery CLI per connector
