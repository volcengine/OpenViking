# Vikingbot Phase 2 反馈链路验证指南

**Author:** OpenViking Team
**Status:** Draft
**Date:** 2026-04-30

---

## 1. 目的

本文用于验证 Vikingbot feedback observability Phase 2 当前已经进入实现状态的显式反馈链路，重点覆盖：

1. `POST /bot/v1/feedback` 可通过 OpenViking Server 代理路径访问
2. 反馈请求能够按 `response_id` 关联到历史 assistant message
3. feedback 事件会追加写入 session JSONL 的 `metadata.feedback_events`
4. bus 会发布 analytics-only 的 `feedback_submitted` 事件
5. `feedback_submitted` 不会泄漏到用户可见 channel

本文优先采用 `openviking-server --with-bot` 的真实代理路径进行验证，而不是直接把 vikingbot gateway 端口当作主验证入口。

---

## 2. 适用范围

本文适用于以下启动方式：

```bash
openviking-server --with-bot
```

在这种模式下：

- OpenViking Server 对外监听业务端口
- 内部自动启动 vikingbot gateway
- Bot API 通过 OpenViking Server 代理暴露在 `/bot/v1`

因此本文默认验证地址形如：

- `http://127.0.0.1:<server-port>/bot/v1`

本文不把 `http://127.0.0.1:18790` 视为主验证入口。

---

## 3. 当前已验证结论

截至 2026-04-30，本轮已完成的验证分为三类。

### 3.1 已通过的代码级验证

已通过如下定向测试：

```bash
./.venv/bin/python -m pytest -o addopts='' bot/tests/test_openapi_auth.py -k feedback
```

结果：`3 passed`

对应验证点：

1. 成功提交 `/bot/v1/feedback` 时，返回 `accepted=true`
2. 成功提交后，bus 中出现 `OutboundEventType.FEEDBACK_SUBMITTED`
3. session JSONL metadata 中出现 `feedback_events[0]`
4. 对不存在的 `response_id` 提交反馈时，返回 `404 Response not found`
5. 当 OpenAPIChannel 持有过期 session cache，而 agent loop 已把 assistant response 写入磁盘时，`/feedback` 会重新加载 session，避免误报 `Response not found`

### 3.2 已通过的运行时代理验证

已通过 `openviking-server --with-bot` 验证代理健康检查：

```bash
curl -sS http://127.0.0.1:30300/bot/v1/health
```

返回结果表明：

1. OpenViking Server 已成功代理 `/bot/v1`
2. `--with-bot` 启动路径在当前环境可用

### 3.3 已通过的真实 `/chat -> /feedback` 闭环验证

本轮已经通过 `openviking-server --with-bot --config ov_conf/ov.conf` 完成真实代理路径闭环验证。

前置条件：

```bash
OPENVIKING_CLI_CONFIG_FILE=ov_conf/ovcli.conf openviking-server --with-bot --config ov_conf/ov.conf
```

验证结果：

1. `POST /bot/v1/chat` 能返回真实 `response_id`
2. `POST /bot/v1/feedback` 能通过 OpenViking Server 代理路径成功提交有效反馈
3. 对不存在的 `response_id` 提交反馈时，OpenViking Server 返回统一错误包裹，其中 message 为 `{"detail":"Response not found"}`
4. `./data/bot/sessions/cli__default__phase2-feedback-session-3.jsonl` 的 metadata 已实际出现 `feedback_events`

本轮真实验证样例：

1. `/chat` 返回 `response_id=71283018f5e1416ab5f22b11eccd6176`
2. `/feedback` 返回：

```json
{
  "accepted": true,
  "response_id": "71283018f5e1416ab5f22b11eccd6176",
  "session_id": "phase2-feedback-session-3",
  "feedback_type": "thumb_up",
  "feedback_delay_sec": 9.228,
  "timestamp": "2026-04-30T11:32:43.355564"
}
```

3. session JSONL metadata 首行包含：

```json
{
  "metadata": {
    "feedback_events": [
      {
        "response_id": "71283018f5e1416ab5f22b11eccd6176",
        "feedback_type": "thumb_up",
        "feedback_text": "helpful",
        "feedback_delay_sec": 9.228
      }
    ]
  }
}
```

---

## 4. 本轮遇到并已解决的问题

### 4.1 全局 CLI 配置污染真实聊天链路

本机全局 `~/.openviking/ovcli.conf` 包含非法字段 `url44`：

```text
ValueError: Invalid CLI config in /home/yangshunyao/.openviking/ovcli.conf:
Unknown config field 'ovcli.url44' (did you mean 'ovcli.url'?)
```

解决方式不是修改用户全局配置，而是在启动 `openviking-server --with-bot` 时显式传入 repo 内的 colocated CLI 配置：

```bash
OPENVIKING_CLI_CONFIG_FILE=ov_conf/ovcli.conf openviking-server --with-bot --config ov_conf/ov.conf
```

这样可以保证 bot 子进程以及 bot 内部 OpenViking client 都读取 `ov_conf/ovcli.conf`。

### 4.2 OpenViking Server 代理层最初没有暴露 `/feedback`

虽然 vikingbot gateway 已实现 `POST /bot/v1/feedback`，但 OpenViking Server 的 `openviking/server/routers/bot.py` 起初只代理：

1. `/health`
2. `/chat`
3. `/chat/stream`

因此通过 `http://127.0.0.1:30300/bot/v1/feedback` 访问时，只会得到 OpenViking Server 自己的 404。

本轮已补齐代理层 `/feedback` 转发，并增加了对应回归测试。

### 4.3 真实运行时存在 session stale-cache 问题

真实闭环验证中还发现一个仅在运行时更容易触发的问题：

1. OpenAPIChannel 自己维护一个 `SessionManager`
2. agent loop 也维护自己的 `SessionManager`
3. 如果一次过早的 `/feedback` 请求先把空 session 缓存进 OpenAPIChannel
4. 后续 assistant response 虽然已经写入磁盘，OpenAPIChannel 仍可能继续读取自己那份过期内存缓存

这会导致真实路径上错误返回 `Response not found`。

本轮已在 `/feedback` handler 中补充“一次磁盘重载重试”，并增加定向回归测试覆盖该场景。

---

## 5. 推荐验证步骤

### 5.1 启动服务

```bash
OPENVIKING_CLI_CONFIG_FILE=ov_conf/ovcli.conf openviking-server --with-bot --config ov_conf/ov.conf
```

预期日志至少包含类似内容：

```text
Bot API proxy enabled, forwarding to http://127.0.0.1:18790
Starting vikingbot gateway...
Vikingbot gateway started (PID: ...)
OpenViking HTTP Server is running on 127.0.0.1:30300
```

### 5.2 先做健康检查

```bash
curl -sS http://127.0.0.1:30300/bot/v1/health
```

预期返回 HTTP 200。

### 5.3 发送一条真实聊天请求

在使用 repo 内 colocated `ovcli.conf` 启动后，发送：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase2-feedback-session",
    "user_id": "phase2-feedback-user",
    "message": "请简单回复一句：用于验证 feedback"
  }'
```

预期返回中至少包含：

```json
{
  "session_id": "phase2-feedback-session",
  "response_id": "...",
  "message": "..."
}
```

### 5.4 提交显式反馈

拿到上一步的 `response_id` 后，发送：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase2-feedback-session",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

预期返回类似：

```json
{
  "accepted": true,
  "response_id": "<response_id>",
  "session_id": "phase2-feedback-session",
  "feedback_type": "thumb_up",
  "feedback_delay_sec": 1.234,
  "timestamp": "..."
}
```

### 5.5 验证不存在的 `response_id` 返回 404

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase2-feedback-session",
    "response_id": "missing-response",
    "feedback_type": "thumb_down"
  }'
```

bot gateway 侧预期返回 HTTP 404，OpenViking Server 当前会包一层统一错误格式，典型返回为：

```json
{
  "status": "error",
  "error": {
    "code": "NOT_FOUND",
    "message": "{\"detail\":\"Response not found\"}"
  }
}
```

### 5.6 检查 session JSONL 持久化

默认情况下，session 文件位于：

```text
{storage.workspace}/bot/sessions
```

例如当前本地配置对应：

```text
./data/bot/sessions
```

找到对应 session 文件后，检查首行 metadata，预期包含：

```json
{
  "metadata": {
    "feedback_events": [
      {
        "response_id": "<response_id>",
        "feedback_type": "thumb_up",
        "feedback_text": "helpful"
      }
    ]
  }
}
```

### 5.7 检查 analytics 事件未泄漏到用户侧 channel

当前实现约束是：

1. bus 按 `channel_key` 分发，而不是按 `event_type` 分发
2. `feedback_submitted` 仅作为 analytics-only 事件存在
3. chat / email / mochat 等用户侧 channel 明确忽略该事件

因此应确认：

1. OpenAPI `/feedback` 返回体中没有额外暴露 `feedback_submitted` 事件流
2. 用户侧 channel 不会把该 analytics 事件发送给终端用户

---

## 6. 排查建议

### 6.1 `/bot/v1/health` 不通

优先检查：

1. 是否确实使用了 `openviking-server --with-bot`
2. 服务端口是否与 `ov.conf` 中的 `server.port` 一致
3. `18790` 是否被旧的 vikingbot 进程占用

### 6.2 `/chat` 失败但 `/health` 正常

优先检查 bot 日志文件，例如：

```text
{storage.workspace}/bot/logs/vikingbot.log
```

本轮实际遇到的排查示例是：

```text
Invalid CLI config in /home/yangshunyao/.openviking/ovcli.conf
Unknown config field 'ovcli.url44'
```

这类问题会阻塞真实 agent 处理，但不代表 `/feedback` 的接口实现本身有问题。

### 6.3 `/chat` 成功但 `/feedback` 直接返回框架 404

优先检查 OpenViking Server 代理层是否真的暴露了 `/bot/v1/feedback`。

如果日志里根本没有 bot gateway 收到 `POST /bot/v1/feedback`，而 HTTP 返回是通用 404，则通常不是 bot gateway 本身的问题，而是 server proxy 没有转发该路由。

### 6.4 `/feedback` 命中 bot gateway 但仍返回 `Response not found`

优先检查：

1. `response_id` 是否确实来自同一 `session_id` 的 assistant response
2. feedback 是否在 assistant response 写盘前就过早提交
3. session 文件首行 metadata 与 assistant message 是否已持久化到同一个 `cli__default__<session>.jsonl`

---

## 7. 结论口径

截至当前，Phase 2 应使用如下口径描述：

1. `/bot/v1/feedback` 已实现
2. 反馈成功、404 失败以及 stale-cache 重载路径已被定向测试验证
3. `openviking-server --with-bot` 代理健康路径已验证可用
4. 完整真实 `/chat -> /feedback` 闭环已在当前环境完成，并确认 session JSONL 会写入 `metadata.feedback_events`
5. 为了走通首选运行路径，本轮额外补齐了 OpenViking Server 的 `/bot/v1/feedback` 代理路由
6. `response_outcome_evaluated` 仍然不在 Phase 2 范围内
