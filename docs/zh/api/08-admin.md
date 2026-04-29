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

## CLI `--sudo` 选项

使用 `ov` CLI 执行需要 ROOT 权限的管理操作时，可以使用 `--sudo` 选项。该选项会使用配置文件 `~/.openviking/ovcli.conf` 中的 `root_api_key` 而非普通 `api_key`。

### 配置要求

在 `~/.openviking/ovcli.conf` 中配置 `root_api_key`：

```json
{
  "url": "http://localhost:1933",
  "api_key": "alice-user-key",
  "root_api_key": "your-root-api-key",
  ...
}
```

### 支持 `--sudo` 的命令

- `ov --sudo admin` - 账户和用户管理
- `ov --sudo system` - 系统工具命令
- `ov --sudo reindex` - 重建索引

### 使用限制

- `--sudo` 仅适用于管理类命令，用于普通数据命令会报错
- 必须配置 `root_api_key` 才能使用 `--sudo`

## API 参考

### create_account

#### 1. API 实现介绍

创建新工作区及其首个管理员用户。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 使用 API Key Manager 创建账户和初始管理员用户
3. 初始化账户级目录结构
4. 初始化管理员用户的个人目录
5. 返回账户信息和用户密钥（非 trusted 模式下）

**代码入口：**
- `openviking/server/routers/admin.py:create_account` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.create_account` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_create_account` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| admin_user_id | str | 是 | - | 首个管理员用户 ID |
| isolate_user_scope_by_agent | bool | 否 | false | 是否按 agent 进一步隔离 user scope |
| isolate_agent_scope_by_user | bool | 否 | false | 是否按 user 进一步隔离 agent scope |

**说明：**
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- `isolate_user_scope_by_agent` 和 `isolate_agent_scope_by_user` 仅可通过 HTTP API 设置，Python SDK 和 CLI 暂不支持

#### 3. 使用示例

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

`trusted` 模式也支持"不带身份头"的 ROOT 回退写法：

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_create_account("acme", "alice")
print(f"Account created: {result['account_id']}")
print(f"Admin user: {result['admin_user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin create-account acme --admin alice
```

**响应示例**

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

---

### list_accounts

#### 1. API 实现介绍

列出所有工作区（仅 ROOT）。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 调用 API Key Manager 获取所有账户列表
3. 返回包含账户 ID、创建时间和用户数量的列表

**代码入口：**
- `openviking/server/routers/admin.py:list_accounts` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.get_accounts` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_accounts` - Python SDK

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/admin/accounts
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

accounts = client.admin_list_accounts()
for account in accounts:
    print(f"Account: {account['account_id']}, created: {account['created_at']}, users: {account['user_count']}")
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin list-accounts
```

**响应示例**

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

---

### delete_account

#### 1. API 实现介绍

删除工作区及其所有关联用户和数据（仅 ROOT）。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 级联删除账户下的所有 AGFS 数据（user/、agent/、session/、resources/）
3. 级联删除向量数据库中该账户的所有记录
4. 最后删除账户元数据和所有用户密钥

**代码入口：**
- `openviking/server/routers/admin.py:delete_account` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.delete_account` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_delete_account` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 要删除的工作区 ID |

**说明：**
- 删除操作是不可逆的，会级联删除该账户下的所有数据
- 如果部分数据删除失败，会记录警告日志并继续删除其他数据

#### 3. 使用示例

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_delete_account("acme")
print(f"Account deleted: {result['deleted']}")
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin delete-account acme
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "deleted": true
  },
  "time": 0.1
}
```

---

### register_user

#### 1. API 实现介绍

在工作区中注册新用户。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 注册新用户
3. 初始化新用户的个人目录
4. 返回用户信息和用户密钥（非 trusted 模式下）

**代码入口：**
- `openviking/server/routers/admin.py:register_user` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.register_user` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_register_user` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| role | str | 否 | "user" | 角色："admin" 或 "user" |

**说明：**
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- ADMIN 只能在自己所属的 account 中注册用户
- 只有 ROOT 可以将新用户角色设置为 "admin"

#### 3. 使用示例

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

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_register_user("acme", "bob", role="user")
print(f"User registered: {result['user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin register-user acme bob --role user
# 如果使用 root_api_key（--sudo）：
ov --sudo admin register-user acme bob --role user
```

**响应示例**

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

### list_users

#### 1. API 实现介绍

列出工作区中的所有用户。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 获取用户列表
3. 应用可选的过滤条件（name、role）和分页限制
4. 返回用户列表（trusted 模式下不包含 user_key）

**代码入口：**
- `openviking/server/routers/admin.py:list_users` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.get_users` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_users` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| limit | int | 否 | 100 | 返回用户数量上限 |
| name | str | 否 | null | 按用户 ID 过滤（前缀匹配） |
| role | str | 否 | null | 按角色过滤 |

**说明：**
- ADMIN 只能列出自己所属的 account 中的用户
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/users
```

```bash
# 列出所有用户
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <root-or-admin-key>"

# 带过滤条件
curl -X GET "http://localhost:1933/api/v1/admin/accounts/acme/users?role=admin&limit=50" \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

users = client.admin_list_users("acme")
for user in users:
    print(f"User: {user['user_id']}, role: {user['role']}")
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin list-users acme
# 如果使用 root_api_key（--sudo）：
ov --sudo admin list-users acme
```

**响应示例**

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

### remove_user

#### 1. API 实现介绍

从工作区中移除用户，同时删除其 API Key。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 删除用户及其 API Key
3. 返回删除确认

**代码入口：**
- `openviking/server/routers/admin.py:remove_user` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.remove_user` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_remove_user` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 要移除的用户 ID |

**说明：**
- ADMIN 只能移除自己所属的 account 中的用户
- 不能删除账户的最后一个 admin 用户

#### 3. 使用示例

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}/users/{user_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_remove_user("acme", "bob")
print(f"User deleted: {result['deleted']}")
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin remove-user acme bob
# 如果使用 root_api_key（--sudo）：
ov --sudo admin remove-user acme bob
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "deleted": true
  },
  "time": 0.1
}
```

---

### set_role

#### 1. API 实现介绍

修改用户角色（仅 ROOT）。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 调用 API Key Manager 更新用户角色
3. 返回更新后的用户信息

**代码入口：**
- `openviking/server/routers/admin.py:set_user_role` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.set_role` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_set_role` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| role | str | 是 | - | 新角色："admin" 或 "user" 或 "root" |

**说明：**
- 只有 ROOT 可以修改用户角色
- 角色可以设置为 "admin"、"user" 或 "root"

#### 3. 使用示例

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

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_set_role("acme", "bob", "admin")
print(f"User: {result['user_id']}, new role: {result['role']}")
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin set-role acme bob admin
```

**响应示例**

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

### regenerate_key

#### 1. API 实现介绍

重新生成用户的 API Key，旧 Key 立即失效。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 重新生成用户密钥
3. 旧密钥立即失效
4. 返回新的用户密钥

**代码入口：**
- `openviking/server/routers/admin.py:regenerate_key` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.regenerate_key` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_regenerate_key` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |

**说明：**
- ADMIN 只能为自己所属的 account 中的用户重新生成密钥
- 旧密钥会立即失效，需要更新使用该密钥的客户端

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_regenerate_key("acme", "bob")
print(f"New user key: {result['user_key']}")
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin regenerate-key acme bob
# 如果使用 root_api_key（--sudo）：
ov --sudo admin regenerate-key acme bob
```

**响应示例**

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
# 步骤 1：ROOT 创建工作区，指定 alice 为首个 admin（需要 --sudo）
ov --sudo admin create-account acme --admin alice
# 返回 alice 的 user_key

# 步骤 2：alice（admin）注册普通用户 bob
# 配置文件中的 api_key 设为 alice 的 user_key，不需要 --sudo
ov admin register-user acme bob --role user
# 返回 bob 的 user_key

# 步骤 3：查看账户下所有用户
ov admin list-users acme

# 步骤 4：ROOT 将 bob 提升为 admin（需要 --sudo）
ov --sudo admin set-role acme bob admin

# 步骤 5：bob 丢失 key，重新生成（旧 key 立即失效）
# alice 作为 admin 可以执行，不需要 --sudo
ov admin regenerate-key acme bob

# 步骤 6：移除用户
ov admin remove-user acme bob

# 步骤 7：删除整个工作区（需要 --sudo）
ov --sudo admin delete-account acme
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
