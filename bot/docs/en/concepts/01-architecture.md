# VikingBot Architecture

VikingBot is the multi-channel AI Agent runtime in the OpenViking repository. It converts input from the CLI, chat platforms, and HTTP APIs into a unified message format. The Agent then builds context, runs model inference, invokes tools, and delivers the result back to the originating channel.

## System Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│ CLI │ Feishu │ Slack │ Telegram │ Discord │ Email │ HTTP API   │
└─────────────────────────────┬────────────────────────────────────┘
                              │ InboundMessage
                    ┌─────────▼─────────┐
                    │    MessageBus     │
                    │ inbound/outbound │
                    │      queues      │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │     AgentLoop     │
                    │ context→model→tool│
                    └───┬─────┬─────┬───┘
                        │     │     │
                ┌───────▼┐ ┌──▼───┐ ┌▼───────────────┐
                │Session │ │Tools │ │  OpenViking    │
                │history │ │Skills│ │resources/memory│
                └────────┘ └──┬───┘ └────────────────┘
                              │
                        ┌─────▼─────┐
                        │  Sandbox  │
                        │files/cmds │
                        └───────────┘
```

## Core Modules

| Module | Responsibility | Main implementation |
|--------|----------------|---------------------|
| **Channels** | Adapt platform events, media, permissions, and reply formats | `vikingbot/channels/` |
| **MessageBus** | Decouple message ingestion, Agent execution, and result delivery | `vikingbot/bus/` |
| **AgentLoop** | Drive multi-step model and tool iterations | `vikingbot/agent/loop.py` |
| **Context** | Assemble identity, workspace, Skills, memories, and history | `vikingbot/agent/context.py` |
| **Providers** | Normalize model, streaming, and tool-call protocols | `vikingbot/providers/` |
| **Tools & Sandbox** | Provide executable capabilities and their runtime boundaries | `vikingbot/agent/tools/`, `vikingbot/sandbox/` |
| **Session** | Cache and persist Bot conversation state | `vikingbot/session/` |
| **OpenViking** | Provide resources, long-term memory, experiences, and session consolidation | `vikingbot/openviking_mount/`, `vikingbot/hooks/` |
| **Gateway** | Run the multi-channel service and expose HTTP APIs | `vikingbot/channels/openapi.py` |

## Main Flow for One Message

```text
Platform event
  → Channel authenticates the caller and extracts text/media
  → Build SessionKey and InboundMessage
  → MessageBus inbound queue
  → AgentLoop loads the Session
  → ContextBuilder builds the system prompt and history
  → Provider calls the model
      ├─ Text response: produce the final reply
      └─ Tool call: ToolRegistry executes it, then calls the model again
  → Save the local Session and synchronize OpenViking according to policy
  → MessageBus outbound queue
  → Channel delivers the reply
```

During processing, the Agent also emits intermediate events such as reasoning, content delta, tool call, tool result, and iteration. OpenAPI can return them as an SSE stream, while ordinary channels may deliver only the final reply.

## Messages and Session Identity

All channels share two message structures:

| Structure | Main content |
|-----------|--------------|
| `InboundMessage` | Sender, text, media, SessionKey, channel metadata, and OpenViking identity |
| `OutboundMessage` | Text, event type, reply target, media, token usage, and response ID |

`SessionKey` consists of `type + channel_id + chat_id`. It is the isolation key for a conversation and also determines outbound routing and workspace selection. When persisted, it is encoded as `type__channel_id__chat_id`.

## Agent Execution Loop

Each model call may return normal text or one or more tool calls. AgentLoop:

1. computes the tools visible to the current channel and request;
2. calls the Provider and publishes streaming events;
3. sends the tool name and arguments to ToolRegistry for validation;
4. injects the SessionKey, sender, sandbox, and OpenViking connection into ToolContext;
5. appends the tool result to the current message context;
6. calls the model again until it produces a final answer or reaches `max_tool_iterations`.

Queue mode uses a single AgentLoop consumer to process inbound messages in order. CLI, Cron, and Heartbeat can invoke the same execution logic directly through `process_direct()`.

## Model Adaptation

The Provider layer gives AgentLoop a unified `chat()` and `chat_stream()` interface and normalizes:

- final text and reasoning;
- streaming text, reasoning, and tool argument deltas;
- Provider-specific system message and thinking parameters;
- `prompt_tokens`, `completion_tokens`, and `total_tokens`.

LiteLLMProvider adapts general model services. OpenViking VLM uses VLMProviderAdapter to implement the same interface. Models are read from the root-level `vlm` configuration by default, while `bot.agents` can override Bot-specific parameters.

## Local Session

A local Session stores user, assistant, tool, and analytics events. It is persisted as JSONL under the Bot data directory's `sessions/` folder. SessionManager maintains an in-memory cache and protects writes with a per-SessionKey asynchronous lock.

Local Sessions and OpenViking Sessions have different responsibilities: the former keeps Bot conversations operational, while the latter handles archival, summaries, long-term memory, and experience extraction. See [OpenViking Integration](./04-openviking-integration.md).

## Runtime Entry Points

| Entry point | Purpose | Components started |
|-------------|---------|--------------------|
| `vikingbot chat` / `ov chat` | One-shot or interactive chat | CLI Channel, AgentLoop, Session, Sandbox |
| `vikingbot gateway` | Long-running service | Gateway, configured Channels, AgentLoop, Cron, Heartbeat, OpenAPI |

VikingBot and OpenViking share `ov.conf`. Bot configuration lives under `bot`, and `OPENVIKING_CONFIG_FILE` can select another file.

## Runtime Modes

| Mode | Behavior |
|------|----------|
| `normal` | Run the normal model, tool, and memory flow |
| `readonly` | Do not register OpenViking resource-write tools or perform active memory consolidation |
| `debug` | Record incoming user messages without running model inference or replying |

## Related Documentation

- [Agent Capabilities](./02-agent-capabilities.md)
- [Channels, Gateway, and Operations](./03-channels-and-gateway.md)
- [OpenViking Integration](./04-openviking-integration.md)
