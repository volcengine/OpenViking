# 认证

OpenViking Server 支持三种认证模式，并带有基于角色的访问控制：`api_key`、`trusted` 和 `dev`。如果未显式配置，模式会自动推导。

## 概述

OpenViking 使用两层 API Key 体系：

| Key 类型 | 创建方式 | 角色 | 用途 |
|----------|---------|------|------|
| Root Key | 服务端配置（`root_api_key`） | ROOT | 全部操作 + 管理操作 |
| User Key | Admin API | ADMIN 或 USER | 按 account 访问 |

所有 API Key 均为纯随机 token，不携带身份信息。服务端通过先比对 root key、再查 user key 索引的方式确定身份。

## 认证模式

| 模式 | `server.auth_mode` | 身份来源 | 典型使用场景 |
|------|--------------------|----------|--------------|
| API Key 模式 | `"api_key"` | API Key，root 请求可附带租户请求头 | 标准多租户部署 |
| Trusted 模式 | `"trusted"` | `X-OpenViking-Account` / `X-OpenViking-User` / 可选 `X-OpenViking-Agent` 请求头；非 localhost 部署还必须配置 `root_api_key` | 部署在受信网关或内网边界之后 |
| Dev 模式 | `"dev"` | 无认证，始终为 ROOT | 仅限本地开发 |

如果未显式配置 `auth_mode`：
- 如果设置了 `root_api_key`（非空）：自动选择 `api_key` 模式
- 如果未设置 `root_api_key`：自动选择 `dev` 模式

> **注意：** 将 `root_api_key` 设置为空字符串 `""` 是非法的。请要么设置一个非空值，要么完全移除该配置项。

## 服务端配置

在 `ov.conf` 的 `server` 段配置认证模式：

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key"
  }
}
```

启动服务：

```bash
openviking-server
```

## 管理账户和用户

本节只适用于 `api_key` 模式。在 `trusted` 模式下，普通请求不会走 user key 的注册或查找链路。

使用 root key 通过 Admin API 创建工作区和用户：

```bash
# 创建工作区 + 首个 admin
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'
# 返回: {"result": {"account_id": "acme", "admin_user_id": "alice", "user_key": "..."}}

# 注册普通用户（ROOT 或 ADMIN 均可）
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "bob", "role": "user"}'
# 返回: {"result": {"account_id": "acme", "user_id": "bob", "user_key": "..."}}
```

## 客户端使用

OpenViking 支持两种方式传递 API Key：

**X-API-Key 请求头**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <user-key>"
```

**Authorization: Bearer 请求头**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "Authorization: Bearer <user-key>"
```

**Python SDK（HTTP）**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
    agent_id="my-agent"
)
```

**CLI（通过 ovcli.conf）**

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

如果使用普通 `user key`，`account` 和 `user` 可以省略，因为服务端可以从 key 反查出来；如果使用 `trusted` 模式，或者用 `root key` 访问租户级 API，则建议明确配置。

**CLI 覆盖参数**

```bash
openviking --account acme --user alice --agent-id my-agent ls viking://
```

### 使用 --sudo 和 Root API Key

CLI 支持在 `ovcli.conf` 中同时配置 `api_key`（用于普通用户操作）和 `root_api_key`（用于管理员操作）：

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

当需要执行管理员命令（`admin`、`system`、`reindex`）时，使用 `--sudo` 标志提升权限：

```bash
# 列出所有账户（需要 root 权限）
ov --sudo admin list-accounts

# 重新索引内容
ov --sudo reindex viking://

# 系统命令
ov --sudo system status
```

`--sudo` 标志：
- 仅适用于管理员命令：`admin`、`system`、`reindex`
- 用于非管理员命令时会报错
- `ovcli.conf` 中未配置 `root_api_key` 时会报错
- 请求时使用 `root_api_key` 替代 `api_key`

### 使用 Root Key 访问租户数据

使用 root key 访问租户级数据 API（如 `ls`、`find`、`sessions` 等）时，必须指定目标 account 和 user，否则服务端将拒绝请求。Admin API 和系统状态端点不受此限制。

**curl**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-secret-root-key" \
  -H "X-OpenViking-Account: acme" \
  -H "X-OpenViking-User: alice"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-secret-root-key",
    account="acme",
    user="alice",
)
```

**ovcli.conf**

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-root-key",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

## Trusted 模式

Trusted 模式不会查 user key，而是直接信任每个请求显式携带的身份请求头：

```json
{
  "server": {
    "auth_mode": "trusted",
    "host": "127.0.0.1"
  }
}
```

Trusted 模式规则：

- 普通数据访问不需要先注册 user key，也不依赖 user key 分发流程
- 租户级请求必须包含 `X-OpenViking-Account` 和 `X-OpenViking-User`
- `X-OpenViking-Agent` 可选，缺省为 `default`
- 每个 trusted 请求都会被解析成 `USER`，身份完全来自请求头，而不是 root key 或 user key
- 如果同时配置了 `root_api_key`，每个请求仍然必须带匹配的 API Key
- 只应部署在受信网络边界之后，或由身份注入网关统一转发

这意味着：

- `trusted` 不是开发模式
- `trusted` 下的普通读写、检索、会话访问不需要先走 Admin API 注册流程
- 创建 account、注册用户、修改角色、重新生成 key 仍然属于 `api_key` 模式下的管理链路；如果服务端运行在 `trusted` 模式而你去调用这些 Admin API，服务端会返回明确错误，说明 `trusted` 不支持这类注册式管理，并提示切换到配置了 `root_api_key` 的 `api_key` 模式

**curl**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-OpenViking-Account: acme" \
  -H "X-OpenViking-User: alice" \
  -H "X-OpenViking-Agent: my-agent"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    account="acme",
    user="alice",
    agent_id="my-agent",
)
```

## Dev 模式

当 `auth_mode = "dev"`（或未配置 `root_api_key` 时自动推导）时，认证禁用，所有请求以 ROOT 身份访问 default account。**此模式仅允许在服务器绑定 localhost 时使用**（`127.0.0.1`、`localhost` 或 `::1`）。如果 `host` 设置为非回环地址（如 `0.0.0.0`）且使用 `dev` 模式，服务器将拒绝启动。

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  }
}
```

或显式配置：

```json
{
  "server": {
    "auth_mode": "dev",
    "host": "127.0.0.1",
    "port": 1933
  }
}
```

> **安全提示：** 默认 `host` 为 `127.0.0.1`。如需将服务暴露到网络，**必须**配置 `root_api_key`。

## 角色与权限

| 角色 | 作用域 | 能力 |
|------|--------|------|
| ROOT | 全局 | 全部操作 + Admin API（创建/删除工作区、管理用户） |
| ADMIN | 所属 account | 常规操作 + 管理所属 account 的用户 |
| USER | 所属 account | 常规操作（ls、read、find、sessions 等） |

在 `trusted` 模式下，请求角色会解析为 `USER`，因此普通流量不适用 ROOT/ADMIN 的注册式管理语义。

## 无需认证的端点

`/health` 端点始终不需要认证，用于负载均衡器和监控工具检查服务健康状态。

```bash
curl http://localhost:1933/health
```

## Admin API 参考

| 方法 | 端点 | 角色 | 说明 |
|------|------|------|------|
| POST | `/api/v1/admin/accounts` | ROOT | 创建工作区 + 首个 admin |
| GET | `/api/v1/admin/accounts` | ROOT | 列出所有工作区 |
| DELETE | `/api/v1/admin/accounts/{id}` | ROOT | 删除工作区 |
| POST | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | 注册用户 |
| GET | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | 列出用户 |
| DELETE | `/api/v1/admin/accounts/{id}/users/{uid}` | ROOT, ADMIN | 移除用户 |
| PUT | `/api/v1/admin/accounts/{id}/users/{uid}/role` | ROOT | 修改用户角色 |
| POST | `/api/v1/admin/accounts/{id}/users/{uid}/key` | ROOT, ADMIN | 重新生成 user key |

## 相关文档

- [多租户](../concepts/11-multi-tenant.md) - 多租户能力、共享边界与接入实践
- [配置](01-configuration.md) - 配置文件说明
- [服务部署](03-deployment.md) - 服务部署
- [API 概览](../api/01-overview.md) - API 参考
