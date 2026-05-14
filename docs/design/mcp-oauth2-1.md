# OpenViking 原生 OAuth 2.1（MCP 客户端授权）实施方案

## Context

**问题**：Claude.ai / Claude Desktop / ChatGPT 等只接受 OAuth 2.1 的 MCP 客户端，必须经由社区项目 [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) 的 Cloudflare Workers 代理才能连接 OpenViking 的 `/mcp`。痛点：

1. **额外部署单元** — 自建 CF Worker + 2 个 KV namespace，运维成本高
2. **生态绑定** — `@cloudflare/workers-oauth-provider` + KV 强绑定 CF Workers，无法脱离 CF 生态
3. **体验差与信任风险** — 用户在浏览器手动粘贴 API Key，且 Worker 部署方有解密 Key 的能力

**目标**：在 OpenViking 服务端原生实现 OAuth 2.1（MCP 子集），消除中间代理；保留 API Key 认证向后兼容；提供顺手的浏览器授权 UX。

**最终决策（与设计早期不同）**：

- **协议层用 `mcp.server.auth` SDK**（已在依赖中）。SDK 提供完整的 RFC 6749 / 7591 / 8414 实现：DCR、authorize 解析、token endpoint、metadata、PKCE S256 校验、redirect_uri 校验、错误码格式化。
- **Token 用 opaque + SQLite，不用 JWT**。Access / refresh / auth_code / OTP 全部是 `secrets.token_urlsafe()` 随机串，按 SHA-256 哈希存表，每次校验做一次 SQLite 查询。**OpenViking 侧零密码学代码**。
- **不做 redirect_uri 白名单**，但 SDK 会强制 strict-equal 校验防 code injection。
- **Phase 1 用 device-flow 风格的 OTP 流程**：authorize page **显示** 6 字符码，用户在 console（已登录环境）**输入** 该码确认授权。比早期的"console 取码、page 输入"流程少一次 tab 切换，且符合 RFC 8628 的心理模型。

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    OpenViking 1933                              │
│                                                                 │
│  ┌────────────────────┐   ┌──────────────────────────┐          │
│  │ mcp.server.auth    │   │ openviking.server.oauth  │          │
│  │ (SDK, 协议层)      │   │ (适配 + 自定义路由)      │          │
│  ├────────────────────┤   ├──────────────────────────┤          │
│  │ /.well-known/      │   │ /.well-known/            │          │
│  │   oauth-auth-server│   │   oauth-protected-       │          │
│  │ /register (DCR)    │   │   resource (PRM 9728)    │          │
│  │ /authorize         │   │ /oauth/authorize/page    │          │
│  │ /token             │   │ /oauth/authorize/page/   │          │
│  │ /revoke            │   │   status (轮询)          │          │
│  └─────────┬──────────┘   │ /api/v1/auth/oauth-      │          │
│            │              │   verify (确认入口)       │          │
│            │              │ /api/v1/auth/otp         │          │
│            │              │   (legacy push)           │          │
│            │              └──────────┬───────────────┘          │
│            │                          │                          │
│            ↓ load_access_token()     ↓ DELETE/INSERT            │
│  ┌────────────────────┐   ┌──────────────────────────┐          │
│  │ auth.py            │   │ workspace/oauth.db       │          │
│  │ resolve_identity   │   │  oauth_clients           │          │
│  │ 识别 ovat_ →       │   │  oauth_codes (otp+code)  │          │
│  │ provider 查找      │   │  oauth_refresh_tokens    │          │
│  │ → ResolvedIdentity │   │  oauth_access_tokens     │          │
│  └────────────────────┘   │  oauth_pending_authorizations       │
│                           │   (display_code, verified, ...)     │
│                           └──────────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                                ↑ verify (Bearer)
┌─────────────────────────────────────────────────────────────────┐
│                    OpenViking Console 8020                      │
│  Settings → "Authorize an MCP client" 表单                      │
│   - 输入 6 字符 display_code → 调 /console/api/v1/ov/auth/      │
│     oauth-verify (proxy → 1933 /api/v1/auth/oauth-verify)       │
│  浏览器 sessionStorage 存 API Key (key=ov_console_api_key)      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 设计要点

### 1. 认证模式

不引入新的 `AuthMode.OAUTH`。OAuth 叠加在现有 `AuthMode.API_KEY` 之上：当 `oauth.enabled = true` 时，`Authorization: Bearer <token>` 优先按 OAuth 处理：

- 若 token 以 `ovat_` 前缀开头 → 走 `provider.load_access_token()` 路径，**fail-closed**（前缀正确但查不到不会回退到 API Key 路径）
- 否则 → 走现有 APIKeyManager 路径，行为与改动前完全一致

`ResolvedIdentity` 新增 `from_oauth: bool` 标记位；`get_request_context` 对 OAuth 身份跳过 ROOT-tenant-headers 强校验（claims 已钉死 account/user）。

### 2. Token 权限范围与撤销

OAuth token = API Key 等效，能调任何当前用户身份能调的 REST 端点（不仅 `/mcp`）。

- **不是权限放大**：opaque token 都钉死 `(account_id, user_id, role)`
- **撤销粒度**：以 `(account, user)` 为单位 — 删除某 user 的 API Key 时一刀切撤销该 user 名下所有 OAuth token，见 `OAuthStore.revoke_user_tokens()`
- Phase 2 计划引入 OAuth scope 做更细收紧

### 3. Token 形态（全部 opaque）

| 类型 | 形态 | 前缀 | TTL | 存储 |
|---|---|---|---|---|
| access_token | `secrets.token_urlsafe(40)` | `ovat_` | 1h | SQLite (SHA-256 哈希) |
| refresh_token | `secrets.token_urlsafe(40)` | `ovrt_` | 30d | SQLite (SHA-256 哈希) |
| authorization_code | `secrets.token_urlsafe(40)` | `ovac_` | 5min | SQLite (SHA-256 哈希) |
| display_code (人类可读) | 6 字符（去歧义字母+数字） | — | 10min | pending_authorizations |
| OTP（legacy push） | 同上 | — | 5min | oauth_codes |

前缀是 fast-path discriminator（不参与鉴权决策）— 让 `auth.py` 在每次请求只对 `ovat_` 开头的 bearer 做 DB 查询，普通 API Key 不受影响。

### 4. 公网 URL 解析（issuer / PRM resource / WWW-Authenticate / page 链接）

统一 4 级回退：

1. `OPENVIKING_PUBLIC_BASE_URL` 环境变量（最高优先级，部署 override）
2. `oauth.issuer` 配置项
3. `X-Forwarded-Proto` + `X-Forwarded-Host`（反代场景）
4. 请求 scheme + `Host` 头（直连）

非 localhost 部署强烈建议显式设置 (1) 或 (2)，因为 SDK 强制 issuer 必须是 HTTPS（除 loopback）。

### 5. PKCE / redirect_uri / 错误格式

由 SDK 强制 S256，`plain` 拒绝；`code_verifier` 长度 43–128。SDK 在 `TokenHandler` 中验证。`/authorize` 时 `OAuthClientMetadata.validate_redirect_uri` 做 strict-equal；`/token` 时再次比对（防 code injection）。RFC 6749 错误码由 SDK 返回。

### 6. WWW-Authenticate 401 头

`/mcp` 鉴权失败时 `_IdentityASGIMiddleware` 注入：
```
WWW-Authenticate: Bearer resource_metadata="https://<host>/.well-known/oauth-protected-resource"
```
URL 走 §4 的 4 级回退。RFC 9728 客户端发现入口。

---

## 端到端流程（device-flow / pull 模式）

```
1. 用户输入 https://my.ov/mcp 到 Claude.ai
2. Claude POST /mcp → 401 + WWW-Authenticate: Bearer resource_metadata="..."
3. Claude GET /.well-known/oauth-protected-resource     → 拿 issuer
4. Claude GET /.well-known/oauth-authorization-server   → 拿 endpoint        [SDK]
5. Claude POST /register {redirect_uris}                → 拿 client_id      [SDK]
6. Claude 浏览器跳到 /authorize → SDK 校验 → 调 provider.authorize() →
   server 生成 display_code (e.g. "AB3X7K") + pending → 302 → 
   /oauth/authorize/page?pending=...                                          [SDK→OV]
7. Page 显示大字 "AB3X7K" + 链接 https://my.ov/console，
   并启动 JS 轮询 /oauth/authorize/page/status?pending=...
8. 用户切到 console (sessionStorage 已经在登录) → Settings →
   "Authorize an MCP client" 输入 AB3X7K → 点 Authorize
9. Console JS POST /console/api/v1/ov/auth/oauth-verify {code, decision}
   ↓ proxy
   1933 POST /api/v1/auth/oauth-verify
   → server 找 pending by display_code → mark verified, 写入 caller 身份
10. Page 下次轮询命中 status=approved → response.redirect_url 含 auth_code
11. Page JS window.location.replace(redirect_url) → Claude 收到 ?code=...&state=...
12. Claude POST /token (PKCE) → ovat_ + ovrt_                                [SDK]
13. Claude POST /mcp (Authorization: Bearer ovat_...) → 通过                  [SDK→auth.py]
```

**同源加速（可选）**：第 7 步 page JS 检测到 `sessionStorage.ov_console_api_key` 存在（即与 console 同域 + 已登录）时，显示绿色 "Quick authorize" 面板。点击该按钮等价于在 console 输入码 → 直接跳到第 10 步。**仍要点击确认**，不会自动一步跳转。要让同源生效，nginx 反代把 8020 与 1933 放到同一域名（`/console/...` → 8020，`/...` → 1933）。

---

## 模块清单

### 新增 / 改写

| 文件 | 用途 | 行数 |
|---|---|---|
| `openviking/server/oauth/storage.py` | SQLite 5 张表 + CRUD + GC + verify/find_pending_by_display_code | ~620 |
| `openviking/server/oauth/provider.py` | `OAuthAuthorizationServerProvider` Protocol 适配；子类化 SDK 的 `AuthorizationCode/RefreshToken/AccessToken` 嵌入 `(account, user, role)`；`authorize()` 自动生成 display_code | ~280 |
| `openviking/server/oauth/router.py` | PRM、authorize page (HTML+JS)、page/status 轮询、`/api/v1/auth/oauth-verify`、legacy `/api/v1/auth/otp` | ~440 |
| `openviking/server/oauth/otp.py` | `generate_otp` / `hash_secret`（stdlib） | ~30 |
| `openviking_cli/utils/config/oauth_config.py` | `OAuthConfig` pydantic | ~70 |

### 修改

| 文件 | 改动 |
|---|---|
| `openviking/server/auth.py` | `_try_resolve_oauth_token`：识别 `ovat_` → `provider.load_access_token` → `ResolvedIdentity(from_oauth=True)` |
| `openviking/server/identity.py` | `ResolvedIdentity.from_oauth: bool` |
| `openviking/server/mcp_endpoint.py` | 401 注入 `WWW-Authenticate` 头；`_scope_to_origin` 4 级回退含 env |
| `openviking/server/app.py` | lifespan 初始化 `OAuthStore` + GC 任务；`create_app` 用 `mcp.server.auth.routes.create_auth_routes()` 挂 SDK routes + 自定义 router；issuer 优先读 `OPENVIKING_PUBLIC_BASE_URL` env |
| `openviking_cli/utils/config/open_viking_config.py` | 接入 `OAuthConfig` |
| `openviking/console/app.py` | 加 `POST /console/api/v1/ov/auth/otp` 和 `POST /console/api/v1/ov/auth/oauth-verify` 转发路由 |
| `openviking/console/static/index.html` | Settings 面板加 "Authorize an MCP client" 表单（device flow 入口）和折叠的 legacy "Get OTP" 入口 |
| `openviking/console/static/app.js` | 表单事件 handler；调 verify 端点；keydown=Enter 触发授权 |

### 删除（vs 早期 JWT 方案）

- `openviking/server/oauth/jwt.py`（手搓 HS256）
- `tests/server/oauth/test_jwt.py`

---

## 端点全表

| 端点 | 方法 | 由谁实现 | 鉴权 | 说明 |
|---|---|---|---|---|
| `/.well-known/oauth-authorization-server` | GET | SDK | 无 | RFC 8414 |
| `/.well-known/oauth-protected-resource` | GET | OpenViking | 无 | RFC 9728，列出 issuer 和 bearer_methods |
| `/register` | POST | SDK | 无 | DCR (RFC 7591)，SDK 生成 client_id/secret，调 `provider.register_client()` |
| `/authorize` | GET/POST | SDK → provider.authorize() | 无 | SDK 校验 client + redirect_uri + PKCE，调 `provider.authorize()` 生成 display_code + pending_id；返回 302 → `/oauth/authorize/page?pending=...` |
| `/oauth/authorize/page` | GET | OpenViking | 无 | 显示 display_code + console 链接 + 同源 quick-authorize 面板（如检测到 sessionStorage 中的 API key）；JS 轮询 status |
| `/oauth/authorize/page/status` | GET | OpenViking | 无 | 返回 `{status: pending\|approved\|expired, redirect_url?}`；status=approved 时原子签发 auth_code 并删除 pending |
| `/token` | POST | SDK | client auth | SDK 验 PKCE / redirect_uri / client，调 `provider.exchange_authorization_code()` 或 `exchange_refresh_token()` |
| `/revoke` | POST | SDK | client auth | SDK 调 `provider.revoke_token()` |
| `POST /api/v1/auth/oauth-verify` | POST | OpenViking | 现有 API Key（`Depends(get_request_context)`） | 接受 `{code, decision: approve\|deny}`；approve 时把 caller 身份写入 pending；deny 时删除 pending |
| `POST /api/v1/auth/otp` | POST | OpenViking | 现有 API Key | **legacy push flow**：生成 OTP 绑定调用方身份；保留供 CLI/脚本场景使用 |

---

## 部署运维

### 启动

| 服务 | 命令 | 默认端口 |
|---|---|---|
| 主服务（API + MCP + OAuth） | `openviking-server [--host --port --config --workers]` | 1933 |
| Web Console | `python -m openviking.console.bootstrap [--host --port --openviking-url --write-enabled]` | 8020 |

> Console 当前没有 `openviking-console` entry point，只能 `python -m`。后续可加。

### 关键配置

| 项 | 何处 | 说明 |
|---|---|---|
| 存储路径 | `ov.conf:storage.workspace`（默认 `./data`） | `oauth.db` 落在 `<workspace>/oauth.db` |
| OAuth 启用 | `ov.conf:oauth.enabled = true` | 默认 false，关闭时所有 OAuth 路径不挂载 |
| Issuer URL | `OPENVIKING_PUBLIC_BASE_URL` env > `ov.conf:oauth.issuer` | 非 localhost 必须 HTTPS |
| TTL | `ov.conf:oauth.{access,refresh,auth_code,otp}_ttl_seconds` | 默认 1h / 30d / 5min / 5min |
| Console 上游 | `--openviking-url`（默认 `http://127.0.0.1:1933`） | 反代后改成 `http://127.0.0.1:1933` 即可 |

### nginx 反代模板（推荐部署形态，**未在仓库**）

```nginx
server {
  listen 443 ssl;
  server_name my.ov;

  # 8020 console
  location /console {
    proxy_pass http://127.0.0.1:8020;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  $host;
  }

  # 其他都走 1933 (REST + MCP + OAuth + .well-known)
  location / {
    proxy_pass http://127.0.0.1:1933;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  $host;
  }
}
```

这种部署下 console 和 OAuth page 同源，page 的"quick-authorize"面板自动可用。后续可在 `deploy/` 下落一份正式模板。

---

## 实施进度

### ✅ M1 — 基础设施
- `OAuthConfig` 接入 `OpenVikingConfig`（默认 disabled）
- `OAuthStore` 5 张表 + CRUD + 原子一次性消费 + revoke_user_tokens
- `oauth/otp.py` OTP 生成
- `app.py` lifespan 注入 store + provider + GC

### ✅ M2 — Bearer 路径与 401 头
- `auth.py` `_try_resolve_oauth_token` 识别 `ovat_` 前缀走 OAuth 路径
- `ResolvedIdentity.from_oauth` 标记位 + `get_request_context` 跳过 ROOT-tenant 强校验
- `mcp_endpoint.py` 401 注入 `WWW-Authenticate` 头（含 4 级 origin 回退）

### ✅ M3 — SDK 接入与完整流程
- `OpenVikingOAuthProvider`（8 个 Protocol 方法）
- 自定义 authorize HTML 页 + display_code 显示 + JS 轮询
- `POST /api/v1/auth/oauth-verify`（device flow 确认入口）
- `POST /api/v1/auth/otp`（legacy push flow，保留）
- `GET /.well-known/oauth-protected-resource`（RFC 9728）
- `app.py` 用 `create_auth_routes()` 挂 SDK 路由

### ✅ M4 — Console 集成
- console proxy 加 `oauth-verify` / `otp` 转发
- Settings 加 "Authorize an MCP client" 表单（device flow）
- legacy "Get OTP" 折叠在 details 内
- 同源 quick-authorize 面板（page JS 检测 sessionStorage）

### ✅ M5 — `OPENVIKING_PUBLIC_BASE_URL` 环境变量
- 4 级 origin 回退在 `_public_origin` (router) 和 `_scope_to_origin` (mcp_endpoint) 共用
- SDK issuer 启动时读

### ⏳ Phase 1 剩余
1. **Claude.ai / Claude Desktop / Cursor 端到端实测**（需要起 server，等 benchmark 跑完）
2. **`deploy/nginx.conf.example`** + 部署文档（同源 quick-authorize 的运维侧条件）
3. **`openviking-console` entry point script**（让 8020 启动统一为 `openviking-console`）

### 🔜 Phase 2 / 3（不在本 PR 范围）
- OAuth scope 机制（`mcp` / `fs.read` / `fs.write` / `admin`）
- GitHub / Google 第三方登录（`identity_links` 表）
- 邮件 OTP 投递（SMTP 集成）
- `ov otp` Rust CLI 子命令

---

## 验证

### 单元 / 集成测试
```bash
pytest tests/server/oauth/ -v   # 38 通过（含完整 device flow happy path）
pytest tests/server/test_auth.py tests/server/test_mcp_endpoint.py -v   # 回归
```

### 端到端 curl（device flow）
```bash
# 1. 注册客户端
curl -X POST -H "Content-Type: application/json" \
  -d '{"redirect_uris":["http://127.0.0.1:9999/cb"],"client_name":"test","token_endpoint_auth_method":"none"}' \
  http://127.0.0.1:1933/register

# 2. PKCE
VERIFIER=$(openssl rand -base64 64 | tr -d '=+/' | head -c 64)
CHALLENGE=$(printf "%s" "$VERIFIER" | openssl dgst -sha256 -binary | basenc --base64url | tr -d '=')

# 3. 浏览器: GET /authorize?... → 跳到 /oauth/authorize/page → 显示 6 字符码 e.g. "AB3X7K"

# 4. 用户在 console 输入 AB3X7K（或直接 curl）
curl -X POST -H "X-Api-Key: $ROOT_KEY" -H "Content-Type: application/json" \
  -d '{"code":"AB3X7K","decision":"approve"}' \
  http://127.0.0.1:1933/api/v1/auth/oauth-verify

# 5. Page 自动 302 回 redirect_uri，从中取 auth_code

# 6. 换 token
curl -X POST -d "grant_type=authorization_code&code=ovac_...&client_id=...&code_verifier=$VERIFIER&redirect_uri=..." \
  http://127.0.0.1:1933/token
# → {"access_token":"ovat_...","refresh_token":"ovrt_...","expires_in":3600}

# 7. 调 MCP
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
- `openviking/server/mcp_endpoint.py`（`WWW-Authenticate` + `_scope_to_origin`）
- `openviking/server/identity.py`（`from_oauth` 字段）
- `openviking/server/app.py`（lifespan + `create_auth_routes` + env-aware issuer）
- `openviking_cli/utils/config/open_viking_config.py`（接入 `OAuthConfig`）
- `openviking/console/app.py`（proxy `/auth/otp`, `/auth/oauth-verify`）
- `openviking/console/static/{index.html,app.js}`（device flow 表单 + 同源 quick-authorize）

**复用**（不改）：
- `openviking/server/identity.py:AuthMode/Role/ResolvedIdentity`
- `openviking_cli/utils/config/storage_config.py:StorageConfig.workspace`
- `mcp.server.auth.*`（官方 SDK，无新依赖）

---

## 风险与已识别问题

| 风险 | 处理 |
|---|---|
| 反代后 `issuer` 派生错（HTTPS 终结于代理） | `OPENVIKING_PUBLIC_BASE_URL` env 或 `oauth.issuer` 配置；非 localhost 部署强烈建议显式设 |
| 同源 quick-authorize 是隐式确认 | 即使检测到 sessionStorage，**仍需点击 "Authorize" 按钮**才生效，不会一步跳转 |
| display_code 暴力枚举 | 6 字符 × 32 字母表 = ~1B 组合；TTL 10min；pending 一次性消费；建议在反代层加每 IP 速率限制 |
| Refresh token 重放 | 实现：检测重放→`store.revoke_user_tokens(account, user)` 一并撤销该 user 名下所有 OAuth state |
| Token 权限范围 = 整个 REST API | 已与用户确认 Phase 1 不限制；Phase 2 引入 scope 机制收紧 |
| API Key → 撤销 OAuth token 的精度 | 当前粒度 `(account, user)`：删 user 时调 `revoke_user_tokens` cascade；满足需求 |
| Console 与 OAuth page 同源依赖反代 | 文档提供 nginx 模板；不反代时退化为"console 复制 OTP，page 输入"流程仍可用 |
