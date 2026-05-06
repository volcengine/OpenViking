# OpenViking 原生 OAuth 2.1（MCP 客户端授权）实施方案

## Context

**问题**：Claude.ai / Claude Desktop / ChatGPT / Cursor 等只接受 OAuth 2.1 的 MCP 客户端，必须经由社区项目 [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) 的 Cloudflare Workers 代理才能连接 OpenViking 的 `/mcp`。痛点：

1. **额外部署单元** — 自建 CF Worker + 2 个 KV namespace，运维成本高
2. **生态绑定** — `@cloudflare/workers-oauth-provider` + KV 强绑定 CF Workers，无法脱离 CF 生态
3. **体验差与信任风险** — 用户在浏览器手动粘贴 API Key，且 Worker 部署方有解密 Key 的能力

**目标**：在 OpenViking 服务端原生实现 OAuth 2.1（MCP 子集），消除中间代理；保留 API Key 认证向后兼容。

**最终决策（与设计早期不同）**：

- **协议层用 `mcp.server.auth` SDK**（已在依赖中），而不是手搓。SDK 提供完整的 RFC 6749 / 7591 / 8414 实现：DCR、authorize 解析、token endpoint、metadata、PKCE S256 校验、redirect_uri 校验、错误码格式化。
- **Token 用 opaque + SQLite，不用 JWT**。Access / refresh / auth_code / OTP 全部是 `secrets.token_urlsafe()` 随机串，按 SHA-256 哈希存表，每次校验做一次 SQLite 查询。后果：**OpenViking 侧零密码学代码，无 review 负担**。
- **不做 redirect_uri 白名单**（与 MCP-Key2OAuth 现状一致），但 SDK 会强制 strict-equal 校验防 code injection
- **Phase 1 仅 OTP** 一种授权方式（CLI / REST API 取 OTP），第三方登录 / 邮件 OTP 留 Phase 2/3

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    OpenViking 1933                              │
│                                                                 │
│  ┌────────────────────┐   ┌──────────────────────────┐          │
│  │ mcp.server.auth    │   │ openviking.server.oauth  │          │
│  │ (SDK, 协议层)      │   │ (适配层 + 自定义路由)    │          │
│  ├────────────────────┤   ├──────────────────────────┤          │
│  │ /.well-known/...   │   │ /oauth/authorize/page    │ HTML 表单│
│  │ /register (DCR)    │   │ /api/v1/auth/otp         │ 取 OTP   │
│  │ /authorize         │   │                          │          │
│  │ /token             │ ──→ provider.py (Protocol 实现)         │
│  │ /revoke            │   │ store.py   (SQLite, 5 张表)         │
│  └────────────────────┘   └──────────────────────────┘          │
│           │                          │                          │
│           ↓ load_access_token()      ↓ DELETE/INSERT            │
│  ┌────────────────────┐   ┌──────────────────────────┐          │
│  │ auth.py            │   │ workspace/oauth.db       │          │
│  │ resolve_identity   │   │  oauth_clients           │          │
│  │ 识别 ovat_ 前缀 →  │   │  oauth_codes (otp+code)  │          │
│  │ provider 查找      │   │  oauth_refresh_tokens    │          │
│  │ → ResolvedIdentity │   │  oauth_access_tokens     │          │
│  └────────────────────┘   │  oauth_pending_authorizations       │
│                           └──────────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 设计要点

### 1. 认证模式

不引入新的 `AuthMode.OAUTH`。OAuth 叠加在现有 `AuthMode.API_KEY` 之上：当 `oauth.enabled = true` 时，`Authorization: Bearer <token>` 优先按 OAuth 处理：

- 若 token 以 `ovat_` 前缀开头 → 走 `provider.load_access_token()` 路径，**fail-closed**（前缀正确但查不到不会回退到 API Key 路径）
- 否则 → 走现有 APIKeyManager 路径，行为与改动前完全一致

`ResolvedIdentity` 新增 `from_oauth: bool` 标记位；`get_request_context` 对 OAuth 身份跳过 ROOT-tenant-headers 强校验（claims 已钉死 account/user）。

### 2. Token 权限范围

OAuth token = API Key 等效，能调任何当前用户身份能调的 REST 端点（不仅 `/mcp`）。

- **不是权限放大**：opaque token 都钉死 `(account_id, user_id, role)`，调用面 = 该用户用 API Key 直接调的范围
- 与 MCP-Key2OAuth 现状一致
- **撤销粒度**：以 `(account, user)` 为单位 — 删除某 user 的 API Key 时一刀切撤销该 user 名下所有 OAuth token（access + refresh + 未消费 code/OTP），见 `OAuthStore.revoke_user_tokens()`
- Phase 2 计划引入 OAuth scope（`mcp` / `fs` / `admin`）做更细粒度收紧

### 3. Token 形态（全部 opaque）

| 类型 | 形态 | 前缀 | TTL | 存储 |
|---|---|---|---|---|
| access_token | `secrets.token_urlsafe(40)` | `ovat_` | 1h | SQLite (SHA-256 哈希) |
| refresh_token | `secrets.token_urlsafe(40)` | `ovrt_` | 30d | SQLite (SHA-256 哈希) |
| authorization_code | `secrets.token_urlsafe(40)` | `ovac_` | 5min | SQLite (SHA-256 哈希) |
| OTP | 6 字符（去歧义字母+数字） | — | 5min | SQLite (SHA-256 哈希) |

前缀是 fast-path discriminator（不参与鉴权决策）— 让 `auth.py` 在每次请求只对 `ovat_` 开头的 bearer 做 DB 查询，普通 API Key 不受影响。

### 4. redirect_uri 策略

由 SDK 处理：
- DCR 接受任意 `redirect_uris[]`
- `/authorize` 时校验请求 `redirect_uri ∈ 注册集合`（`OAuthClientMetadata.validate_redirect_uri` strict-equal）
- `/token` 时再次 strict-equal 比对（SDK 内部）
- SDK 验证 issuer 必须 HTTPS（除 `localhost` / `127.0.0.1`）

### 5. PKCE

由 SDK 强制 S256，`plain` 拒绝。`code_verifier` 长度 43–128。SDK 在 `TokenHandler` 中验证 `SHA256(verifier) → base64url == challenge`。

### 6. WWW-Authenticate 401 头

`/mcp` 鉴权失败时 `_IdentityASGIMiddleware` 注入：
```
WWW-Authenticate: Bearer resource_metadata="https://<host>/.well-known/oauth-protected-resource"
```
honor `X-Forwarded-Proto/Host`，是 Claude.ai 走 OAuth 发现流程的入口（RFC 9728）。

> 注：`/.well-known/oauth-protected-resource` 端点本身 SDK 暂不提供，需我们自己实现（Phase 1 待办）。

---

## 模块清单

### 新增 / 改写

| 文件 | 用途 | 行数 |
|---|---|---|
| `openviking/server/oauth/storage.py` | SQLite 5 张表（clients / codes / refresh / access / pending）+ CRUD + GC | ~480 |
| `openviking/server/oauth/provider.py` | `OAuthAuthorizationServerProvider` Protocol 适配器；子类化 SDK 的 `AuthorizationCode/RefreshToken/AccessToken` 嵌入 `(account, user, role)` | ~280 |
| `openviking/server/oauth/router.py` | 自定义 authorize HTML 页 + `POST /api/v1/auth/otp` | ~210 |
| `openviking/server/oauth/otp.py` | `generate_otp` / `hash_secret`（stdlib） | ~30 |
| `openviking_cli/utils/config/oauth_config.py` | `OAuthConfig` pydantic（enabled / TTL / db_filename / rate_limit） | ~70 |

### 修改

| 文件 | 改动 |
|---|---|
| `openviking/server/auth.py` | `_try_resolve_oauth_token`：识别 `ovat_` → `provider.load_access_token` → `ResolvedIdentity(from_oauth=True)` |
| `openviking/server/identity.py` | `ResolvedIdentity.from_oauth: bool` |
| `openviking/server/mcp_endpoint.py` | 401 注入 `WWW-Authenticate` 头；`_oauth_enabled` 改查 `oauth_provider` |
| `openviking/server/app.py` | lifespan 初始化 `OAuthStore` + GC 任务；`create_app` 用 `mcp.server.auth.routes.create_auth_routes()` 挂 SDK routes + 自定义 router |
| `openviking_cli/utils/config/open_viking_config.py` | 接入 `OAuthConfig` |

### 删除（vs 早期 JWT 方案）

- `openviking/server/oauth/jwt.py`（手搓 HS256）
- `tests/server/oauth/test_jwt.py`

---

## 端点全表

| 端点 | 方法 | 由谁实现 | 鉴权 | 说明 |
|---|---|---|---|---|
| `/.well-known/oauth-authorization-server` | GET | SDK | 无 | RFC 8414 |
| `/.well-known/oauth-protected-resource` | GET | **TODO** | 无 | RFC 9728，Phase 1 待办 |
| `/register` | POST | SDK | 无 | DCR (RFC 7591)，SDK 生成 client_id/secret，调 `provider.register_client()` |
| `/authorize` | GET/POST | SDK → provider.authorize() | 无 | SDK 校验 client + redirect_uri + PKCE，调 `provider.authorize()` 返回 URL，本实现返回 `/oauth/authorize/page?pending=...` |
| `/oauth/authorize/page` | GET | OpenViking | 无 | 渲染 OTP 输入表单 |
| `/oauth/authorize/page` | POST | OpenViking | OTP | 校验 OTP→签 auth code→302 回 `redirect_uri?code=...&state=...` |
| `/token` | POST | SDK | client auth | SDK 验 PKCE / redirect_uri / client，调 `provider.exchange_authorization_code()` 或 `exchange_refresh_token()` |
| `/revoke` | POST | SDK | client auth | SDK 调 `provider.revoke_token()` |
| `POST /api/v1/auth/otp` | POST | OpenViking | 现有 API Key（`Depends(get_request_context)`） | 生成 OTP，绑定调用方身份 |

---

## 端到端流程（Claude.ai 视角）

```
1. 用户输入 https://my.ov/mcp
2. Claude POST /mcp → 401 + WWW-Authenticate: Bearer resource_metadata="..."
3. Claude GET /.well-known/oauth-protected-resource → 拿 issuer       [TODO]
4. Claude GET /.well-known/oauth-authorization-server → 拿 endpoint   [SDK]
5. Claude POST /register {redirect_uris} → 拿 client_id              [SDK]
6. Claude 浏览器跳转 /authorize?... → 302 → /oauth/authorize/page    [SDK→OV]
7. (用户) curl POST /api/v1/auth/otp -H "X-Api-Key: ..."             [OV]
   → {"otp":"ABC234"}
8. (用户) 在 page 粘 OTP → POST /oauth/authorize/page → 302 回 Claude [OV]
9. Claude POST /token (PKCE) → access_token + refresh_token          [SDK]
10. Claude POST /mcp (Authorization: Bearer ovat_...) → 通过          [SDK→auth.py]
```

---

## 实施进度

### ✅ M1 — 基础设施（已完成）
- `OAuthConfig` 接入 `OpenVikingConfig`（默认 disabled）
- `OAuthStore` 5 张表 + CRUD + 原子一次性消费（`UPDATE ... RETURNING`）
- `oauth/otp.py` OTP 生成
- `app.py` lifespan 注入 store + provider + GC

### ✅ M2 — Bearer 路径与 401 头（已完成）
- `auth.py` `_try_resolve_oauth_token` 识别 `ovat_` 前缀走 OAuth 路径
- `ResolvedIdentity.from_oauth` 标记位 + `get_request_context` 跳过 ROOT-tenant 强校验
- `mcp_endpoint.py` 401 注入 `WWW-Authenticate` 头

### ✅ M3 — SDK 接入与完整流程（已完成）
- `OpenVikingOAuthProvider`（8 个 Protocol 方法）
- 自定义 authorize HTML 页 + OTP 提交 → 签 code → 302 回 redirect_uri
- `POST /api/v1/auth/otp` REST 端点
- `app.py` 用 `create_auth_routes()` 挂 SDK 路由
- 32 单元 + 集成测试通过（含 DCR → OTP → authorize → token 完整 happy path + refresh 旋转 + 重放检测）

### ⏳ Phase 1 剩余（约 0.5 天）
1. **`/.well-known/oauth-protected-resource`**（RFC 9728）— SDK 不提供，需我们写一个 ~30 行的 route 输出 `{resource, authorization_servers, bearer_methods_supported}`。这是 Claude.ai 端到端流程的最后缺口。
2. **issuer 自动派生** — 当前若 `oauth.issuer` 为空回落到 `http://127.0.0.1:1933`，反代后不准。读 `X-Forwarded-Proto/Host` 派生（已在 mcp_endpoint 的 helper 实现，复用即可）。
3. **Claude.ai 端到端实测**

### 🔜 Phase 2 / 3（不在本 PR 范围）
- OAuth scope 机制（`mcp` / `fs.read` / `fs.write` / `admin`）
- Console proxy 同源授权（8020 已登录时一键授权）
- GitHub / Google 第三方登录（`identity_links` 表）
- 邮件 OTP 投递（SMTP 集成）
- `ov otp` Rust CLI 子命令（用户取 OTP 不需开终端 curl）

---

## 验证

### 单元 / 集成测试
```bash
pytest tests/server/oauth/ -v   # 32 通过
pytest tests/server/test_auth.py tests/server/test_mcp_endpoint.py -v   # 回归
```

### M3 端到端 curl
```bash
# 1. 取 OTP
curl -X POST -H "X-Api-Key: $ROOT_KEY" http://127.0.0.1:1933/api/v1/auth/otp
# → {"otp":"ABC234","expires_at":...}

# 2. DCR
curl -X POST -H "Content-Type: application/json" \
  -d '{"redirect_uris":["http://127.0.0.1:9999/cb"],"client_name":"test","token_endpoint_auth_method":"none"}' \
  http://127.0.0.1:1933/register

# 3. PKCE
VERIFIER=$(openssl rand -base64 64 | tr -d '=+/' | head -c 64)
CHALLENGE=$(printf "%s" "$VERIFIER" | openssl dgst -sha256 -binary | basenc --base64url | tr -d '=')

# 4. 浏览器: GET /authorize → 跳到 /oauth/authorize/page → 粘 OTP → 302 回 cb?code=...

# 5. 换 token
curl -X POST -d "grant_type=authorization_code&code=...&client_id=...&code_verifier=$VERIFIER&redirect_uri=..." \
  http://127.0.0.1:1933/token
# → {"access_token":"ovat_...","refresh_token":"ovrt_...","expires_in":3600}

# 6. 调 MCP
curl -X POST -H "Authorization: Bearer ovat_..." \
  http://127.0.0.1:1933/mcp -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

### 向后兼容
- `oauth.enabled=false`（默认）：`auth.py` 中 `oauth_provider is None`，OAuth 分流被跳过；行为与改动前一致
- `oauth.enabled=true`：`Authorization: Bearer <api_key>`（无 `ovat_` 前缀）仍走 APIKeyManager；现有客户端无感知

---

## 关键文件路径速查

**新增**：
- `openviking/server/oauth/{provider,storage,router,otp,__init__}.py`
- `openviking_cli/utils/config/oauth_config.py`
- `tests/server/oauth/test_{storage,router,auth_integration,mcp_www_authenticate}.py`

**修改**：
- `openviking/server/auth.py`（`_try_resolve_oauth_token`）
- `openviking/server/mcp_endpoint.py`（`WWW-Authenticate` 头）
- `openviking/server/identity.py`（`from_oauth` 字段）
- `openviking/server/app.py`（lifespan + `create_auth_routes`）
- `openviking_cli/utils/config/open_viking_config.py`（接入 `OAuthConfig`）

**复用**（不改）：
- `openviking/server/identity.py:AuthMode/Role/ResolvedIdentity`
- `openviking_cli/utils/config/storage_config.py:StorageConfig.workspace`
- `mcp.server.auth.*`（官方 SDK，~1.5K 行可读源码，无新依赖）

---

## 风险与已识别问题

| 风险 | 处理 |
|---|---|
| 反代后 `issuer` 派生错（HTTPS 终结于代理） | 读 `X-Forwarded-Proto/Host`；非 localhost 部署强烈建议显式配 `oauth.issuer` |
| Claude Desktop 当前不发 `resource` 参数 | OK：`AccessToken.resource` 仅当 token 请求带 `resource` 时设置；SDK 默认行为兼容 |
| Refresh token 重放检测后撤销 | 实现：`provider.exchange_refresh_token` 检测重放→`store.revoke_user_tokens(account, user)` 一并撤销该 user 名下所有 OAuth state |
| DCR 速率限制 | Phase 1 内置进程内令牌桶（10/小时/IP，配置项 `authorize_rate_limit_per_min`，需在路由里实际使用） |
| 授权页 CSRF | 已加 `frame-ancestors 'none'` + `X-Frame-Options: DENY`；OTP 短码本身一次性 + 5min TTL，无需额外 token |
| Token 权限范围 = 整个 REST API（非仅 `/mcp`） | 已与用户确认 Phase 1 不限制；Phase 2 引入 scope 机制收紧 |
| API Key → 撤销 OAuth token 的精度 | 当前粒度 `(account, user)`：删 user 时调 `revoke_user_tokens` cascade；满足需求（用户确认） |
