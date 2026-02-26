# 认证

OpenViking Server 支持多租户 API Key 认证和基于角色的访问控制。

## 概述

OpenViking 使用两层 API Key 体系：

| Key 类型 | 创建方式 | 角色 | 用途 |
|----------|---------|------|------|
| Root Key | 服务端配置（`root_api_key`） | ROOT | 全部操作 + 管理操作 |
| User Key | Admin API | ADMIN 或 USER | 按 account 访问 |

所有 API Key 均为纯随机 token，不携带身份信息。服务端通过先比对 root key、再查 user key 索引的方式确定身份。

## 服务端配置

在 `ov.conf` 的 `server` 段配置 root API key：

```json
{
  "server": {
    "root_api_key": "your-secret-root-key"
  }
}
```

启动服务：

```bash
python -m openviking serve
```

## 管理账户和用户

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
  "agent_id": "my-agent"
}
```

## 角色与权限

| 角色 | 作用域 | 能力 |
|------|--------|------|
| ROOT | 全局 | 全部操作 + Admin API（创建/删除工作区、管理用户） |
| ADMIN | 所属 account | 常规操作 + 管理所属 account 的用户 |
| USER | 所属 account | 常规操作（ls、read、find、sessions 等） |

## 开发模式

不配置 `root_api_key` 时，认证禁用，所有请求以 ROOT 身份访问 default account。**此模式仅允许在服务器绑定 localhost 时使用**（`127.0.0.1`、`localhost` 或 `::1`）。如果 `host` 设置为非回环地址（如 `0.0.0.0`）且未配置 `root_api_key`，服务器将拒绝启动。

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  }
}
```

> **安全提示：** 默认 `host` 为 `127.0.0.1`。如需将服务暴露到网络，**必须**配置 `root_api_key`。

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

- [配置](01-configuration.md) - 配置文件说明
- [服务部署](03-deployment.md) - 服务部署
- [API 概览](../api/01-overview.md) - API 参考
