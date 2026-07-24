# VikingBot API

When OpenViking Server starts with `--with-bot`, it proxies core VikingBot interaction endpoints under `/bot/v1`. These endpoints return `503` when Bot is not enabled.

## API Reference

### health()

Check whether the Bot Gateway is available.

**HTTP API**

```bash
curl http://localhost:1933/bot/v1/health
```

### chat()

Send one message and wait for the complete response. `session_id` is optional; the Gateway creates a session when it is omitted.

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

### chat_stream()

Return reasoning, tool-call, content-delta, and final-response events through Server-Sent Events.

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

### feedback()

Submit explicit feedback for an existing response. `feedback_type` supports `thumb_up`, `thumb_down`, and `rating`; `rating` also requires `feedback_score`.

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

## Client Scope

The standard OpenViking Python, TypeScript, and Go SDKs do not currently wrap the Bot proxy. Chat is available through the `ov chat` CLI and HTTP. The VikingBot Gateway itself exposes additional Session and Channel APIs; see the [VikingBot documentation](https://github.com/volcengine/OpenViking/blob/main/bot/README.md#http-api).

## Related Documentation

- [VikingBot Concept](../concepts/15-vikingbot.md) - architecture and interaction flow
- [VikingBot Guide](../guides/17-vikingbot.md) - setup and operation
