# 上下文网关管理 API

OpenViking Server 在 `/api/v1/admin/context-gateway` 下转发上下文网关的管理请求。Studio 的上下文网关页面通过这些端点管理上游、上下文配置和网关密钥，并读取请求日志。模型请求不走这组 API，客户端直接发到网关自己的端口（见[上下文网关](../guides/15-context-gateway.md)）。

**前提条件**：

- `ov.conf` 中 `context_gateway.enabled` 为 `true`，并且网关进程可以通过 `context_gateway.url` 访问。
- OpenViking Server 和网关使用同一个管理令牌（默认读取 `OPENVIKING_CONTEXT_GATEWAY_ADMIN_TOKEN`，至少 32 个字符）。
- 调用方使用 ADMIN 密钥或 root 密钥。USER 密钥返回 `403`。

**代码入口**：

- `openviking/server/routers/context_gateway.py` - OpenViking Server 代理、角色检查和账号隔离
- `context_gateway/app.py` - 网关管理路由
- `context_gateway/models.py` - 上游、上下文配置和密钥模型

## 代理方式

| 方法 | 路径 | 说明 |
|------|------|------|
| GET / POST / PUT / DELETE | `/api/v1/admin/context-gateway/{path}` | 以调用方账号转发到 `{context_gateway.url}/admin/{path}` |

发往 `/api/v1/admin/context-gateway/{path}` 的请求会以相同的方法、查询参数和请求体转发到 `{context_gateway.url}/admin/{path}`。OpenViking Server 用管理令牌替换调用方的凭据，并把 `X-OpenViking-Account` 设为调用方所在的账号，因此每次调用只能读取和修改本账号的网关对象。账号管理员访问不到其他账号的数据，调用方也无法指定转发目标。

只有第一段路径是 `overview`、`logs`、`guides`、`upstreams`、`policies`、`keys`、`users` 或 `tools` 的请求会被转发。其他路径以及包含 `..` 的路径返回 `404`。网关返回的状态码和响应体原样返回给调用方。

## API 参考

表格中的路径都相对于 `/api/v1/admin/context-gateway/`。管理 API 把上下文配置称为 `policies`。

### 概览和日志

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `overview` | 根据最近 10,000 条日志汇总用量：请求数、输出 token、首次调用和续写的缓存命中率、降级、召回统计和保存异常，另附 OpenViking 健康状态和 `log_retention_days` |
| GET | `logs?limit=200` | 最新的请求日志；`limit` 取 1–1000，默认 200 |
| GET | `guides` | 客户端应使用的地址：`base_url` 和 `public_url_configured` |
| GET | `tools` | 上下文配置可以提供的 OpenViking 工具：`name`、`description`，以及存在时的 `annotations` |

`tools` 借用本账号某个网关密钥绑定的 OpenViking 密钥读取工具列表。账号里还没有网关密钥时返回空列表；用哪个密钥都读不到时返回错误。

**HTTP API**

```bash
curl http://localhost:1933/api/v1/admin/context-gateway/overview \
  -H "X-API-Key: your-admin-key"
```

**响应示例**

```json
{
  "requests": 128,
  "last_request_at": 1785000000.0,
  "output_tokens": 45210,
  "cache": {
    "first_call": {"requests": 40, "input_tokens": 320000, "cached_tokens": 250000, "cache_hit_ratio": 0.78},
    "continuation": {"requests": 88, "input_tokens": 910000, "cached_tokens": 860000, "cache_hit_ratio": 0.95}
  },
  "degradations": {},
  "recall_count": 96,
  "recall_requests": 40,
  "recall_ms": 182.5,
  "capture_issues": {"retrying": 0, "paused": 0},
  "sample_limit": 10000,
  "openviking": {"status": "ok", "healthy": true, "version": "0.4.16", "auth_mode": "api_key"},
  "log_retention_days": 30
}
```

### 上游和上下文配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `upstreams` | 列出上游 |
| PUT | `upstreams/{upstream_id}` | 创建或替换上游 |
| DELETE | `upstreams/{upstream_id}` | 删除上游 |
| POST | `upstreams/{upstream_id}/test` | 检查网关能否访问服务商的模型列表 |
| GET | `policies` | 列出上下文配置 |
| PUT | `policies/{policy_id}` | 创建或替换上下文配置 |
| DELETE | `policies/{policy_id}` | 删除上下文配置 |

`upstream_id` 和 `policy_id` 由调用方指定。`PUT` 的请求体是完整对象，未知字段会被拒绝。各字段的默认值和取值范围见[配置参考](../guides/22-context-gateway-operations.md#配置参考)中的上游设置和上下文配置设置。

上游的 `api_key` 和 `headers` 的值只写不读，响应中分别换成 `has_api_key` 和 `header_names`。`PUT` 时 `api_key` 留空会保留已存的密钥；省略 `headers` 会保留所有已存的请求头，某个请求头的值留空则保留这个名字已存的值。

如果还有网关密钥在用某个上游或上下文配置，删除它会返回 `409`，需要先吊销或更新这些密钥。

**HTTP API**

```bash
curl http://localhost:1933/api/v1/admin/context-gateway/upstreams \
  -H "X-API-Key: your-admin-key"
```

**响应示例**

```json
[
  {
    "id": "openai",
    "revision": 2,
    "name": "OpenAI",
    "protocol": "chat",
    "vendor": "openai",
    "base_url": "https://api.openai.com/v1",
    "auth_mode": "managed",
    "has_api_key": true,
    "header_names": [],
    "models": ["gpt-5"],
    "aliases": {},
    "context_windows": {},
    "priority": 0,
    "enabled": true,
    "allow_gateway_tools": true,
    "coding_plan": false,
    "allow_coding_plan": false,
    "cache_min_tokens": 1024
  }
]
```

`upstreams/{upstream_id}/test` 返回 `{"ok": true, "status": 200}`，其中 `status` 是服务商返回的状态码；如果 10 秒内连不上服务商，返回 `{"ok": false, "reason": "upstream_unavailable"}`。

### 网关密钥

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `keys` | 列出网关密钥，不含密钥本身 |
| POST | `keys` | 签发网关密钥；密钥只在这次响应中出现 |
| DELETE | `keys/{key_id}` | 吊销网关密钥 |
| POST | `keys/{key_id}/capture/reset` | 让某个会话的对话保存从下一次请求起重新同步 |

`POST keys` 的请求体：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 显示名称 |
| `openviking_key` | string | 条件必填 | 网关密钥代表的用户的 OpenViking 密钥；与 `user_id` 二选一 |
| `user_id` | string | 条件必填 | 调用方账号中的用户，由 OpenViking Server 读取这个用户的密钥；仅账号管理员可用 |
| `policy_id` | string | 是 | 上下文配置 |
| `upstream_ids` | string[] | 是 | 至少一个上游 |
| `models` | string[] | 否 | 允许的模型；为空时允许上游提供的所有模型 |

OpenViking 密钥必须属于调用方账号中的 USER 或 ADMIN。只有 OpenViking Server 能读出已存的用户密钥时，`user_id` 才可用；启用了密钥哈希时改传 `openviking_key`。root 调用方也必须传 `openviking_key`，因为 root 密钥解析出的账号可能和 Studio 显示的账号不同。

已签发的密钥不能修改，`PUT keys/{key_id}` 返回 `405`。需要修改时签发一个新密钥，再吊销旧密钥。

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/admin/context-gateway/keys \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"name":"alice laptop","user_id":"alice","policy_id":"default","upstream_ids":["openai"]}'
```

**响应示例**

```json
{
  "id": "3f6c…",
  "revision": 1,
  "name": "alice laptop",
  "policy_id": "default",
  "upstream_ids": ["openai"],
  "models": [],
  "user_id": "alice",
  "prefix": "ovcg_Xk3p9Q",
  "created_at": 1785000000.0,
  "key": "ovcg_Xk3p9Q…"
}
```

`keys/{key_id}/capture/reset` 的请求体是 `{"session": "<会话>", "protocol": "chat"}`，`protocol` 取 `anthropic`、`chat` 或 `responses`。`session` 可以填客户端发送的会话请求头的值（例如 `X-OpenViking-Session`），也可以填请求日志里的 `session` 字段；没有会话请求头的会话只能用后者。成功时返回 `{"status": "ready", "message": "…"}`；密钥或会话不存在时返回 `404`。

### 用户数据

| 方法 | 路径 | 说明 |
|------|------|------|
| DELETE | `users/{user_id}/data` | 吊销用户的所有网关密钥，并删除网关中这个用户的对话状态 |

已经保存到 OpenViking 的会话不受影响。

## 错误

OpenViking Server 自身产生的错误使用标准错误响应格式：

| 状态码 | 错误码 | 原因 |
|--------|--------|------|
| 400 | `INVALID_ARGUMENT` | root 在 `POST keys` 中传了 `user_id`；`user_id` 不存在 |
| 400 | `INVALID_ARGUMENT` | `POST keys` 同时传了 `user_id` 和 `openviking_key`（原始状态码 422） |
| 403 | `PERMISSION_DENIED` | 调用方不是 ROOT 或 ADMIN |
| 404 | `NOT_FOUND` | 路径不在转发范围内 |
| 409 | `CONFLICT` | 服务端读不出所选用户的密钥 |
| 503 | `UNAVAILABLE` | 上下文网关未启用、管理令牌未配置，或连不上网关（原始状态码 502） |

网关返回的错误保留网关的状态码，响应体形如 `{"detail": "Unknown context policy"}`：

| 状态码 | 原因 |
|--------|------|
| 400 | `POST keys` 指定的上下文配置或上游不存在 |
| 403 | OpenViking 密钥属于其他账号 |
| 404 | 资源、密钥或会话不存在 |
| 405 | 调用了 `PUT keys/{key_id}` |
| 409 | 删除仍被密钥使用的上游或上下文配置 |
| 422 | 请求体无效。提交的值可能包含密钥，所以错误信息不会回显它们 |

网关无法验证 OpenViking 密钥时，响应体是 `{"error": {"message": "<原因>"}}`，原因值见[签发密钥失败](../guides/22-context-gateway-operations.md#签发密钥失败)。

## 相关文档

- [上下文网关](../guides/15-context-gateway.md) - 网关的工作方式和客户端接入
- [上下文网关部署与运维](../guides/22-context-gateway-operations.md) - 部署、在 Studio 中管理网关和配置参考
- [管理员](08-admin.md) - 账号、用户和角色
