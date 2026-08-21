# 管理员（多租户）

Admin API 用于多租户环境下的账户和用户管理。包括工作区（account）的创建与删除、用户注册与移除、角色变更、API Key 重新生成。

该 API 适用于 `api_key` 和 `trusted` 两种模式下的管理链路：
- 在 `api_key` 模式下，角色始终从 API Key 推导。
- 在 `trusted` 模式下，普通请求仍然不依赖 user key 注册流程；当请求 `/api/v1/admin/*` 并携带已配置的 `root_api_key` 时，受信上游会按 ROOT 授权。

对于 `/api/v1/admin/*`，`trusted` 模式允许不携带显式身份头；也允许携带与 URL 中 account/user 匹配的目标身份头。只要部署级 `root_api_key` 校验通过，这类请求都会按 ROOT 处理。普通 trusted 数据 API 的身份和角色仍然来自 `X-OpenViking-Account` + `X-OpenViking-User`。

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
| 列出 agents（已废弃，返回空列表） | Y | Y（本 account） | N |
| 重新生成 User Key | Y | Y（本 account） | N |
| 将用户提升为 ADMIN | Y | Y（本 account） | N |

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
- `ov --sudo admin migrate` - legacy agent/session 迁移和 cleanup
- `ov --sudo task status/list` - 查询 root/system 后台任务，例如迁移任务

### 使用限制

- `--sudo` 仅适用于上面的命令，用于普通数据命令会报错
- 必须配置 `root_api_key` 才能使用 `--sudo`

## API 参考

### get_agent_evolution_status

返回调用方所属 account 的 Agent 进化实时状态。ROOT 操作已配置的默认
account，ADMIN 仅操作自己所属的 account。

**HTTP API**

```
GET /api/v1/admin/agent-evolution
```

```bash
curl http://localhost:1933/api/v1/admin/agent-evolution \
  -H "X-API-Key: <root-key>"
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "enabled": false,
    "account_id": "default"
  },
  "time": 0.1
}
```

`enabled` 优先读取
`/local/{account_id}/_system/setting.json` 中的 account 级覆盖值；未配置时使用
`server.agent_evolution.enabled`。Session commit 会实时读取生效值，无需重启。

现有更新接口名保持不变：

```http
PUT /api/v1/admin/agent-evolution
Content-Type: application/json

{"enabled": true}
```

### account_settings

ROOT 可管理任意 account，ADMIN 仅可管理自己所属的 account。通用配置接口仅允许
显式列入白名单的字段；当前只允许修改 `agent_evolution.enabled`。

```http
GET /api/v1/admin/accounts/{account_id}/settings
PATCH /api/v1/admin/accounts/{account_id}/settings
Content-Type: application/json

{"agent_evolution": {"enabled": true}}
```

覆盖已有配置前，内核会先备份到
`/local/{account_id}/_system/setting.backup.json`。

### user_settings

ROOT 可管理任意 User，ADMIN 仅可管理所属 account 内的 User。User 配置接口当前
仅允许修改 `memory_policy`。顶层统一的 `memory_types` 控制允许抽取的记忆类型。
用户记忆根据每条 Message 的 `peer_id` 自动写入 Self 或 Peer；Agent 记忆始终只写入
Self。

```http
GET /api/v1/admin/accounts/{account_id}/users/{user_id}/settings
PATCH /api/v1/admin/accounts/{account_id}/users/{user_id}/settings
Content-Type: application/json

{
  "memory_policy": {
    "memory_types": ["profile", "preferences", "events", "entities", "experiences"]
  }
}
```

响应直接返回 User 级 `memory_policy`，并展开默认记忆类型和 Agent 记忆依赖；配置
`experiences` 时会展开为 `cases`、`trajectories`、`experiences`；
该结果不受 account 级 Agent 进化开关影响，Account 开关由独立接口管理。
更新前会备份到该 User 的 `settings/user_config.backup.json`。未显式配置策略的
Session 在 commit 时读取该 User 最新策略；User 未覆盖时，依次回退到
`server.user_config_defaults.memory_policy` 和内核默认策略。若要清除已持久化的
User override 并重新继承上述默认值，请 PATCH `{"memory_policy": null}`。
`{"memory_policy": {}}` 表示显式策略，不会清除 override。

---

### create_account

#### 1. API 实现介绍

创建新工作区及其首个管理员用户。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 使用 API Key Manager 创建账户和初始管理员用户
3. 初始化账户级目录结构
4. 初始化管理员用户的个人目录
5. 写入可选的初始管理员用户配置
6. 返回账户信息和用户密钥（非 trusted 模式下）

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
| seed | str | 否 | `null` | 可选的确定性 API Key seed。传入后，key secret 为 `sha256(user_id + "\0" + seed)` |
| user_config | object | 否 | `null` | 首个管理员用户的初始配置。支持 `add_targets.resource_uri`、`add_targets.skill_uri` 和 `memory_policy` |

**说明：**
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- 省略 `seed` 时使用默认随机 API Key。seed 应视为密钥材料；过短的 seed 会让 key 更容易被猜测。
- 不再支持 account 级 namespace 隔离配置。用户记忆使用 user-scoped namespace，一对多外部参与者通过 `peer_id` 表达。
- `user_config.add_targets.resource_uri` 必须是可写资源目录 URI：`viking://resources` 或 `viking://resources/...`、`viking://user/resources` 或 `viking://user/resources/...`、`viking://user/{user_id}/resources` 或 `viking://user/{user_id}/resources/...`、`viking://user/{user_id}/peers/{peer_id}/resources` 或 `viking://user/{user_id}/peers/{peer_id}/resources/...`。
- `user_config.add_targets.skill_uri` 只能是 `viking://user/skills` 或 `viking://agent/skills`。v1 不支持显式写成 `viking://user/{user_id}/skills`。

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
    "seed": "alice-seed"
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

# 然后在 trusted 模式下使用；管理权限来自 root_api_key
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -H "X-OpenViking-Account: platform" \
  -H "X-OpenViking-User: gateway-admin" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
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

result = client.admin_create_account("acme", "alice", seed="alice-seed")
print(f"Account created: {result['account_id']}")
print(f"Admin user: {result['admin_user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_create_account(
    "acme-private",
    "alice",
    user_config={
        "add_targets": {
            "resource_uri": "viking://user/resources",
            "skill_uri": "viking://user/skills",
        }
    },
)
```

**TypeScript SDK**

```typescript
console.log(await client.adminCreateAccount("account-id", "admin-user-id"));
```

**Go SDK**

```go
result, err := client.AdminCreateAccount(ctx, "acme", "alice")
if err != nil {
    return err
}
fmt.Println(result["account_id"])

seed := "alice-seed"
result, err = client.AdminCreateAccountWithOptions(ctx, "acme-private", "alice", &openviking.AdminCreateAccountOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{
            "resource_uri": "viking://user/resources",
            "skill_uri":    "viking://user/skills",
        },
    },
})
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin create-account acme --admin alice
ov --sudo admin create-account acme --admin alice --seed alice-seed

ov --sudo admin create-account acme-private --admin alice \
  --user-config-json '{"add_targets":{"resource_uri":"viking://user/resources","skill_uri":"viking://user/skills"}}'
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "admin_user_id": "alice",
    "user_key": "7f3a9c1e..."
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

**TypeScript SDK**

```typescript
console.log(await client.adminListAccounts());
```

**Go SDK**

```go
accounts, err := client.AdminListAccounts(ctx)
if err != nil {
    return err
}
fmt.Println(accounts)
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
2. 级联删除账户下的所有 AGFS 数据（`user/` 和 `resources/`；sessions 位于 `user/` 下）
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

**TypeScript SDK**

```typescript
await client.adminDeleteAccount("account-id");
```

**Go SDK**

```go
result, err := client.AdminDeleteAccount(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(result["deleted"])
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
4. 写入可选的初始用户配置
5. 返回用户信息和用户密钥（非 trusted 模式下）

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
| role | str | 否 | "user" | 要分配的角色。`ROOT` 和同 account 的 `ADMIN` 可直接注册 `"user"` 或 `"admin"`。ROOT 身份只来自 `server.root_api_key`。 |
| seed | str | 否 | `null` | 可选的确定性 API Key seed。传入后，key secret 为 `sha256(user_id + "\0" + seed)` |
| user_config | object | 否 | `null` | 新用户的初始配置。支持 `add_targets.resource_uri`、`add_targets.skill_uri` 和 `memory_policy` |

**说明：**
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- 省略 `seed` 时使用默认随机 API Key。seed 应视为密钥材料；过短的 seed 会让 key 更容易被猜测。
- ADMIN 只能在自己所属的 account 中注册用户
- 无法通过用户注册接口直接创建 `"root"` 角色
- `user_config.add_targets.resource_uri` 必须是可写资源目录 URI：`viking://resources` 或 `viking://resources/...`、`viking://user/resources` 或 `viking://user/resources/...`、`viking://user/{user_id}/resources` 或 `viking://user/{user_id}/resources/...`、`viking://user/{user_id}/peers/{peer_id}/resources` 或 `viking://user/{user_id}/peers/{peer_id}/resources/...`。
- `user_config.add_targets.skill_uri` 只能是 `viking://user/skills` 或 `viking://agent/skills`。v1 不支持显式写成 `viking://user/{user_id}/skills`。

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
    "role": "user",
    "seed": "bob-seed"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_register_user("acme", "bob", role="user", seed="bob-seed")
print(f"User registered: {result['user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_register_user(
    "acme",
    "bob-private",
    role="user",
    user_config={"add_targets": {"resource_uri": "viking://user/resources/project-a"}},
)
```

**TypeScript SDK**

```typescript
console.log(await client.adminRegisterUser("account-id", "user-id", "user"));
```

**Go SDK**

```go
result, err := client.AdminRegisterUser(ctx, "acme", "bob", "user")
if err != nil {
    return err
}
fmt.Println(result["user_id"])

seed := "bob-seed"
result, err = client.AdminRegisterUserWithOptions(ctx, "acme", "bob-private", "user", &openviking.AdminRegisterUserOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{"resource_uri": "viking://user/resources/project-a"},
    },
})
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin register-user acme bob --role user
ov admin register-user acme bob --role user --seed bob-seed
# 如果使用 root_api_key（--sudo）：
ov --sudo admin register-user acme bob --role user

ov admin register-user acme bob-private --role user \
  --user-config-json '{"add_targets":{"resource_uri":"viking://user/resources/project-a"}}'
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

**TypeScript SDK**

```typescript
console.log(await client.adminListUsers("account-id"));
```

**Go SDK**

```go
users, err := client.AdminListUsers(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(users)
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

**TypeScript SDK**

```typescript
await client.adminRemoveUser("account-id", "user-id");
```

**Go SDK**

```go
result, err := client.AdminRemoveUser(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["deleted"])
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

将账户用户提升为 ADMIN。ROOT 可以操作任意账户；ADMIN 只能操作自己的账户。

**处理流程：**
1. 验证请求者具有 ROOT 或 ADMIN 权限，并限制 ADMIN 只能操作自己的账户
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
| role | str | 是 | - | 固定为 "admin" |

**说明：**
- ROOT 和 ADMIN 可以将用户提升为 ADMIN；ADMIN 只能操作自己的账户
- 该接口不支持设置 "user" 或 "root"；ROOT 身份只来自 `server.root_api_key`

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

**TypeScript SDK**

```typescript
await client.adminSetRole("account-id", "user-id", "admin");
```

**Go SDK**

```go
result, err := client.AdminSetRole(ctx, "acme", "bob", "admin")
if err != nil {
    return err
}
fmt.Println(result["role"])
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
| seed | str | 否 | `null` | JSON request body 中可选的确定性 API Key seed。传入后，key secret 为 `sha256(user_id + "\0" + seed)` |

**说明：**
- ADMIN 只能为自己所属的 account 中的用户重新生成密钥
- 旧密钥会立即失效，需要更新使用该密钥的客户端
- 省略 `seed` 时使用默认随机重新生成逻辑。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{"seed": "bob-new-seed"}'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_regenerate_key("acme", "bob", seed="bob-new-seed")
print(f"New user key: {result['user_key']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminRegenerateKey("account-id", "user-id"));
```

**Go SDK**

```go
result, err := client.AdminRegenerateKey(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["user_key"])

seed := "bob-new-seed"
result, err = client.AdminRegenerateKeyWithOptions(ctx, "acme", "bob", &openviking.AdminRegenerateKeyOptions{
    Seed: &seed,
})
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin regenerate-key acme bob
ov admin regenerate-key acme bob --seed bob-new-seed
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

### migrate_legacy_data

#### 1. API 实现介绍

将 0.3.x legacy `viking://agent/...` / `viking://session/...` 数据迁移到 0.4.0 的 user / peer namespace，或在确认迁移结果后清理旧 namespace。该接口仅 ROOT 可调用，并以后台 task 执行。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. `action=migrate` 时执行 preflight，检查 account registry、session owner 等前置条件
3. 创建 root 级后台 task
4. 迁移时复制文件和已有向量记录；cleanup 时先删除旧向量记录，再删除旧 AGFS 目录

迁移不会自动调用 `reindex`。如果迁移后的检索结果不符合预期，需要用户对新路径手动执行 reindex。

**代码入口：**
- `openviking/server/routers/admin.py:migrate_legacy_data` - HTTP 路由
- `openviking/service/legacy_migration.py:LegacyDataMigration` - 迁移实现

#### 2. 接口和参数说明

**HTTP API**

```
POST /api/v1/admin/migrate
```

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| action | str | 否 | migrate | `migrate` 执行迁移；`cleanup` 清理旧 namespace |

**迁移结果字段**

| 字段 | 说明 |
|------|------|
| migrated.files / migrated.directories | 复制的文件和目录数量 |
| migrated.vector_records | 复制的已有向量记录数量 |
| migrated.skipped_vector_records | 因没有向量 payload 而跳过的旧记录数量 |
| migrated.operations | 按迁移类别统计的操作数量 |
| skipped / warnings / created_users | 跳过项、告警、自动创建的用户 |

**Cleanup 结果字段**

| 字段 | 说明 |
|------|------|
| cleanup.directories | 删除的 legacy 目录数量 |
| cleanup.vector_records | 删除的旧向量记录数量 |
| cleanup.targets | 已清理的 legacy scope |
| skipped / warnings | 跳过项和告警 |

#### 3. 使用示例

**HTTP API**

```bash
# 执行迁移
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "migrate"}'

# 清理旧 namespace
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "cleanup"}'
```

**Python SDK**

```python
print(client.admin_migrate(cleanup=False))
```

**TypeScript SDK**

```typescript
console.log(await client.adminMigrate(false));
```

**Go SDK**

```go
result, err := client.AdminMigrate(ctx, &openviking.AdminMigrateOptions{
    Cleanup: false,
})
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

```bash
ov --sudo admin migrate --output json
ov --sudo admin migrate --cleanup --output json
```

**响应示例**

```json
{
  "task_id": "legacy_migration_..."
}
```

---

<a id="用户添加位置设置"></a>

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

# 步骤 4：将用户提升为 admin
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
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
