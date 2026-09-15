# VikingBot API

OpenViking Server 启用 `--with-bot` 后，会在 `/bot/v1` 下代理 VikingBot 的核心交互接口。未启用 Bot 时，这些端点返回 `503`。

**代码入口**：

- `openviking/server/routers/bot.py` - OpenViking Server 代理与身份转发
- `bot/vikingbot/channels/openapi.py` - VikingBot Gateway 路由实现
- `bot/vikingbot/channels/openapi_models.py` - 请求、响应和 SSE 事件模型

## API 参考

### health()

检查 Bot Gateway 是否可用。

**HTTP API**

```bash
curl http://localhost:1933/bot/v1/health
```

**响应示例**

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-07-24T09:00:00"
}
```

### chat()

发送文本和/或图片并等待完整回复。`session_id` 可省略；省略时 Gateway 会创建新会话。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `message` | string | 条件必填 | `""` | 用户文本；`images` 为空时必填 |
| `images` | array | 条件必填 | `[]` | 最多 4 个 OpenAI 风格的 `image_url`；`message` 为空时必填 |
| `session_id` | string | 否 | 自动生成 | 继续已有会话时传入 |
| `context` | array | 否 | `null` | 额外上下文消息，每项包含 `role` 和 `content` |
| `need_reply` | boolean | 否 | `true` | 是否需要 Bot 回复 |
| `disabled_tools` | string[] | 否 | `[]` | 本次请求禁用的工具名 |
| `channel_id` | string | 否 | `null` | 多 Channel 路由标识 |

**HTTP API**

```bash
curl -X POST http://localhost:1933/bot/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message":"总结我的项目进展","session_id":"optional-session-id"}'
```

图片可以使用模型可访问的 HTTPS URL，或内联 Base64 Data URL：

```bash
curl -X POST http://localhost:1933/bot/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "message": "描述这张图片",
    "images": [{
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/photo.png"
      }
    }]
  }'
```

内联 Base64 图片支持 JPEG、PNG、GIF 和 WebP，解码后单张最大 10 MiB；内联 SVG 和 MIME
签名不匹配的图片会被拒绝。对于 HTTPS URL，Gateway 只校验 URL 结构，不会下载或检查远程
资源，因此远程格式支持及相关错误由具体 provider 决定。本地文件路径仍会被拒绝。可选的
`detail` 支持 `auto`、`low`、`high`；为获得最好的模型兼容性，建议省略。

**CLI**

```bash
ov chat -m "总结我的项目进展"
```

**响应示例**

```json
{
  "session_id": "session-id",
  "response_id": "response-id",
  "message": "这是当前项目进展摘要……",
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

以 Server-Sent Events 返回推理、工具调用、增量内容和最终响应事件。请求字段与 `chat()` 相同；Gateway 会自动启用流式模式。

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

**SSE 响应示例**

每条消息使用 `data: <json>` 格式，响应头 `X-VikingBot-Session-ID` 包含本次会话 ID。

```text
data: {"event":"reasoning_delta","data":"正在检查知识库…","timestamp":"2026-07-24T09:00:00"}

data: {"event":"content_delta","data":"当前知识库包含","timestamp":"2026-07-24T09:00:01"}

data: {"event":"response","data":{"content":"当前知识库包含……","response_id":"response-id"},"timestamp":"2026-07-24T09:00:02"}
```

`event` 可能为 `reasoning`、`reasoning_delta`、`tool_call`、`tool_result`、`content_delta`、`iteration` 或 `response`。

### feedback()

对已经生成的回复提交显式反馈。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | 产生目标回复的会话 ID |
| `response_id` | string | 是 | 目标助手回复 ID |
| `feedback_type` | string | 是 | `thumb_up`、`thumb_down` 或 `rating` |
| `feedback_score` | number | 条件必填 | `feedback_type=rating` 时必须提供 |
| `feedback_reason` | string | 否 | 反馈原因标签 |
| `feedback_text` | string | 否 | 自由文本反馈 |
| `channel_id` | string | 否 | 多 Channel 路由标识 |

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

**响应示例**

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

目标回复不存在时返回 `404`；`rating` 缺少 `feedback_score` 时返回请求校验错误。

## 客户端范围

标准 OpenViking Python、TypeScript 和 Go SDK 当前不封装 Bot 代理接口；Chat 可通过 `ov` CLI 与 HTTP 使用。VikingBot Gateway 自身还提供 Session 和 Channel API，详见 [VikingBot 文档](https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md#http-api)。

## 相关文档

- [VikingBot 概念](../concepts/15-vikingbot.md) - 架构和交互流程
- [VikingBot 指标验证](../guides/12-vikingbot-metrics-validation.md) - Chat、Feedback 和指标链路
