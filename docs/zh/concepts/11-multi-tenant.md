# 多租户

OpenViking 的多租户不是“为每个团队部署一套独立服务”，而是在同一个 OpenViking Server 内，用 `account`、`user`、`agent` 三层身份边界来隔离和共享数据。

它适合两类典型场景：

- 多个团队或客户共享一套 OpenViking 服务，但数据必须隔离
- 一个团队内的多个用户和多个 Agent 需要共享资源、隔离记忆

## 能做什么

启用多租户后，你可以：

- 用一个 OpenViking Server 服务多个团队、客户或应用
- 用 `account` 隔离不同团队的数据
- 在同一个 `account` 内共享 `resources`
- 用 `user` 隔离用户级记忆和会话
- 用 `agent_id` 进一步区分 agent 级记忆、技能和指令空间
- 用 ROOT / ADMIN / USER 角色分层管理权限
- 支持 OpenClaw 插件、Vikingbot、CLI、HTTP SDK 等不同接入方式

## 核心身份模型

### `account_id`

`account` 是最外层租户边界，可以理解为工作区、团队或客户空间。

- 不同 `account` 之间的数据默认完全隔离
- Root 用户可以创建、删除 `account`
- `resources`、`user`、`agent`、`session` 都落在某个 `account` 下

### `user_id`

`user` 是 account 内的用户边界。

- 用户记忆和用户会话按 `user_id` 隔离
- 普通 user 只能访问自己的 user space
- admin 可以管理本 account 下的用户

### `agent_id`

`agent_id` 用于区分 agent 级空间。

- Agent URI 形状由 account 级 namespace policy 决定
- `isolate_agent_scope_by_user = false` 时使用 `viking://agent/{agent_id}/...`
- `isolate_agent_scope_by_user = true` 时使用 `viking://agent/{agent_id}/user/{user_id}/...`

### 角色

| 角色 | 作用域 | 典型能力 |
|------|--------|----------|
| ROOT | 全局 | 创建/删除 account、跨租户访问、管理用户 |
| ADMIN | 单个 account | 管理本 account 的用户、重置 user key |
| USER | 单个 account | 访问自己的 user/agent/session 数据和 account 内共享资源 |

## 认证模式

OpenViking Server 支持两种多租户相关认证模式：

| 模式 | 配置 | 身份来源 | 适用场景 |
|------|------|----------|----------|
| `api_key` | `server.auth_mode = "api_key"` | Root key 或 user key | 标准部署方式 |
| `trusted` | `server.auth_mode = "trusted"` | 上游显式注入 `X-OpenViking-Account` / `X-OpenViking-User` | 受信网关后面 |

### `root_api_key` 的作用

配置 `server.root_api_key` 后，OpenViking 才进入正式多租户模式：

- Root key 用于管理 account 和 user
- User key 由 Admin API 生成，用于普通业务读写
- 服务端会从 user key 反解出 `account_id`、`user_id` 和角色

如果 `auth_mode = "api_key"` 且未配置 `root_api_key`，服务端会进入开发模式：

- 默认所有请求都被视为 ROOT
- 默认身份是 `default/default/default`
- 只允许绑定在 localhost 上使用

## 共享与隔离边界

### 逻辑层

| 数据类型 | 是否跨 account 共享 | account 内是否共享 | 默认隔离边界 |
|----------|---------------------|-------------------|--------------|
| `resources` | 否 | 是 | account |
| `user` | 否 | 否 | user |
| `agent` | 否 | 由 account 级 namespace policy 决定 | 默认 `agent` |
| `session` | 否 | 否 | user / session |

### 存储层

对用户来说，URI 仍然是统一的 `viking://...`：

```text
viking://resources/project-a/
viking://user/alice/memories/
viking://agent/91f3ab12cd34/memories/
```

但底层存储会自动带上 account 前缀：

```text
/local/{account_id}/resources/project-a/
/local/{account_id}/user/alice/memories/
/local/{account_id}/agent/91f3ab12cd34/memories/
```

因此多租户隔离不是靠“不同 URI 前缀”，而是靠请求上下文中的 `account_id`、`user_id`、`agent_id` 共同生效。

### 检索层

语义检索同样受租户约束：

- 非 ROOT 请求会自动按 `account_id` 过滤
- `resources` 会允许检索 account 内共享资源
- `memory` 和 `skill` 会进一步按当前 `user space` / `agent space` 过滤

这意味着“能搜到什么”与“能读到什么”保持一致，不会因为向量召回而越权。

## 标准使用流程

### 1. 启用多租户

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key"
  }
}
```

### 2. ROOT 创建工作区和首个管理员

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-root-key" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

### 3. ADMIN 或 ROOT 注册普通用户

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <admin-or-root-key>" \
  -d '{
    "user_id": "bob",
    "role": "user"
  }'
```

### 4. 普通业务访问优先使用 user key

常规读写、搜索、会话提交等请求，优先用 user key：

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <bob-user-key>" \
  -H "X-OpenViking-Agent: coding-agent"
```

这样服务端可以直接从 key 反解身份，无需额外传 `account` / `user`。

### 5. 只有 ROOT 访问租户级数据 API 时才显式带租户头

ROOT 访问 Admin API 不需要租户头，但访问 `ls`、`find`、`sessions` 这类租户级数据 API 时，必须显式指定目标租户：

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <root-key>" \
  -H "X-OpenViking-Account: acme" \
  -H "X-OpenViking-User: alice" \
  -H "X-OpenViking-Agent: coding-agent"
```

## 接入实践

### OpenClaw 插件 2.0：每个实例使用 user key

OpenClaw 插件当前的多租户实践是“插件侧只持有一个用户身份”：

- 远程模式配置 `baseUrl + apiKey + agent_prefix`
- `apiKey` 推荐配置为某个 user 的 user key
- 服务端从 user key 自动解析 `account_id` 和 `user_id`
- 插件显式传递 `X-OpenViking-Agent`

典型配置：

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.openviking.config.apiKey "<user-api-key>"
openclaw config set plugins.entries.openviking.config.agent_prefix "<agent-prefix>"
```

这种模式的特点：

- 接入简单，插件不需要管理 account/user 生命周期
- 最适合“一个 OpenClaw 实例对应一个 OpenViking 用户”的场景
- `agentId` 参与决定 agent 级空间，便于区分不同 OpenClaw 实例或不同 agent 角色
- 同一 account 内的 `resources` 可共享，`user` / `agent` memory 会按身份隔离

### OpenClaw 插件为何通常不配 `account` / `user`

因为在 `api_key` 模式下，user key 已经足够表达身份：

- `account`、`user` 由服务端从 key 反解
- 插件只需要额外告诉服务端当前的 `agentId`
- 插件内部会根据运行时身份去解析默认的 `user` / `agent` 记忆空间

如果给插件直接配置 root key，则普通租户数据 API 会缺少 `X-OpenViking-Account` / `X-OpenViking-User`，这不适合作为日常读写方式。

### Vikingbot：root key 代管用户身份

Vikingbot 当前的实践与 OpenClaw 插件不同，它更接近“平台代理多个终端用户”：

- bot 连接 OpenViking 时持有 root key
- bot 配置固定的 `account_id`
- bot 会在该 account 下自动注册用户
- bot 会缓存每个 user 的 user key，并尽量用对应 user key 去提交/检索 memory

相关配置示例：

```json
{
  "bot": {
    "ov_server": {
      "server_url": "http://127.0.0.1:1933",
      "root_api_key": "test",
      "account_id": "default",
      "admin_user_id": "default"
    }
  }
}
```

这种模式的特点：

- 适合一个 bot 服务承载多个聊天用户
- 同一 account 下所有用户共享 `resources`
- 用户记忆通过自动注册的 user 身份隔离
- bot 侧需要承担更多租户生命周期管理逻辑

## 什么时候选哪种实践

| 场景 | 推荐方式 |
|------|----------|
| 一个 OpenClaw 实例对应一个固定身份 | OpenClaw 插件 + user key |
| 一个网关/机器人服务承载很多最终用户 | Vikingbot + root key 代管用户 |
| 受信网关统一注入身份 | `trusted` 模式 |
| 单机本地体验、无需真正租户隔离 | 开发模式（无 `root_api_key`） |

## 常见误区

### 1. `root_api_key` 不是常规业务 key

Root key 主要用于：

- 创建/删除 account
- 注册用户
- 重置 key
- 运维和调试

正常业务请求优先使用 user key。

### 2. `agentId` 不决定 account

`agentId` 只决定 agent 级空间，不决定租户归属。

- account 边界由 `account_id` 决定
- user 边界由 `user_id` 决定
- agent 边界由 `agent_id` 决定

### 3. 不配置 `root_api_key` 不等于“单租户正式部署”

这只是开发模式：

- 默认全部请求以 ROOT 身份运行
- 不适合暴露到公网或团队共享环境

### 4. OpenClaw 插件和 Vikingbot 不是同一种租户实践

- OpenClaw 插件：更像“客户端拿到一个 user 身份后直接访问”
- Vikingbot：更像“平台代理多个用户，并代为申请和管理 user key”

## 相关文档

- [认证](../guides/04-authentication.md) - 认证模式、请求头和 key 规则
- [配置](../guides/01-configuration.md) - `root_api_key`、`auth_mode`、`agent_id`
- [管理员（多租户）](../api/08-admin.md) - Admin API 参考
- [API 概览](../api/01-overview.md) - CLI / HTTP 连接方式
- [数据加密](./10-encryption.md) - 多租户下的静态数据加密
- [多租户示例](../../../examples/multi_tenant/README.md) - 完整管理流程示例
- [OpenClaw 插件](../../../examples/openclaw-plugin/README_CN.md) - OpenClaw 的接入方式
- [Vikingbot](../../../bot/README_CN.md) - bot 的多用户接入方式
