# VikingBot API

OpenViking Server 启用 `--with-bot` 后，会在 `/bot/v1` 下代理 VikingBot 的核心交互接口。未启用 Bot 时，这些端点返回 `503`。

## API 参考

### health()

检查 Bot Gateway 是否可用。

**HTTP API**

```bash
curl http://localhost:1933/bot/v1/health
```

### chat()

发送一条消息并等待完整回复。`session_id` 可省略；省略时 Gateway 会创建新会话。

**HTTP API**

```bash
curl -X POST http://localhost:1933/bot/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message":"总结我的项目进展","session_id":"optional-session-id"}'
```

**CLI**

```bash
ov chat -m "总结我的项目进展"
```

### chat_stream()

以 Server-Sent Events 返回推理、工具调用、增量内容和最终响应事件。

**HTTP API**

```bash
curl -N -X POST http://localhost:1933/bot/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message":"分析当前知识库"}'
```

**CLI**

```bash
ov chat -m "分析当前知识库"
```

### feedback()

对已经生成的回复提交显式反馈。`feedback_type` 支持 `thumb_up`、`thumb_down` 和 `rating`；使用 `rating` 时必须同时提供 `feedback_score`。

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

## 客户端范围

标准 OpenViking Python、TypeScript 和 Go SDK 当前不封装 Bot 代理接口；聊天入口由 `ov chat` CLI 和 HTTP 提供。VikingBot Gateway 自身还提供 Session 和 Channel API，详见 [VikingBot 文档](https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md#http-api)。

## 相关文档

- [VikingBot 概念](../concepts/15-vikingbot.md) - 架构和交互流程
- [VikingBot 指标验证](../guides/12-vikingbot-metrics-validation.md) - Chat、Feedback 和指标链路
