# Vikingbot Phase 1 功能验证指南

**Author:** OpenViking Team
**Status:** Draft
**Date:** 2026-04-29

---

## 1. 目的

本文用于验证 Vikingbot feedback observability Phase 1 当前已经落地的能力，验证方式基于 `openviking-server --with-bot` 启动模式。

本次 Phase 1 需要确认的核心点有四个：

1. OpenAPI 返回中存在稳定的 `response_id`
2. session JSONL 中 assistant message 持久化了 `response_id`
3. Langfuse 的 generation / tool span metadata 中带有 `response_id`
4. `response_completed` 没有暴露到用户侧 channel 或 OpenAPI 对外返回中

---

## 2. 适用范围

本文适用于以下启动方式：

```bash
openviking-server --with-bot
```

在这种模式下：

- OpenViking Server 对外监听 `1933`
- 内部会自动启动 `vikingbot gateway`
- Bot API 会通过 OpenViking Server 代理暴露在 `/bot/v1`

因此本文所有 HTTP 验证请求都走：

- `http://127.0.0.1:1933/bot/v1`

而不是直接访问 `vikingbot gateway` 自身端口。

---

## 3. 前置条件

开始前请确认：

1. OpenViking 可以正常启动，并带上 `--with-bot`
2. bot 已配置可用模型，能够正常回答问题
3. 如果要验证 Langfuse，已经安装 `bot-langfuse` 依赖并在 `ov.conf` 中开启 Langfuse

Langfuse 配置示例：

```json
{
  "bot": {
    "langfuse": {
      "enabled": true,
      "secret_key": "your-secret-key",
      "public_key": "your-public-key",
      "base_url": "http://localhost:3000"
    }
  }
}
```

如果你修改了配置，需要重启：

```bash
openviking-server --with-bot
```

---

## 4. 启动服务

在一个终端中启动：

```bash
openviking-server --with-bot
```

预期日志中至少应看到类似内容：

```text
Bot API proxy enabled, forwarding to http://127.0.0.1:18790
Starting vikingbot gateway...
Vikingbot gateway started (PID: ...)
OpenViking HTTP Server is running on 127.0.0.1:1933
```

如果这里启动失败，后面的验证都不成立，先解决启动问题。

---

## 5. 验证步骤

### 5.1 健康检查

先确认 Bot API 代理已经可访问：

```bash
curl -sS http://127.0.0.1:1933/bot/v1/health
```

预期返回 HTTP 200。

如果这里失败，说明 `--with-bot` 没有生效，或者内部 bot gateway 没有成功启动。

### 5.2 验证非流式返回包含 `response_id`

发送一条最小请求：

```bash
curl -sS -X POST "http://127.0.0.1:1933/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase1-verify-session",
    "user_id": "phase1-verify-user",
    "message": "请简单回复一句：验证 response_id"
  }'
```

预期返回中至少包含以下结构：

```json
{
  "session_id": "phase1-verify-session",
  "response_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "message": "...",
  "events": [
    {
      "type": "response",
      "data": {
        "content": "...",
        "response_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      },
      "timestamp": "..."
    }
  ]
}
```

重点检查：

1. 顶层有 `response_id`
2. `events` 中最终 `type == "response"` 的事件里，`data.response_id` 存在
3. 顶层 `response_id` 与 `events[*].data.response_id` 一致

如果只看到 `message`，但没有 `response_id`，说明 Phase 1 的 API 贯通未生效。

### 5.3 验证流式返回包含 `response_id`

发送一条流式请求：

```bash
curl -N -X POST "http://127.0.0.1:1933/bot/v1/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase1-verify-stream",
    "user_id": "phase1-verify-user",
    "stream": true,
    "message": "请简单回复一句：验证 stream response_id"
  }'
```

预期 SSE 中最终 `response` 事件的数据类似：

```json
{
  "type": "response",
  "data": {
    "content": "...",
    "response_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  },
  "timestamp": "..."
}
```

重点检查：

1. 流式最终 `response` 事件带有 `data.response_id`
2. 没有额外出现 `response_completed` 事件

### 5.4 验证 session JSONL 已写入 `response_id`

Phase 1 当前会把 `response_id` 持久化到 assistant message 中。

默认情况下，bot data 目录是：

```text
~/.openviking/data/bot
```

session 目录是：

```text
~/.openviking/data/bot/sessions
```

如果你在 `ov.conf` 中设置了 `storage.workspace`，则实际目录为：

```text
{storage.workspace}/bot/sessions
```

先搜索刚才的 session：

```bash
rg "phase1-verify-session|response_id" ~/.openviking/data/bot/sessions
```

也可以直接列出 session 文件：

```bash
ls ~/.openviking/data/bot/sessions
```

找到对应文件后，检查 assistant message，预期类似：

```json
{
  "role": "assistant",
  "content": "...",
  "timestamp": "...",
  "response_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "token_usage": {
    "prompt_tokens": 123,
    "completion_tokens": 45,
    "total_tokens": 168
  }
}
```

重点检查：

1. `role == "assistant"` 的消息里有 `response_id`
2. 该 `response_id` 与 HTTP 返回中的 `response_id` 一致

如果 session 中没有 `response_id`，说明只有 API 层返回了该字段，但持久化链路未生效。

### 5.5 验证 Langfuse generation metadata 包含 `response_id`

这一步要求已经启用 Langfuse。

完成第 5.2 步后，打开 Langfuse UI，找到刚才对应的 trace / generation，检查 metadata。

预期至少包含：

- `response_id`
- `has_tools`
- `finish_reason`

重点检查：

1. generation metadata 中存在 `response_id`
2. Langfuse 中看到的 `response_id` 与 HTTP 返回一致

### 5.6 验证 Langfuse tool span metadata 包含 `response_id`

再发一条更容易触发工具的问题，例如：

```bash
curl -sS -X POST "http://127.0.0.1:1933/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase1-verify-tool",
    "user_id": "phase1-verify-user",
    "message": "请查看当前工作区有哪些文件，并简短总结"
  }'
```

然后在 Langfuse 中查看对应 tool span metadata。

预期至少包含：

- `response_id`
- `duration_ms`
- `success`

重点检查：

1. tool span metadata 中存在 `response_id`
2. generation 与 tool span 使用的是同一个 `response_id`

### 5.7 验证 `response_completed` 没有对外暴露

`response_completed` 当前是内部分析事件，不应直接暴露给 OpenAPI 客户端，也不应出现在用户可见 channel 中。

验证方式：

1. 检查第 5.2 和 5.3 步的 HTTP 返回
2. 确认返回事件中只包含以下类型：
   - `response`
   - `reasoning`
   - `tool_call`
   - `tool_result`
3. 确认没有出现：
   - `response_completed`

如果你同时在使用 CLI chat、Email 或 Mochat 等用户侧 channel，也应确认：

1. 用户只看到正常回答
2. 没有额外空消息
3. 没有出现 `response_completed` 或内部 JSON 数据

说明：

- 当前 `response_completed` 是通过内部 bus 发布的分析事件
- 它不是 OpenAPI 对外契约的一部分
- 因此本阶段的正确行为是“内部可用、外部不可见”

---

## 6. 通过标准

如果以下条件全部满足，可以认为当前 Phase 1 实现已验证通过：

1. `/bot/v1/chat` 返回顶层 `response_id`
2. 最终 `response` 事件的 `data.response_id` 存在
3. session JSONL 的 assistant message 中写入了 `response_id`
4. Langfuse generation metadata 中存在 `response_id`
5. 如果触发工具，Langfuse tool span metadata 中也存在 `response_id`
6. OpenAPI 或用户侧 channel 没有暴露 `response_completed`

---

## 7. 常见问题

### 7.1 `/bot/v1/health` 不通

优先检查：

1. 是否确实使用了 `openviking-server --with-bot`
2. 启动日志中是否出现 `Bot API proxy enabled`
3. 内部 bot gateway 是否启动成功
4. 启动前是否已经有其他进程占用了 `18790`

如果 `18790` 已经被单独运行的 `vikingbot` 或旧进程占用，`openviking-server --with-bot` 会直接拒绝启动，并输出类似错误：

```text
Error: vikingbot gateway port 18790 is already in use.
  A previous process is still bound - refusing to start a duplicate.
```

### 7.2 API 能回答，但没有 `response_id`

优先检查：

1. 当前进程是否已经重启到包含 Phase 1 改动的新代码
2. 是否命中了 `/bot/v1/chat` 的正常回答路径
3. 是否不是旧版本服务还在占用端口

### 7.3 session 文件里没有 `response_id`

优先检查：

1. 是否找错了 session 文件目录
2. 是否配置了非默认 `storage.workspace`
3. 是否实际看到的是历史旧 session 文件

### 7.4 Langfuse 中看不到 `response_id`

优先检查：

1. 是否已安装 `bot-langfuse` 依赖
2. `ov.conf` 中 `bot.langfuse.enabled` 是否为 `true`
3. 是否已经重启服务
4. 当前请求是否确实打到了启用 Langfuse 的 bot 进程

---

## 8. 最小验证闭环

如果只想用最短时间确认核心链路，建议至少完成以下三步：

1. 调用一次 `/bot/v1/chat` 并确认返回中有 `response_id`
2. 在 session JSONL 中确认 assistant message 写入了相同的 `response_id`
3. 确认 OpenAPI 对外返回中没有 `response_completed`

这三步成功，说明 Phase 1 最核心的“response identity + response facts 基础链路”已经基本打通。
