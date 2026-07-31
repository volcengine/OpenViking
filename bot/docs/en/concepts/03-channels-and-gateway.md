# Channels, Gateway, and Operations

Channels adapt different chat platforms into unified messages. The Gateway assembles Channels, AgentLoop, HTTP APIs, scheduled tasks, and observability into a long-running service.

## Supported Channels

| Type | Connection | Main capabilities |
|------|------------|-------------------|
| `feishu` | Feishu events/long connection | DMs, groups, topics, mention rules, media |
| `slack` | Socket Mode | DM and group policies |
| `telegram` | Bot API | Text, media, and audio transcription |
| `discord` | Gateway | Text and media |
| `whatsapp` | Node.js WebSocket bridge | WhatsApp message forwarding |
| `dingtalk` | Stream SDK | Message ingestion and replies |
| `qq` | QQ Bot API | Message ingestion and replies |
| `email` | IMAP + SMTP | Inbox polling and automatic replies |
| `mochat` | Socket.IO / Watch API | Session watching, mentions, and delayed replies |
| `openapi` / `bot_api` | FastAPI | HTTP Chat API and SSE |

Interactive CLI sessions use ChatChannel, while one-shot commands use SingleTurnChannel. Both use the same unified message model.

## Channel Responsibilities

BaseChannel and platform-specific implementations jointly handle:

1. starting and stopping connections and reporting runtime status;
2. enforcing sender policies such as `allow_from`;
3. extracting text, images, attachments, and reply metadata;
4. building SessionKey and InboundMessage;
5. converting OutboundMessage into a platform-native reply;
6. displaying processing state or reactions when supported by the platform.

Platform differences remain inside individual Channels, so AgentLoop does not depend on Feishu, Slack, or other platform SDKs.

ChannelManager creates every enabled instance from `bot.channels` and distinguishes multiple Bots of the same type using `type__channel_id`. It consumes the MessageBus outbound queue and routes replies back to the originating channel using SessionKey.

## Gateway Runtime

`vikingbot gateway` starts the following components in one asyncio process:

```text
FastAPI / Uvicorn
  + OpenAPIChannel
  + Configured chat Channels
  + MessageBus
  + AgentLoop
  + CronService
  + HeartbeatService
```

The default listen address is `127.0.0.1:18790`. If `gateway.host` is not localhost, `bot.gateway.token` is required or the Gateway refuses to start.

## Bot HTTP API

The Bot API is available under `/bot/v1`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/bot/v1/health` | Bot health status |
| POST | `/bot/v1/chat` | Synchronous chat |
| POST | `/bot/v1/chat/stream` | SSE streaming chat |
| POST | `/bot/v1/chat/channel` | Call a specific Bot Channel |
| POST | `/bot/v1/chat/channel/stream` | Stream a call to a specific Bot Channel |
| POST | `/bot/v1/feedback` | Submit user feedback |
| GET/POST | `/bot/v1/sessions` | List or create API Sessions |
| GET/DELETE | `/bot/v1/sessions/{id}` | Retrieve or delete an API Session |

ChatRequest supports a session ID, additional context, reply control, request-level disabled tools, and a channel ID. ChatResponse returns a response ID, final text, intermediate events, relevant memories, and token usage.

SSE emits reasoning, content delta, tool call, tool result, iteration, and final response events.

## OpenViking API Proxy

When an OpenViking Server is configured, the Gateway also provides:

| Path | Purpose |
|------|---------|
| `/health` | Aggregate Gateway and OpenViking upstream status |
| `/api/v1/{path}` | Proxy OpenViking APIs |

The proxy removes hop-by-hop headers, forwards validated identity headers, and preserves the upstream response status. See [OpenViking Integration](./04-openviking-integration.md) for the complete connection and identity flow.

## Access Control

The Gateway enforces several security boundaries:

1. non-local listeners require `X-Gateway-Token`;
2. loopback requests may use the local development boundary;
3. an OpenViking API Key is checked through upstream `/health` to validate identity and the effective auth mode;
4. only a trusted OpenViking Server proxy may supply `openviking_connection`;
5. API Sessions are isolated by combining the authenticated principal scope with the external session ID.

Ordinary request fields such as `user_id`, account ID, or connection data cannot prove an OpenViking identity by themselves.

## Feedback and Outcome Evaluation

Every final reply receives a `response_id`. Clients may submit thumbs up, thumbs down, or a numeric rating, along with an optional reason and text. The Gateway stores the feedback, calculates feedback delay, and emits a `feedback_submitted` event.

When the user continues the conversation, Outcome Evaluator can infer whether the previous reply succeeded from subsequent behavior and emit a `response_outcome_evaluated` event. `vikingbot feedback-stats` aggregates local Sessions to report feedback coverage, ratings, outcome status, tool usage, and latency.

## Langfuse and Logging

With `bot.langfuse.enabled=true`, model calls, token usage, latency, tool events, and outcome metadata are sent to Langfuse. A Langfuse initialization failure does not block the main Bot flow.

Runtime logs use Loguru. Gateway's `--verbose` flag enables more detailed logs. Analytics-only events are separated from normal replies and are not accidentally sent to chat platforms.

## Implementation Locations

| Area | Path |
|------|------|
| Channel base and management | `vikingbot/channels/base.py`, `manager.py` |
| Platform adapters | `vikingbot/channels/*.py` |
| Gateway/OpenAPI | `vikingbot/channels/openapi.py` |
| API models | `vikingbot/channels/openapi_models.py` |
| Runtime assembly | `vikingbot/cli/commands.py` |
| Feedback and outcomes | `vikingbot/observability/` |
| Langfuse | `vikingbot/integrations/langfuse.py` |

## Related Documentation

- [VikingBot Architecture](./01-architecture.md)
- [Agent Capabilities](./02-agent-capabilities.md)
- [OpenViking Integration](./04-openviking-integration.md)
- [Channel Configuration](./05-channel.md)
