# 多租户

OpenViking 的多租户不是“为每个团队部署一套独立服务”，而是在同一个 OpenViking Server 内，用 `account` 和 `user` 两层身份边界来隔离和共享数据。

它适合两类典型场景：

- 多个团队或客户共享一套 OpenViking 服务，但数据必须隔离
- 一个团队内的多个用户需要共享资源、隔离记忆

## 能做什么

启用多租户后，你可以：

- 用一个 OpenViking Server 服务多个团队、客户或应用
- 用 `account` 隔离不同团队的数据
- 在同一个 `account` 内共享 `resources`
- 用 `user` 隔离用户级记忆和会话
- 用 ROOT / ADMIN / USER 角色分层管理权限
- 支持 OpenClaw 插件、Vikingbot、CLI、HTTP SDK 等不同接入方式

## 核心身份模型

### `account_id`

`account` 是最外层租户边界，可以理解为工作区、团队或客户空间。

- 不同 `account` 之间的数据默认完全隔离
- Root 用户可以创建、删除 `account`
- `resources`、`user`、`session` 都落在某个 `account` 下

### `user_id`

`user` 是 account 内的用户边界。

- 用户记忆和用户会话按 `user_id` 隔离
- 普通 user 只能访问自己的 user space
- admin 可以管理本 account 下的用户

### 角色

| 角色 | 作用域 | 典型能力 |
|------|--------|----------|
| ROOT | 全局 | 创建/删除 account、跨租户访问、管理用户 |
| ADMIN | 单个 account | 管理本 account 的用户、重置 user key |
| USER | 单个 account | 访问自己的 user/peer/session 数据和 account 内共享资源 |

## 认证模式

OpenViking Server 支持两种多租户相关认证模式：

| 模式 | 配置 | 身份来源 | 适用场景 |
|------|------|----------|----------|
| `api_key` | `server.auth_mode = "api_key"` | Root key 或 user key | 标准部署方式 |
| `trusted` | `server.auth_mode = "trusted"` | 上游显式注入 `X-OpenViking-Account` / `X-OpenViking-User` | 受信网关后面 |

在 trusted 模式下，上游网关还可以断言 `X-OpenViking-Role: user` 或 `X-OpenViking-Role: admin`。角色断言要求服务端已配置 `root_api_key`，且请求携带匹配的 API Key。`X-OpenViking-Role: root` 会被拒绝；ROOT 只保留给已校验的 Admin API 回退路径。

### `root_api_key` 的作用

配置 `server.root_api_key` 后，OpenViking 才进入正式多租户模式：

- Root key 用于管理 account 和 user
- User key 由 Admin API 生成，用于普通业务读写
- 服务端会从 user key 反解出 `account_id`、`user_id` 和角色

如果 `auth_mode = "api_key"` 且未配置 `root_api_key`，服务端会进入开发模式：

- 默认所有请求都被视为 ROOT
- 默认身份是 `default/default`
- 只允许绑定在 localhost 上使用

## 共享与隔离边界

### 逻辑层

| 数据类型 | 是否跨 account 共享 | account 内是否共享 | 默认隔离边界 |
|----------|---------------------|-------------------|--------------|
| 共享资源 (`viking://resources`) | 否 | 是 | account |
| 用户资源 (`viking://user/{user_id}/resources`) | 否 | 否 | user |
| Peer 资源 (`viking://user/{user_id}/peers/{peer_id}/resources`) | 否 | 否 | user / peer |
| 记忆 | 否 | 否 | user / peer |
| 技能 | 否 | 否 | user |
| 会话 | 否 | 否 | user / session |

### 存储层

对用户来说，URI 仍然是统一的 `viking://...`：

```text
viking://resources/project-a/
viking://user/alice/memories/
viking://user/alice/resources/
viking://user/alice/peers/web-visitor-alice/resources/
```

但底层存储会自动带上 account 前缀：

```text
/local/{account_id}/resources/project-a/
/local/{account_id}/user/alice/memories/
/local/{account_id}/user/alice/resources/
/local/{account_id}/user/alice/peers/web-visitor-alice/resources/
```

因此多租户隔离不是靠“不同 URI 前缀”，而是靠请求上下文中的 `account_id` 和 `user_id` 共同生效。

### 文件系统与检索层

文件系统操作和语义检索都受租户约束：

- 非 ROOT 请求会自动按 `account_id` 过滤
- `resources` 会允许检索 account 内共享资源
- `memory`、用户资源和 `skill` 会进一步按当前 `user space` 过滤
- Actor peer 会把 `viking://user/{user}/peers` 过滤到一个 peer，并作用于文件系统和检索操作

这意味着“能搜到什么”与“能读到什么”保持一致，不会因为向量召回而越权。

<a id="peer-restricted-view"></a>

### Peer 集合过滤

`peer_id` 是当前 user 边界内的内容范围，不会改变 tenant 或 user 身份。

当一次请求只应该看到当前用户 peer 集合中的某一个 peer 时，设置
`X-OpenViking-Actor-Peer: <peer_id>`，或使用 SDK/CLI 的 `actor_peer_id`：

- 空 target 检索仍包含当前用户根和公共 `viking://resources`。
- 检索解析到 `viking://user/{user}/peers` 时，只选择该 peer 的 memories/resources。
- 文件系统操作不能 read、list、tree、grep/search/find、write、move 或 delete `viking://user/{user}/peers` 下的其他 peer。
- User-scoped memories、resources、skills、共享 resources 和 session 归属不因 actor peer 改变。
- peer ID 必须是安全的单段路径标识，例如 `web-visitor-alice`。

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
  -H "X-API-Key: <bob-user-key>"
```

这样服务端可以直接从 key 反解身份，无需额外传 `account` / `user`。

### 5. 数据 API 身份来自 user 或 admin key

在 `api_key` 模式下，`ls`、`find`、`sessions` 这类租户级数据 API 会从 API
key 自身解析有效的 account 和 user。不要在该模式下发送
`X-OpenViking-Account` 或 `X-OpenViking-User`；基于 header 的身份断言只属于
trusted mode。

`ADMIN` key 可以用它自己的 account/user 身份访问数据 API：

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <admin-user-key>"
```

`ROOT` key 用于 Admin API 以及少量 system/monitoring API。它在 `api_key`
模式下不能访问租户级数据 API，因为它没有绑定到某个租户用户。数据访问请使用
user/admin key；如果需要上游断言身份，请使用 trusted mode。

## 接入实践

### OpenClaw 插件 2.0：每个实例使用 user key

OpenClaw 插件当前的多租户实践是“插件侧只持有一个用户身份”：

- 远程模式配置 `baseUrl + apiKey`，可选 `peer_role` / `peer_prefix`
- `apiKey` 推荐配置为某个 user 的 user key
- 服务端从 user key 自动解析 `account_id` 和 `user_id`
- 插件把 OpenClaw agent 身份保留在 peer/session metadata 中，而不是租户 header 中

典型配置：

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.openviking.config.apiKey "<user-api-key>"
openclaw config set plugins.entries.openviking.config.peer_role assistant
openclaw config set plugins.entries.openviking.config.peer_prefix "<peer-prefix>"
```

这种模式的特点：

- 接入简单，插件不需要管理 account/user 生命周期
- 最适合“一个 OpenClaw 实例对应一个 OpenViking 用户”的场景
- `peer_prefix` 用于区分 OpenClaw 运行时身份，参与 peer/session 元数据
- 同一 account 内的 `resources` 可共享，memory 按 user scope 隔离

### OpenClaw 插件为何通常不配 `account` / `user`

因为在 `api_key` 模式下，user key 已经足够表达身份：

- `account`、`user` 由服务端从 key 反解
- 插件可以提供 `peer_prefix` 作为运行时身份标签
- 插件内部写入 user-scoped memory，并用 `peer_id` 表达每条消息的说话人

如果给插件直接配置 root key，则普通租户数据 API 没有从 key 绑定出来的租户用户，
这不适合作为日常读写方式。

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

正常业务请求应按调用者身份使用 user key 或 admin key。

### 2. `peer_id` 不决定 account

`peer_id` 表示当前用户下的交互对象。它不创建租户，但可以通过显式 peer URI 或
peer 集合过滤选择当前用户内的 peer 内容子空间，例如
`viking://user/{user_id}/peers/{peer_id}/memories` 或
`viking://user/{user_id}/peers/{peer_id}/resources`。

- account 边界由 `account_id` 决定
- user 边界由 `user_id` 决定
- peer 内容仍位于该 user 边界内

### 3. 不配置 `root_api_key` 不等于“单租户正式部署”

这只是开发模式：

- 默认全部请求以 ROOT 身份运行
- 不适合暴露到公网或团队共享环境

### 4. OpenClaw 插件和 Vikingbot 不是同一种租户实践

- OpenClaw 插件：更像“客户端拿到一个 user 身份后直接访问”
- Vikingbot：更像“平台代理多个用户，并代为申请和管理 user key”

## 相关文档

- [认证](../guides/04-authentication.md) - 认证模式、请求头和 key 规则
- [配置](../guides/01-configuration.md) - `root_api_key` 和 `auth_mode`
- [管理员（多租户）](../api/08-admin.md) - Admin API 参考
- [API 概览](../api/01-overview.md) - CLI / HTTP 连接方式
- [数据加密](./10-encryption.md) - 多租户下的静态数据加密
- [多租户示例](https://github.com/volcengine/OpenViking/blob/main/examples/multi_tenant/README.md) - 完整管理流程示例
- [OpenClaw 插件](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README_CN.md) - OpenClaw 的接入方式
- [Vikingbot](https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md) - bot 的多用户接入方式
