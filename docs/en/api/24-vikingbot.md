# VikingBot API

When OpenViking Server starts with `--with-bot`, it proxies VikingBot's core interaction endpoints below `/bot/v1`. These endpoints return `503` when Bot is not enabled.

**Code entry points**:

- `openviking/server/routers/bot.py` - OpenViking Server proxy and identity forwarding
- `bot/vikingbot/channels/openapi.py` - VikingBot Gateway routes
- `bot/vikingbot/channels/openapi_models.py` - request, response, and SSE event models

## API Reference

### health()

Check whether the Bot Gateway is available.

**HTTP API**

```bash
curl http://localhost:1933/bot/v1/health
```

**Response Example**

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-07-24T09:00:00"
}
```

### chat()

Send a message and wait for the complete reply. Omit `session_id` to create a new session.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `message` | string | Yes | - | Non-empty user message |
| `session_id` | string | No | Generated | Existing session to continue |
| `context` | array | No | `null` | Additional messages containing `role` and `content` |
| `need_reply` | boolean | No | `true` | Whether the Bot should reply |
| `disabled_tools` | string[] | No | `[]` | Tool names disabled for this request |
| `channel_id` | string | No | `null` | Multi-channel routing identifier |

**HTTP API**

```bash
curl -X POST http://localhost:1933/bot/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message":"Summarize my project progress","session_id":"optional-session-id"}'
```

**CLI**

```bash
ov chat -m "Summarize my project progress"
```

**Response Example**

```json
{
  "session_id": "session-id",
  "response_id": "response-id",
  "message": "Here is the current project summary…",
  "events": null,
  "relevant_memories": null,
  "token_usage": {
    "prompt_tokens": 120,
    "completion_tokens": 42,
    "total_tokens": 162
  },
  "timestamp": "2026-07-24T09:00:00"
}
```

### chat_stream()

Return reasoning, tool calls, content deltas, and the final response as Server-Sent Events. The request fields are the same as `chat()`; the Gateway enables streaming automatically.

**HTTP API**

```bash
curl -N -X POST http://localhost:1933/bot/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message":"Analyze the current knowledge base"}'
```

**CLI**

```bash
ov chat -m "Analyze the current knowledge base"
```

**SSE Response Example**

Each message uses `data: <json>` format. The `X-VikingBot-Session-ID` response header contains the session ID.

```text
data: {"event":"reasoning_delta","data":"Inspecting the knowledge base…","timestamp":"2026-07-24T09:00:00"}

data: {"event":"content_delta","data":"The knowledge base contains","timestamp":"2026-07-24T09:00:01"}

data: {"event":"response","data":{"content":"The knowledge base contains…","response_id":"response-id"},"timestamp":"2026-07-24T09:00:02"}
```

`event` can be `reasoning`, `reasoning_delta`, `tool_call`, `tool_result`, `content_delta`, `iteration`, or `response`.

### feedback()

Submit explicit feedback for an existing assistant response.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | Yes | Session containing the target response |
| `response_id` | string | Yes | Target assistant response ID |
| `feedback_type` | string | Yes | `thumb_up`, `thumb_down`, or `rating` |
| `feedback_score` | number | Conditional | Required when `feedback_type=rating` |
| `feedback_reason` | string | No | Feedback reason label |
| `feedback_text` | string | No | Free-form feedback |
| `channel_id` | string | No | Multi-channel routing identifier |

**HTTP API**

```bash
curl -X POST http://localhost:1933/bot/v1/feedback \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "session_id":"session-id",
    "response_id":"response-id",
    "feedback_type":"thumb_up"
  }'
```

**Response Example**

```json
{
  "accepted": true,
  "response_id": "response-id",
  "session_id": "session-id",
  "feedback_type": "thumb_up",
  "feedback_delay_sec": 8.42,
  "timestamp": "2026-07-24T09:00:08"
}
```

A missing target response returns `404`. Rating feedback without `feedback_score` returns a request validation error.

## Client Scope

The standard OpenViking Python, TypeScript, and Go SDKs do not currently wrap the Bot proxy. Chat is available through the `ov chat` CLI and HTTP. The VikingBot Gateway also exposes Session and Channel APIs; see the [VikingBot documentation](https://github.com/volcengine/OpenViking/blob/main/bot/README.md#http-api).

## Related Documentation

- [VikingBot Concepts](../concepts/15-vikingbot.md) - architecture and interaction flow
- [VikingBot Guide](../guides/17-vikingbot.md) - setup and Chat workflow
