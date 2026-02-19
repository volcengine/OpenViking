# OpenViking 多租户设计方案

## Context

OpenViking 已定义了 `UserIdentifier(account_id, user_id, agent_id)` 三元组（PR #120），但多租户隔离尚未实施。当前状态：

- **认证**：单一全局 `api_key`，HMAC 比较（`openviking/server/auth.py`）
- **无 RBAC**：所有认证用户拥有完全访问权限
- **无存储隔离**：`VikingFS._uri_to_path` 将 `viking://` 映射到 `/local/`，无 account_id 前缀
- **VectorDB**：单一 `context` collection，无租户过滤
- **服务层**：`OpenVikingService` 持有单例 `_user`，不支持请求级用户上下文

目标：实现完整的多租户支持，包括 API Key 管理、RBAC、存储隔离。不考虑向后兼容。

---

## 一、整体架构

```
Request
  │
  ▼
[Auth Middleware] ── 提取 API Key，按前缀分派：root 比对 / acct 查表 / user 解签名 → (account_id, user_id, role)
  │
  ▼
[RBAC Guard] ── 按角色检查操作权限
  │
  ▼
[RequestContext] ── UserIdentifier + Role 注入为 FastAPI 依赖
  │
  ▼
[Router] ── 传递 RequestContext 到 Service
  │
  ▼
[Service Layer] ── 请求级用户上下文（非单例）
  │
  ├─► [VikingFS] ── 单例，接受 RequestContext 参数，_uri_to_path 按 account_id 隔离，逐层权限过滤
  └─► [VectorDB] ── 单 collection，查询注入 account_id + owner_space 过滤
```

核心原则：
- **身份从 API Key 解析**，贯穿全链路
- **account 级隔离**：AGFS 路径前缀 + VectorDB account_id 过滤
- **user/agent 级隔离**：目录遍历时逐层过滤，只展示当前用户有权限的目录和文件
- VikingFS 通过 RequestContext 获取租户和用户信息

---

## 二、API Key 管理

### 2.1 三层 Key 结构

| 类型 | 格式 | 解析结果 | 存储位置 |
|------|------|----------|----------|
| Root Key | `ovk_root_{random_hex_32}` | role=ROOT | `ov.conf` server 段 |
| Account Key | `ovk_acct_{random_hex_32}` | (account_id, role=ACCOUNT_ADMIN) | AGFS `/_system/keys.json` 内存缓存 |
| User Key | `ovk_user_...`（格式取决于选型，见 2.2） | (account_id, user_id, role=USER) | 取决于选型（见 2.2） |

### 2.2 User Key 方案（待评审选型）

User Key 有两种候选方案，需要在设计评审中确定。

**共同点**：两个方案的 key 格式都是 `ovk_user_...`，对外 API 完全一致，差异仅在内部生成和验证机制。

#### 方案 A：加密式 token（确定性推导，适用用 user 过多，存储同步压力大）

**核心思路**：把 `account_id:user_id` 用 AES-GCM 加密生成 key。服务端解密即可还原身份，key 本身不需要存储。

**生成**：`AES_GCM_Encrypt(private_key, "acme:alice")` → `ovk_user_Ek3xJ9fQm2...`
**验证**：`AES_GCM_Decrypt(private_key, key)` → `"acme:alice"` → 查注册表确认用户存在

**特征**：同一个 `(account_id, user_id)` 永远推导出同一个 key（确定性）。

**完整场景**：

```
1. 管理员注册 alice，获取 key
   POST /api/v1/admin/accounts/acme/users         {"user_id": "alice"}  → 注册成功
   GET  /api/v1/admin/accounts/acme/users/alice/key                     → ovk_user_Ek3x...

2. 再次获取 alice 的 key（幂等，返回同一个值）
   GET  /api/v1/admin/accounts/acme/users/alice/key                     → ovk_user_Ek3x...（和上次完全一样）

3. alice 用 key 访问 API
   GET /api/v1/fs/ls?uri=viking://  -H "X-API-Key: ovk_user_Ek3x..."   → 200 OK

4. alice 丢了 key，管理员重新获取（无感恢复，key 不变）
   GET  /api/v1/admin/accounts/acme/users/alice/key                     → ovk_user_Ek3x...（还是同一个）

5. alice 的 key 泄露，想换一个 → 无法单独换，必须轮转 private_key（全部用户的 key 都会变）

6. 管理员移除 alice
   DELETE /api/v1/admin/accounts/acme/users/alice                       → 注册表删除 alice
   alice 再用 key 访问 → 解密成功但注册表找不到 → 401
```

**存储**：keys.json 只存注册列表，不存 key。`ov.conf` 需要 `private_key` 字段。

```json
{ "accounts": { "acme": { "users": ["alice", "bob"] } } }
```

#### 方案 B：随机 key + 查表（每个 user key 都存储）

**核心思路**：注册用户时生成随机 key，存入 keys.json。验证时查表匹配。

**生成**：`secrets.token_hex(32)` → `ovk_user_7f3a...`（存入 keys.json）
**验证**：在 keys.json 中查找 key → 得到 `account_id` 和 `user_id`

**特征**：每次获取 key 都生成新的随机值，旧 key 立即失效。

**完整场景**：

```
1. 管理员注册 alice，获取 key
   POST /api/v1/admin/accounts/acme/users         {"user_id": "alice"}  → 注册成功
   GET  /api/v1/admin/accounts/acme/users/alice/key                     → ovk_user_7f3a...

2. 再次获取 alice 的 key（生成新 key，旧 key 立即失效）
   GET  /api/v1/admin/accounts/acme/users/alice/key                     → ovk_user_b82d...（新 key）
   alice 用旧的 ovk_user_7f3a... 访问 → 401（已失效）

3. alice 用新 key 访问 API
   GET /api/v1/fs/ls?uri=viking://  -H "X-API-Key: ovk_user_b82d..."   → 200 OK

4. alice 丢了 key，管理员重新获取（生成新 key，alice 需要更新）
   GET  /api/v1/admin/accounts/acme/users/alice/key                     → ovk_user_c93f...（又一个新 key）

5. alice 的 key 泄露，想换一个 → 重新获取即可，只影响 alice，不影响 bob

6. 管理员移除 alice
   DELETE /api/v1/admin/accounts/acme/users/alice                       → 注册表和 key 一起删除
   alice 再用 key 访问 → 查表找不到 → 401
```

**存储**：keys.json 存注册列表 + 所有 user key。`ov.conf` 不需要 `private_key`。

```json
{
    "accounts": { "acme": { "users": ["alice", "bob"] } },
    "user_keys": {
        "ovk_user_c93f...": { "account_id": "acme", "user_id": "alice" },
        "ovk_user_d91f...": { "account_id": "acme", "user_id": "bob" }
    }
}
```

#### 方案对比

| 维度 | 方案 A（加密式 token） | 方案 B（随机 key + 查表） |
|------|----------------------|------------------------|
| key 可读性 | 不可读（AES 密文） | 不可读（随机 hex） |
| 信息泄露 | 无 | 无 |
| 存储 | 只存注册列表 | 存每个 key + 身份映射 |
| `ov.conf` | 需要 `private_key` | 不需要 |
| 密码学依赖 | AES-GCM | 无（`secrets.token_hex`） |
| key 丢失恢复 | 重新推导同一个 key | 重新生成新 key，旧的失效 |
| 单用户换 key | 不支持（需轮转 private_key） | 支持 |
| 验证方式 | 解密 + 查注册表 | 查 key 表 |

### 2.3 Key 存储

- **Root Key**：`ov.conf` 的 `server` 段
- **Private Key**（仅方案 A）：`ov.conf` 的 `server` 段
- **Account Keys + User Keys/注册列表**：AGFS `/_system/keys.json`，启动时加载到内存，写操作同步持久化
- `_system` 路径与租户 URI 自然隔离（合法 scope 为 resources/user/agent/session/queue/transactions）

**为什么 Account/User Key 存 AGFS 而不是本地文件**：

`root_api_key` 是静态配置（部署时手动写入 ov.conf），但 account key 和 user key 是运行时通过 Admin API 动态增删的，不能放 ov.conf。动态存储有两个选择：

| | 本地文件 | AGFS |
|---|---|---|
| 单节点 | 可以 | 可以 |
| 多节点部署 | 各节点各自一份，需自行解决同步 | 共享存储，天然一致 |
| 已有依赖 | 需新增本地文件路径管理 | 复用现有 AGFS 基础设施 |
| 启动依赖 | 无 | 需要 AGFS 先启动 |

选择 AGFS 的核心理由是**多节点一致性**：生产环境可能多个 OpenViking server 共享同一个 AGFS 后端，存 AGFS 则所有节点读同一份数据，一个节点创建的 account 其他节点立即可见。启动依赖不是问题——APIKeyManager 在 `service.initialize()` 之后才初始化（见 T5）。

### 2.4 新模块 `openviking/server/api_keys.py`

```python
class APIKeyManager:
    """API Key 生命周期管理与解析"""

    def __init__(self, root_key: Optional[str], private_key: Optional[bytes], agfs_url: str)
    async def load()                                     # 从 AGFS 加载 keys.json 到内存
    async def save()                                     # 持久化到 AGFS
    def resolve(api_key: str) -> ResolvedIdentity        # Key → 身份 + 角色（含注册检查）
    def create_account(account_id: str) -> str           # 创建账户，返回 account key
    def delete_account(account_id: str)                  # 删除账户（清理 key 和用户列表）
    def derive_user_key(account_id, user_id) -> str      # 推导用户 key
    def register_user(account_id, user_id)               # 注册用户到账户
    def remove_user(account_id, user_id)                 # 移除用户
```

---

## 三、认证流程

### 3.1 核心类型

新建 `openviking/server/identity.py`：

```python
class Role(str, Enum):
    ROOT = "root"
    ACCOUNT_ADMIN = "account_admin"
    USER = "user"

@dataclass
class ResolvedIdentity:
    role: Role
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None  # 来自 X-OpenViking-Agent header

@dataclass
class RequestContext:
    user: UserIdentifier       # account_id + user_id + agent_id
    role: Role
```

### 3.2 认证流程

1. 从 `X-API-Key` 或 `Authorization: Bearer` 提取 Key
2. 若未配置 `root_api_key`，进入**本地开发模式**：返回 `(role=ROOT, account_id="default", user_id="default")`
3. 按前缀分派：
   - `ovk_root_` → HMAC 比对 root key
   - `ovk_acct_` → 查内存 account_keys dict
   - `ovk_user_` → 解析身份（方式取决于 2.2 选型）+ 注册检查
4. 从 `X-OpenViking-Agent` header 读取 `agent_id`（默认 `"default"`）
5. 构造 `RequestContext(UserIdentifier(account_id, user_id, agent_id), role)`

### 3.3 FastAPI 依赖注入

改动 `openviking/server/auth.py`：

```python
async def resolve_identity(request, x_api_key, authorization, x_openviking_agent) -> ResolvedIdentity
def require_role(*roles) -> Depends  # 角色守卫工厂
def get_request_context(identity) -> RequestContext  # 构造 RequestContext
```

所有 Router 从 `Depends(verify_api_key)` 迁移到 `Depends(get_request_context)`。

---

## 四、RBAC 模型

### 4.1 为什么是三层角色

采用 ROOT / ACCOUNT_ADMIN / USER 三层角色，而非 ROOT + USER 两层，理由如下：

1. **与三层 Key 结构自然对应**：root key → ROOT，account key → ACCOUNT_ADMIN，user key → USER。角色和 key 一一映射，认证解析逻辑简单直接。
2. **委托式管理链路**：ROOT 创建 account 并下发 account key → 租户管理员自行注册用户并下发 user key。如果只有两层，所有用户管理都必须经过 ROOT，ROOT 成为运营瓶颈。
3. **权限最小化**：user key 泄露只影响单个用户数据；account key 泄露影响该租户但不波及其他租户；root key 影响全局。三层划分使得每层 key 的爆炸半径最小。
4. **数据访问边界**：ACCOUNT_ADMIN 可访问本 account 下所有用户数据（管理审计需要），USER 只能访问自己的隔离空间。这个区分在两层模型中无法表达。
5. **增量成本低**：实现上只是 Role 枚举多一个值 + Admin API 多几个权限检查，不会增加架构复杂度。

### 4.2 角色与权限

| 角色 | 身份 | 能力 |
|------|------|------|
| ROOT | 系统管理员 | 一切：创建/删除账户、生成 Account Key、跨租户访问 |
| ACCOUNT_ADMIN | 账户管理员 | 管理本账户用户、下发 User Key、账户内全量数据访问 |
| USER | 普通用户 | 访问自己的 user/agent/session/resources scope |

权限矩阵：

| 操作 | ROOT | ACCOUNT_ADMIN | USER |
|------|------|---------------|------|
| 创建/删除账户 | Y | N | N |
| 生成 Account Key | Y | N | N |
| 注册/移除用户 | Y | Y (本账户) | N |
| 下发 User Key | Y | Y (本账户) | N |
| FS 读写 (own scope) | Y | Y | Y |
| 跨账户访问 | Y | N | N |
| VectorDB 搜索 | Y (全局) | Y (本账户) | Y (本账户) |
| Session 管理 | Y | Y (本账户所有) | Y (仅自己的) |
| 系统状态 | Y | Y | N |

### 4.3 Agent 归属（待评审）

Agent 通过 User 的 API Key 认证（继承用户身份）。但 Agent 的数据目录（记忆/技能/指令）有两种归属方式：

#### 方案 A：Agent 目录按 agent_id 共享（跨用户共享）

目录仅由 `agent_id` 决定。多个用户使用同一 `agent_id` 时，访问同一份 agent 数据。

```
/{account_id}/agent/{md5(agent_id)[:12]}/memories/cases/
/{account_id}/agent/{md5(agent_id)[:12]}/skills/
/{account_id}/agent/{md5(agent_id)[:12]}/instructions/
```

场景：团队共用编程助手 agent，alice 教它的技能 bob 也能用。

| 维度 | 说明 |
|------|------|
| 优势 | agent 知识多用户协作累积；目录结构简单 |
| 劣势 | 无用户间隔离——alice 对 agent 说的话 bob 也能看到 |
| 适用 | 团队共享工具型 agent |

#### 方案 B：Agent 目录按 user_id + agent_id 隔离（每用户独立）

目录由 `user_id + agent_id` 共同决定。每个用户与 agent 的组合有独立数据空间。

```
/{account_id}/agent/{md5(user_id + agent_id)[:12]}/memories/cases/
/{account_id}/agent/{md5(user_id + agent_id)[:12]}/skills/
/{account_id}/agent/{md5(user_id + agent_id)[:12]}/instructions/
```

场景：alice 和 bob 各自使用同一 agent，但各自有独立的记忆和技能空间。

| 维度 | 说明 |
|------|------|
| 优势 | 用户间 agent 数据完全隔离；与现有 `unique_space_name()` 兼容 |
| 劣势 | agent 知识无法跨用户累积；存储开销 = 用户数 × agent 数 |
| 适用 | 个人助手类 agent |

#### 方案对比

| 维度 | 方案 A（共享） | 方案 B（隔离） |
|------|--------------|--------------|
| 目录路径 | `agent/{hash(agent_id)}/...` | `agent/{hash(user+agent)}/...` |
| agent 知识 | 多用户共同累积 | 每用户独立累积 |
| 隐私 | 无用户间隔离 | 完全隔离 |
| 存储开销 | 低 | 高 |
| 后续扩展 | 可加"用户私有记忆层" | 可加"共享知识库" |

**讨论要点**：产品形态偏向团队协作共享 agent 还是个人私有 agent？是否需要混合模式？

### 4.4 Admin API

新增 Router: `openviking/server/routers/admin.py`

```
POST   /api/v1/admin/accounts                              创建账户 (ROOT)
GET    /api/v1/admin/accounts                              列出账户 (ROOT)
DELETE /api/v1/admin/accounts/{account_id}                 删除账户 (ROOT)，级联清理数据
POST   /api/v1/admin/accounts/{account_id}/users           注册用户 (ROOT, ACCOUNT_ADMIN)
DELETE /api/v1/admin/accounts/{account_id}/users/{uid}     移除用户 (ROOT, ACCOUNT_ADMIN)
POST   /api/v1/admin/accounts/{account_id}/key             生成 Account Key (ROOT)
GET    /api/v1/admin/accounts/{account_id}/users/{uid}/key 下发 User Key (ROOT, ACCOUNT_ADMIN)
```

---

## 五、存储隔离

### 5.1 三维隔离模型

存储隔离有三个独立维度：account、user、agent。

- **account**：顶层隔离，不同租户之间完全不可见
- **user**：同一 account 内，不同用户的私有数据互不可见。用户记忆、资源、session 属于用户本人
- **agent**：同一 account 内，不同 agent 的数据互不可见。agent 的学习记忆、技能、指令属于 agent 本身。agent 目录归属方式（跨用户共享 vs 每用户独立）见 4.3 节待评审

**Space 标识符**：`UserIdentifier` 新增两个方法，拆分现有的 `unique_space_name()`：

```python
def user_space_name(self) -> str:
    """用户级 space，不含 agent_id"""
    return f"{self._account_id}_{hashlib.md5(self._user_id.encode()).hexdigest()[:8]}"

def agent_space_name(self) -> str:
    """Agent 级 space，实现取决于 4.3 节选型"""
    # 方案 A: md5(agent_id)  方案 B: md5(user_id + agent_id)
    return hashlib.md5(self._agent_id.encode()).hexdigest()[:12]  # 方案 A 示例
```

### 5.2 各 Scope 的隔离方式

| scope | AGFS 路径 | 隔离维度 | 说明 |
|-------|-----------|----------|------|
| `user/memories` | `/{account_id}/user/{user_space}/memories/` | account + user | 用户偏好、实体、事件属于用户本人 |
| `agent/memories` | `/{account_id}/agent/{agent_space}/memories/` | account + agent | agent 的学习记忆（归属方式见 4.3） |
| `agent/skills` | `/{account_id}/agent/{agent_space}/skills/` | account + agent | agent 的能力集（归属方式见 4.3） |
| `agent/instructions` | `/{account_id}/agent/{agent_space}/instructions/` | account + agent | agent 的行为规则（归属方式见 4.3） |
| `resources/` | `/{account_id}/resources/{user_space}/` | account + user | 用户的知识资源 |
| `session/` | `/{account_id}/session/{user_space}/{session_id}/` | account + user | 用户的对话记录 |
| `transactions/` | `/{account_id}/transactions/` | account | 账户级事务记录 |
| `_system/` | `/_system/` | 系统级 | 不属于任何 account |

### 5.3 AGFS 文件系统隔离

**改动文件**: `openviking/storage/viking_fs.py`

VikingFS 保持单例，不持有任何租户状态。多租户通过参数传递实现：

**调用链路**：
1. 公开方法（`ls`、`read`、`write` 等）接收 `ctx: RequestContext` 参数
2. 公开方法从 `ctx.account_id` 提取 account_id，传给内部方法
3. 内部方法（`_uri_to_path`、`_path_to_uri`、`_collect_uris` 等）接收 `account_id: str` 参数，不依赖 ctx

**URI → AGFS 路径转换**（加 account_id 前缀）：

```
viking://user/{user_space}/memories/x + account_id="acme"
→ /local/acme/user/{user_space}/memories/x
```

**AGFS 路径 → URI 转换**（去 account_id 前缀）：

```
/local/acme/user/{user_space}/memories/x + account_id="acme"
→ viking://user/{user_space}/memories/x
```

返回给调用方的 URI 不含 account_id，对用户透明。account_id 只存在于 AGFS 物理路径层。

```python
# 公开方法：接收 ctx，提取 account_id，结果按权限过滤
async def ls(self, uri: str, ctx: RequestContext) -> List[str]:
    path = self._uri_to_path(uri, account_id=ctx.account_id)
    entries = await self._agfs.ls(path)
    uris = [self._path_to_uri(e, account_id=ctx.account_id) for e in entries]
    return [u for u in uris if self._is_accessible(u, ctx)]  # 权限过滤，见 5.4

# 内部方法：只接收 account_id，不依赖 ctx
def _uri_to_path(self, uri: str, account_id: str = "") -> str:
    remainder = uri[len("viking://"):].strip("/")
    if account_id:
        return f"/local/{account_id}/{remainder}" if remainder else f"/local/{account_id}"
    return f"/local/{remainder}" if remainder else "/local"

def _path_to_uri(self, path: str, account_id: str = "") -> str:
    inner = path[len("/local/"):]                    # "acme/user/{space}/memories/x"
    if account_id and inner.startswith(account_id + "/"):
        inner = inner[len(account_id) + 1:]          # "user/{space}/memories/x"
    return f"viking://{inner}"
```

### 5.4 逐层权限过滤（Phase2）

user/agent 级隔离通过**逐层遍历时过滤**实现。用户可以从公共根目录（如 `viking://resources`）开始遍历，但每一层只能看到自己有权限的条目。

**示例**：

```
# alice（USER 角色）
ls viking://resources           → 只看到 {alice_user_space}/
ls viking://agent/memories      → 只看到 alice 当前 agent 的 {agent_space}/
ls viking://user/memories       → 只看到 {alice_user_space}/

# admin（ACCOUNT_ADMIN 角色）
ls viking://resources           → 看到所有用户的 space 目录
```

**实现**：VikingFS 新增 `_is_accessible()` 方法：

```python
def _is_accessible(self, uri: str, ctx: RequestContext) -> bool:
    """判断当前用户是否能访问该 URI"""
    if ctx.role in (Role.ROOT, Role.ACCOUNT_ADMIN):
        return True

    # 结构性目录（不含 space，如 viking://user/memories）→ 允许遍历
    space_in_uri = self._extract_space_from_uri(uri)
    if space_in_uri is None:
        return True

    # 含 space 的 URI → 检查 space 是否属于当前用户或其 agent
    return space_in_uri in (
        ctx.user.user_space_name(),
        ctx.user.agent_space_name(),
    )
```

- **列举操作**（`ls`、`tree`、`glob`）：AGFS 返回全量结果后，用 `_is_accessible` 过滤
- **读写操作**（`read`、`write`、`mkdir` 等）：执行前调 `_is_accessible` 校验，无权限则拒绝
- **将来加 ACL**：`_is_accessible` 内部扩展为查 ACL 表，接口不变（见 5.7）

### 5.5 VectorDB 租户隔离

**改动文件**: `openviking/storage/collection_schemas.py`

单 `context` collection，schema 新增两个字段：

- `account_id`（string）：account 级过滤
- `owner_space`（string）：user/agent 级过滤，值为记录所有者的 `user_space_name()` 或 `agent_space_name()`

查询过滤策略（由 retriever 根据 ctx 构造）：

| 角色 | 过滤条件 |
|------|---------|
| ROOT | 无 |
| ACCOUNT_ADMIN | `account_id` = ctx.account_id |
| USER | `account_id` = ctx.account_id AND `owner_space` IN (ctx.user.user_space_name(), ctx.user.agent_space_name()) |

写入时，`Context` 对象携带 `account_id` 和 `owner_space`，通过 `EmbeddingMsgConverter` 透传到 VectorDB。`owner_space` 始终只存原始所有者，不因共享而修改。

### 5.6 目录初始化

**改动文件**: `openviking/core/directories.py`

- 创建新账户时，初始化 account 级预设目录结构（公共根：`viking://user`、`viking://agent`、`viking://resources` 等）
- 用户首次访问时，懒初始化 user space 子目录（`viking://user/{user_space}/memories/preferences` 等）
- agent 首次使用时，懒初始化 agent space 子目录（`viking://agent/{agent_space}/memories/cases` 等）

### 5.7 未来 ACL 扩展方向（本版不实现）

当需要支持用户间资源共享（如 alice 共享某个 resources 目录给 bob）时，有两种扩展路径：

**方案 a：独立 ACL 表**

共享关系存储在独立的 ACL 表中（AGFS 或 VectorDB），不修改数据记录本身：

```
# ACL 记录
{ "grantee_space": "bob_user_space", "granted_uri_prefix": "viking://resources/{alice_space}/project-x" }

# bob 查询时
1. 解析可访问 space 列表：own spaces + 查 ACL 表得到被授权的 spaces
2. VectorDB filter: owner_space IN [bob_user_space, bob_agent_space, alice_user_space]
3. VikingFS _is_accessible: 检查 own space OR ACL 授权
```

优势：数据记录不变，授权/撤销即时生效，不需要批量更新记录。

**方案 b：VectorDB 新增 `shared_spaces` 字段**

在被共享的**目录记录**（非叶子节点）上新增 `shared_spaces` 列表字段，标记哪些 space 有访问权限：

```
# 目录记录
{ "uri": "viking://resources/{alice_space}/project-x", "owner_space": "alice_space", "shared_spaces": ["bob_space"] }

# bob 遍历时
_is_accessible 检查: owner_space 匹配 OR space in shared_spaces
```

优势：权限信息自包含在目录节点上，遍历时不需要额外查 ACL 表。需要配合遍历时的权限继承（子节点继承父目录的 shared_spaces）。

两种方案可结合使用。具体选型在 ACL 设计时确定。

---

## 六、配置变更

### `ov.conf` server 段

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "ovk_root_...",
    "private_key": "hex-encoded-32-byte-secret",
    "cors_origins": ["*"]
  }
}
```

**改动文件**: `openviking/server/config.py`

```python
@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 1933
    root_api_key: Optional[str] = None   # 替代原 api_key
    private_key: Optional[str] = None    # 仅 User Key 方案 A 需要，见 2.2 节
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
```

- `root_api_key`：替代原有的 `api_key`，用于 ROOT 身份认证。为 None 时进入本地开发模式（跳过认证），等同当前行为。
- `private_key`：hex 编码的 32 字节密钥，用于 User Key 方案 A（加密式 token）中 AES 加密/解密 user key payload。如果最终选型为方案 B（随机 key + 查表），此字段不需要，可移除。见 2.2 节 User Key 方案选型。

---

## 七、客户端变更

核心变化：多租户前客户端需要自行传递 `account_id` 和 `user_id`，多租户后这两个字段由服务端从 API Key 解析，客户端只需提供 `api_key` 和可选的 `agent_id`。

| 项目 | 多租户前 | 多租户后 |
|------|---------|---------|
| 身份来源 | 客户端构造 UserIdentifier | 服务端从 API Key 解析 |
| 必须参数 | url, api_key, account_id, user_id | url, api_key |
| 可选参数 | agent_id | agent_id |
| 身份 header | `X-OpenViking-User` + `X-OpenViking-Agent` | 仅 `X-OpenViking-Agent` |

### 7.1 Python SDK

**改动文件**: `openviking_cli/client/http.py`, `openviking_cli/client/sync_http.py`

```python
# 多租户后：身份由服务端从 api_key 解析
client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="ovk_user_7f3a9c1e...",   # 服务端解析出 account_id + user_id
    agent_id="coding-agent",           # 可选，默认 "default"
)
```

### 7.2 CLI

**改动文件**: `openviking_cli/session/user_id.py`

`ovcli.conf` 新增 `agent_id` 字段：

```json
{
  "url": "http://localhost:1933",
  "api_key": "ovk_user_7f3a9c1e...",
  "agent_id": "coding-agent",
  "output": "table"
}
```

CLI 发起请求时通过 `X-OpenViking-Agent` header 携带 agent_id。不再需要配置 `account_id` 和 `user_id`。

### 7.3 嵌入模式（不做多租户）

嵌入模式直接调用 Service 层，内部构造固定默认 RequestContext，不涉及 API Key 和 RBAC：

```python
client = ov.Client(path="/data/openviking")
# 内部等价于 RequestContext(user=default, role=ROOT)
```

---

## 八、部署模式与单租户兼容（待评审）

### 8.1 背景

多租户改变了存储路径结构（新增 `{account_id}` 前缀和 `{user_space}` 子目录层）和 VectorDB schema（新增 `account_id`/`owner_space` 字段）。现有单租户部署只使用 `root_api_key`，不涉及 account/user/agent 概念，存储路径是扁平的。需要决定是否兼容这类部署。

### 8.2 方案 A：双模式共存（配置开关）

通过 `ov.conf` 的 `multi_tenant` 字段区分两种互斥的部署模式，启动时确定，运行期不可切换。

**单租户模式**（`multi_tenant` 不设或 `false`，默认）：

与当前行为完全一致，升级无影响：

- 不配置 `root_api_key` → dev 模式，跳过认证
- 配置 `root_api_key` → 简单 HMAC 比对，通过即可访问全部数据
- 存储路径不变：`viking://resources/doc.md` → `/local/resources/doc.md`
- VectorDB 记录不含租户字段，查询不注入租户过滤
- Admin API 端点不注册，访问返回 404

**多租户模式**（`multi_tenant: true`）：

```json
{
  "server": {
    "multi_tenant": true,
    "root_api_key": "ovk_root_...",
    "cors_origins": ["*"]
  }
}
```

- 必须配置 `root_api_key`
- 启动时初始化 APIKeyManager，加载 keys.json，注册 Admin API 端点
- 存储路径带 account_id 前缀和 space 目录层
- VectorDB 写入携带 `account_id` + `owner_space`，查询按角色注入过滤
- 完整 RBAC（ROOT / ACCOUNT_ADMIN / USER）

**两种模式对比**：

| 维度 | 单租户模式 | 多租户模式 |
|------|-----------|-----------|
| 配置 | `multi_tenant` 不设或 `false` | `multi_tenant: true` |
| 认证 | 可选 `root_api_key`，单一密钥 | 三层 Key（root / account / user） |
| 存储路径 | 扁平：`/local/resources/...` | 隔离：`/local/{account_id}/resources/{user_space}/...` |
| VectorDB | 无租户字段 | 写入和查询携带租户字段 |
| Admin API | 不注册 | 注册 |
| RBAC | 无 | ROOT / ACCOUNT_ADMIN / USER |

**启动时分派**：

```python
if config.multi_tenant:
    api_key_manager = APIKeyManager(root_key=config.root_api_key, ...)
    await api_key_manager.load()
    app.state.api_key_manager = api_key_manager
    app.include_router(admin_router)        # 注册 Admin API
    viking_fs.set_multi_tenant(True)        # VikingFS 使用隔离路径
else:
    app.state.api_key_manager = None        # 单租户，无 key 管理
    viking_fs.set_multi_tenant(False)       # VikingFS 使用扁平路径
```

**约束**：

- 同一份数据不能跨模式使用（路径结构和 VectorDB schema 不同）
- 嵌入模式始终为单租户，不受 `multi_tenant` 配置影响

| 维度 | 说明 |
|------|------|
| 优势 | 现有单租户部署零成本升级；多租户是增量能力，不影响已有用户 |
| 劣势 | 代码需按模式分派路径逻辑和认证逻辑，增加分支；VikingFS、认证中间件、VectorDB 写入/查询均需判断模式 |

### 8.3 方案 B：仅多租户

不保留单租户模式，所有部署统一走多租户路径结构。现有单租户部署升级后需要重新导入数据。

- 不配置 `root_api_key` → dev 模式，跳过认证，使用默认 account/user
- 配置 `root_api_key` → ROOT 角色，account_id="default"，user_id="default"
- 存储路径统一：`viking://resources/doc.md` → `/local/default/resources/{default_user_space}/doc.md`
- VectorDB 记录统一携带 `account_id`/`owner_space` 字段

| 维度 | 说明 |
|------|------|
| 优势 | 代码无分支，路径逻辑和认证逻辑统一；VikingFS、VectorDB 只有一套实现 |
| 劣势 | 现有单租户部署升级后路径变化，已有数据不可见，需重新导入 |

---

## 九、实施分期与任务拆解

### Phase 1：API 层多租户能力定义（已完成）

实施顺序：`T1 → T3 → T2 → T4 → T5 → T10/T11 并行 → T12`

---

#### T1: 身份与角色类型定义

**新建** `openviking/server/identity.py`，依赖：无

定义三个类型，供后续所有任务引用：

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from openviking.session.user_id import UserIdentifier

class Role(str, Enum):
    ROOT = "root"
    ACCOUNT_ADMIN = "account_admin"
    USER = "user"

@dataclass
class ResolvedIdentity:
    """认证中间件的输出：从 API Key 解析出的原始身份信息"""
    role: Role
    account_id: Optional[str] = None   # ROOT 可能无 account_id
    user_id: Optional[str] = None      # ROOT/ACCOUNT_ADMIN 可能无 user_id
    agent_id: Optional[str] = None     # 来自 X-OpenViking-Agent header

@dataclass
class RequestContext:
    """请求级上下文，贯穿 Router → Service → VikingFS 全链路"""
    user: UserIdentifier    # 完整三元组（account_id, user_id, agent_id）
    role: Role

    @property
    def account_id(self) -> str:
        return self.user.account_id
```

**注意**：`RequestContext` 而非 `ResolvedIdentity` 是下游使用的类型。`ResolvedIdentity` 只在 auth 层内部使用，转换为 `RequestContext` 后传递。原因：`ResolvedIdentity` 的字段都是 Optional（ROOT 没有 account_id），而 `RequestContext.user` 是确定的 `UserIdentifier`——对于 ROOT，填入 `account_id="default"`。

---

#### T3: ServerConfig 更新

**修改** `openviking/server/config.py`，依赖：无

改动点：

```python
# 改前
@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 1933
    api_key: Optional[str] = None                          # ← 删除
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

# 改后
@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 1933
    root_api_key: Optional[str] = None                     # ← 替代 api_key
    multi_tenant: bool = False                             # ← 新增，见第 8 节
    private_key: Optional[str] = None                      # ← 新增，仅 User Key 方案 A
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
```

`load_server_config()` 中对应修改读取字段：
```python
config = ServerConfig(
    host=server_data.get("host", "0.0.0.0"),
    port=server_data.get("port", 1933),
    root_api_key=server_data.get("root_api_key"),          # ← 改
    multi_tenant=server_data.get("multi_tenant", False),   # ← 新增
    private_key=server_data.get("private_key"),             # ← 新增
    cors_origins=server_data.get("cors_origins", ["*"]),
)
```

---

#### T2: API Key Manager

**新建** `openviking/server/api_keys.py`，依赖：T1

##### 内存数据结构

```python
# keys.json 在 AGFS 中的结构
{
    "account_keys": {
        "ovk_acct_abc123...": {
            "account_id": "acme_corp",
            "created_at": "2026-02-12T10:00:00Z"
        }
    },
    "accounts": {
        "acme_corp": {
            "created_at": "2026-02-12T10:00:00Z",
            "users": ["alice", "bob"]
        }
    }
}
```

对应内存结构：
```python
self._account_keys: Dict[str, str] = {}       # {key_str -> account_id}
self._accounts: Dict[str, AccountInfo] = {}    # {account_id -> AccountInfo}
# AccountInfo = dataclass(created_at, users: Set[str])
```

##### 方法逻辑

**`__init__(root_key, private_key, agfs_url)`**：
- 存储 root_key, private_key
- 创建 pyagfs.AGFSClient(agfs_url) 用于读写 `/_system/keys.json`

**`async load()`**：
- 从 AGFS 读取 `/_system/keys.json`
- 若文件不存在，初始化空结构并写入
- 解析 JSON 填充 `_account_keys` 和 `_accounts`

**`async save()`**：
- 将内存数据序列化为 JSON，写回 AGFS `/_system/keys.json`

**`resolve(api_key) -> ResolvedIdentity`**：
```
if key.startswith("ovk_root_"):
    hmac.compare_digest(key, self._root_key) → ResolvedIdentity(role=ROOT)
elif key.startswith("ovk_acct_"):
    account_id = self._account_keys.get(key) → ResolvedIdentity(role=ACCOUNT_ADMIN, account_id)
elif key.startswith("ovk_user_"):
    account_id, user_id = 解析身份（方式取决于 2.2 选型）
    检查 account_id 在 _accounts 中存在
    检查 user_id 在 _accounts[account_id].users 中存在
    → ResolvedIdentity(role=USER, account_id, user_id)
else:
    raise UnauthenticatedError
```

**`create_account(account_id) -> str`**：
- 验证 account_id 格式（复用 UserIdentifier 的验证正则）
- 检查 account_id 不重复
- 生成 `ovk_acct_{secrets.token_hex(32)}`
- 写入 _account_keys 和 _accounts
- 调用 save() 持久化
- 返回 account key

**`delete_account(account_id)`**：
- 从 _accounts 删除
- 从 _account_keys 中删除对应的 key
- 调用 save() 持久化
- **注意**：AGFS 数据和 VectorDB 数据的级联清理不在 APIKeyManager 内完成，由 Admin Router 的调用方负责

**`derive_user_key(account_id, user_id) -> str`**：
- 检查 account_id 和 user_id 已注册
- 调用 `derive_user_key(self._private_key, account_id, user_id)` 返回

**`register_user(account_id, user_id)`**：
- 检查 account_id 存在
- 将 user_id 加入 `_accounts[account_id].users`
- 调用 save()

**`remove_user(account_id, user_id)`**：
- 从 `_accounts[account_id].users` 中移除
- 调用 save()

---

#### T4: 认证中间件重写

**重写** `openviking/server/auth.py`，依赖：T1, T2, T3

删除现有的 `verify_api_key()`、`get_user_header()`、`get_agent_header()`，替换为：

**`resolve_identity(request, x_api_key, authorization, x_openviking_agent) -> ResolvedIdentity`**：
```
1. api_key_manager = request.app.state.api_key_manager
2. 若 api_key_manager 为 None（本地开发模式）：
   返回 ResolvedIdentity(role=ROOT, account_id="default", user_id="default", agent_id="default")
3. 提取 key（同现有逻辑：X-API-Key 或 Bearer）
4. identity = api_key_manager.resolve(key)
5. identity.agent_id = x_openviking_agent or "default"
6. 返回 identity
```

**`get_request_context(identity: ResolvedIdentity = Depends(resolve_identity)) -> RequestContext`**：
```
account_id = identity.account_id or "default"
user_id = identity.user_id or "default"
agent_id = identity.agent_id or "default"
return RequestContext(
    user=UserIdentifier(account_id, user_id, agent_id),
    role=identity.role,
)
```

**`require_role(*allowed_roles) -> dependency`**：
```python
def require_role(*allowed_roles: Role):
    async def _check(ctx: RequestContext = Depends(get_request_context)):
        if ctx.role not in allowed_roles:
            raise PermissionDeniedError(f"Requires role: {allowed_roles}")
        return ctx
    return _check
```

---

#### T5: App 初始化集成

**修改** `openviking/server/app.py`，依赖：T2, T4

改动点在 `create_app()` 和 `lifespan()`：

```python
# 改前
app.state.api_key = config.api_key

# 改后（按 multi_tenant 配置分派，见第 8 节）
if config.multi_tenant:
    api_key_manager = APIKeyManager(
        root_key=config.root_api_key,
        agfs_url=service._agfs_url,
    )
    await api_key_manager.load()
    app.state.api_key_manager = api_key_manager
    app.include_router(admin_router)        # 注册 Admin API
    viking_fs.set_multi_tenant(True)        # VikingFS 使用隔离路径
else:
    app.state.api_key_manager = None        # 单租户模式
```

删除 `app.state.api_key`。

**注意**：APIKeyManager 初始化必须在 service.initialize() 之后，因为需要 AGFS URL。时序是：
1. `service = OpenVikingService()` → 启动 AGFS
2. `await service.initialize()` → 初始化 VikingFS/VectorDB
3. `api_key_manager = APIKeyManager(agfs_url=service._agfs_url)` → 用 AGFS 读 keys.json
4. `await api_key_manager.load()`

---

#### T10: Router 依赖注入迁移

**修改文件**：`server/routers/` 下所有 router，依赖：T4

##### Phase 1 改动（已完成）

所有 router 的依赖从 `verify_api_key` 迁移到 `get_request_context`，但 **service 调用不变**（ctx 仅接收，不向下传递）：

```python
# 改前
@router.get("/ls")
async def ls(uri: str, _: bool = Depends(verify_api_key)):
    service = get_service()
    result = await service.fs.ls(uri)
    ...

# Phase 1 改后（ctx 接收但不传递）
@router.get("/ls")
async def ls(uri: str, _ctx: RequestContext = Depends(get_request_context)):
    service = get_service()
    result = await service.fs.ls(uri)  # service 调用不变
    ...
```

##### Phase 2 改动（待实施，依赖 T9）

Service 层适配完成后，将 ctx 传给 service 方法：

```python
# Phase 2 改后
async def ls(uri: str, ctx: RequestContext = Depends(get_request_context)):
    service = get_service()
    result = await service.fs.ls(uri, ctx=ctx)  # 传递 ctx
    ...
```

##### 需要改的 router 列表

| Router 文件 | 端点数量 | 备注 |
|-------------|---------|------|
| `filesystem.py` | ~10 | ls, tree, stat, mkdir, rm, mv, glob 等 |
| `content.py` | ~3 | read, abstract, overview |
| `search.py` | ~2 | find, search |
| `resources.py` | ~2 | add_resource, add_skill |
| `sessions.py` | ~5 | create, list, get, delete, extract, add_message |
| `relations.py` | ~3 | relations, link, unlink |
| `pack.py` | ~2 | export, import |
| `system.py` | ~1 | health（可能不需要 ctx） |
| `debug.py` | ~3 | status, observer 等 |
| `observer.py` | ~1 | 系统监控 |

---

#### T11: Admin Router

**新建** `openviking/server/routers/admin.py`，依赖：T2, T4

##### 端点逻辑

**POST /api/v1/admin/accounts** — 创建账户
```
权限：require_role(ROOT)
入参：{"account_id": "acme_corp"}
逻辑：
  1. api_key_manager.create_account(account_id) → account_key
  2. 为新账户初始化 AGFS 目录结构（调用 DirectoryInitializer）
返回：{"account_id": "acme_corp", "account_key": "ovk_acct_..."}
```

**GET /api/v1/admin/accounts** — 列出账户
```
权限：require_role(ROOT)
逻辑：遍历 api_key_manager._accounts
返回：[{"account_id": "acme_corp", "created_at": "...", "user_count": 2}, ...]
```

**DELETE /api/v1/admin/accounts/{account_id}** — 删除账户
```
权限：require_role(ROOT)
逻辑：
  1. api_key_manager.delete_account(account_id)
  2. 级联清理 AGFS：rm -r /{account_id}/ （通过 VikingFS）
  3. 级联清理 VectorDB：删除 account_id=X 的所有记录
返回：{"deleted": true}
```

**POST /api/v1/admin/accounts/{account_id}/users** — 注册用户
```
权限：require_role(ROOT, ACCOUNT_ADMIN)
额外检查：ACCOUNT_ADMIN 只能操作自己的 account
入参：{"user_id": "alice"}
逻辑：api_key_manager.register_user(account_id, user_id)
返回：{"account_id": "acme_corp", "user_id": "alice"}
```

**DELETE /api/v1/admin/accounts/{account_id}/users/{uid}** — 移除用户
```
权限：require_role(ROOT, ACCOUNT_ADMIN)
逻辑：api_key_manager.remove_user(account_id, uid)
返回：{"deleted": true}
```

**POST /api/v1/admin/accounts/{account_id}/key** — 生成新 Account Key
```
权限：require_role(ROOT)
逻辑：生成新 key，替换旧 key（一个 account 只有一个 key）
返回：{"account_key": "ovk_acct_..."}
```

**GET /api/v1/admin/accounts/{account_id}/users/{uid}/key** — 下发 User Key
```
权限：require_role(ROOT, ACCOUNT_ADMIN)
逻辑：api_key_manager.derive_user_key(account_id, uid)
返回：{"user_key": "ovk_user_..."}
```

注册到 `server/routers/__init__.py` 和 `server/app.py`。

---

#### T12: 客户端 SDK 更新

##### Phase 1 改动（已完成）：HTTP 客户端

**修改文件**：`openviking_cli/client/http.py`, `openviking_cli/client/sync_http.py`，依赖：T4

HTTP 模式新增 `agent_id` 参数，通过 `X-OpenViking-Agent` header 发送：

```python
def __init__(self, url=None, api_key=None, agent_id=None):
    self._agent_id = agent_id

# headers 构建
headers = {}
if self._api_key:
    headers["X-API-Key"] = self._api_key
if self._agent_id:
    headers["X-OpenViking-Agent"] = self._agent_id
```

身份由服务端从 API Key 解析，客户端不构造 `UserIdentifier`。

##### Phase 2 改动（待实施，依赖 T9）：嵌入模式

**修改文件**：`openviking/client/local.py`，依赖：T9

嵌入模式不做多租户。Service 层 ctx 适配完成后，LocalClient 构造固定默认 ctx 传给 service 方法：

```python
def __init__(self, path=None):
    self._service = OpenVikingService(path=path)
    self._ctx = RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role.ROOT,
    )

async def ls(self, uri, ...):
    return await self._service.fs.ls(uri, ctx=self._ctx)
```

嵌入模式不涉及 API Key、RBAC、多租户隔离。

---

#### T14-P1: 认证与管理测试（已完成）

**T14a: APIKeyManager 单元测试**
- root key 验证（正确/错误）
- account key 生成和解析
- user key 推导和验证（正确签名/错误签名/未注册用户）
- 用户注册/移除后 key 有效性变化
- keys.json 持久化和加载

**T14b: 认证中间件测试**
- 三种 key 类型的 resolve_identity 流程
- 本地开发模式（无 root_api_key）
- require_role 守卫
- 无效 key / 缺失 key 的错误码

**T14e: 回归**
- 现有测试改为使用 dev mode（不配置 root_api_key）

---

### Phase 2：存储层隔离实现（后续）

实施顺序：`T6/T7 并行 → T8 → T9 → T13 → T14-P2`

---

#### T6: VikingFS 多租户改造

**修改** `openviking/storage/viking_fs.py`，依赖：T1

##### 需要加 `ctx` 参数的方法（全部公开方法）

VikingFS 有以下公开方法需要加 `ctx: RequestContext` 参数：

| 方法 | 调用 `_uri_to_path` | 备注 |
|------|---------------------|------|
| `read(uri, ctx)` | Y | |
| `write(uri, data, ctx)` | Y | |
| `mkdir(uri, ctx, ...)` | Y | |
| `rm(uri, ctx, ...)` | Y | |
| `mv(old_uri, new_uri, ctx)` | Y | |
| `grep(uri, pattern, ctx, ...)` | Y | |
| `stat(uri, ctx)` | Y | |
| `glob(pattern, uri, ctx)` | Y（间接，通过 tree） | |
| `tree(uri, ctx)` | Y | |
| `ls(uri, ctx)` | Y | |
| `find(query, ctx, ...)` | N（不直接调 _uri_to_path，但 retriever 需要 ctx） | |
| `search(query, ctx, ...)` | N（同上） | |
| `abstract(uri, ctx)` | Y | |
| `overview(uri, ctx)` | Y | |
| `relations(uri, ctx)` | Y | |
| `link(from_uri, uris, ctx, ...)` | Y | |
| `unlink(from_uri, uri, ctx)` | Y | |
| `write_file(uri, content, ctx)` | Y | |
| `read_file(uri, ctx)` | Y | |
| `read_file_bytes(uri, ctx)` | Y | |
| `write_file_bytes(uri, content, ctx)` | Y | |
| `append_file(uri, content, ctx)` | Y | |
| `move_file(from_uri, to_uri, ctx)` | Y | |
| `write_context(uri, ctx, ...)` | Y | |
| `read_batch(uris, ctx, ...)` | Y（间接） | |

##### 核心改动

VikingFS 新增 `_multi_tenant: bool` 标志，启动时由 app 设置（见 T5、第 8 节）。

`_uri_to_path` 和 `_path_to_uri` 行为取决于模式：
- 单租户（`_multi_tenant=False`）：忽略 account_id，保持旧扁平路径
- 多租户（`_multi_tenant=True`）：加 account_id 前缀

```python
def _uri_to_path(self, uri: str, account_id: str = "") -> str:
    remainder = uri[len("viking://"):].strip("/")
    if self._multi_tenant and account_id:
        return f"/local/{account_id}/{remainder}" if remainder else f"/local/{account_id}"
    return f"/local/{remainder}" if remainder else "/local"

def _path_to_uri(self, path: str, account_id: str = "") -> str:
    if path.startswith("viking://"):
        return path
    elif path.startswith("/local/"):
        inner = path[7:]  # 去掉 /local/
        if self._multi_tenant and account_id and inner.startswith(account_id + "/"):
            inner = inner[len(account_id) + 1:]  # 去掉 account_id 前缀
        return f"viking://{inner}"
    ...
```

##### 私有方法的处理

内部方法 `_collect_uris`, `_delete_from_vector_store`, `_update_vector_store_uris`, `_ensure_parent_dirs`, `_read_relation_table`, `_write_relation_table` 不直接接受 ctx，而是由公开方法调用时已经完成了 `_uri_to_path` 转换，传入的是 AGFS path。

但 `_collect_uris` 内部调用 `_path_to_uri` 时需要 account_id 来正确还原 URI → 需要传 account_id 或 ctx 给这些内部方法。

**策略**：内部方法统一加 `account_id: str = ""` 参数（不用整个 ctx），公开方法从 `ctx.account_id` 提取后传入。

---

#### T7: VectorDB schema 扩展

**修改** `openviking/storage/collection_schemas.py`，依赖：无

在 `context_collection()` 的 Fields 列表中新增：

```python
{"FieldName": "account_id", "FieldType": "string"},
```

位置放在 `id` 之后、`uri` 之前。

同时修改 `TextEmbeddingHandler.on_dequeue()`：`inserted_data` 中应已包含 `account_id`（由 T8 中 EmbeddingMsg 携带）。此处不需要额外改动，只需确保 schema 定义了该字段。

---

#### T8: 检索层与数据写入的租户过滤

**修改文件**：`retrieve/hierarchical_retriever.py`, `core/context.py`，依赖：T1, T7

##### 8a. Context 对象增加 account_id 和 owner_space

`openviking/core/context.py` 中 `Context` 类需增加两个字段：

```python
account_id: str = ""      # 所属 account
owner_space: str = ""     # 所有者的 user_space_name() 或 agent_space_name()
```

`to_dict()` 输出包含这两个字段，`EmbeddingMsgConverter.from_context()` 无需改动即可透传到 VectorDB。

上游构造 Context 时需从 RequestContext 填入这两个字段：
- `ResourceService` / `SkillProcessor` → `account_id=ctx.account_id`, `owner_space=ctx.user.user_space_name()` 或 `agent_space_name()`（取决于 scope）
- `MemoryExtractor.create_memory()` → 同上
- `DirectoryInitializer._ensure_directory()` → 同上

##### 8b. HierarchicalRetriever 注入多级过滤

`retrieve/hierarchical_retriever.py` 的 `retrieve()` 方法需接受 `ctx: RequestContext` 参数，根据角色构造不同粒度的过滤条件（见第五节 5.5）：

```python
async def retrieve(self, query: TypedQuery, ctx: RequestContext, ...) -> QueryResult:
    filters = []
    if ctx.role == Role.ACCOUNT_ADMIN:
        filters.append({"op": "must", "field": "account_id", "conds": [ctx.account_id]})
    elif ctx.role == Role.USER:
        filters.append({"op": "must", "field": "account_id", "conds": [ctx.account_id]})
        filters.append({"op": "must", "field": "owner_space",
                        "conds": [ctx.user.user_space_name(), ctx.user.agent_space_name()]})
    # ROOT 无过滤
```

调用方（`VikingFS.find()`, `VikingFS.search()`）从 ctx 传入。

---

#### T9: Service 层适配

**修改文件**：`service/core.py` 及 `service/fs_service.py`, `service/search_service.py`, `service/session_service.py`, `service/resource_service.py`, `service/relation_service.py`, `service/pack_service.py`, `service/debug_service.py`，依赖：T1, T6

##### 核心变更：去除 `_user` 单例

`OpenVikingService.__init__()` 中删除 `self._user`。
`set_dependencies()` 调用中删除 `user=self.user` 参数。

##### 各 sub-service 改动模式

所有 sub-service 当前的模式是：
```python
class XXXService:
    def set_dependencies(self, viking_fs, ..., user=None):
        self._viking_fs = viking_fs
        self._user = user  # ← 删除

    async def some_method(self, ...):
        # 使用 self._viking_fs 和 self._user
```

改为：
```python
class XXXService:
    def set_dependencies(self, viking_fs, ...):  # 去掉 user
        self._viking_fs = viking_fs

    async def some_method(self, ..., ctx: RequestContext):  # 加 ctx
        # 使用 self._viking_fs 和 ctx
```

##### 逐 service 改动清单

**FSService**（`service/fs_service.py`）：
- 当前：`ls(uri)`, `tree(uri)`, `stat(uri)`, `mkdir(uri)`, `rm(uri)`, `mv(old, new)`, `read(uri)`, `abstract(uri)`, `overview(uri)`, `grep(uri, pattern)`, `glob(pattern, uri)`
- 改为：所有方法加 `ctx` 参数，传递给 VikingFS 调用

**SearchService**（`service/search_service.py`）：
- 当前：`find(query, ...)`, `search(query, ...)`
- 改为：加 `ctx`，传给 VikingFS.find/search

**SessionService**（`service/session_service.py`）：
- 当前：`session(session_id)`, `sessions()`, `delete(session_id)`, `extract(session_id)` 使用 `self._user`
- 改为：加 `ctx`，构造 Session 时从 ctx 获取 user，extract 时传 ctx.user 给 compressor
- session 路径变为 `viking://session/{ctx.user.user_space_name()}/{session_id}`

**ResourceService**（`service/resource_service.py`）：
- 当前：`add_resource(...)`, `add_skill(...)` 使用 `self._user`
- 改为：加 `ctx`，构造 Context 时填入 `account_id=ctx.account_id`, `owner_space=ctx.user.user_space_name()`（resources scope）或 `ctx.user.agent_space_name()`（agent scope）
- 资源路径使用 `viking://resources/{ctx.user.user_space_name()}/...`，技能路径使用 `viking://agent/skills/{ctx.user.agent_space_name()}/...`

**RelationService**（`service/relation_service.py`）：
- 当前：`relations(uri)`, `link(from, to)`, `unlink(from, to)`
- 改为：加 `ctx`，传给 VikingFS

**PackService**（`service/pack_service.py`）：
- 当前：`export_ovpack(uri)`, `import_ovpack(data)`
- 改为：加 `ctx`，传给 VikingFS

**DebugService**（`service/debug_service.py`）：
- 当前：`get_status()`, `observer` 等系统级方法
- 改为：部分方法可能不需要 ctx（如 health check），但 observer 需要

---

#### T13: 目录初始化适配

**修改文件**：`core/directories.py`，依赖：T6, T8

##### 核心改动

`DirectoryInitializer` 当前在 `service.initialize()` 中调用，初始化全局预设目录。多租户后改为三种初始化时机：

1. **创建新 account 时**（Admin API T11）→ 初始化该 account 的公共根目录（`viking://user`、`viking://agent`、`viking://resources` 等）
2. **用户首次访问时** → 懒初始化 user space 子目录（`viking://user/{user_space}/memories/preferences` 等）
3. **agent 首次使用时** → 懒初始化 agent space 子目录（`viking://agent/{agent_space}/memories/cases` 等）

方法签名改为接受 `ctx: RequestContext`：

```python
async def initialize_account_directories(self, ctx: RequestContext) -> int:
    """初始化 account 级公共根目录"""
    ...

async def initialize_user_directories(self, ctx: RequestContext) -> int:
    """初始化 user space 子目录"""
    ...

async def initialize_agent_directories(self, ctx: RequestContext) -> int:
    """初始化 agent space 子目录"""
    ...
```

`_ensure_directory` 和 `_create_agfs_structure` 中需要：
- 通过 ctx 传入 account_id 给 VikingFS
- 构造 Context 时填入 `account_id` 和 `owner_space`，写入 VectorDB 的记录也包含这两个字段

---

#### T14-P2: 隔离与可见性测试

**T14c: 存储隔离测试**
- `_uri_to_path` 加 account_id 前缀正确性
- `_path_to_uri` 反向转换正确性
- `_is_accessible` 对 USER/ACCOUNT_ADMIN/ROOT 的行为
- VectorDB 查询带 account_id + owner_space 多级过滤
- 同 account 下不同 user 无法互相访问 resources 和 memories
- 同 account 下共用同一 agent 的用户能访问该 agent 的数据

**T14d: 端到端集成测试**
- Root Key 创建 account → Account Key 注册 user → User Key 写数据 → 另一 account 查不到
- 同 account 两个 user 写 resources → 互相查不到
- 同 account 两个 user 使用同一 agent → agent 数据共享
- 删除用户后旧 key 认证失败
- 删除 account 后数据清理

---

## 九、关键文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `openviking/server/identity.py` | **新建** | Role, ResolvedIdentity, RequestContext |
| `openviking/server/api_keys.py` | **新建** | APIKeyManager |
| `openviking/server/routers/admin.py` | **新建** | Admin 管理端点 |
| `openviking/server/auth.py` | 重写 | verify_api_key → resolve_identity + require_role + get_request_context |
| `openviking/server/config.py` | 修改 | api_key → root_api_key + multi_tenant + private_key |
| `openviking/server/app.py` | 修改 | 初始化 APIKeyManager |
| `openviking/storage/viking_fs.py` | 修改 | 方法加 ctx 参数，_uri_to_path 加 account_id 前缀 |
| `openviking/storage/collection_schemas.py` | 修改 | context collection 加 account_id + owner_space 字段 |
| `openviking/retrieve/hierarchical_retriever.py` | 修改 | 查询注入 account_id + owner_space 多级过滤 |
| `openviking/service/core.py` | 修改 | 去除单例 _user，传递 RequestContext |
| `openviking/service/*.py` | 修改 | 各 sub-service 接受 RequestContext |
| `openviking/server/routers/*.py` | 修改 | 迁移到 get_request_context 依赖 |
| `openviking/core/directories.py` | 修改 | 按 account 初始化目录 |
| `openviking/client/http.py` | 修改 | 新增 agent_id 参数 |
| `openviking/session/user_id.py` | 修改 | 新增 user_space_name() 和 agent_space_name() 方法 |

---

## 十、验证方案

1. **单元测试**：APIKeyManager 的 key 生成、推导、验证、注册检查
2. **集成测试**：Account A 无法看到 Account B 的数据（AGFS + VectorDB）
3. **端到端测试**：
   - Root Key 创建账户 → Account Key 注册用户 → User Key 操作数据 → 验证隔离
   - 删除用户后旧 user key 失败
   - 删除账户后级联清理数据
   - 本地开发模式（无 Key）正常工作
4. **单租户模式测试**（若采用第 8 节方案 A）：
   - `multi_tenant=false` 时存储路径为旧扁平结构
   - Admin API 返回 404
   - `root_api_key` 认证行为与多租户前一致
5. **回归测试**：现有测试适配新认证流程（使用 dev mode）

---

## 待评审决策项（TODO）

以下设计点需要在评审中讨论确定：

1. **User Key 方案选型**（见 2.2 节）：方案 A（加密式 token，确定性推导，不存储 key）vs 方案 B（随机 key + 查表，无密码学依赖）。选型影响 `ov.conf` 是否需要 `private_key` 字段、APIKeyManager 的实现逻辑、keys.json 的存储结构。

2. **Agent 目录归属模型**（见 4.3 节）：方案 A（按 agent_id 共享，跨用户）vs 方案 B（按 user_id + agent_id 隔离，每用户独立）。选型影响 agent 记忆/技能/指令的可见范围和 `agent_space_name()` 实现。

3. **单租户兼容**（见 8 节）：
   - **方案 A：支持双模式**——通过 `multi_tenant` 配置开关区分单租户/多租户。单租户保持当前扁平路径，行为不变；两种模式互斥、不可切换。代码需按模式分派路径逻辑和认证逻辑。
   - **方案 B：仅多租户**——所有部署统一走多租户路径结构，不保留单租户模式。现有数据需要迁移或重新导入。代码无分支，路径逻辑统一。

