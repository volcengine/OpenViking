---
description: 查询 OpenViking 网关的配置字段、环境变量、上下文配置设置和上游设置。
---

# OpenViking 网关配置参考

本页供修改 `ov.conf`、`gateway.conf` 或环境变量的运维人员查阅，也方便把 Studio 中的名称对应到管理 API 的字段名。每张表都列出名称、默认值和可选值；设置的作用和什么时候需要改，见各节链接的功能页面。

## 配置放在哪里 {#configuration-reference}

嵌入网关和独立网关的字段大部分相同，区别在于写在哪个文件里、用什么命令生成。只有一种部署形态使用的字段，在[网关字段](#gateway-fields)的“适用于”一列中标出。

| | 嵌入 | 独立 |
| --- | --- | --- |
| 文件 | `ov.conf` 的 `gateway` 段 | `gateway.conf` 的根层，没有 `gateway` 外层 |
| 生成命令 | `openviking-server init` | `openviking-gateway init` |
| 读取哪个文件 | Server 自己的 `ov.conf` | `--config` 或 `OPENVIKING_GATEWAY_CONFIG_FILE` 指定的文件，且文件必须存在；两者都没设置时，读取 `~/.openviking/gateway.conf` 和 `/etc/openviking/gateway.conf` 中第一个存在的文件 |
| host、port 和 workers | 跟随 Server | 由 `host`、`port`、`workers` 字段设置 |

`OPENVIKING_CONFIG_FILE` 只用来选择 Server 的配置，不会让独立网关读取它指向的文件。

## 网关字段 {#gateway-fields}

| 字段 | 适用于 | 默认值 | 可选值 | 含义 |
| --- | --- | --- | --- | --- |
| `enabled` | 嵌入 | `false` | | 在 Server 中开启网关 |
| `openviking_url` | 独立 | —（必填） | HTTP(S) URL | OpenViking Server 的地址 |
| `host` | 独立 | `127.0.0.1` | | 监听地址 |
| `port` | 独立 | `1935` | 1–65,535 | 监听端口 |
| `workers` | 独立 | `1` | 1–64 | worker 进程数 |
| `keep_alive_seconds` | 独立 | `75` | 大于 0 | 两次请求之间保留空闲 HTTP 连接的秒数；应大于负载均衡器的后端空闲超时 |
| `public_url` | 两者 | 空 | HTTP(S) URL，不带 `/gateway` | 客户端访问的对外地址，`/gateway` 由网关自己加。为空时，嵌入网关沿用 Server 的对外地址；两者都没有时，接入指南使用请求来源地址，MCP 文件上传和下载也不可用（`mcp_file_links` 为 `direct` 时除外）。见[对外地址](../guides/22-gateway-operations.md#public-address) |
| `mcp_file_links` | 两者 | `proxy` | `proxy`、`direct` | MCP 上传和下载工具返回的链接如何交给客户端。`proxy` 改写为 `public_url` 加 `/gateway/uploads` 或 `/gateway/downloads`，需要设置 `public_url`。`direct` 原样透传 OpenViking 的链接，需要 Server 的 `OPENVIKING_PUBLIC_BASE_URL`（或 `server.public_base_url`）是客户端能访问的地址 |
| `storage_path` | 两者 | `~/.openviking/gateway` | | 本地存储目录 |
| `storage` | 两者 | 两个后端均为 `sqlite` | 见[存储后端](#storage) | 分别选择后端，共用 Redis 连接配置 |
| `encryption` | 独立 | 不设置（明文） | 字段同 `ov.conf` 的 `encryption` 段 | 加密本地保存的数据，见[加密](../guides/22-gateway-operations.md#encryption)；嵌入网关沿用 Server 的设置 |
| `management_api` | 两者 | `true` | `true`、`false` | 注册网关管理路由；关闭时也移除独立网关的 OpenViking 管理代理和 Studio，模型路由和 `/health` 继续可用 |
| `management_key` | 两者 | 空 | 至少 32 个字符 | 可选的[网关管理密钥](../guides/22-gateway-operations.md#gateway-management-key) |
| `openviking_keys` | 两者 | `false` | `true`、`false` | 允许客户端用用户的 OpenViking API 密钥代替网关密钥，使用账号的默认上下文配置和全部已启用的上游，见[直接使用 OpenViking API 密钥](09-gateway-keys.md#openviking-keys) |
| `tenancy` | 两者 | `account` | `account`、`key` | 按账号或按密钥隔离 OpenViking 密钥请求；`key` 要求 `management_api: false` |
| `default_upstreams` | 两者 | `{}` | 稳定 ID 到[上游设置](#upstream-settings)的对象映射 | 账号没有存储上游时供 OpenViking 密钥使用，见[部署默认上游](07-upstreams.md#deployment-default-upstreams) |
| `default_profile` | 两者 | 内置上下文配置默认值 | [上下文配置设置](#context-profile-settings) | 账号没有 ID 为 `default` 的配置时使用，也是嵌入式 dev 模式初始化写入的配置 |
| `min_server_version` | 两者 | `0.4.16` | 版本号 | 独立网关要求的 OpenViking Server 最低版本 |
| `session_ttl_seconds` | 两者 | `2592000`（30 天） | 3600 及以上 | 对话闲置多少秒后过期；也决定缓存的 OpenViking 密钥及其用户记录的固定寿命 |
| `response_ttl_seconds` | 两者 | `2592000`（30 天） | 60 及以上 | Responses 响应与上游的对应关系保留多少秒 |
| `request_log_sink` | 两者 | `store` | `store`, `stdout` | 将请求和后台保存日志写入存储，或以每条一行 JSON 写到 stdout 并立即刷新；见[观测](../guides/22-gateway-operations.md#monitoring) |
| `log_retention_days` | 两者 | `30` | 1 及以上 | 存储中的请求日志保留天数；stdout 的保留期限由日志采集系统管理 |
| `max_body_bytes` | 两者 | `33554432`（32 MiB） | 1,024 及以上 | 请求和上传的大小上限，单位为字节 |
| `upstream_timeout_seconds` | 两者 | `600` | 大于 0 | 服务商请求超时 |
| `health_interval_seconds` | 两者 | `60` | 1 及以上 | 检查 OpenViking 状态的间隔秒数 |
| `capture_concurrency` | 两者 | `1` | 1 及以上 | 每个进程并发投递的会话数；停机时停止新 claim，最多等待在途投递 100 秒 |
| `client_rules` | 两者 | `{}`（只用内置规则） | 见[客户端规则](#client-rules) | 在网关内置的辅助请求和客户端插入消息识别规则上增删条目 |

例如，独立网关的 `gateway.conf` 可以在尚未配置任何账号时路由 OpenViking 密钥：

```json
{
  "openviking_url": "http://openviking:1933",
  "openviking_keys": true,
  "default_upstreams": {
    "ark-chat": {
      "name": "Ark Chat",
      "protocol": "chat",
      "base_url": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "$ARK_API_KEY",
      "models": ["your-model-id"]
    }
  },
  "default_profile": {"max_tokens": 2400}
}
```

## 存储后端 {#storage}

默认使用本地 SQLite，无需外部服务。`storage.kernel` 和 `storage.management` 分别选择 `sqlite` 或 `redis`；Redis 后端共用一个客户端及 `storage.redis` 连接配置。使用 Redis 时安装 `pip install "openviking[expiry]"`，官方镜像已经包含依赖。网关 CI 覆盖 Redis 单机和 Cluster；Sentinel 配置可以通过校验，但不在网关 CI 覆盖范围内。

```json
{
  "storage": {
    "kernel": "redis",
    "management": "redis",
    "key_prefix": "ovgw:",
    "redis": {
      "mode": "standalone",
      "endpoints": ["rediss://redis.example.com:6379"],
      "username": "gateway",
      "password_env": "OPENVIKING_GATEWAY_REDIS_PASSWORD",
      "pool_size": 32,
      "command_timeout_ms": 5000
    }
  },
  "session_ttl_seconds": 604800
}
```

`OPENVIKING_GATEWAY_STORAGE` 接受相同 `storage` 对象的 JSON。密码通过指定的环境变量注入，例如来自 Kubernetes Secret。`key_prefix` 默认为 `ovgw:`，不能包含 `{`、`}`、`*`、`?`、`[`、`]` 或 `\`；所有键都以该前缀加 `v1:` 开头。不同部署使用不同前缀。

`storage.redis` 复用 OpenViking 的 `RedisCacheConfig`：`mode`、`endpoints`、`username`、`password_env`、旧式 `password`、`db`、`pool_size`、`connect_timeout_ms`、`command_timeout_ms`、`default_ttl_seconds`、`tls_insecure_skip_verify`，以及 Sentinel 字段 `master_name`、`sentinel_username`、`sentinel_password_env`、`sentinel_password`。为容纳较大的对话值，网关把命令超时默认值从 20 改为 5000 毫秒；`default_ttl_seconds` 不控制网关保留期限。URL 只包含协议、主机和端口，凭据和数据库使用专门字段。Cluster 使用 `mode: "cluster"`、多个种子地址和 `db: 0`。TLS 由 `rediss://` 协议决定；私有 CA 通过证书卷和指向证书的 `SSL_CERT_FILE` 配置（Helm 使用 `extraVolumes`、`extraVolumeMounts`）。

为网关单独准备 Redis 实例，使用 `volatile-lru` 驱逐策略并启用 AOF；`redis.conf` 设置和监控项见 [Redis 内存策略](../guides/22-gateway-operations.md#redis-memory-policy)。

替换密码和前缀后，运行以下命令创建 ACL 用户。Cluster 必须在**每个节点**分别执行，因为 ACL 不在节点之间同步：

```bash
redis-cli ACL SETUSER gateway reset on '>replace-with-secret' '~ovgw:*' \
  +@read +@write +@scripting +@connection +@transaction +ping +time +scan +unlink
```

Cluster 还需授予 `'+cluster|slots' '+cluster|shards'`。Lua 脚本需要 `+time`；CI 会验证这些权限及前缀隔离。应用命令遇到连接错误不会自动重试，因为失败的写入可能已经到达 Redis。

## 环境变量 {#environment-variables}

每个网关字段都能用环境变量设置，变量名是 `OPENVIKING_GATEWAY_` 加上大写的字段名，两种部署形态都适用：

```bash
OPENVIKING_GATEWAY_PUBLIC_URL=https://ov.example.com
OPENVIKING_GATEWAY_MANAGEMENT_KEY='<management-key>'
```

- **环境变量优先于配置文件。** 非空的环境变量覆盖文件中的值，值为空等于没有设置。
- 以 `tcp://` 或 `udp://` 开头的值会被忽略。这样，Kubernetes 为名为 `openviking-gateway` 的 Service 自动注入的变量（例如 `OPENVIKING_GATEWAY_PORT=tcp://…`）不会覆盖文件。
- 文件中的值也可以写成 `"$VAR"`，从环境变量中取值。
- `encryption` 块只能写在文件里。
- `client_rules`、`storage`、`default_upstreams` 和 `default_profile` 用 JSON 传整段配置，例如 `OPENVIKING_GATEWAY_CLIENT_RULES='{"auxiliary_patterns":{"add":["You are the Acme title writer"]}}'` 或 `OPENVIKING_GATEWAY_DEFAULT_PROFILE='{"max_tokens":2400}'`。
- 容器通常不需要配置文件，用环境变量设置需要的字段即可。

## 校验 {#validation}

| 对象 | 规则 |
| --- | --- |
| `openviking_url` 和 `public_url` | 必须是 HTTP(S)，不能含凭证、查询参数或 fragment |
| 上游的 `base_url` | 规则不同，见[接口](07-upstreams.md#endpoint) |
| `management_key` | 解析 `$VAR` 引用后至少 32 个字符；`$VAR` 没能解析时校验失败 |
| `tenancy` | `key` 要求 `management_api: false`；`openviking_keys` 关闭时此字段不生效 |
| `client_rules` | 新增的模式必须是合法的 Python 正则表达式；新增的标签名以英文字母开头，只能包含英文字母、数字、`_`、`.`、`:` 和 `-` |
| 未知字段 | 校验失败 |
| 已删除的字段 | 忽略，启动时打印警告：`encryption_key_env`、`session_ttl_days`（已由 `session_ttl_seconds` 取代），以及 `ov.conf` 中[迁移](../guides/22-gateway-operations.md#migration)列出的 v0.5 字段 |

## 客户端规则 {#client-rules}

网关靠固定文本区分[辅助请求](10-overview-and-requests.md#request-types)、客户端代用户插入的消息和真正的用户消息，这些文本列在内置的 [`client-rules.json`](https://github.com/volcengine/OpenViking/blob/main/openviking_gateway/client-rules.json) 里。如果某个客户端的标题、摘要请求在请求日志里显示为**新消息**，或者真实消息被当成了辅助请求，可以在 `client_rules` 里增删条目：

```json
{
  "gateway": {
    "enabled": true,
    "client_rules": {
      "auxiliary_patterns": {
        "add": ["You are the Acme title writer"],
        "remove": ["Create a short.{0,40}title"]
      },
      "noise_tags": { "add": ["acme-context"] }
    }
  }
}
```

在 `gateway.conf` 里，`client_rules` 直接写在根层级，不需要外面的 `gateway`。

| 列表 | 条目 | 命中后的效果 |
| --- | --- | --- |
| `auxiliary_patterns` | 正则表达式，不区分大小写 | 分别与 Messages 的 `system` 字段、Responses 的 `instructions` 和最后一条用户消息匹配，命中的请求算作**辅助请求**。Chat Completions `messages` 和 Responses `input` 里的 system、developer 消息不参与匹配，这类客户端要匹配用户消息里的文本。 |
| `noise_patterns` | 正则表达式，区分大小写 | 网关读取对话时忽略这些文本：不参与请求分类和记忆检索，也不写入保存的消息和工具结果，但仍原样发给模型。删完为空的用户消息不算新用户消息，请求按同一轮的延续处理。 |
| `noise_tags` | 标签名 | `<tag …>…</tag>` 块（不区分大小写），处理方式同 `noise_patterns`。 |
| `plugin_tags` | 标签名 | 系统提示词或客户端消息中出现 `<tag`，表示客户端里运行着 OpenViking 记忆插件。该对话此后网关只做转发：不加记忆、不提供 OpenViking 工具、不保存（见[两者同时使用时](../guides/15-gateway.md#when-both-are-in-use)）。只添加插件自己写入的标签。 |

- **`add` 追加到内置列表，`remove` 从内置列表中去掉条目。** `remove` 的条目要和 `client-rules.json` 里的写法完全一致。没有匹配到任何条目的 `remove`（例如升级后内置条目改了写法）会被忽略，启动时打印警告。内置列表不能整体替换，这样后续版本新增的条目仍然生效。
- **只匹配客户端自己写的文本。** 用客户端自己提示词里的整句，不要用短语。用户消息一旦命中，这一整轮都算辅助请求，不检索也不保存；如果模式还能命中主对话的系统提示词，对话里的每个请求都会这样。噪声模式要锚定整条消息（JSON 里写作 `"\\A…\\Z"`），避免从真实消息里删掉文字。
- **修改在启动时生效。** 改完后重启 Server 或独立网关。

## 上下文配置的设置 {#context-profile-settings}

在 Studio 中：**上下文配置** → 打开一份配置。下面几张表按编辑器的分区排列，先写 Studio 中的名称，再写管理 API 中的名称（管理 API 把上下文配置称为 `policies`）。**名称**（`name`，默认 `Default`）是配置的显示名称；各分区的作用见[上下文配置](08-context-profiles.md#context-profiles)。**允许客户端覆盖设置**（`client_overrides`，默认 `true`）允许客户端用 `X-OpenViking-Context` 请求头为新对话修改其余所有设置，见[允许客户端覆盖设置](08-context-profiles.md#client-overrides)。

### 召回记忆 {#profile-recall}

作用见[召回记忆](08-context-profiles.md#recall-memory)。

| Studio 名称 | API 名称 | 默认值 | 取值范围 | 含义 |
| --- | --- | --- | --- | --- |
| 召回记忆 | `recall` | `true` | | 为每条新的用户消息检索记忆。 |
| 会话开头提供用户画像 | `profile` | `true` | | 新对话开始时提供画像，独立于召回。 |
| 开头内容预算 | `profile_max_tokens` | `4000` | 0–32,000 | 画像和目录的独立预算；0 表示省略。目录需要提供 OpenViking 的 `read` 工具（`openviking_read`）。 |
| 显示召回摘要 | `show_recall` | `false` | | 在回复开头说明 OpenViking 补充了什么，或召回失败的原因。模型看不到。 |
| 检索范围 | `context_types` | `memory`、`resource`、`skill` | 开启召回时至少一个（仅 Studio 校验） | 检索哪些内容：记忆、资源、技能。 |
| 单条消息预算 | `max_tokens` | `1600` | 64–32,000 | 一条消息最多补充的 token 数。 |
| 单个上下文窗口预算 | `session_max_tokens` | `30000` | 0 及以上 | 一个上下文窗口内最多补充的 token 数，每次压缩后重新计算；0 表示关闭召回。 |
| 相关度阈值 | `score_threshold` | `0.35` | 0–1 | 接受的最低相关度分数。 |
| 超时时间 | `recall_timeout` | `2`（秒） | 大于 0，最多 30 | 检索最多等多久，超时后消息不带记忆继续发送。 |
| 检索文本长度 | `query_max_chars` | `8000` | 3–32,000 | 取消息的多少个字符作为检索文本。 |
| 按分类限量 | `quotas` | `{}`（关闭） | 键：`events`、`entities`、`preferences`、`experiences`、`resources`、`skills`；设置时至少一项大于 0（仅 Studio 校验） | 每个分类最多取的条数；只检索大于 0 的分类。 |

管理 API 保存 `context_types` 和 `quotas` 的键时不做校验。`quotas` 里出现未知的键时，OpenViking 会拒绝检索请求，导致每次召回都失败。

### 保存对话 {#profile-saving}

作用见[保存对话](08-context-profiles.md#save-conversations)。

| Studio 名称 | API 名称 | 默认值 | 取值范围 | 含义 |
| --- | --- | --- | --- | --- |
| 保存对话 | `capture` | `true` | | 把完成的轮次保存到 OpenViking。 |
| 最新回复等待时长 | `idle_seconds` | `600`（秒） | 1 及以上 | 停顿多久之后保存最后一轮并提交会话。 |
| 提交阈值 | `commit_tokens` | `20000` | 1 及以上 | 会话中待提交的 token 数达到这个值时提交。 |
| 保留最近消息 | `keep_recent_messages` | `10` | 0–1,000 | 提交后会话中保留的消息数。 |

### 长对话 {#profile-long-conversations}

作用见[网关中的长对话](05-long-conversations.md#long-conversations)。

| Studio 名称 | API 名称 | 默认值 | 取值范围 | 含义 |
| --- | --- | --- | --- | --- |
| 压缩 | `compaction` | `true` | | 接近上下文窗口时，用摘要替换对话较早的部分。 |
| 压缩时机 | `compaction_threshold` | `0.9` | 0.5–0.98 | 上下文窗口用到多大比例时压缩。 |
| 摘要长度上限 | `summary_max_tokens` | `8000` | 1,000–32,000 | 一份摘要最多多少 token。 |
| 默认上下文窗口 | `context_window` | 未设置（按 1,000,000 计算） | 1,024 及以上 | 上游没有列出该模型时使用的窗口，见[设置模型的上下文窗口](05-long-conversations.md#context-window)。 |
| Agent 自管上下文窗口 | `agent_windows` | `false` | | 实验性。让模型自己开启新的上下文窗口；需要 OpenViking 工具。 |
| 软提醒时机 | `window_soft_ratio` | `0.7` | 0.3–0.95，须低于 `window_hard_ratio` | 窗口用到这个比例时，提醒模型尽快开启新窗口。 |
| 硬提醒时机 | `window_hard_ratio` | `0.85` | 0.4–0.97 | 窗口用到这个比例时，要求模型立即开启新窗口。 |

### OpenViking 工具 {#profile-tools}

作用见[选择工具](04-openviking-tools.md#choose-tools)和[上限](04-openviking-tools.md#limits)。

| Studio 名称 | API 名称 | 默认值 | 取值范围 | 含义 |
| --- | --- | --- | --- | --- |
| OpenViking 工具 | `gateway_tools` | `true` | | 为 Chat Completions、完整历史的 Responses 和 Anthropic Messages 提供 OpenViking 工具。 |
| 工具（未勾选的项） | `disabled_tools` | 会修改数据的 8 个工具 | OpenViking 工具原名，不带 `openviking_` | 新对话不提供这些工具，其余可用工具都提供，包括以后新增的。 |
| 显示工具调用 | `show_tool_calls` | `true` | | 每次 OpenViking 工具调用都在回复里加一行提示。 |
| 每次请求的工具轮数 | `tool_max_rounds` | `null`（不限） | 1 及以上 | 工具调用达到这个轮数后，拒绝继续调用 OpenViking 工具。 |
| 单次调用超时 | `tool_timeout_seconds` | `30`（秒） | 大于 0，最多 120 | 超时的调用向模型返回错误。 |
| 结果大小上限 | `tool_result_bytes` | `65536`（字节） | 1,024–1,048,576 | 超出部分在交给模型前截掉。 |
| 总时长 | `tool_total_seconds` | `null`（不限） | 大于 0 | 超过这个秒数模型仍在调用工具时，请求失败。 |
| Token 预算 | `tool_total_tokens` | `null`（不限） | 1,024 及以上 | 工具调用和结果在一次请求中最多增加的估算 token 数，用完后拒绝继续调用 OpenViking 工具。 |

API 请求省略 `disabled_tools` 时使用默认列表；列表为空且 `gateway_tools` 开启时，提供全部可用工具。

## 上游设置 {#upstream-settings}

在 Studio 中：**上游** → 打开一个上游。下面几张表按编辑器的四个分区排列；各字段的作用见[上游](07-upstreams.md#upstreams)。

### 接口 {#upstream-endpoint}

| Studio 名称 | API 名称 | 默认值 | 可选值 | 含义 |
| --- | --- | --- | --- | --- |
| 名称 | `name` | —（必填） | | 显示名称。 |
| 服务商 | `vendor` | `generic` | `generic`、`anthropic`、`openai`、`deepseek`、`ark`（火山方舟）、`byteplus`（BytePlus 方舟） | Studio 只提供服务商支持的协议，API 接受任意组合。 |
| 协议 | `protocol` | —（必填） | `anthropic`（Anthropic Messages）、`chat`（Chat Completions）、`responses`（Responses） | 这个上游使用的 API。 |
| Base URL | `base_url` | —（必填） | HTTP(S) URL | 服务商地址；默认地址见[什么是上游](07-upstreams.md#upstreams)，拼接规则见[接口](07-upstreams.md#endpoint)。 |
| 拼接协议接口路径 | `append_endpoint_path` | `true` | | 拼接所选协议的接口路径；关闭时把 `base_url` 直接作为完整请求地址。 |

### 凭证 {#upstream-credentials}

| Studio 名称 | API 名称 | 默认值 | 可选值 | 含义 |
| --- | --- | --- | --- | --- |
| API Key 由谁提供 | `auth_mode` | `managed` | `managed`（由网关保管 API Key）、`passthrough`（每个客户端自带 API Key） | 见[凭证](07-upstreams.md#credentials)。 |
| 密钥发送方式 | `key_header` | `authorization` | `authorization`（Bearer）、`api-key`、`x-api-key` | `base_url` 含 `{api_key}` 时不生效。 |
| API Key | `api_key` | 空 | | 只写，见[凭证](07-upstreams.md#credentials)。 |
| 额外请求头 | `headers` | `{}` | 请求头名称 → 取值 | 只写，见[凭证](07-upstreams.md#credentials)。 |
| 这是 Coding Plan 或订阅密钥 | `coding_plan` | `false` | | 除非另外允许，否则拒绝发往这个上游的请求。 |
| 允许通过网关使用这个套餐 | `allow_coding_plan` | `false` | | 允许使用被标记为 Coding Plan 的密钥。 |

### 模型 {#upstream-models}

| Studio 名称 | API 名称 | 默认值 | 可选值 | 含义 |
| --- | --- | --- | --- | --- |
| 模型 | `models` | `[]` | 模型名 | 提供的模型名；留空表示接受任何名称。 |
| 模型别名 | `aliases` | `{}` | 客户端使用的名称 → 发给上游的模型 | |
| 上下文窗口 | `context_windows` | `{}` | 模型 → token 数，每项至少 1,024 | 各模型的上下文窗口，见[设置模型的上下文窗口](05-long-conversations.md#context-window)。 |

### 路由 {#upstream-routing}

| Studio 名称 | API 名称 | 默认值 | 可选值 | 含义 |
| --- | --- | --- | --- | --- |
| 优先级 | `priority` | `0` | 整数 | 数值越大，新对话越优先使用。 |
| 启用 | `enabled` | `true` | | 停用的上游不接收请求。 |
| 允许 OpenViking 工具 | `allow_gateway_tools` | `true` | | 能否通过这个上游提供 OpenViking 工具。 |
| 补全推理内容回传 | `replay_reasoning` | `null`（跟随服务商） | `null`、`true`、`false` | 把客户端丢掉的推理内容补回之前的回复。`null` 时 `deepseek`、`ark` 和 `byteplus` 开启，其他服务商关闭；`true` 或 `false` 覆盖服务商默认值。Studio 中开关与服务商默认值一致时保存为 `null`。 |
| 最小可缓存长度 | `cache_min_tokens` | `1024` | 0 及以上 | 仅火山方舟和 BytePlus 方舟；影响缓存资格的报告。 |

## 下一步 {#next-steps}

- [独立部署](../guides/22-gateway-operations.md#standalone-deployment)：生成 `gateway.conf`，单独运行网关。
- [在 Server 中启用](../guides/22-gateway-operations.md#enable-it-in-the-server)：在 `ov.conf` 中开启嵌入网关。
- [上下文配置](08-context-profiles.md)和[上游](07-upstreams.md)：用 Studio 的说法解释上面这些设置的作用。
- [管理 API](../api/25-gateway.md)：网关密钥的字段、全部管理操作、权限规则和错误原因。
