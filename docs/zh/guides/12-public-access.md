# 公网访问与反向代理

OpenViking 默认在 1933 端口对外提供 REST API、MCP、OAuth、`.well-known/*`
以及 Web Studio (`/studio`)。本指南讲怎么把它放到公网 HTTPS 域名后面。

> **为什么需要 HTTPS**：OAuth 2.1 / MCP SDK 对非 localhost 的 issuer
> **强制要求 HTTPS** —— Claude.ai、Claude Desktop、ChatGPT、Cursor 等 OAuth
> MCP 客户端没有 TLS 拒绝连接，会报 "Issuer URL must be HTTPS"。
> 仅 API Key 鉴权的客户端（含 Claude Code `--header` 直连）在 HTTP 下也能
> 工作，但生产环境仍建议上 TLS。

前提：有公网域名、80 + 443 端口可达、DNS 已指向。

## 先配置认证，再开放反向代理

发布任何路由前都要配置认证，即使上游只监听 `127.0.0.1`。反向代理会让这个本地服务可从公网访问。默认 `dev` 模式以 ROOT 身份接受请求；TLS 和 `OPENVIKING_PUBLIC_BASE_URL` 都不会增加身份认证。

使用 API Key 认证时，将下面的配置合并到 `ov.conf`，用密钥替换占位值，然后重启服务：

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "<your-secret-root-key>"
  }
}
```

root key 仅用于管理操作，例如通过 [Admin API](04-authentication.md) 创建 account 和首个管理员。租户数据 API 使用返回的 user/admin key。不要把 root key 放进公开的浏览器客户端，也不要让反向代理为所有请求自动附加 root key。后端端口应保持私有，让客户端通过预期的 HTTPS 入口访问。

允许用户接入前，在公网 URL 上验证：

```bash
# 上述 API Key 配置下预期为 401，不能成功返回目录列表。
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://ov.your-domain.com/api/v1/fs/ls?uri=viking://resources'

# 先将 OPENVIKING_API_KEY 设置为绑定租户身份的 user/admin key。预期为 200。
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-API-Key: $OPENVIKING_API_KEY" \
  'https://ov.your-domain.com/api/v1/fs/ls?uri=viking://resources'

# health 刻意不要求认证；这里的 200 不能证明访问控制已生效。
curl -sS https://ov.your-domain.com/health
```

如果使用 OIDC 或可信身份网关，应遵循对应模式的 [认证要求](04-authentication.md)，并验证未认证的数据请求会被拒绝。不要让可自行填写身份请求头的调用者直接访问 `trusted` 后端。

<a id="添加-https公网访问"></a>

## 方式 A：用自带 Caddy 自动签发 Let's Encrypt 证书（推荐）

`docker compose up` 默认带一个 Caddy 反代容器。给它追加一个域名块即可让
443 端口跑起来。

### 1. 创建 `.env`

```dotenv
OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com
OV_ACME_EMAIL=admin@your-domain.com   # 可选；推荐用于 Let's Encrypt
```

`OPENVIKING_PUBLIC_BASE_URL` 同时被 OpenViking 容器（发布在 OAuth 元数据和
`WWW-Authenticate` 头中）和 Caddy（作为 HTTPS 站点地址）读取。

### 2. 在 `Caddyfile` 追加域名块

```caddyfile
{$OPENVIKING_PUBLIC_BASE_URL} {
    reverse_proxy openviking:{$OPENVIKING_SERVER_PORT:1933}
    # 绑定 ACME 注册邮箱（可选）：
    # tls {$OV_ACME_EMAIL}
}
```

### 3. 取消 `docker-compose.yml` 中的 HTTPS 注释

三处：

```yaml
# caddy.ports 里取消注释：
- "80:80"
- "443:443"

# caddy.volumes 里取消注释：
- caddy_data:/data
- caddy_config:/config

# 文件末尾取消注释：
volumes:
  caddy_data:
  caddy_config:
```

只通过代理提供公网入口时，删除 OpenViking 的宿主机端口映射，或绑定到 `127.0.0.1`。旧的 `1934:1934` 映射也应删除或仅绑定 localhost。Caddy 仍可通过 Compose 网络访问 `openviking:1933`。

### 4. 启动

```bash
docker compose up -d
```

Caddy 会为配置的域名申请并续期证书，签发失败时查看 Caddy 日志。按请求触发签发需要单独配置，见 [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)。

### 5. 验证

```bash
curl https://ov.your-domain.com/health
# {"status": "ok"}

# OAuth 元数据（如果 oauth.enabled = true）：
curl https://ov.your-domain.com/.well-known/oauth-authorization-server

# 浏览器访问 Studio：
open https://ov.your-domain.com/studio
```

## 方式 B：用你自己的反向代理

已经有 nginx / Traefik / Envoy / Cloudflare 在做 TLS 终止时，直接把上游指向
OV 服务的 1933 端口。

### nginx

示例假设 nginx 与 OpenViking 在同一宿主机。超时值需按最长请求调整；关闭响应缓冲可及时传递 MCP 流式响应，见 [nginx 代理缓冲](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)。

```nginx
server {
    listen 443 ssl;
    http2 on;  # nginx < 1.25.1：删除此行，改用 `listen 443 ssl http2;`
    server_name ov.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/ov.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:1933;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }
}

server {
    listen 80;
    server_name ov.your-domain.com;
    return 301 https://$host$request_uri;
}
```

### Caddy（宿主机运行，不走 compose）

```caddyfile
ov.your-domain.com {
    reverse_proxy 127.0.0.1:1933
}
```

### Cloudflare / CDN

配置验证证书的 HTTPS 源站或私有源站隧道，限制源站只接受代理访问。设置 `OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com`，转发公网 host/protocol 请求头，并核对代理能否保留流式响应、允许所需请求时长和上传大小。

## 告诉服务端公网 URL

OAuth 元数据和 `WWW-Authenticate` 头需要包含公网 origin。OAuth 地址辅助函数的解析顺序（**优先级从高到低**）：

1. `OPENVIKING_PUBLIC_BASE_URL` 环境变量
2. `ov.conf` 里的 `oauth.issuer`
3. `X-Forwarded-Proto` + `X-Forwarded-Host` 请求头
4. 请求的 `Host` 头

MCP 上传 URL 的配置回退项是 `server.public_base_url`，并非 `oauth.issuer`。统一设置环境变量可让两者使用同一地址；若同时设置 `oauth.issuer`，保持 origin 一致。

使用反向代理时，在服务端进程环境中设置选项 1：

```bash
export OPENVIKING_PUBLIC_BASE_URL="https://ov.your-domain.com"
```

Compose 部署应修改 `.env` 并运行 `docker compose up -d`，在其他 shell 中执行 `export` 不会更新已有容器。只配置 OAuth issuer 时也可以使用 `ov.conf`：

```jsonc
{
  "oauth": {
    "enabled": true,
    "issuer": "https://ov.your-domain.com"
  }
}
```

## 兼容备注：`:1934` 单上游反代

`docker compose up` 默认在 1934 端口启一个 Caddy 反代，单纯 `reverse_proxy
openviking:1933`，**仅为兼容已经书签到 1934 的旧部署保留**。新部署直接连
私网中的 1933，公网客户端使用上面的 HTTPS 入口；不需要这个入口可以从 `docker-compose.yml` 注释
掉 caddy 服务和 1934 端口映射。

## 相关文档

- [部署指南](03-deployment.md) — Docker、systemd、Kubernetes
- [OAuth 指南](11-oauth.md) — OAuth 2.1 配置与客户端接入
- [认证](04-authentication.md) — API Key 管理
