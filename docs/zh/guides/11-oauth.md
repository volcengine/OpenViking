# OAuth 2.1 接入指南

OpenViking 服务端原生实现 OAuth 2.1。任何需要 OAuth 的客户端 — 包括 MCP
客户端（Claude.ai / Claude Desktop / ChatGPT / Cursor）以及其他浏览器应用
— 都可以直接对服务器授权，无需任何第三方代理。协议层（DCR、authorize、
token、metadata）由官方 `mcp.server.auth` SDK 提供，整体遵循 OAuth 2.1
规范，非 MCP 的 OAuth 客户端也能正常工作。

## 推荐配置

下面是能让 Claude.ai 等公网客户端跑通的最小配置。前提：你有一个公网域名，
80/443 端口可达，并安装了 docker compose。

1. **在 `docker-compose.yml` 旁创建 `.env`：**

   ```dotenv
   OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com
   OV_ACME_EMAIL=admin@your-domain.com   # 可选；推荐用于 Let's Encrypt
   ```

2. **在 `docker-compose.yml` 旁放一份 `Caddyfile`：**

   ```caddyfile
   {$OPENVIKING_PUBLIC_BASE_URL} {
       @console path /console /console/*
       handle @console {
           reverse_proxy openviking:8020
       }
       handle {
           reverse_proxy openviking:1933
       }
   }
   ```

3. **在 `~/.openviking/ov.conf` 启用 OAuth**（不开的话服务端会忽略 env
   变量，`/oauth/*` 路由也不会挂载）：

   ```json
   { "oauth": { "enabled": true } }
   ```

4. **取消 `docker-compose.yml` 末尾的 `caddy:` service 和 `volumes:` 段的
   注释**，DNS 指向本机，然后：

   ```bash
   docker compose up -d
   ```

5. **接入客户端。** Claude.ai → Connectors → Add → 输入
   `https://ov.your-domain.com/mcp`。浏览器打开授权页，显示一个 6 字符码；
   再打开 `https://ov.your-domain.com/console`，用 API Key 登录，把码粘到
   Settings → "Authorize an MCP client" → 点 Authorize。浏览器自动跳回
   Claude.ai，连接器就位。

线上路径就这五步。后续章节解释每一块为什么这样设计、不用 docker compose
怎么做、出问题时怎么用 curl 排查。

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

2. **同时启动 API server (1933) 和 console (8020)：**

   ```bash
   openviking-server
   # 另开一个终端
   python -m openviking.console.bootstrap --write-enabled
   ```

3. **登录 console**：访问 <http://127.0.0.1:8020/console>，把 API Key 粘到
   Settings → 点 Save。"Authorize an MCP client" 表单现在可用。

4. **接一个本地 MCP 客户端**（例如 MCP Inspector）到
   `http://127.0.0.1:1933/mcp`。客户端会走上面那套流程；把 authorize 页显示
   的 6 字符码复制到 console 表单 → 点 Authorize → 客户端拿到 token。

线上接 Claude.ai / Claude Desktop 走下面的 HTTPS 部署。

---

## 生产部署（HTTPS）

线上 OAuth 客户端需要：

1. 一个公网域名指向你的服务器（下面例子里用 `my.ov`）
2. 在反代层做 TLS 终止（Caddy 或 nginx）
3. 本机同时跑 `openviking-server` (1933) 和 console (8020)
4. 反代把两者放到**同一域名**下，OAuth 页才能与 console 同源（quick-authorize 才能用）

### 为什么需要反代

MCP 与 OAuth 端点**必须挂在公网域名根下**：

| 路径 | 来源 |
|---|---|
| `/.well-known/oauth-authorization-server` | RFC 8414 — 客户端拼 issuer URL + 此路径 |
| `/.well-known/oauth-protected-resource` | RFC 9728 — 通过 401 `WWW-Authenticate` 头发现 |
| `/register`、`/authorize`、`/token` | MCP SDK 默认挂在根 |
| `/mcp` | MCP 默认惯例 |

所以 **`openviking-server` (1933) 是"根域服务"**，console (8020) 住在
`/console/...` 子路径下（这是 console 现状：HTML 里硬编码了
`/console/styles.css`、`/console/app.js` 等等）。

### 告诉服务端自己的公网地址

OAuth 子系统会在多处发布带域名的 URL（issuer、PRM、`WWW-Authenticate`）。
解析顺序，**优先级从高到低**：

1. `OPENVIKING_PUBLIC_BASE_URL` 环境变量
2. `ov.conf` 里的 `oauth.issuer`
3. `X-Forwarded-Proto` + `X-Forwarded-Host` 请求头
4. 请求的 `Host` 头

反代后强烈建议显式设置 1 或 2，二选一：

```bash
# 进程环境（systemd / docker / …）
export OPENVIKING_PUBLIC_BASE_URL="https://my.ov"
```

```jsonc
// ov.conf
{
  "oauth": {
    "enabled": true,
    "issuer": "https://my.ov"
  }
}
```

### 反代配置 — Caddy（推荐）

Caddy 自动签发并续期 Let's Encrypt 证书。console 自己住在 `/console/...` 下
（HTML 里硬编码），所以用 `handle` 而不是 `handle_path`，**不**剥前缀。

`/etc/caddy/Caddyfile`：

```caddyfile
my.ov {
    @console path /console /console/*
    handle @console {
        reverse_proxy 127.0.0.1:8020
    }
    handle {
        # 其他都走 1933：MCP、OAuth、REST、.well-known
        reverse_proxy 127.0.0.1:1933
    }
}
```

`caddy reload` 后访问 <https://my.ov/console>，登录 API Key，OAuth 流程
就绪。Caddy 自动加 `X-Forwarded-Proto` 与 `X-Forwarded-Host`。

### 反代配置 — nginx

```nginx
server {
    listen 443 ssl http2;
    server_name my.ov;

    ssl_certificate     /etc/letsencrypt/live/my.ov/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/my.ov/privkey.pem;

    # 8020 console 在 /console/... 下
    location /console {
        proxy_pass http://127.0.0.1:8020;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }

    # 其他都走 1933（MCP、OAuth、REST、.well-known）
    location / {
        proxy_pass http://127.0.0.1:1933;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }
}

# HTTP → HTTPS 跳转
server {
    listen 80;
    server_name my.ov;
    return 301 https://$host$request_uri;
}
```

不需要 `proxy_pass_header Authorization` 或 `proxy_pass_header X-Api-Key` —
那些指令是用来透传**上游响应**头的；客户端请求头（Authorization、X-Api-Key）
默认就会被原样转发到上游。

### Docker Compose

仓库里的 `docker-compose.yml` 已带一份注释掉的 Caddy service。把
"本地 1933/8020" 升到 "公网 HTTPS at `https://my.ov`" 的步骤：

1. **在 `docker-compose.yml` 旁创建 `.env`**。`OPENVIKING_PUBLIC_BASE_URL`
   是公网地址的唯一来源，OpenViking 容器和 Caddy 都读它，只需设置一次：

   ```dotenv
   OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com
   OV_ACME_EMAIL=admin@your-domain.com   # 可选；推荐用于 Let's Encrypt
   ```

2. **在 `docker-compose.yml` 旁放一份 `Caddyfile`**。Caddy 接受完整 URL 作
   为 site 地址（`https://` 前缀会启用自动 HTTPS）：

   ```caddyfile
   {$OPENVIKING_PUBLIC_BASE_URL} {
       @console path /console /console/*
       handle @console {
           reverse_proxy openviking:8020
       }
       handle {
           reverse_proxy openviking:1933
       }
   }
   ```

   想把 Let's Encrypt 注册绑定到特定邮箱，可以在 site 块里加
   `tls {$OV_ACME_EMAIL}`。

3. **取消 `docker-compose.yml` 末尾的 `caddy:` service 和 `volumes:` 段的注释。**

4. **DNS** 指向本机公网 IP，确保 80/443 端口能进。

5. `docker compose up -d`。首次 HTTPS 请求会触发 ACME 签发，之后会缓存。

> Caddy service 通过 compose 的容器 DNS 直连，`reverse_proxy openviking:8020`
> 不需要把 8020 暴露到 host。Caddy 接管公网入口后，可以删掉
> `"8020:8020"` 与 `"1933:1933"` 这两条 host 端口映射。

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

- [MCP 规范 — Authorization](https://modelcontextprotocol.io/specification/2025-03-26/server/authorization)
- [RFC 8414 — OAuth 2.0 Authorization Server Metadata](https://datatracker.ietf.org/doc/html/rfc8414)
- [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 7591 — Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 7636 — PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [Caddyfile 语法](https://caddyserver.com/docs/caddyfile)
- [OpenViking MCP 集成指南](06-mcp-integration.md)
