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
[Auth Middleware] ── 提取 API Key，先比对 root key，再查 user key 表 → (account_id, user_id, role)
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

### 2.1 两层 Key 结构

| 类型 | 格式 | 解析结果 | 存储位置 |
|------|------|----------|----------|
| Root Key | `secrets.token_hex(32)` | role=ROOT | `ov.conf` server 段 |
| User Key | `secrets.token_hex(32)` | (account_id, user_id, role) | per-account `/{account_id}/_system/users.json` |

所有 API Key 均为纯随机 token，不带前缀，不携带任何身份信息。Key 本身不区分 root 还是 user —— 服务端通过查表确定身份：先比对 root key，不匹配则查 user key 索引。

用户的角色（ADMIN / USER）不由 key 决定，而是存储在 account 内的用户注册表中。

### 2.2 User Key 机制

注册用户时生成随机 key，存入对应 account 的 `users.json`。验证时查表匹配。

**生成**：`secrets.token_hex(32)` → `7f3a9c1e...`（存入 users.json）
**验证**：先比对 root key → 不匹配 → 在内存索引中查找 → 得到 `(account_id, user_id, role)`

**完整场景**：

```
1. Root 创建工作区 acme，指定 alice 为首个 admin
   POST /api/v1/admin/accounts  {"account_id": "acme", "admin_user_id": "alice"}
   → 创建工作区 + 注册 alice(role=admin) + 返回 alice 的 key: 7f3a9c1e...

2. alice 用 key 访问 API
   GET /api/v1/fs/ls?uri=viking://  -H "X-API-Key: 7f3a9c1e..."   → 200 OK

3. alice（admin）注册普通用户 bob
   POST /api/v1/admin/accounts/acme/users  {"user_id": "bob"}      → 注册成功 + 返回 key: d91f5b2a...

4. bob 丢了 key，alice 重新生成（旧 key 立即失效）
   POST /api/v1/admin/accounts/acme/users/bob/key                  → e82d4e0f...（新 key）
   bob 用旧的 d91f5b2a... 访问 → 401（已失效）

5. bob 的 key 泄露 → 重新生成即可，只影响 bob

6. alice 移除 bob
   DELETE /api/v1/admin/accounts/acme/users/bob                    → 注册表和 key 一起删除
   bob 再用 key 访问 → 查表找不到 → 401
```

### 2.3 Key 存储

- **Root Key**：`ov.conf` 的 `server` 段（静态配置）
- **全局工作区列表**：AGFS `/_system/accounts.json`
- **Per-account 用户注册表**：AGFS `/{account_id}/_system/users.json`

存储结构示例：

```json
// /_system/accounts.json —— 全局工作区列表
{
    "accounts": {
        "default": { "created_at": "2026-02-13T00:00:00Z" },
        "acme": { "created_at": "2026-02-13T10:00:00Z" }
    }
}

// /acme/_system/users.json —— acme 工作区的用户注册表
{
    "users": {
        "alice": { "role": "admin", "key": "7f3a9c1e..." },
        "bob":   { "role": "user",  "key": "d91f5b2a..." }
    }
}
```

启动时加载所有 account 的 `users.json` 到内存，构建全局 key → (account_id, user_id, role) 索引。写操作持久化到对应 account 目录。

**为什么存 AGFS**：User key 是运行时通过 Admin API 动态增删的，不能放 ov.conf。选择 AGFS 的核心理由是多节点一致性——多个 server 共享同一个 AGFS 后端时，一个节点创建的用户其他节点立即可见。

### 2.4 新模块 `openviking/server/api_keys.py`

```python
class APIKeyManager:
    """API Key 生命周期管理与解析"""

    def __init__(self, root_key: Optional[str], agfs_url: str)
    async def load()                                     # 加载所有 account 的 users.json 到内存
    async def save_account(account_id: str)              # 持久化指定 account 的 users.json
    def resolve(api_key: str) -> ResolvedIdentity        # Key → 身份 + 角色
    def create_account(account_id: str, admin_user_id: str) -> str  # 创建工作区 + 首个 admin，返回 admin 的 user key
    def delete_account(account_id: str)                  # 删除工作区
    def register_user(account_id, user_id, role) -> str  # 注册用户，返回 user key
    def remove_user(account_id, user_id)                 # 移除用户
    def regenerate_key(account_id, user_id) -> str       # 重新生成 user key（旧 key 失效）
    def set_role(account_id, user_id, role)              # 修改用户角色（仅 ROOT）
```

---

## 三、认证流程

### 3.1 核心类型

新建 `openviking/server/identity.py`：

```python
class Role(str, Enum):
    ROOT = "root"
    ADMIN = "admin"          # account 内的管理员（用户属性，非 key 类型）
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
2. 若未配置 `root_api_key`，进入 **dev 模式**：返回 `(role=ROOT, account_id="default", user_id="default")`
3. 顺序匹配（Key 无前缀，纯随机 token）：
   - HMAC 比对 root key → 匹配则 role=ROOT
   - 查 user key 内存索引 → 匹配则得到 (account_id, user_id, role)，role 为 ADMIN 或 USER
   - 均不匹配 → 401 Unauthorized
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

### 4.1 三层角色

采用 ROOT / ADMIN / USER 三层角色。ADMIN 是用户在 account 内的角色属性，不由 key 类型决定。两层 key（root/user）+ 角色属性的设计：

1. **委托式管理链路**：ROOT 创建 account 并指定首个 admin → admin 自行注册用户并下发 user key。ROOT 不需要介入日常用户管理。
2. **灵活的 admin 管理**：一个 account 可以有多个 admin，ROOT 可以随时提升/降低用户角色。
3. **权限最小化**：user key 泄露只影响单个用户数据；admin 泄露影响该 account 但不波及其他 account；root key 影响全局。
4. **数据访问边界**：ADMIN 可访问本 account 下所有用户数据（管理审计需要），USER 只能访问自己的隔离空间。

### 4.2 角色与权限

| 角色 | 身份 | 能力 |
|------|------|------|
| ROOT | 系统管理员 | 一切：创建/删除工作区、指定 admin、跨租户访问 |
| ADMIN | 工作区管理员 | 管理本 account 用户、下发 User Key、账户内全量数据访问 |
| USER | 普通用户 | 访问自己的 user/agent/session scope + account 内共享 resources |

权限矩阵：

| 操作 | ROOT | ADMIN | USER |
|------|------|-------|------|
| 创建/删除工作区 | Y | N | N |
| 提升用户为 admin | Y | N | N |
| 注册/移除用户 | Y | Y (本 account) | N |
| 下发/重置 User Key | Y | Y (本 account) | N |
| FS 读写 (own scope) | Y | Y | Y |
| 跨 account 访问 | Y | N | N |
| VectorDB 搜索 | Y (全局) | Y (本 account) | Y (本 account) |
| Session 管理 | Y | Y (本 account 所有) | Y (仅自己的) |
| 系统状态 | Y | Y | N |

### 4.3 Agent 归属

Agent 目录由 `user_id + agent_id` 共同决定，每个用户与 agent 的组合有独立数据空间：

```
/{account_id}/agent/{md5(user_id + agent_id)[:12]}/memories/cases/
/{account_id}/agent/{md5(user_id + agent_id)[:12]}/skills/
/{account_id}/agent/{md5(user_id + agent_id)[:12]}/instructions/
```

alice 和 bob 使用同一 agent_id 时，各自有独立的记忆和技能空间，互不可见。如果后续需要团队共享 agent 知识，可通过 ACL 机制（见 5.7）扩展。

### 4.4 Admin API

新增 Router: `openviking/server/routers/admin.py`

```
POST   /api/v1/admin/accounts                              创建工作区 + 首个 admin (ROOT)
GET    /api/v1/admin/accounts                              列出工作区 (ROOT)
DELETE /api/v1/admin/accounts/{account_id}                 删除工作区 (ROOT)，级联清理数据
POST   /api/v1/admin/accounts/{account_id}/users           注册用户 (ROOT, ADMIN)
DELETE /api/v1/admin/accounts/{account_id}/users/{uid}     移除用户 (ROOT, ADMIN)
GET    /api/v1/admin/accounts/{account_id}/users/{uid}/key 重新生成 User Key (ROOT, ADMIN)
PUT    /api/v1/admin/accounts/{account_id}/users/{uid}/role 修改用户角色 (ROOT)
```

---

## 五、存储隔离

### 5.1 三维隔离模型

存储隔离有三个独立维度：account、user、agent。

- **account**：顶层隔离，不同租户之间完全不可见
- **user**：同一 account 内，不同用户的私有数据互不可见。用户记忆、资源、session 属于用户本人
- **agent**：同一 account 内，agent 目录由 user_id + agent_id 共同决定，每用户独立（见 4.3）

**Space 标识符**：`UserIdentifier` 新增两个方法，拆分现有的 `unique_space_name()`：

```python
def user_space_name(self) -> str:
    """用户级 space，不含 agent_id"""
    return f"{self._account_id}_{hashlib.md5(self._user_id.encode()).hexdigest()[:8]}"

def agent_space_name(self) -> str:
    """Agent 级 space，由 user_id + agent_id 共同决定"""
    return hashlib.md5((self._user_id + self._agent_id).encode()).hexdigest()[:12]
```

### 5.2 各 Scope 的隔离方式

| scope | AGFS 路径 | 隔离维度 | 说明 |
|-------|-----------|----------|------|
| `user/memories` | `/{account_id}/user/{user_space}/memories/` | account + user | 用户偏好、实体、事件属于用户本人 |
| `agent/memories` | `/{account_id}/agent/{agent_space}/memories/` | account + user + agent | agent 的学习记忆，每用户独立 |
| `agent/skills` | `/{account_id}/agent/{agent_space}/skills/` | account + user + agent | agent 的能力集，每用户独立 |
| `agent/instructions` | `/{account_id}/agent/{agent_space}/instructions/` | account + user + agent | agent 的行为规则，每用户独立 |
| `resources/` | `/{account_id}/resources/` | account | account 内共享的知识资源 |
| `session/` | `/{account_id}/session/{user_space}/{session_id}/` | account + user | 用户的对话记录 |
| `transactions/` | `/{account_id}/transactions/` | account | 账户级事务记录 |
| `_system/`（全局） | `/_system/` | 系统级 | 全局工作区列表 |
| `_system/`（per-account） | `/{account_id}/_system/` | account | 用户注册表 |

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
ls viking://resources           → 看到 account 内共享的 resources（无 user 隔离）
ls viking://agent/memories      → 只看到 alice 当前 agent 的 {agent_space}/
ls viking://user/memories       → 只看到 {alice_user_space}/

# admin（ADMIN 角色）
ls viking://resources           → 同上，resources 在 account 内共享
ls viking://user/memories       → 看到所有用户的 space 目录
```

**实现**：VikingFS 新增 `_is_accessible()` 方法：

```python
def _is_accessible(self, uri: str, ctx: RequestContext) -> bool:
    """判断当前用户是否能访问该 URI"""
    if ctx.role in (Role.ROOT, Role.ADMIN):
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
| ADMIN | `account_id` = ctx.account_id |
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
    "root_api_key": "your-secret-root-key",
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
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
```

- `root_api_key`：替代原有的 `api_key`，用于 ROOT 身份认证。为 None 时进入本地开发模式（跳过认证）。
- 已移除 `private_key`（User Key 采用随机存储方案，不需要加密密钥）和 `multi_tenant`（统一多租户，不区分部署模式）。

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
    api_key="7f3a9c1e...",             # 服务端查表解析出 account_id + user_id
    agent_id="coding-agent",           # 可选，默认 "default"
)
```

### 7.2 CLI

**改动文件**: `openviking_cli/session/user_id.py`

`ovcli.conf` 新增 `agent_id` 字段：

```json
{
  "url": "http://localhost:1933",
  "api_key": "7f3a9c1e...",
  "agent_id": "coding-agent",
  "output": "table"
}
```

CLI 发起请求时通过 `X-OpenViking-Agent` header 携带 agent_id。不再需要配置 `account_id` 和 `user_id`。

### 7.3 嵌入模式

嵌入模式支持多租户，通过构造参数传入 `UserIdentifier`。无 API Key 认证，身份由调用方直接声明（嵌入模式的调用方是可信代码）。

```python
# 默认（单用户，使用 default 工作区）
client = ov.Client(path="/data/openviking")

# 多租户（指定身份）
from openviking_cli.session.user_id import UserIdentifier
user = UserIdentifier("acme", "alice", "coding-agent")
client = ov.Client(path="/data/openviking", user=user)
```

内部将 `UserIdentifier` 转为 `RequestContext` 传给 Service 层，路径隔离和权限过滤逻辑与 HTTP 模式一致。

---

## 八、部署模式

多租户为**破坏性改造**，不保留单租户模式。所有部署统一走多租户路径结构。

### 8.1 统一路径结构

所有 account（包括 default）使用层级路径：

```
/local/{account_id}/resources/...
/local/{account_id}/user/{user_space}/memories/...
/local/{account_id}/agent/{agent_space}/memories/...
```

原有扁平路径 `/local/resources/...` 不再使用，现有数据需重新导入。

### 8.2 运行模式

| 配置 | 行为 |
|------|------|
| 不配置 `root_api_key` | Dev 模式：跳过认证，使用 default account + default user + ROOT 角色 |
| 配置 `root_api_key` | 生产模式：强制 API Key 认证，支持多 account 和多用户 |

两种配置使用**完全相同的路径结构和 VectorDB schema**，区别仅在认证层：
- Dev 模式不验证 Key，自动填充默认身份
- 生产模式从 Key 解析身份

代码无分支逻辑，VikingFS 和 VectorDB 只有一套实现。

### 8.3 升级与数据迁移

旧版（单租户）升级到多租户后，存储结构变化：

| 影响 | 旧结构 | 新结构 |
|------|--------|--------|
| resources | `/local/resources/...` | `/local/default/resources/...` |
| user memories | `/local/user/memories/...` | `/local/default/user/{default_space}/memories/...` |
| agent data | `/local/agent/memories/...` | `/local/default/agent/{default_space}/memories/...` |
| session | `/local/session/...` | `/local/default/session/{default_space}/...` |
| VectorDB | 无 `account_id` 字段 | 需补 `account_id="default"` + `owner_space` |

迁移目标始终是 `default` account + `default` user，映射关系完全确定。

提供 CLI 迁移命令（Phase 2 实现）：

```bash
python -m openviking migrate
```

迁移逻辑：
1. 检测旧结构（`/local/resources/` 存在但 `/local/default/` 不存在）
2. 创建 default account 目录结构
3. 搬迁 AGFS 文件到新路径
4. Batch update VectorDB 记录，补充 `account_id` 和 `owner_space` 字段
5. 输出迁移报告（搬迁文件数、更新记录数）

用户升级流程：停服 → 备份 → 执行 `migrate` → 验证 → 启动新版

---

## 九、实施分期与任务拆解

### Phase 1：API 层多租户能力定义

实施顺序：`T1 → T3 → T2 → T4 → T5 → T10/T11 并行 → T12 → T16-P1 → T17-P1 → T14-P1`

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
    ADMIN = "admin"          # account 内的管理员（用户属性，非 key 类型）
    USER = "user"

@dataclass
class ResolvedIdentity:
    """认证中间件的输出：从 API Key 解析出的原始身份信息"""
    role: Role
    account_id: Optional[str] = None   # ROOT 可能无 account_id
    user_id: Optional[str] = None      # ROOT 可能无 user_id
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
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
```

`load_server_config()` 中对应修改读取字段：
```python
config = ServerConfig(
    host=server_data.get("host", "0.0.0.0"),
    port=server_data.get("port", 1933),
    root_api_key=server_data.get("root_api_key"),          # ← 改
    cors_origins=server_data.get("cors_origins", ["*"]),
)
```

---

#### T2: API Key Manager

**新建** `openviking/server/api_keys.py`，依赖：T1

##### 存储结构

Per-account 存储，两级文件：

```python
# /_system/accounts.json — 全局工作区列表
{
    "accounts": {
        "default": {"created_at": "2026-02-12T10:00:00Z"},
        "acme": {"created_at": "2026-02-13T08:00:00Z"}
    }
}

# /{account_id}/_system/users.json — 该 account 的用户注册表
{
    "users": {
        "alice": {"role": "admin", "key": "7f3a9c1e..."},
        "bob": {"role": "user", "key": "d91f5b2a..."}
    }
}
```

内存索引（启动时从所有 account 加载）：
```python
self._user_keys: Dict[str, UserKeyEntry] = {}   # {key_str -> (account_id, user_id, role)}
self._accounts: Dict[str, AccountInfo] = {}      # {account_id -> AccountInfo(users)}
```

##### 方法逻辑

**`__init__(root_key, agfs_url)`**：
- 存储 root_key
- 创建 pyagfs.AGFSClient(agfs_url) 用于读写 AGFS 文件

**`async load()`**：
- 从 AGFS 读取 `/_system/accounts.json`，若不存在则创建 default account
- 遍历每个 account，读取 `/{account_id}/_system/users.json`
- 构建全局 key → (account_id, user_id, role) 索引

**`async save_account(account_id)`**：
- 将指定 account 的用户数据写回 `/{account_id}/_system/users.json`
- 同时更新 `/_system/accounts.json`（若 account 列表有变化）

**`resolve(api_key) -> ResolvedIdentity`**：
```
# Key 无前缀，顺序匹配
if hmac.compare_digest(key, self._root_key):
    → ResolvedIdentity(role=ROOT)
entry = self._user_keys.get(key)
if entry:
    → ResolvedIdentity(role=entry.role, account_id=entry.account_id, user_id=entry.user_id)
raise UnauthenticatedError
```

**`create_account(account_id, admin_user_id) -> str`**：
- 验证 account_id 格式
- 检查 account_id 不重复
- 创建 account 记录到 `_accounts`
- 注册首个 admin 用户，生成 `secrets.token_hex(32)` 作为 key
- 持久化 `/_system/accounts.json` 和 `/{account_id}/_system/users.json`
- 返回 admin 的 user key

**`delete_account(account_id)`**：
- 从 `_accounts` 删除
- 从 `_user_keys` 中删除该 account 的所有 key
- 删除 `/_system/accounts.json` 中的记录
- **注意**：AGFS 数据和 VectorDB 数据的级联清理由 Admin Router 调用方负责

**`register_user(account_id, user_id, role="user") -> str`**：
- 检查 account_id 存在
- 生成 `secrets.token_hex(32)` 作为 key
- 写入 account 用户表和全局索引
- 调用 `save_account(account_id)`
- 返回 user key

**`remove_user(account_id, user_id)`**：
- 从 account 用户表和全局索引中移除
- 调用 `save_account(account_id)`

**`regenerate_key(account_id, user_id) -> str`**：
- 删除旧 key 的全局索引
- 生成新随机 key
- 更新用户表和全局索引
- 调用 `save_account(account_id)`
- 返回新 key

**`set_role(account_id, user_id, role)`**：
- 更新用户角色（仅 ROOT 可调用）
- 更新全局索引中的 role
- 调用 `save_account(account_id)`

---

#### T4: 认证中间件重写

**重写** `openviking/server/auth.py`，依赖：T1, T2, T3

删除现有的 `verify_api_key()`、`get_user_header()`、`get_agent_header()`，替换为：

**`resolve_identity(request, x_api_key, authorization, x_openviking_agent) -> ResolvedIdentity`**：
```
1. api_key_manager = request.app.state.api_key_manager
2. 若 api_key_manager 为 None（dev 模式，未配置 root_api_key）：
   返回 ResolvedIdentity(role=ROOT, account_id="default", user_id="default", agent_id="default")
3. 提取 key（同现有逻辑：X-API-Key 或 Bearer）
4. identity = api_key_manager.resolve(key)
   - 先 HMAC 比对 root key → 匹配则 role=ROOT
   - 再查 user key 索引 → 匹配则得到 account_id, user_id, role(ADMIN/USER)
   - 均不匹配 → 401
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

# 改后
if config.root_api_key:
    # 生产模式：初始化 APIKeyManager
    api_key_manager = APIKeyManager(
        root_key=config.root_api_key,
        agfs_url=service._agfs_url,
    )
    await api_key_manager.load()
    app.state.api_key_manager = api_key_manager
else:
    # Dev 模式：跳过认证，使用默认身份
    app.state.api_key_manager = None

# Admin API 始终注册（dev 模式下通过 role 守卫限制访问）
app.include_router(admin_router)
```

删除 `app.state.api_key`。

**注意**：APIKeyManager 初始化必须在 service.initialize() 之后，因为需要 AGFS URL。时序是：
1. `service = OpenVikingService()` → 启动 AGFS
2. `await service.initialize()` → 初始化 VikingFS/VectorDB
3. `api_key_manager = APIKeyManager(agfs_url=service._agfs_url)` → 用 AGFS 读 accounts.json + users.json
4. `await api_key_manager.load()`

---

#### T10: Router 依赖注入迁移

**修改文件**：`server/routers/` 下所有 router，依赖：T4

##### Phase 1 改动

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

**POST /api/v1/admin/accounts** — 创建工作区 + 首个 admin
```
权限：require_role(ROOT)
入参：{"account_id": "acme_corp", "admin_user_id": "alice"}
逻辑：
  1. api_key_manager.create_account(account_id, admin_user_id) → admin_user_key
  2. 为新账户初始化 AGFS 目录结构（调用 DirectoryInitializer）
返回：{"account_id": "acme_corp", "admin_user_id": "alice", "user_key": "<random_token>"}
```

**GET /api/v1/admin/accounts** — 列出工作区
```
权限：require_role(ROOT)
逻辑：遍历 api_key_manager._accounts
返回：[{"account_id": "acme_corp", "created_at": "...", "user_count": 2}, ...]
```

**DELETE /api/v1/admin/accounts/{account_id}** — 删除工作区
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
权限：require_role(ROOT, ADMIN)
额外检查：ADMIN 只能操作自己的 account
入参：{"user_id": "bob", "role": "user"}
逻辑：api_key_manager.register_user(account_id, user_id, role) → user_key
返回：{"account_id": "acme_corp", "user_id": "bob", "user_key": "<random_token>"}
```

**DELETE /api/v1/admin/accounts/{account_id}/users/{uid}** — 移除用户
```
权限：require_role(ROOT, ADMIN)
额外检查：ADMIN 只能操作自己的 account
逻辑：api_key_manager.remove_user(account_id, uid)
返回：{"deleted": true}
```

**PUT /api/v1/admin/accounts/{account_id}/users/{uid}/role** — 修改用户角色
```
权限：require_role(ROOT)
入参：{"role": "admin"}
逻辑：api_key_manager.set_role(account_id, uid, role)
返回：{"account_id": "acme_corp", "user_id": "bob", "role": "admin"}
```

**POST /api/v1/admin/accounts/{account_id}/users/{uid}/key** — 重新生成 User Key
```
权限：require_role(ROOT, ADMIN)
额外检查：ADMIN 只能操作自己的 account
逻辑：api_key_manager.regenerate_key(account_id, uid) → new_key（旧 key 立即失效）
返回：{"user_key": "<random_token>"}
```

注册到 `server/routers/__init__.py` 和 `server/app.py`。

---

#### T12: 客户端 SDK 更新

##### Phase 1 改动：HTTP 客户端

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

嵌入模式支持多租户，通过构造参数传入 `UserIdentifier`，无 API Key 认证：

```python
def __init__(self, path=None, user: UserIdentifier = None):
    self._service = OpenVikingService(path=path)
    self._ctx = RequestContext(
        user=user or UserIdentifier.the_default_user(),
        role=Role.ROOT,  # 嵌入模式无 RBAC，默认 ROOT 权限
    )

async def ls(self, uri, ...):
    return await self._service.fs.ls(uri, ctx=self._ctx)
```

嵌入模式不涉及 API Key 认证，但使用与服务模式相同的多租户路径结构（按 account_id 隔离）。

---

#### T16-P1: 用户文档更新（Phase 1）

**修改文件**：`docs/en/` + `docs/zh/` 对应文件，依赖：T4, T11, T12

Phase 1 涉及认证和 API 层变更，需同步更新以下文档（中英文各一份）：

| 文档 | 改动 |
|------|------|
| `guides/01-configuration.md` | server 段 `api_key` → `root_api_key`；ovcli.conf 新增 `agent_id` 字段说明 |
| `guides/04-authentication.md` | 重写：多租户认证机制（root key / user key）、RBAC 三层角色、Admin API 管理 key 的流程 |
| `guides/03-deployment.md` | 配置示例改用 `root_api_key`；客户端连接示例加 `agent_id`；新增多租户部署说明 |
| `api/01-overview.md` | 客户端示例加 `agent_id`；认证说明扩展为多租户；新增 Admin API 端点文档 |
| `getting-started/03-quickstart-server.md` | 示例更新 `root_api_key` + `agent_id` |

---

#### T17-P1: 示例更新（Phase 1）

**修改文件**：`examples/` 目录，依赖：T4, T11, T12

Phase 1 涉及认证体系和客户端接口变更，需同步更新示例：

| 文件 | 改动 |
|------|------|
| `examples/ov.conf.example` | `api_key` → `root_api_key` |
| `examples/server_client/ov.conf.example` | 同上 |
| `examples/server_client/client_sync.py` | 新增 `--agent-id` 参数 |
| `examples/server_client/client_async.py` | 新增 `agent_id` 参数 |
| `examples/server_client/client_cli.sh` | 添加 `X-OpenViking-Agent` header 示例 |
| `examples/server_client/ovcli.conf.example` | 新增 `agent_id` 字段 |

新增多租户管理示例 `examples/multi_tenant/`：

```
examples/multi_tenant/
├── README.md                  # 多租户管理流程说明
├── ov.conf.example            # 启用 root_api_key 的配置示例
├── admin_workflow.py          # ROOT 创建 account → 注册 admin → admin 注册 user
├── admin_workflow.sh          # 等效的 curl 命令版本
└── user_workflow.py           # user key 日常操作（ls、add_resource、find）
```

`admin_workflow.py` 覆盖：
- ROOT 创建工作区（含首个 admin）
- Admin 注册普通 user 并获取 user key
- 列出所有账户和用户
- 删除用户和账户

`user_workflow.py` 覆盖：
- 使用 user key 连接 server
- 执行常规操作（ls, add_resource, find, session）
- 验证无权限访问 admin API 时返回 403

---

#### T14-P1: 认证与管理测试

**T14a: APIKeyManager 单元测试**
- root key 验证（正确/错误）
- user key 注册、生成、解析（含角色：admin/user）
- 用户注册/移除后 key 有效性变化
- key 重新生成后旧 key 失效
- per-account users.json 持久化和加载
- create_account 同时创建首个 admin

**T14b: 认证中间件测试**
- resolve_identity 流程：root key 匹配 → ROOT，user key 查表 → ADMIN/USER
- user key 解析出 ADMIN 或 USER 角色（取决于用户注册表中的 role）
- dev 模式（无 root_api_key）
- require_role 守卫
- 无效 key / 缺失 key 的错误码

**T14e: 回归**
- 现有测试改为使用 dev mode（不配置 root_api_key）

---

### Phase 2：存储层隔离实现（后续）

实施顺序：`T6/T7 并行 → T8 → T9 → T13 → T15 → T16-P2 → T17-P2 → T14-P2`

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

统一多租户路径，`_uri_to_path` 和 `_path_to_uri` 始终按 account_id 前缀处理：

```python
def _uri_to_path(self, uri: str, account_id: str = "") -> str:
    remainder = uri[len("viking://"):].strip("/")
    if account_id:
        return f"/local/{account_id}/{remainder}" if remainder else f"/local/{account_id}"
    return f"/local/{remainder}" if remainder else "/local"

def _path_to_uri(self, path: str, account_id: str = "") -> str:
    if path.startswith("viking://"):
        return path
    elif path.startswith("/local/"):
        inner = path[7:]  # 去掉 /local/
        if account_id and inner.startswith(account_id + "/"):
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
    if ctx.role == Role.ADMIN:
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
- 改为：加 `ctx`，构造 Context 时填入 `account_id=ctx.account_id`, `owner_space=ctx.user.agent_space_name()`（agent scope）
- 资源路径使用 `viking://resources/...`（account 内共享，无 user_space），技能路径使用 `viking://agent/skills/{ctx.user.agent_space_name()}/...`

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

#### T15: 数据迁移脚本

**新建** `openviking/cli/migrate.py`，依赖：T6, T7

提供 `python -m openviking migrate` 命令，将旧版单租户数据迁移到多租户路径结构。

##### 迁移逻辑

1. **检测**：检查旧结构是否存在（`/local/resources/` 存在但 `/local/default/` 不存在）
2. **AGFS 搬迁**：
   - `/local/resources/...` → `/local/default/resources/...`
   - `/local/user/...` → `/local/default/user/{default_user_space}/...`
   - `/local/agent/...` → `/local/default/agent/{default_agent_space}/...`
   - `/local/session/...` → `/local/default/session/{default_space}/...`
3. **VectorDB 更新**：batch update 所有记录，补充 `account_id="default"` 和 `owner_space={default_space}`
4. **报告**：输出搬迁文件数、更新记录数、耗时

##### 安全措施

- 迁移前检查目标路径不存在，避免覆盖
- 迁移失败时回滚已搬迁的文件
- 支持 `--dry-run` 预览迁移计划

---

#### T16-P2: 用户文档更新（Phase 2）

**修改文件**：`docs/en/` + `docs/zh/` 对应文件，依赖：T6, T8, T15

Phase 2 涉及存储隔离和路径变更，需同步更新以下文档（中英文各一份）：

| 文档 | 改动 |
|------|------|
| `concepts/01-architecture.md` | 新增多租户架构说明、身份解析流程、数据隔离层次 |
| `concepts/05-storage.md` | URI → AGFS 路径映射加 account_id 前缀；多租户存储布局图 |
| `concepts/04-viking-uri.md` | URI 在多租户下的 account 作用域说明 |
| `about/02-changelog.md` | 多租户版本变更说明 |

---

#### T17-P2: 示例更新（Phase 2）

**修改文件**：`examples/` 目录，依赖：T6, T9

Phase 2 涉及存储隔离，需新增隔离相关示例：

| 文件 | 改动 |
|------|------|
| `examples/multi_tenant/isolation_demo.py` | **新增**：演示不同 account/user 间的数据隔离 |
| `examples/multi_tenant/agent_sharing_demo.py` | **新增**：演示同 account 下不同用户共享 agent 数据 |
| `examples/quick_start.py` | 嵌入模式加 `UserIdentifier` 参数说明 |

`isolation_demo.py` 覆盖：
- ROOT 创建两个 account
- 每个 account 的 user 分别写入 resources 和 memories
- 验证 account A 的 user 看不到 account B 的数据
- 验证同 account 内不同 user 的 memories 互相隔离
- 验证 resources 在同 account 内共享可见

`agent_sharing_demo.py` 覆盖：
- 同一 account 下两个 user 使用同一 agent_id
- 验证 agent memories/skills 在两个 user 间共享
- 验证 user memories 仍然互相隔离

---

#### T14-P2: 隔离与可见性测试

**T14c: 存储隔离测试**
- `_uri_to_path` 加 account_id 前缀正确性
- `_path_to_uri` 反向转换正确性
- `_is_accessible` 对 USER/ADMIN/ROOT 的行为
- VectorDB 查询带 account_id + owner_space 多级过滤
- 同 account 下不同 user 无法互相访问 resources 和 memories
- 同 account 下同一用户不同 agent 的数据互相隔离

**T14d: 端到端集成测试**
- Root Key 创建 account（含首个 admin）→ Admin 注册 user → User Key 写数据 → 另一 account 查不到
- 同 account 两个 user 写 resources → 互相查不到
- 同 account 同一 user 不同 agent → agent 数据隔离
- 删除用户后旧 key 认证失败
- 删除 account 后数据清理

---

## 九、关键文件清单

| 文件 | 改动类型 | 阶段 | 说明 |
|------|----------|------|------|
| `openviking/server/identity.py` | **新建** | P1 | Role(ROOT/ADMIN/USER), ResolvedIdentity, RequestContext |
| `openviking/server/api_keys.py` | **新建** | P1 | APIKeyManager（per-account 存储，全局索引） |
| `openviking/server/routers/admin.py` | **新建** | P1 | Admin 管理端点（account/user CRUD、角色管理） |
| `openviking/server/auth.py` | 重写 | P1 | verify_api_key → resolve_identity + require_role + get_request_context |
| `openviking/server/config.py` | 修改 | P1 | api_key → root_api_key |
| `openviking/server/app.py` | 修改 | P1 | 初始化 APIKeyManager，注册 Admin Router |
| `openviking_cli/client/http.py` | 修改 | P1 | 新增 agent_id 参数 |
| `openviking_cli/client/sync_http.py` | 修改 | P1 | 新增 agent_id 参数 |
| `openviking/server/routers/*.py` | 修改 | P1+P2 | P1: 迁移到 get_request_context；P2: ctx 传递给 service |
| `openviking/storage/viking_fs.py` | 修改 | P2 | 方法加 ctx 参数，_uri_to_path 加 account_id 前缀 |
| `openviking/storage/collection_schemas.py` | 修改 | P2 | context collection 加 account_id + owner_space 字段 |
| `openviking/retrieve/hierarchical_retriever.py` | 修改 | P2 | 查询注入 account_id + owner_space 多级过滤 |
| `openviking/service/core.py` | 修改 | P2 | 去除单例 _user，传递 RequestContext |
| `openviking/service/*.py` | 修改 | P2 | 各 sub-service 接受 RequestContext |
| `openviking/core/directories.py` | 修改 | P2 | 按 account 初始化目录 |
| `openviking/core/context.py` | 修改 | P2 | 新增 account_id、owner_space 字段 |
| `openviking/client/local.py` | 修改 | P2 | 支持 UserIdentifier 参数（嵌入模式多租户） |
| `openviking_cli/session/user_id.py` | 修改 | P2 | 新增 user_space_name() 和 agent_space_name() 方法 |
| `openviking/cli/migrate.py` | **新建** | P2 | 数据迁移脚本 |
| `docs/en/guides/*.md` + `docs/zh/guides/*.md` | 修改 | P1 | 配置、认证、部署文档更新 |
| `docs/en/api/01-overview.md` + `docs/zh/api/01-overview.md` | 修改 | P1 | API 概览加 Admin API、agent_id |
| `docs/en/concepts/*.md` + `docs/zh/concepts/*.md` | 修改 | P2 | 架构、存储、URI 文档更新 |
| `docs/en/about/02-changelog.md` + `docs/zh/about/02-changelog.md` | 修改 | P2 | 版本变更说明 |
| `examples/ov.conf.example` | 修改 | P1 | `api_key` → `root_api_key` |
| `examples/server_client/ov.conf.example` | 修改 | P1 | 同上 |
| `examples/server_client/client_sync.py` | 修改 | P1 | 新增 `agent_id` 参数 |
| `examples/server_client/client_async.py` | 修改 | P1 | 新增 `agent_id` 参数 |
| `examples/multi_tenant/` | **新建** | P1 | 多租户管理工作流示例（admin_workflow + user_workflow） |
| `examples/multi_tenant/isolation_demo.py` | **新建** | P2 | 数据隔离验证示例 |
| `examples/multi_tenant/agent_sharing_demo.py` | **新建** | P2 | agent 共享验证示例 |

---

## 十、验证方案

1. **单元测试**：
   - APIKeyManager 的 key 生成、注册、验证、角色解析
   - per-account 存储的持久化和加载
   - create_account 同时创建首个 admin 用户
   - key 重新生成后旧 key 失效
2. **集成测试**：Account A 无法看到 Account B 的数据（AGFS + VectorDB）
3. **端到端测试**：
   - Root Key 创建工作区（含首个 admin）→ Admin 注册 user → User Key 操作数据 → 验证隔离
   - 删除用户后旧 user key 失败
   - 删除 account 后级联清理数据
   - Dev 模式（无 root_api_key）正常工作，使用 default account
4. **回归测试**：现有测试适配新认证流程（使用 dev mode）

---

## 待评审决策项（TODO）

以下设计点在 V2 评审中已全部确定：

1. ~~**User Key 方案选型**（见 2.2 节）~~ —— 已确定：方案 B（随机 key + 查表），不需要 `private_key`。
2. ~~**Agent 目录归属模型**（见 4.3 节）~~ —— 已确定：方案 B（按 user_id + agent_id 隔离）。
3. ~~**单租户兼容**（见 8 节）~~ —— 已确定：破坏性改造，不保留单租户模式。

所有待评审项已解决，无遗留决策。

---

## 评审记录

### 2026-02-13

#### 设计决策确定

1. **去掉 Account Key**：三层 Key（root/account/user）简化为两层（root/user）。ADMIN 不再由 key 类型决定，而是用户在 account 内的角色属性，存储在 `users.json` 中。一个 account 可以有多个 admin。
2. **Account = 工作区**：Account 是由 ROOT 创建的工作区（workspace）。`/_system/accounts.json` 维护全局工作区列表，每个工作区有独立的用户注册表 `/{account_id}/_system/users.json`。系统启动时自动创建 default 工作区。
3. **User Key 方案 B**：随机 key + 查表存储。不需要 `private_key` 配置，不需要加密库。key 丢失后重新生成，旧 key 立即失效。
4. **Agent 目录方案 B**：按 user_id + agent_id 隔离。`agent_space_name()` = `md5(user_id + agent_id)[:12]`，每个用户与 agent 的组合有独立数据空间。
5. **破坏性改造**：不保留单租户模式，统一多租户路径结构。所有 account（含 default）使用 `/{account_id}/...` 层级路径。
6. **嵌入模式支持多租户**：通过构造参数传入 `UserIdentifier`，默认使用 default 工作区 + default 用户。
7. **API Key 无前缀**：所有 key 为纯随机 token（`secrets.token_hex(32)`），不携带身份信息。服务端通过先比对 root key、再查 user key 索引的方式确定身份。
8. **Resources account 级共享**：resources 在 account 内共享，不按 user_space 隔离。路径为 `/{account_id}/resources/...`。
9. **ROOT 支持全部功能**：ROOT 权限为超集，既能做管理操作也能使用常规产品功能。dev 模式默认 ROOT 角色。
10. **配置简化**：`ov.conf` server 段移除 `private_key` 和 `multi_tenant`，仅保留 `root_api_key` 和 `cors_origins`。
11. **创建 account 同时指定首个 admin**：`POST /admin/accounts` 一步完成工作区创建 + 首个 admin 注册 + 返回 user key。
12. **队列/Observer account 级可见性**：底层单例，查询时按 account_id 过滤。放在 Phase 2。

#### 新增任务

- **T15**：数据迁移脚本（`python -m openviking migrate`），将旧版单租户数据迁移到多租户路径结构，Phase 2 实现
- **T16-P1**：Phase 1 用户文档更新（配置、认证、部署、API 概览、快速开始）
- **T16-P2**：Phase 2 用户文档更新（架构、存储、URI、变更日志）
- **T17-P1**：Phase 1 示例更新（config 文件 + 多租户管理工作流示例）
- **T17-P2**：Phase 2 示例更新（数据隔离验证 + agent 共享验证示例）

#### Key 存储方案

评审讨论了 key 存储结构的三种方案（user_id 做主键 / key 做主键 / 双索引），确定采用方案 A（user_id 做主键）。文件结构用于持久化和人工排查，运行时认证全走内存索引（`dict[key] → identity`），O(1) 查找。


