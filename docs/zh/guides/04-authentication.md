# 认证

选择认证模式后，配置服务端身份来源，再用对应凭证连接客户端。

<a id="📚-前置知识-我该用哪个"></a>

## 选择认证模式

| 认证模式 | 是什么？ | 适合谁？ |
|---------|----------|---------|
| **API Key** | OpenViking 自己管理用户和密钥 | 小团队、独立部署 |
| **OIDC** | 对接企业单点登录（Okta/Auth0/Keycloak/Azure AD 等） | 企业 SSO 集成 |
| **LDAP** | 对接企业用户目录（Windows AD/OpenLDAP） | 已有企业目录服务 |
| **Trusted** | 上游网关/反向代理断言身份 | 部署在受信任内网/网关后 |
| **Dev** | 无认证，仅本地开发 | 本地开发，不接入公网 |

<a id="🔍-决策树"></a>

### 决策树

已有单点登录服务选 OIDC，已有企业目录选 LDAP；由网关验证并注入身份时选 Trusted。没有这些上游服务时，使用 API Key 管理账号和凭证。

未显式设置 `auth_mode` 时，有非空 `root_api_key` 会选用 `api_key`，未配置则选用 `dev`。空字符串 `root_api_key` 无效。

---

<a id="🏁-快速开始-3分钟跑起来"></a>

## 快速开始

<a id="方案一-api-key-最简单"></a>

### 方案一：API Key

配置 `root_api_key`，启动后用它创建 account 和首个管理员。日常数据操作使用返回的 user/admin key。

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key-here"
  }
}
```

启动服务器：
```bash
openviking-server
```

用 API 管理用户：
```bash
# 创建账号 + 管理员
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key-here" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "my-team", "admin_user_id": "alice"}'
```

创建账号后，保存响应中的 `user_key`，将它用于 CLI、SDK 和数据 API。root key 不用于租户数据读写。

### 方案二：OIDC（企业 SSO）

OIDC 和 LDAP 需要可选认证依赖。在运行服务端的 Python 环境中安装：

```bash
uv pip install "openviking[auth]"
```

**最小配置（不需要 mapping）：**

```json
{
  "server": {
    "auth_mode": "oidc",
    "oidc": {
      "issuer": "https://your-company.okta.com"
    }
  }
}
```

默认自动使用：
- `account_id` → 所有用户在同一组织（"default"）
- `user_id` → 使用 OIDC 标准字段 `sub`
- `role` → 默认为 `user`

启动后用有效的 OIDC token 请求数据接口，验证身份映射和访问权限；匿名 `/health` 成功只证明服务可访问。

**进阶配置（需要隔离团队时）：**
参考下方 [Identity Mapping](#identity-mapping-是什么) 和高级 Mapping 示例。

### 方案三：LDAP（企业目录）

**最小配置（搜索绑定模式）：**

```json
{
  "server": {
    "auth_mode": "ldap",
    "ldap": {
      "host": "ldap.your-company.com",
      "port": 636,
      "use_ssl": true,
      "bind_dn": "cn=openviking,dc=your-company,dc=com",
      "bind_password": "${LDAP_PASSWORD}",
      "base_dn": "dc=your-company,dc=com",
      "user_search_filter": "(uid=%s)"
    }
  }
}
```

默认自动使用：
- `account_id` → 所有用户在同一组织（"default"）
- `user_id` → 使用 LDAP 字段 `uid`
- `role` → 默认为 `user`

---

<a id="📚-概念详解"></a>

## 身份与映射

### OIDC 是什么？

OIDC (OpenID Connect) 是业界标准的单点登录协议。

| 术语 | 说明 |
|------|------|
| **Issuer** | OIDC 提供商的 URL（如 `https://your-company.okta.com`） |
| **Claims** | Token 里包含的用户信息（如 `sub` = 用户ID，`email` = 邮箱） |
| **JWKS** | 用于验证 Token 签名的密钥集（自动从 Issuer 发现） |
| **Audience** | 可选，验证 Token 是发给谁的 |

### LDAP 是什么？

LDAP (Lightweight Directory Access Protocol) 是企业用户目录的标准协议。

| 术语 | 说明 |
|------|------|
| **DN** | 唯一标识对象的路径（如 `uid=alice,ou=users,dc=example,dc=com`） |
| **Base DN** | 搜索用户的根节点（如 `dc=example,dc=com`） |
| **Bind DN** | 连接 LDAP 用的服务账号 |
| **Attribute** | 用户属性（如 `uid`, `email`, `memberOf`） |

### Identity Mapping 是什么？

简单说：**把外部身份源的字段映射到 OpenViking 的身份上。**

```
外部身份源       →       Mapping 规则       →       OpenViking 身份
─────────────────────────────────────────────────────────────────────
OIDC Claims: {
  "sub": "user123",          →      claim="sub"        →  user_id = "user123"
  "department": "eng",       →      claim="department" →  account_id = "eng"
  "role": "admin"            →      mapping={"admin":"admin"} → role = "admin"
}
```

支持的模式：
- **Organization**：所有用户在同一组织（默认）
- **Team**：按部门/团队隔离（从外部字段提取）

---

<a id="⚙️-完整配置参考"></a>

## 完整配置参考

### OIDC 完整配置

```json
{
  "server": {
    "auth_mode": "oidc",
    "oidc": {
      "issuer": "https://your-company.okta.com",
      "client_id": "0oabc123def456",
      "client_secret": "${OIDC_CLIENT_SECRET}",
      "audience": "openviking",
      "jwks_uri": "https://your-company.okta.com/oauth2/v1/keys",
      "token_location": "header",
      "token_header_name": "Authorization",
      "token_header_prefix": "Bearer ",
      "identity": {
        "account_id": {
          "mode": "organization",
          "source": "claim",
          "claim": "tenant_id",
          "prefix": "org-",
          "fallback": "default-org"
        },
        "user_id": {
          "source": "claim",
          "claim": "sub",
          "prefix": "user-",
          "normalize": "lowercase"
        },
        "role": {
          "source": "claim",
          "claim": "role",
          "mapping": {
            "administrator": "admin",
            "developer": "user",
            "viewer": "user"
          },
          "default": "user"
        }
      }
    }
  }
}
```

### LDAP 完整配置

```json
{
  "server": {
    "auth_mode": "ldap",
    "ldap": {
      "host": "ldap.your-company.com",
      "port": 636,
      "use_ssl": true,
      "use_starttls": false,
      "bind_dn": "cn=openviking,dc=your-company,dc=com",
      "bind_password": "${LDAP_PASSWORD}",
      "base_dn": "dc=your-company,dc=com",
      "user_search_filter": "(uid=%s)",
      "user_search_base": "ou=users,dc=your-company,dc=com",
      "username_attribute": "uid",
      "email_attribute": "mail",
      "name_attribute": "cn",
      "user_dn_pattern": "uid=%s,ou=users,dc=your-company,dc=com",
      "identity": {
        "account_id": {
          "mode": "team",
          "source": "dn_attribute",
          "attribute": "ou",
          "prefix": "team-",
          "fallback": "default"
        },
        "user_id": {
          "source": "attribute",
          "attribute": "uid",
          "normalize": "lowercase"
        },
        "role": {
          "source": "group_membership",
          "group_mapping": {
            "cn=openviking-admins,ou=groups,dc=your-company,dc=com": "admin",
            "cn=openviking-users,ou=groups,dc=your-company,dc=com": "user"
          },
          "default": "user"
        }
      }
    }
  }
}
```

### 高级 Mapping 示例

#### 1. 正则提取

从邮箱里提取用户名：
```json
{
  "user_id": {
    "source": "claim",
    "claim": "email",
    "regex": "^([^@]+)@",
    "regex_group": 1
  }
}
```
输入：`alice@example.com` → 输出：`alice`

#### 2. 多字段回退

尝试多个字段，第一个有值的生效：
```json
{
  "user_id": {
    "source": "claim",
    "claims": ["username", "email", "sub"],
    "fallback": "guest"
  }
}
```

#### 3. 组合字段

把 Tenant ID 和 Department 拼起来：
```json
{
  "account_id": {
    "source": "composite",
    "parts": [
      {"source": "claim", "claim": "tenant_id"},
      {"literal": "-"},
      {"source": "claim", "claim": "department"}
    ]
  }
}
```
输入：`tenant_id="acme"`, `department="eng"` → 输出：`acme-eng`

---

<a id="🐛-故障排除"></a>

## 故障排除

### OIDC 常见问题

#### 问题 1：「找不到 issuer」

```
❌ oidc_issuer_configured: Issuer is not configured
```

**解决方法：**
- 检查 URL 是否有协议（`https://`）
- 确认 URL 是否可以访问：
  ```bash
  curl https://your-company.okta.com/.well-known/openid-configuration
  ```

#### 问题 2：「无法获取 JWKS」

```
⚠️ oidc_jwks_accessible: Failed to fetch JWKS: ...
```

**解决方法：**
- 检查网络连接
- 可能是防火墙问题，尝试手动配置 `jwks_uri`

#### 问题 3：「Token 验证失败」

**诊断步骤：**
1. 检查 Token 格式是否正确
2. 查看日志，确认 `iss` 匹配配置
3. 如果是过期 Token，这是正常的，需要重新登录

---

### LDAP 常见问题

#### 问题 1：「无法连接 LDAP」

```
⚠️ ldap_connection: Failed to connect to LDAP: Connect timeout
```

**解决方法：**
- 检查服务器地址和端口
- 确认 `use_ssl` 设置正确：
  - LDAPS (SSL) 通常端口 636
  - 普通 LDAP 通常端口 389
  - 可以用 `ldapsearch` 测试：
    ```bash
    ldapsearch -x -H ldaps://ldap.your-company.com:636 -b "dc=your-company,dc=com"
    ```

#### 问题 2：「Bind 失败」

```
⚠️ ldap_connection: Bind failed: Invalid credentials
```

**解决方法：**
- 检查 `bind_dn` 和 `bind_password`
- 确认 Bind 账号在 LDAP 中存在

#### 问题 3：「找不到用户」

```
❌ 搜索结果为空
```

**解决方法：**
- 检查 `user_search_filter` 是否正确
- 检查 `base_dn` 是否正确
- 尝试用 `ldapsearch` 手动测试：
  ```bash
  ldapsearch -x -H ldaps://ldap.your-company.com:636 -D "cn=openviking,dc=your-company,dc=com" -W -b "dc=your-company,dc=com" "(uid=alice)"
  ```

---

### 通用调试技巧

1. **启用 Debug 日志**（启动时看到更多细节）
2. **先看健康检查**（启动时自动运行）
3. **检查配置格式**（JSON 是否有效）
4. **查看日志**（重点看错误信息前面的内容）

---

<a id="🔧-自定义认证插件-高级"></a>

## 自定义认证插件

需要接入其他身份源时，可以实现并注册自定义认证插件。

服务端采用插件化认证架构。每种 `auth_mode` 对应一个 `AuthPlugin` 实现。内置插件（`dev`, `api_key`, `trusted`, `oidc`, `ldap`）会自动注册；第三方插件可通过继承 `AuthPlugin` 并在启动前注册来扩展。

### 插件接口（`openviking.server.auth.plugin.AuthPlugin`）

| 方法 | 用途 |
|------|------|
| `resolve_identity(request, api_key, x_openviking_account, x_openviking_user)` | 将凭据解析为 `ResolvedIdentity` |
| `validate_config(config)` | 在启动时校验 `ServerConfig`；遇到致命错误应调用 `sys.exit(1)` |
| `initialize(app, service, config)` | 在 `app.state` 上初始化运行时状态（如 `APIKeyManager`） |
| `get_request_context_checks(path, identity)` | 可选的认证后路径/身份检查 |
| `requires_api_key_manager()` | Admin API 路由是否需要 `APIKeyManager` |
| `can_skip_api_key_for_bot_proxy()` | Bot 代理是否可以跳过 API Key 校验（如 `dev` 模式） |

### 注册自定义插件示例

```python
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.auth.registry import register_auth_plugin
from openviking.server.identity import ResolvedIdentity, Role

@register_auth_plugin
class CustomAuthPlugin(AuthPlugin):
    auth_mode = "custom"

    async def resolve_identity(self, request, *, api_key=None, x_openviking_account=None, x_openviking_user=None):
        # 自定义认证逻辑...
        return ResolvedIdentity(role=Role.USER, account_id="...", user_id="...")

    def validate_config(self, config):
        pass

    async def initialize(self, app, service, config):
        pass
```

然后在 `ov.conf` 中设置 `server.auth_mode = "custom"`。

### 自定义角色

内置的 `Role` 类支持动态注册自定义角色及权限等级：

```python
from openviking.server.identity import Role

Role.register("operator", rank=1)  # 降权检查时，与 ADMIN 同级
```

rank 用于降权检查，不会继承 ADMIN 的接口权限。`require_role()` 和 `require_auth_role()` 按角色名检查，路由需要明确允许该自定义角色。

---

## 内置认证模式详情

### Trusted 模式

trusted 模式的普通数据面请求无需预先注册 user 或创建 user API Key。服务端会异步批量注册
该 account/user，使其最终出现在既有 account/user 管理接口中（默认五分钟刷盘）。注册不会
创建 user API Key，也不会修改 group 或已有 user 的角色。设置
`server.trusted_identity_flush_interval_seconds` 为 `0` 可完全关闭注册能力；
`/api/v1/admin/*` 请求不会被注册。

```bash
# 创建工作区 + 首个 admin
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key-here" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'
# 返回: {"result": {"account_id": "acme", "admin_user_id": "alice", "user_key": "..."}}

# 注册普通用户（ROOT 或 ADMIN 均可）
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: your-secret-root-key-here" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "bob", "role": "user"}'
# 返回: {"result": {"account_id": "acme", "user_id": "bob", "user_key": "..."}}
```

ACL 用户组是例外：组和成员通过 [Admin API](../api/08-admin.md#用户组) 维护，成员必须是当前 account 已注册的用户。客户端不能通过 header 或 token claim 声明组；服务端认证用户后查询组注册表，并把结果写入本次请求的 `RequestContext.group_ids`。

受信部署也可以通过受信网关调用 Admin API，目前支持两种方式：

- 携带受信部署自身的 `root_api_key`。对于 `/api/v1/admin/*`，服务端校验该 key 后会将请求视为 ROOT。
- 如果 Admin 路由指向具体 account/user，也可以同时携带 `X-OpenViking-Account` + `X-OpenViking-User`。这些 header 必须与目标 URL 匹配，并会保留为请求身份；授权仍来自受信 `root_api_key`。

角色更新 API 只支持将用户提升为 ADMIN。Trusted Admin API 的管理权限来自已校验的部署 root key，无需也不支持创建 ROOT 用户。

配置了 root key 的 Trusted 部署可直接调用上面的 Admin API，无需先切换到 API Key 模式或注册网关管理员。

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
import openviking_sdk as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
)
```

**CLI（通过 ovcli.conf）**

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>"
}
```

API Key 模式下，服务端从 user/admin key 解析 account 和 user，不接受用身份请求头切换用户。Trusted 模式下的数据请求需要明确提供 account 和 user。

**CLI 请求**

```bash
ov ls viking://
```

### 使用 --sudo 和 Root API Key

CLI 支持在 `ovcli.conf` 中同时配置 `api_key`（用于普通用户操作）和 `root_api_key`（用于管理员操作）：

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "root_api_key": "<root-key>"
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

### 租户数据访问

租户级数据 API（如 `ls`、`find`、resources、sessions 等）在 `api_key`
模式下必须使用绑定了 account/user 的 key。这个 key 可以是 `USER` key，也可以是
`ADMIN` key；`ADMIN` key 会以它自己的 user 身份访问数据，不能通过
`X-OpenViking-Account` / `X-OpenViking-User` 切换身份。

ACL 检查还会使用服务端解析的 account 内用户组。成员变更从下一次请求生效，不需要签发新 API Key，也不会修改资源 ACL。

`ROOT` key 没有绑定租户 user，因此在 `api_key` 模式下不能访问租户级数据 API。
如果部署需要由上游网关断言 `account` / `user`，请使用 `trusted` 模式，而不是在
root key 请求上携带身份 header。

**ovcli.conf**

```json
{
  "url": "http://localhost:1933",
  "auth_mode": "trusted",
  "api_key": "your-trusted-server-key",
  "account": "acme",
  "user": "alice"
}
```

## Trusted 模式

Trusted 模式不会查询 user key，而是直接信任每个请求显式携带的身份请求头：

```json
{
  "server": {
    "auth_mode": "trusted",
    "host": "127.0.0.1"
  }
}
```

数据请求必须提供 `X-OpenViking-Account` 和 `X-OpenViking-User`。若配置了 `root_api_key`，请求还必须携带匹配的 API Key，以验证上游网关。

角色默认从已注册用户查询，未注册时为 USER。配置并验证 root key 后，上游可用 `X-OpenViking-Role: user` 或 `admin` 断言角色，不接受 `root`。Admin API 的 ROOT 授权来自部署 root key。

### Dev 模式

当 `auth_mode = "dev"`（或未配置 `root_api_key` 时自动推导）时，认证禁用，请求使用 ROOT 身份；未提供身份请求头时，account 和 user 均为 `default`。Dev 模式只允许监听 `127.0.0.1`、`localhost` 或 `::1`，监听其他地址会拒绝启动。

```json
{
  "server": {
    "host": "127.0.0.1"
  }
}
```

> 默认 `host` 为 `127.0.0.1`。监听网络地址前，选择并配置 API Key、OIDC、LDAP 或受保护的 Trusted 部署。

---

## CLI 配置 LDAP 认证

OpenViking CLI (`ov`) 支持通过 LDAP 进行认证。配置完成后，所有 CLI 命令会自动使用 LDAP 凭据。

### 配置方式

#### 1. 配置文件方式（推荐）

编辑 `~/.openviking/ovcli.conf` 文件，添加 LDAP 认证配置：

```json
{
    "url": "http://localhost:1933",
    "auth_mode": "ldap",
    "ldap_username": "alice",
    "ldap_password": "your-ldap-password"
}
```

**配置项说明：**

| 配置项 | 必需 | 说明 |
|--------|------|------|
| `url` | 是 | OpenViking 服务器地址 |
| `auth_mode` | 是 | 认证模式，设置为 `"ldap"` 启用 LDAP |
| `ldap_username` | 是 | LDAP 用户名（UID） |
| `ldap_password` | 是 | LDAP 密码；缺少此字段时 CLI 不发送 Basic Auth |

#### 2. 混合配置

用 `OPENVIKING_CLI_CONFIG_FILE` 选择另一份完整配置文件。`ov` 不读取 `OPENVIKING_URL` 或 `OPENVIKING_ACCOUNT` 作为连接覆盖值（`ov chat` 例外，它用 `OPENVIKING_URL` 拼出 bot endpoint）；这些变量属于 Agent 插件配置。

### 使用 CLI

配置完成后，所有 CLI 命令会自动使用 LDAP 认证：

```bash
# 列出资源
ov ls viking://

# 读取资源
ov read viking://resources/example.md

# 写入资源
ov write viking://resources/test.md --content "Hello LDAP!"
```

### 构建 Rust CLI（如需更新）

如果修改了 Rust CLI 源码，需要重新构建：

```bash
make build-cli
```

构建后的二进制文件位于 `openviking/bin/ov`。

### 切换认证模式

编辑 `~/.openviking/ovcli.conf`，修改 `auth_mode` 为 `"api_key"` 或删除该字段即可切换认证模式。

### 安全建议

1. **限制配置文件访问**：当前 CLI 从配置文件读取 LDAP 密码。只允许当前用户读取，避免提交到仓库或写入共享日志
2. **使用 HTTPS**：生产环境中确保服务器使用 HTTPS 连接
3. **最小权限**：使用普通用户账户进行日常操作，管理员账户仅用于管理任务
4. **定期轮换密码**：遵循组织的密码安全策略

### 故障排查

**"Missing LDAP credentials" 错误：**
- 检查 `auth_mode` 是否设置为 `"ldap"`
- 确认 `ldap_username` 和 `ldap_password` 配置正确

**"LDAP authentication failed" 错误：**
- 验证 LDAP 用户名和密码是否正确
- 检查 LDAP 服务器是否可访问
- 查看服务器端日志获取详细错误信息

**"Permission denied" 错误：**
- 确认用户 LDAP 组是否映射到正确的 OpenViking 角色
- 检查操作是否需要管理员权限
- 联系系统管理员确认权限配置

**调试模式：**
```bash
# 启用详细日志
RUST_LOG=debug ov ls viking://

# 检查配置
ov config validate
```

---

## 相关文档

- [多租户](../concepts/11-multi-tenant.md) - 多租户能力、共享边界与接入实践
- [资源访问控制（ACL）](../concepts/15-acl.md) - account 内资源权限
- [配置](01-configuration.md) - 配置文件说明
- [服务部署](03-deployment.md) - 服务部署
- [API 概览](../api/01-overview.md) - API 参考

---

<a id="📝-附录-admin-api-参考"></a>

## Admin API 参考

| 方法 | 端点 | 角色 | 说明 |
|------|------|------|------|
| POST | `/api/v1/admin/accounts` | ROOT | 创建工作区 + 首个 admin |
| GET | `/api/v1/admin/accounts` | ROOT | 列出所有工作区 |
| DELETE | `/api/v1/admin/accounts/{id}` | ROOT | 删除工作区 |
| POST | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | 注册用户 |
| GET | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | 列出用户 |
| DELETE | `/api/v1/admin/accounts/{id}/users/{uid}` | ROOT, ADMIN | 移除用户 |
| PUT | `/api/v1/admin/accounts/{id}/users/{uid}/role` | ROOT, ADMIN | 将用户提升为 ADMIN；ADMIN 仅限本账户 |
| POST | `/api/v1/admin/accounts/{id}/users/{uid}/key` | ROOT, ADMIN | 重新生成 user key |
