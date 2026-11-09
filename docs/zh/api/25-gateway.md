# OpenViking 网关管理 API

管理 API 用来查看权限、配置上游和上下文配置、签发网关密钥、读取用量和日志。所有路径都以 `/gateway/admin/` 开头，由网关直接提供：网关在 OpenViking Server 内运行时，访问 Server 的端口（默认 1933）；独立运行时，访问网关自己的端口（默认 1935）。示例中的 `SERVER_URL` 指这个地址本身，不带路径，例如 `http://127.0.0.1:1933`。

模型客户端不调用这组接口。它们用网关密钥访问 `/gateway/v1/*`、`/gateway/api/v3/*` 或 `/gateway/api/compatible/v1/*`，见[OpenViking 网关](../gateway/03-connect-clients.md#connect-clients)。

## 凭证和账号范围 {#credentials-and-account-scope}

每个管理请求都要带 OpenViking API 密钥，放在 `X-API-Key` 或 `Authorization: Bearer` 中。网关向 OpenViking 确认这把密钥属于哪个账号、哪个用户、什么角色，确认结果最多缓存 30 秒。本地 dev 模式不需要密钥，调用方固定为 root 身份 `default/default`。网关不支持 trusted 认证。

上游和上下文配置（API 中叫 `policies`）属于账号，同一账号的用户共用。请求默认作用于调用方所在的账号；root 可以用 query 参数 `account_id` 指定其他账号，admin 和 user 只能访问自己的账号。

部署者可以另外设置网关管理密钥（配置方法见[网关管理密钥](../guides/22-gateway-operations.md#gateway-management-key)）。设置之后，修改上游和上下文配置的请求必须在 `X-OpenViking-Gateway-Management-Key` 中带上它，OpenViking 的 admin 和 root 也不例外。请求带了管理密钥但值不对时，网关直接返回 401 `management_key_invalid`，不会降级成只读请求。

| 操作 | 未设置管理密钥 | 已设置管理密钥 |
| --- | --- | --- |
| 读取上游和上下文配置 | 本账号的任何用户 | 本账号的任何用户；单独持有管理密钥也可以，并能指定任意账号 |
| 创建、修改或删除上游和上下文配置；测试上游 | 本账号的任何用户 | 只有带正确管理密钥的请求 |
| 导入 OpenViking 的模型配置 | 嵌入部署中的 root（包括本地 dev） | 只有带正确管理密钥的请求，且只限嵌入部署 |
| 签发、读取、吊销网关密钥，重置保存 | user 只能操作自己的密钥；admin 操作本账号；root 操作所选账号 | 同左，管理密钥不增加任何权限 |
| 读取日志和概览 | user 只看自己的；admin 和 root 看整个账号 | 同左，始终需要 OpenViking 身份 |
| 删除某个用户的网关数据 | 本账号的 admin，或 root | 同左 |
| 删除整个账号的网关数据 | root | 同左 |

管理密钥可以不带 OpenViking API 密钥单独使用，适合自动化脚本：它能读写上游和上下文配置、调用 `/access`，用 `account_id` 指定账号（省略时为 `default`）。签发密钥、读取日志和概览始终需要 OpenViking 身份；两种凭证同时提供，也不会让这些操作获得更高的权限。设置、更换或去掉管理密钥都不影响已签发的网关密钥。

只读视图不返回上游的 API Key、请求头的值和请求头名称，只保留 `has_api_key` 和 `auth_mode`。

## 权限与发现 {#access-and-discovery}

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| GET | `/gateway/admin/access` | 调用方的权限：`managed`、`can_write_objects`、`can_import_vlm`、`can_issue_for_others`、`role`、`account_id`、`user_id`、`warnings` |
| GET | `/gateway/admin/overview` | 调用方可见的最近 10,000 条日志汇总：用量、缓存命中、召回、保存异常、OpenViking 状态和日志保留期 |
| GET | `/gateway/admin/logs` | 调用方可见的最新日志，以 JSON 数组返回，最新的在前；`limit` 默认 200，取值 1–1000。字段见[日志记录](#log-records)。 |
| GET | `/gateway/admin/guides` | 客户端接入地址 `base_url`，以及 `public_url_configured`；`base_url` 是 `public_url` 加 `/gateway`，未设置 `public_url` 时取请求的来源地址加 `/gateway` |
| GET | `/gateway/admin/tools` | 调用方能用的 OpenViking 工具：名称、描述和 annotations，用调用方自己的 OpenViking 密钥读取（OpenViking 拒绝 root 访问 `/mcp`，所以 root 改用本账号已签发网关密钥对应的 OpenViking 密钥，没有时返回上次读到的目录） |

Studio 只根据 `/access` 决定显示哪些按钮，自己写的管理工具也应这样做。独立网关连接公共 OpenViking 服务且没有设置管理密钥时，`warnings` 中会出现 `public_openviking_without_management_key`。它只是告警，不限制任何操作。

```bash
curl "$SERVER_URL/gateway/admin/access" \
  -H "X-API-Key: $OPENVIKING_API_KEY"
```

```json
{
  "managed": true,
  "can_write_objects": false,
  "can_import_vlm": false,
  "can_issue_for_others": true,
  "role": "admin",
  "account_id": "team",
  "user_id": "alice",
  "warnings": []
}
```

OpenViking 拒绝 API 密钥时，返回 401 `openviking_http_401`。

### 日志记录 {#log-records}

日志只记元数据，不含消息内容。发往模型服务商的请求结束时写一条记录；对话的保存状态变化时也写一条（`kind` 为 `capture`）。网关在请求到达服务商之前就拒绝的，不记日志。除 `time` 外，字段都是可选的，不适用时省略。

| 字段 | 含义 |
| --- | --- |
| `time` | 记录写入时间，Unix 时间戳（秒） |
| `request_id` | 网关为请求分配的 ID |
| `kind` | `user`、`continuation`、`auxiliary`、`subagent`、`count`、`passthrough` 或 `capture`，见[请求日志](../gateway/10-overview-and-requests.md#requests) |
| `model` | 客户端发送的模型名 |
| `status` | 服务商返回的 HTTP 状态码 |
| `upstream_id`、`credential_id` | 处理请求的上游，以及发出请求的网关密钥 |
| `session`、`protocol` | 对话及其协议，[重置保存](#gateway-keys)时要用 |
| `degradation` | 异常代码，见[降级请求的异常](../gateway/12-troubleshooting.md#issues-on-degraded-requests) |
| `recall_reason`、`recall_count`、`recall_ms` | 记忆检索的结果代码、补充的条数和检索耗时，见[没有补充记忆](../gateway/12-troubleshooting.md#no-memory-is-added) |
| `replay_hits` | 原样放回记忆的历史消息数 |
| `capture_status`、`capture_reason`、`capture_retry_at` | 保存状态（`active`、`disabled`、`retrying` 或 `paused`）、原因，以及下次重试时间（Unix 时间戳，秒），见[保存状态](../gateway/10-overview-and-requests.md#saving-status) |
| `tool_skip_reason` | 请求没有拿到 OpenViking 工具的原因，见[什么时候不提供工具](../gateway/04-openviking-tools.md#when-tools-are-not-offered) |
| `tool_stop_reason` | 工具 Token 预算用完、后续调用被拒绝时为 `token_budget`，见[上限](../gateway/04-openviking-tools.md#limits) |
| `compaction_failed` | 压缩失败的原因（`still_over_threshold` 时摘要已保留），见[长对话超出上下文长度](../gateway/12-troubleshooting.md#long-conversations-hit-the-context-limit) |
| `context_tokens`、`context_window` | 估算的上下文大小，以及用来比较的上下文窗口 |
| `input_tokens`、`output_tokens`、`cached_tokens`、`cache_write_tokens` | 本次请求所有服务商调用（含 OpenViking 工具轮次）的 token 用量合计。`input_tokens` 已包含缓存命中和缓存写入的 token。 |
| `duration_ms` | 从把请求发给服务商到回复结束的耗时 |

以 `first_upstream_` 和 `hidden_upstream_` 开头的字段把 token 用量拆成首次服务商调用和 OpenViking 工具轮次两部分，首次调用缓存命中率按前者计算。

`/overview` 由这些记录汇总而来。除用量和缓存数据外，还返回 `degradations`（异常代码 → 次数）、`capture_issues`（正在重试或已暂停保存的对话数）、`cache_alerts`、`openviking`（OpenViking 的 `status`，降级时另有 `reason`）和 `log_retention_days`。

## 上游和上下文配置 {#upstreams-and-context-profiles}

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/gateway/admin/upstreams` | 列出上游 |
| GET | `/gateway/admin/upstreams/{identifier}` | 读取单个上游 |
| PUT | `/gateway/admin/upstreams/{identifier}` | 创建或替换上游 |
| DELETE | `/gateway/admin/upstreams/{identifier}` | 删除没有密钥在用的上游 |
| POST | `/gateway/admin/upstreams/{identifier}/test` | 测试上游连接或模型 |
| POST | `/gateway/admin/upstreams/import-vlm` | 导入 OpenViking 的模型配置 |
| GET | `/gateway/admin/policies` | 列出上下文配置 |
| GET | `/gateway/admin/policies/{identifier}` | 读取单个上下文配置 |
| PUT | `/gateway/admin/policies/{identifier}` | 创建或替换上下文配置 |
| DELETE | `/gateway/admin/policies/{identifier}` | 删除没有密钥在用的上下文配置 |

ID 由调用方指定。`PUT` 提交完整对象，未知字段会导致校验失败。各字段的含义、默认值和取值范围见[配置参考](../gateway/13-configuration.md#configuration-reference)。

上游的 `api_key` 和 `headers` 只写不读：

- `base_url` 不变时，`api_key` 留空或省略就保留已存的密钥；省略 `headers` 保留全部已存请求头；提交 `headers` 对象则替换整个集合，其中值为空的项沿用这个名称已存的值。
- **修改 `base_url` 会清除已存的 API Key 和全部请求头值。** 新地址需要的凭证要随这次请求重新提交，这样已存的密钥不会被发往另一个地址。
- 可编辑视图返回 `has_api_key` 和 `header_names`，不返回值。

```bash
curl -X PUT "$SERVER_URL/gateway/admin/upstreams/openai?account_id=team" \
  -H "X-OpenViking-Gateway-Management-Key: $GATEWAY_MANAGEMENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"OpenAI","protocol":"chat","base_url":"https://api.openai.com/v1","api_key":"<provider-key>"}'
```

删除仍有网关密钥在用的对象，返回 409 `in_use` 和 `key_count`。先吊销这些密钥；只是想让请求立即停止发往某个上游，可以停用它而不删除。

### 上游测试 {#upstream-tests}

`POST /gateway/admin/upstreams/{identifier}/test` 支持两种模式：空请求体 `{}` 通过 `GET /v1/models` 检查连接；`{"model":"<模型名>"}` 使用已存的凭证和请求头发送一次很小的非流式生成请求。模型名会先经过上游的别名映射。模型请求使用已配置的 Chat Completions、Responses 或 Anthropic Messages 接口；关闭路径拼接时，也会遵循配置的完整接口 URL。上游允许 OpenViking 工具时，请求会带上当前账号的 OpenViking 工具，并按默认上下文配置筛选；工具目录暂时不可用时，降级为不带工具继续测试。模型测试不设置 reasoning effort，由模型使用默认值。

响应包含 `{ok, kind, status, reason, url}`，其中 `kind` 为 `connection` 或 `model`。测试失败时还会在 `response` 中返回完整的上游 HTTP 响应，包括 `http_version`、`status`、`reason`、`headers`、`body` 和可选的 `truncated` 标记。响应体最多 16 KiB；没有收到响应时不含 `status`。超时时间取已配置上游超时与 60 秒中的较小值。两种测试都需要写权限；模型测试会真实请求服务商并可能产生少量费用。

Anthropic 测试同时在 `x-api-key` 和 `Authorization: Bearer` 中发送密钥；上游请求头未设置 `anthropic-version` 时，默认使用 `2023-06-01`。Chat Completions 和 Responses 测试只发送 Bearer 鉴权。`key_header` 不是 `authorization`，或者 `base_url` 含 `{api_key}` 时，按[凭证](../gateway/07-upstreams.md#credentials)中的规则发送；返回的 `url` 保留 `{api_key}` 占位符。

两种测试对服务商响应的判断不同：

| 测试 | 服务商的响应 | `reason` | `ok` |
| --- | --- | --- | --- |
| 连接 | 上游是完整请求地址（`append_endpoint_path` 为 `false`），没有模型列表。网关不发请求，请改用模型测试。 | `exact_endpoint_untestable` | `false` |
| 模型 | Coding Plan 上游尚未授权网关使用。网关不发请求。 | `coding_plan_blocked` | `false` |
| 两者 | 没有可用的 API Key：没有保存，或者上游要求每个客户端自带。网关不发请求。 | `upstream_key_missing` | `false` |
| 连接 | 2xx，响应是带 `data` 列表的 JSON 对象 | `passed` | `true` |
| 连接 | 其他 2xx：不是合法 JSON、响应体超过 16 KiB、没有 `data` 列表，或带 `error` 字段 | `upstream_key_rejected` | `false` |
| 连接 | 404 或 405：服务商不提供模型列表，API Key 未经验证 | `models_unavailable` | `true` |
| 连接 | 429：地址有响应，但 API Key 未经验证 | `rate_limited` | `true` |
| 模型 | 2xx，响应符合已配置的模型协议 | `passed` | `true` |
| 模型 | 其他 2xx：不是合法 JSON、响应体超过 16 KiB，或不符合已配置的协议 | `invalid_response` | `false` |
| 模型 | 429 | `rate_limited` | `false` |
| 模型 | 404 或 405 | `model_request_rejected` | `false` |
| 两者 | 400 或 422 | `model_request_rejected` | `false` |
| 两者 | 401 或 403 | `upstream_key_rejected` | `false` |
| 两者 | 网络错误、超时、重定向、5xx 或其他状态码 | `upstream_unavailable` | `false` |

连接测试返回 `passed`，说明地址可达，已存凭证也有效。`models_unavailable` 和 `rate_limited` 只能说明地址可达，Studio 显示为中性色的 **连接可用**；要验证 API Key，请再做一次模型测试。Coding Plan 授权只拦截模型测试，未授权的 Coding Plan 上游照样可以做连接测试。模型测试通过说明所选模型完成了本次请求，不代表其他模型也可用，也不会授权使用 Coding Plan。

### 导入 VLM 配置 {#import-vlm-settings}

`POST /gateway/admin/upstreams/import-vlm` 把 OpenViking Server 的 VLM 凭证导入为所选账号的上游，只在嵌入部署中可用；独立网关返回 404 `vlm_import_unavailable`。

每个导入的上游 ID 固定为 `vlm-<credential id>`，重复导入不会产生重复的上游。再次导入时，来自服务端的字段（`base_url`、`api_key`、`headers`、`protocol`、`vendor`、`coding_plan`、`append_endpoint_path`、`key_header`）按服务端配置刷新，其他字段保留在网关里改过的值，包括 `name`、`enabled`、`priority`、`models`、`aliases`、`context_windows` 和 `allow_coding_plan`。路径中含 `coding` 或 `plan` 段的上游会被标为 Coding Plan，在设置 `allow_coding_plan` 之前归入 `needs_confirmation`。

```json
{
  "imported": [{"id": "vlm-default", "name": "default"}],
  "needs_confirmation": [{"id": "vlm-coding", "name": "coding"}],
  "skipped": [{"id": "vlm-codex", "reason": "openai_codex"}]
}
```

| 跳过原因 `reason` | 说明 |
| --- | --- |
| `openai_codex` | Codex OAuth 令牌会定期刷新，网关无法长期使用。 |
| `litellm_no_api_base` | LiteLLM 凭证没有 `api_base`。在 OpenViking 配置中补上后重新导入。 |
| `litellm_unsupported_route` | LiteLLM 路由不使用 OpenAI Chat Completions API，例如 `gemini/`、`bedrock/` 或 `vertex_ai/`。请手动把这个服务商添加为上游。 |
| `invalid_configuration` | 凭证构不成有效的上游，例如 base URL 无效。 |

## 网关密钥 {#gateway-keys}

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/gateway/admin/keys` | 列出调用方可见的密钥，不含密钥本身 |
| GET | `/gateway/admin/keys/{identifier}` | 读取单个可见密钥 |
| POST | `/gateway/admin/keys` | 签发密钥，响应中的 `key` 是唯一一次返回的完整密钥 |
| DELETE | `/gateway/admin/keys/{identifier}` | 吊销密钥，保留用户数据 |
| POST | `/gateway/admin/keys/{identifier}/capture/reset` | 让一段对话重新开始保存 |
| DELETE | `/gateway/admin/users/{user_id}/data` | admin 或 root：吊销该用户的全部密钥，删除其对话状态和日志 |
| DELETE | `/gateway/admin/account/data` | root：删除账号的全部网关对象、密钥、对话状态和日志 |

签发请求体：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 显示名称。 |
| `policy_id` | 是 | 使用的上下文配置。 |
| `upstream_ids` | 是 | 可用的上游，至少一个。 |
| `models` | 否 | 允许的模型名（客户端发送的名称，包括别名）；空列表表示不限。 |
| `user_id` | 否 | 密钥归属的 OpenViking 用户，默认是调用方自己。root 不是账号里的用户，必须指定用户（或提供 `openviking_key`）。user 只能填自己。 |
| `openviking_key` | 否 | 该用户的 OpenViking API 密钥。通常不用填，见下文。 |

网关需要拿到目标用户的 OpenViking API 密钥，之后以这个用户的身份检索和保存记忆：

- 给自己签发时，直接使用调用方的密钥（user 或 admin）。
- admin 或 root 给别人签发时，网关用调用方的凭证向 OpenViking 读取该用户的密钥。OpenViking 只保存密钥哈希时读不出来，返回 409 `user_key_unreadable`，这时要在 `openviking_key` 中提供该用户的密钥。
- 网关会向 OpenViking 核实这把密钥：它必须属于所选账号；提供了 `user_id` 时，还必须属于这个用户。
- root API 密钥不能绑定到网关密钥，本地 dev 的 `default/default` 例外。

```bash
curl -X POST "$SERVER_URL/gateway/admin/keys?account_id=team" \
  -H "X-API-Key: $OPENVIKING_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Alice laptop","user_id":"alice","policy_id":"default","upstream_ids":["openai"]}'
```

```json
{
  "id": "3f9c…",
  "name": "Alice laptop",
  "user_id": "alice",
  "policy_id": "default",
  "upstream_ids": ["openai"],
  "models": [],
  "prefix": "ovgw_Xk2pQ7a",
  "created_at": 1760000000.0,
  "revision": 1,
  "key": "ovgw_Xk2pQ7a…"
}
```

立即保存响应中的 `key`，网关只保留它的哈希。`GET` 返回同样的字段，但没有 `key`。网关密钥不能修改，没有对应的 `PUT`；要换上下文配置、上游、模型或用户，就签发新密钥，再吊销旧的。

用户的 OpenViking API 密钥重新生成或用户被删除后，绑定它的网关密钥会失效并被删除，用户数据保留，见[网关密钥](../gateway/09-gateway-keys.md#gateway-keys)。

**重置保存。** 请求体为 `{"session":"<会话请求头的值或日志中的 session>","protocol":"chat"}`，`protocol` 取 `chat`、`anthropic` 或 `responses`。成功返回 `{"status":"ready","message":"…"}`，网关会在这段对话的下一个请求里，把完整历史写入一个新的 OpenViking 会话。找不到这段对话时返回 404 `not_found`。

**删除数据。** 两个删除接口会清掉网关里尚未保存的对话内容和日志，不影响已经存入 OpenViking 的会话和记忆。保留期和其他删除途径见[安全与数据](../gateway/11-security-and-data.md)。

## 错误 {#errors}

管理接口的错误响应带稳定的顶层 `reason`，以及一句简短的英文说明 `detail`；部分错误另有附加字段，例如：

```json
{"reason": "in_use", "detail": "Gateway keys still use this object; revoke them first.", "key_count": 2}
```

请按 `reason` 判断，不要匹配 `detail` 文本。服务端没有开启网关时，这组路径不存在，返回 404；Studio 把这种情况显示为 `not_enabled`。

| HTTP | `reason` | 含义和处理 |
| --- | --- | --- |
| 401 | `management_key_invalid` | 管理密钥不正确。向部署者确认当前的管理密钥，或者见[忘记网关管理密钥](../guides/22-gateway-operations.md#lost-the-management-key)。 |
| 403 | `management_key_required` | 已设置管理密钥，修改上游或上下文配置时必须带上它。 |
| 401 | `openviking_http_401` | OpenViking 拒绝了 API 密钥。换用当前有效的密钥。 |
| 401 | `openviking_identity_missing` | OpenViking 没有返回这把密钥对应的用户。 |
| 403 | `trusted_mode_unsupported` | OpenViking 使用 trusted 认证。改用 `api_key` 认证。 |
| 403 | `other_account` | 密钥或请求的账号不在调用方的权限范围内。 |
| 403 | `forbidden` | 操作超出调用方的角色或用户范围。 |
| 403 | `root_key_not_allowed` | root API 密钥不能绑定到网关密钥。改用某个用户的 API 密钥。 |
| 409 | `user_key_unreadable` | OpenViking 只保存了该用户密钥的哈希。在 `openviking_key` 中提供该用户的密钥。 |
| 409 | `in_use` | 还有 `key_count` 个网关密钥在用这个对象。 |
| 400 | `unknown_user`、`unknown_policy`、`unknown_upstream` | 指定的用户、上下文配置或上游不存在。 |
| 400 | `user_required` | root 签发密钥时没有提供 `user_id` 或 `openviking_key`。指定密钥归属的用户。 |
| 404 | `not_found` | 对象不存在，或者调用方看不到它。 |
| 422 | `invalid_configuration` | 请求体或 Base URL 不合法。错误信息不会回显提交的凭证。 |
| 403 | `vlm_import_forbidden` | 调用方没有导入权限，以 `/access` 的 `can_import_vlm` 为准。 |
| 404 | `vlm_import_unavailable` | 独立网关读不到服务端的模型配置，请手动添加上游。 |
| 503 | `openviking_unavailable`、`openviking_unreachable` | 网关连不上 OpenViking。检查 OpenViking 是否运行；独立网关还要检查 `openviking_url`（`gateway.conf` 或 `OPENVIKING_GATEWAY_OPENVIKING_URL`）。 |
| 503 | `openviking_version_mismatch` | 仅独立网关：OpenViking Server 低于要求的版本，响应中的 `required_version` 给出最低版本。 |

OpenViking 返回的其他 HTTP 错误记为 `openviking_http_<status>`；网关自身的通用 HTTP 错误记为 `gateway_http_<status>`，例如对不支持的方法返回 405 `gateway_http_405`。

模型客户端收到的 401 不属于管理 API。原因可能是网关密钥缺失、错误或已吊销，也可能是它绑定的 OpenViking 密钥已经失效，排查方法见[客户端收到网关返回的错误](../gateway/12-troubleshooting.md#clients-get-an-error-from-the-gateway)。
