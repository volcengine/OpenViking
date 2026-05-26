# 公网访问与反向代理

OpenViking 默认在 1933 端口对外提供 REST API、MCP、OAuth、`.well-known/*`
以及 Web Studio (`/studio`)。本指南讲怎么把它放到公网 HTTPS 域名后面。

> **为什么需要 HTTPS**：OAuth 2.1 / MCP SDK 对非 localhost 的 issuer
> **强制要求 HTTPS** —— Claude.ai、Claude Desktop、ChatGPT、Cursor 等 OAuth
> MCP 客户端没有 TLS 拒绝连接，会报 "Issuer URL must be HTTPS"。
> 仅 API Key 鉴权的客户端（含 Claude Code `--header` 直连）在 HTTP 下也能
> 工作，但生产环境仍建议上 TLS。

前提：有公网域名、80 + 443 端口可达、DNS 已指向。

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
    reverse_proxy openviking:1933
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

### 4. 启动

```bash
docker compose up -d
```

首次 HTTPS 请求触发 ACME 证书签发，后续使用缓存。Caddy 自动续期。

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

```nginx
server {
    listen 443 ssl http2;
    server_name ov.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/ov.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:1933;
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

CDN 源站指向 `http://your-server-ip:1933`。设置
`OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com` 让服务端知道自己的
公网地址。确保 CDN 转发 `Host`、`X-Forwarded-Proto`、`X-Forwarded-Host` 头。

## 告诉服务端公网 URL

OAuth 元数据、`WWW-Authenticate` 头、资源 URL 都需要包含公网 origin。
解析顺序（**优先级从高到低**）：

1. `OPENVIKING_PUBLIC_BASE_URL` 环境变量
2. `ov.conf` 里的 `oauth.issuer`
3. `X-Forwarded-Proto` + `X-Forwarded-Host` 请求头
4. 请求的 `Host` 头

在反代后面，务必显式设置选项 1：

```bash
export OPENVIKING_PUBLIC_BASE_URL="https://ov.your-domain.com"
```

或者 `ov.conf`：

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
1933 即可，没有任何路由价值；不需要这个入口可以从 `docker-compose.yml` 注释
掉 caddy 服务和 1934 端口映射。

## 相关文档

- [部署指南](03-deployment.md) — Docker、systemd、Kubernetes
- [OAuth 指南](11-oauth.md) — OAuth 2.1 配置与客户端接入
- [认证](04-authentication.md) — API Key 管理
