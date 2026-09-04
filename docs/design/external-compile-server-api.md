# Compile Server 对接说明

## 整体流程

```text
ov compile
  -> POST /api/v1/compile
  -> OV 校验 URI 和权限，创建 TaskRecord，再写入 QueueFS
  -> OV 调用 Compile Server 创建 session
  -> OV 保存 session_id，轮询状态并更新 TaskRecord
  -> Compile Server 使用 X-API-Key 访问对应用户和 OV 数据
  -> 用户通过 /api/v1/tasks 查询或取消 OV task
```

OV 负责公开任务的持久化、查询、取消、重试和重启恢复。Compile Server 负责实际执行和 session 状态。外部服务是多实例时，session 必须放在共享存储中。

所有请求都必须校验当前用户的 OV API Key：

```http
X-API-Key: <当前用户的 OV API Key>
```

`X-API-Key` 用于解析用户和对应 OV 实例。外部服务需要额外确认请求来自 OV 时，可配置 `compile_api.gateway_token` 并校验 `X-Gateway-Token`。两种凭证都不能写入日志或任务公开状态。

## 1. 创建 session

```http
POST /bot/v1/compile
Idempotency-Key: <OV task_id>
```

```json
{
  "from": ["viking://resources/source"],
  "to": "viking://resources/output",
  "skill": "viking://agent/skills/wiki",
  "reason": "optional",
  "args": {
    "model_name": "optional",
    "user_key": "optional-sensitive-value"
  }
}
```

返回：

```json
{"session_id": "ma-session-123"}
```

同一个 `Idempotency-Key` 重复请求时必须返回同一个 `session_id`，不能重复执行。OV 收到响应后会把 `session_id` 写入任务私有状态；进程重启后直接继续轮询。

## 2. 查询 session

```http
POST /compile/status
```

```json
{"session_id": "ma-session-123"}
```

响应：

```json
{
  "status": "running",
  "stage": "compile: running",
  "error": null,
  "meta": {
    "token_usage": {
      "input_tokens": 1200,
      "output_tokens": 300,
      "total_tokens": 1500
    }
  },
  "result": null
}
```

`status` 建议明确返回 `pending`、`running`、`cancelling`、`completed`、`failed` 或 `cancelled`。为兼容当前 MA 响应，OV 在缺少 `status` 时也会从 `stage` 的 terminal 词判断终态。成功结果可放在 `result`；没有结果时，OV 使用 `meta` 作为任务结果。

## 3. 取消 session

```http
POST /compile/cancel
```

```json
{"session_id": "ma-session-123"}
```

返回结构与查询接口一致。取消接口必须幂等；任务已经结束时返回当前终态。

## 配置

```json
{
  "compile_api": {
    "base_url": "https://compile.example.com",
    "http_timeout_seconds": 10,
    "poll_interval_ms": 3000
  }
}
```

配置非空 `base_url` 即启用外部 Compile。`gateway_token` 可选；仅在外部服务要求网关身份时配置。

`408`、`425`、`429` 和 `5xx` 会被 OV 当作暂时错误重试；其他 `4xx` 会结束任务。VikingBot 已实现同一套 session 接口，可用于本地联调，但不实现 MA 专用的 `args`。
