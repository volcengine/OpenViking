# 管理员（多租户）

Admin API 用于多租户环境下的账户和用户管理。包括工作区（account）的创建与删除、用户注册与移除、角色变更、API Key 重新生成。

该 API 适用于 `api_key` 和 `trusted` 两种模式下的管理链路：
- 在 `api_key` 模式下，角色始终从 API Key 推导。
- 在 `trusted` 模式下，普通请求仍然不依赖 user key 注册流程；但受信任网关可以使用已注册且具有适当角色的用户调用 Admin API（角色从用户注册表查询）。

在 `trusted` 模式下，角色通过查询 `X-OpenViking-Account` + `X-OpenViking-User` 从用户注册表确定。如果用户不存在，角色默认为 `USER`。
对于 `/api/v1/admin/*`，`trusted` 模式还允许不携带显式身份头；这类请求会被视为 ROOT，适用于仅通过部署级 `root_api_key` 认证的受信上游。

## 角色与权限

| 角色 | 说明 |
|------|------|
| ROOT | 系统管理员，拥有全部权限 |
| ADMIN | 工作区管理员，管理本 account 内的用户 |
| USER | 普通用户 |

| 操作 | ROOT | ADMIN | USER |
|------|------|-------|------|
| 创建/删除工作区 | Y | N | N |
| 列出工作区 | Y | N | N |
| 注册/移除用户 | Y | Y（本 account） | N |
| 重新生成 User Key | Y | Y（本 account） | N |
| 修改用户角色 | Y | N | N |

## API 参考

### create_account()

创建新工作区及其首个管理员用户。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| admin_user_id | str | 是 | - | 首个管理员用户 ID |
| isolate_user_scope_by_agent | bool | 否 | false | 是否按 agent 进一步隔离 user scope |
| isolate_agent_scope_by_user | bool | 否 | false | 是否按 user 进一步隔离 agent scope |

**HTTP API**

```
POST /api/v1/admin/accounts
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice",
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
  }'
```

**CLI**

```bash
openviking admin create-account acme --admin alice
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "admin_user_id": "alice",
    "user_key": "7f3a9c1e...",
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
  },
  "time": 0.1
}
```

`trusted` 模式示例：

```bash
# 首先，在 api_key 模式下注册网关管理员用户
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "platform",
    "admin_user_id": "gateway-admin"
  }'

# 然后提升为 root，以便执行跨 account 的管理操作
curl -X PUT http://localhost:1933/api/v1/admin/accounts/platform/users/gateway-admin/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "root"}'

# 然后在 trusted 模式下使用
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -H "X-OpenViking-Account: platform" \
  -H "X-OpenViking-User: gateway-admin" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice",
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
  }'
```

`trusted` 模式也支持“不带身份头”的 ROOT 回退写法：

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

---

### list_accounts()

列出所有工作区（仅 ROOT）。

**HTTP API**

```
GET /api/v1/admin/accounts
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>"
```

**CLI**

```bash
openviking admin list-accounts
```

**响应**

```json
{
  "status": "ok",
  "result": [
    {"account_id": "default", "created_at": "2026-02-12T10:00:00Z", "user_count": 1},
    {"account_id": "acme", "created_at": "2026-02-13T08:00:00Z", "user_count": 2}
  ],
  "time": 0.1
}
```

在 `trusted` 模式下，同类响应不会返回 `user_key`。

---

### delete_account()

删除工作区及其所有关联用户和数据（仅 ROOT）。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 要删除的工作区 ID |

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

**CLI**

```bash
openviking admin delete-account acme
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme"
  },
  "time": 0.1
}
```

---

### register_user()

在工作区中注册新用户。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| role | str | 否 | "user" | 角色："admin" 或 "user" |

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{
    "user_id": "bob",
    "role": "user"
  }'
```

**CLI**

```bash
openviking admin register-user acme bob --role user
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "user_key": "d91f5b2a..."
  },
  "time": 0.1
}
```

---

### list_users()

列出工作区中的所有用户。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/users
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
openviking admin list-users acme
```

**响应**

```json
{
  "status": "ok",
  "result": [
    {"user_id": "alice", "role": "admin"},
    {"user_id": "bob", "role": "user"}
  ],
  "time": 0.1
}
```

---

### remove_user()

从工作区中移除用户，同时删除其 API Key。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 要移除的用户 ID |

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}/users/{user_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
openviking admin remove-user acme bob
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob"
  },
  "time": 0.1
}
```

---

### set_role()

修改用户角色（仅 ROOT）。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| role | str | 是 | - | 新角色："admin" 或 "user" |

**HTTP API**

```
PUT /api/v1/admin/accounts/{account_id}/users/{user_id}/role
```

```bash
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "admin"}'
```

**CLI**

```bash
openviking admin set-role acme bob admin
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "role": "admin"
  },
  "time": 0.1
}
```

---

### regenerate_key()

重新生成用户的 API Key，旧 Key 立即失效。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
openviking admin regenerate-key acme bob
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "user_key": "e82d4e0f..."
  },
  "time": 0.1
}
```

---

## 完整示例

### 典型管理流程

```bash
# 步骤 1：ROOT 创建工作区，指定 alice 为首个 admin
openviking admin create-account acme --admin alice
# 返回 alice 的 user_key

# 步骤 2：alice（admin）注册普通用户 bob
openviking admin register-user acme bob --role user
# 返回 bob 的 user_key

# 步骤 3：查看账户下所有用户
openviking admin list-users acme

# 步骤 4：ROOT 将 bob 提升为 admin
openviking admin set-role acme bob admin

# 步骤 5：bob 丢失 key，重新生成（旧 key 立即失效）
openviking admin regenerate-key acme bob

# 步骤 6：移除用户
openviking admin remove-user acme bob

# 步骤 7：删除整个工作区
openviking admin delete-account acme
```

### HTTP API 等效流程

```bash
# 步骤 1：创建工作区
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'

# 步骤 2：注册用户（使用 alice 的 admin key）
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
  -d '{"user_id": "bob", "role": "user"}'

# 步骤 3：列出用户
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <alice-key>"

# 步骤 4：修改角色（需要 ROOT key）
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "admin"}'

# 步骤 5：重新生成 key
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>"

# 步骤 6：移除用户
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <alice-key>"

# 步骤 7：删除工作区
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

---

## 相关文档

- [多租户](../concepts/11-multi-tenant.md) - 多租户模型、角色和共享边界
- [API 概览](01-overview.md) - 认证与响应格式
- [会话管理](05-sessions.md) - 会话管理
- [系统](07-system.md) - 系统和监控 API
