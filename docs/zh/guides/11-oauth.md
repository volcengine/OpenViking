# OAuth 2.1 接入指南

OpenViking 服务端原生实现 OAuth 2.1。任何需要 OAuth 的客户端 — 包括 MCP
客户端（Claude.ai / Claude Desktop / ChatGPT / Cursor）以及其他浏览器应用
— 都可以直接对服务器授权，无需任何第三方代理。协议层（DCR、authorize、
token、metadata）由官方 `mcp.server.auth` SDK 提供，整体遵循 OAuth 2.1
规范，非 MCP 的 OAuth 客户端也能正常工作。

## 推荐配置

> **前提**：公网 HTTPS。OAuth 2.1（以及 MCP SDK）对非 localhost 的 issuer
> **强制要求 HTTPS**。请参阅[公网访问指南](12-public-access.md)了解如何配置
> Caddy 或 nginx 的 HTTPS。

1. **配置 HTTPS** — 按[公网访问指南](12-public-access.md)设置好
   `https://ov.your-domain.com`（Caddy + `.env` + `docker compose up`）。

2. **在 `~/.openviking/ov.conf` 启用 OAuth：**

   ```json
   { "oauth": { "enabled": true } }
   ```

3. **重启**（`docker compose restart openviking`）。

4. **接入客户端。** Claude.ai → Connectors → Add → 输入
   `https://ov.your-domain.com/mcp`。浏览器打开授权页，显示一个 6 字符码；
   再打开 `https://ov.your-domain.com/console`，用 API Key 登录，把码粘到
   Settings → "Authorize an MCP client" → 点 Authorize。浏览器自动跳回
   Claude.ai，连接器就位。

线上路径就这四步。后续章节解释每一块为什么这样设计、本地怎么不走 HTTPS 做
联调、出问题时怎么用 curl 排查。

---

## 为什么需要原生 OAuth

部分 MCP 客户端只接受 OAuth 2.1，不接受 API Key。在此之前唯一的方案是部署社区的
[MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) Cloudflare Worker
代理，把 OAuth 翻译成 API Key bearer。原生支持解决了：

- 额外部署单元（CF Worker + 2 个 KV namespace）
- 第三方信任面（代理运营方有解密上游 API Key 的能力）
- 用户在浏览器里手动粘贴 API Key 的体验

API Key 认证仍按原方式工作，OAuth 只是叠加层。

---

## 工作原理

OpenViking 实现的是 **device-flow 风格**的 OTP 流程（更接近 RFC 8628），
而不是早期"console 取码 → page 输入"的 push 流程。MCP 客户端打开浏览器
授权时：

```
1.  MCP 客户端  POST /mcp                                       → 401 + WWW-Authenticate
2.  MCP 客户端  GET  /.well-known/oauth-protected-resource      (RFC 9728)
3.  MCP 客户端  GET  /.well-known/oauth-authorization-server    (RFC 8414)
4.  MCP 客户端  POST /register                                  动态客户端注册 (RFC 7591)
5.  MCP 客户端  GET  /authorize?...                             (浏览器重定向)
6.  服务端     →    /oauth/authorize/page?pending=...
                    页面显示一个 6 字符码，例如 "AB3X7K"
7.  用户      打开 OpenViking Web Console（已登录状态）
              → Settings → "Authorize an MCP client" → 输入 AB3X7K → 点 Authorize
8.  服务端    把 pending 标记为 verified，绑定 console 用户的身份
              （account / user / role）
9.  页面      轮询 /oauth/authorize/page/status，命中 "approved"，
              自动跳转回 MCP 客户端的 redirect_uri 并附 auth code
10. MCP 客户端 POST /token (PKCE S256)                          → access_token (ovat_...)
                                                                  + refresh_token (ovrt_...)
11. MCP 客户端 POST /mcp (Authorization: Bearer ovat_...)        → 调工具
```

6 字符码显示在 authorize 页，**用户把它输入到 console 表单**（而不是反过来）。
这样确认动作发生在用户已登录的环境，符合"我在已登录的地方批准那边的请求"的直觉。

如果 console 与 OAuth 页面同源（推荐部署形态），page 还会检测用户已有的 console
session，提供一键 "Quick authorize" 按钮 — **依然需要点一下**，不会自动跳。

服务端还保留了"push 模式"的 OTP 端点 `POST /api/v1/auth/otp` 供 CLI 场景使用：
console 也提供一个折叠的 "Generate OTP" 入口供脚本类客户端使用。

---

## 快速验证（HTTP，仅本地）

最快确认 OAuth 装配正确的方式是在 `127.0.0.1` 跑一遍。MCP SDK 接受
`http://127.0.0.1` 与 `http://localhost` 作为 issuer URL 而无需 HTTPS — 但
Claude.ai / Claude Desktop 等线上客户端**只接受公网 HTTPS**，所以这个模式只
适合用 [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
之类的本地工具做联调。

1. **在 `~/.openviking/ov.conf` 启用 OAuth：**

   ```json
   {
     "oauth": {
       "enabled": true
     }
   }
   ```

2. **启动：**

   ```bash
   docker compose up -d
   ```

   或不用 Docker：

   ```bash
   openviking-server
   # 另开一个终端
   python -m openviking.console.bootstrap --write-enabled
   ```

3. **登录 console**：访问 <http://127.0.0.1:1934/console>（不走聚合代理时用
   `:8020/console`），把 API Key 粘到 Settings → 点 Save。"Authorize an MCP
   client" 表单现在可用。

4. **接一个本地 MCP 客户端**（例如 MCP Inspector）到
   `http://127.0.0.1:1934/mcp`（或 `:1933/mcp`）。客户端会走上面那套流程；把
   authorize 页显示的 6 字符码复制到 console 表单 → 点 Authorize → 客户端拿到
   token。

线上接 Claude.ai / Claude Desktop 走[公网访问指南](12-public-access.md)。

---

## 生产部署（HTTPS）

OAuth 2.1 对非 localhost 的 issuer **强制要求 HTTPS**。
[公网访问指南](12-public-access.md)详细介绍了 Caddy、nginx、docker compose、
CDN 的配置方法。简要步骤：

1. 按[公网访问指南 § 添加 HTTPS](12-public-access.md#添加-https公网访问)
   配置好 `https://your-domain.com`，使 1934 端口走 TLS。
2. 启用 OAuth：`ov.conf` 里 `{ "oauth": { "enabled": true } }`。
3. 重启：`docker compose restart openviking`。
4. 在 `.env` 设置 `OPENVIKING_PUBLIC_BASE_URL=https://your-domain.com`
   （服务端用它作为 OAuth 元数据和 `WWW-Authenticate` 的 issuer）。

HTTPS + OAuth 就绪后，按下面的方式接入客户端。

---

## 接入仅支持 OAuth 的 MCP 客户端

### Claude.ai (Web)

1. Settings → Connectors → **Add connector**。
2. 输入 `https://my.ov/mcp` 作为服务器 URL。
3. Claude 弹出授权页面，记下 6 字符码。
4. 另开标签访问 <https://my.ov/console>（弹出页里也有这个链接）。
5. 登录（如未登录）→ Settings → "Authorize an MCP client" → 粘码 → **Authorize**。
6. 弹出页自动跳回 Claude，token 已颁发。

### Claude Desktop / Claude Code

Claude Desktop 流程相同。Claude Code 直接用 API Key 更简单：

```bash
claude mcp add --transport http openviking https://my.ov/mcp \
  --header "Authorization: Bearer <api-key>"
```

如果你想让 Claude Code 走 OAuth，体验和 Claude.ai 一致。

### ChatGPT (Codex / Plus / Enterprise)

Settings → Beta features → Custom Connectors。输入 MCP URL，ChatGPT 通过
`/.well-known/...` 文档自动发现 OAuth 端点，走相同的 authorize → token 流程。

### Cursor

Cursor 看到 401 + `WWW-Authenticate: Bearer resource_metadata=...` 后会自动
进入 OAuth 流程。在 Cursor 的 MCP 设置里加 URL 即可。

---

## 用 `curl` 验证完整流程

不需要真实 MCP 客户端：

```bash
# 1. 注册客户端
curl -X POST -H "Content-Type: application/json" \
     -d '{"redirect_uris":["http://127.0.0.1:9999/cb"],"client_name":"test","token_endpoint_auth_method":"none"}' \
     https://my.ov/register
# → {"client_id":"...", ...}

# 2. PKCE 对
VERIFIER=$(openssl rand -base64 64 | tr -d '=+/' | head -c 64)
CHALLENGE=$(printf "%s" "$VERIFIER" | openssl dgst -sha256 -binary | basenc --base64url | tr -d '=')

# 3. 浏览器访问 authorize URL，页面会显示 6 字符码
echo "https://my.ov/authorize?response_type=code&client_id=$CID&redirect_uri=http://127.0.0.1:9999/cb&code_challenge=$CHALLENGE&code_challenge_method=S256&state=xyz"

# 4. 在 console 输入码（或直接 curl）
curl -X POST -H "X-Api-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"code":"AB3X7K","decision":"approve"}' \
     https://my.ov/api/v1/auth/oauth-verify

# 5. 浏览器自动 302 到 /cb?code=ovac_...&state=xyz，记下 code

# 6. 用 auth code 换 token
curl -X POST \
     -d "grant_type=authorization_code&code=ovac_...&client_id=$CID&code_verifier=$VERIFIER&redirect_uri=http://127.0.0.1:9999/cb" \
     https://my.ov/token
# → {"access_token":"ovat_...","refresh_token":"ovrt_...","expires_in":3600}

# 7. 用 access token 调 MCP
curl -X POST -H "Authorization: Bearer ovat_..." \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
     https://my.ov/mcp
```

---

## 配置参考

`ov.conf` 片段：

```jsonc
{
  "oauth": {
    "enabled": false,                       // 默认关闭
    "issuer": null,                         // 例 "https://my.ov"（可选；env 变量优先级更高）
    "access_token_ttl_seconds": 3600,       // 1 小时
    "refresh_token_ttl_seconds": 2592000,   // 30 天
    "auth_code_ttl_seconds": 300,           // 5 分钟
    "otp_ttl_seconds": 300,                 // 5 分钟
    "db_filename": "oauth.db",              // 相对 storage.workspace
    "authorize_rate_limit_per_min": 10
  }
}
```

环境变量：

| 变量 | 用途 |
|---|---|
| `OPENVIKING_PUBLIC_BASE_URL` | 最高优先级的公网 origin override（用作 issuer / PRM / `WWW-Authenticate`） |
| `OPENVIKING_CONFIG_FILE` | `ov.conf` 路径（也可用 `--config`） |

---

## Token 模型

| Token | 形态 | 前缀 | TTL | 存储 |
|---|---|---|---|---|
| Access token | `secrets.token_urlsafe(40)` | `ovat_` | 1 小时 | SQLite (SHA-256 索引) |
| Refresh token | `secrets.token_urlsafe(40)` | `ovrt_` | 30 天 | SQLite (SHA-256 索引) |
| Authorization code | `secrets.token_urlsafe(40)` | `ovac_` | 5 分钟 | SQLite (SHA-256 索引) |
| Display code（页面） | 6 字符（去 O/0/I/1） | — | 10 分钟 | SQLite (`oauth_pending_authorizations`) |

所有 token 都是 opaque（不签发 JWT），服务端**没有任何加密密钥需要管理**。
每次请求按 SHA-256 哈希查 SQLite，撤销 token 是一次 `UPDATE`。

### Token 与身份

每个 token 在签发时绑定一个 `(account_id, user_id, role)` 三元组。OAuth
token 拥有的权限 = 颁发它时所用 API Key 的权限，**不更多也不更少**。

API Key 轮换或删除时，运维侧应同时撤销该 user 名下的 OAuth token：
`OAuthStore.revoke_user_tokens(account_id, user_id)`（后续会通过 console
admin 端点暴露）。

---

## 故障排查

### Claude.ai 直接报 "We couldn't connect" 没弹出授权页

Claude.ai 第一步是 GET `/.well-known/oauth-protected-resource`。如果这一步
404，OAuth 流程就根本不会启动。检查：

```bash
curl -i https://my.ov/.well-known/oauth-protected-resource
```

应当返回带 `authorization_servers` 字段的 JSON。如果是 404，要么
`oauth.enabled = false`，要么反代没把 `/.well-known/...` 路径转发到 1933。

### "Issuer URL must be HTTPS"

MCP SDK 拒绝非 `127.0.0.1` / `localhost` 的 `http://` issuer。三选一：

- 设置 `OPENVIKING_PUBLIC_BASE_URL=https://my.ov`
- 在 `ov.conf` 里把 `oauth.issuer` 写成 `https://...`
- 仅本地测试时让客户端直连 `http://127.0.0.1:1933`

### Authorize 页有码，但 console 报 "Invalid code"

码是 6 字符**全大写**，传输时区分大小写。console 的输入框会自动转大写。如果手
动输入，注意字母与数字的混淆字符（字母表已经排除了 `O`、`0`、`I`、`1`）。

### Refresh 一次后再用旧 token 被拒

Refresh token 是一次性的。如果旧 refresh 与新 refresh **同时被使用**（例如客户
端有 bug），第二个会被拒绝，整条 token 链会被撤销（RFC 9700 §4.14）。客户端必
须重新走 authorize 流程。

### `/mcp` 401 没有 `WWW-Authenticate` 头

这个头只在 `app.state` 上有 `oauth_provider` 时才发出 — 即
`oauth.enabled = true`。检查：

```bash
curl -i https://my.ov/mcp -d '{}' -H 'Content-Type: application/json' | grep -i www-authenticate
```

---

## 参考

- [公网访问与反向代理指南](12-public-access.md) — HTTPS、Caddy、nginx、docker compose
- [MCP 规范 — Authorization](https://modelcontextprotocol.io/specification/2025-03-26/server/authorization)
- [RFC 8414 — OAuth 2.0 Authorization Server Metadata](https://datatracker.ietf.org/doc/html/rfc8414)
- [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 7591 — Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 7636 — PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [OpenViking MCP 集成指南](06-mcp-integration.md)
