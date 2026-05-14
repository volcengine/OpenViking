# 公网访问与反向代理

OpenViking 运行两个内部服务：

| 端口 | 服务 | 处理内容 |
|------|------|---------|
| 1933 | API 服务 | REST API、MCP、OAuth、`.well-known/*` |
| 8020 | Console | Web 管理界面，路径前缀 `/console/...` |

自带的 **Caddy 反向代理**将两者合并到一个端口 — **1934** — 客户端只需一个
URL。`docker compose up` 即可开箱使用。

## 端口总览

```
                   ┌────────────────────────┐
Internet / LAN ──► │  Caddy  :1934  (HTTP)  │
                   │                        │
                   │  /console/*  → :8020   │
                   │  /*          → :1933   │
                   └────────────────────────┘
```

1934 端口是纯 HTTP — 适用于本地开发、内网环境，也可以作为外部 TLS 终止代理
或 CDN 的上游。

如果需要**公网 HTTPS**（Claude.ai、ChatGPT、Cursor 等 MCP 客户端走 OAuth 时
必须），Caddy 还能在 443 端口自动签发 Let's Encrypt 证书。见下方
[添加 HTTPS](#添加-https-公网访问)。

## 快速开始（本地 / 内网）

```bash
docker compose up -d
```

两个服务在一个端口上可达：

```bash
curl http://localhost:1934/health                  # API 健康检查
curl http://localhost:1934/api/v1/system/status \
     -H "X-Api-Key: YOUR_KEY"                     # REST API
open http://localhost:1934/console                  # Web Console
```

1933 和 8020 仍可直接访问 — `docker-compose.yml` 里保留了端口映射方便调试。
确认 1934 一切正常后，可以把那两行注释掉。

## 添加 HTTPS（公网访问）

前提：有公网域名、80 + 443 端口可达、DNS 已指向。

### 1. 创建 `.env`

```dotenv
OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com
OV_ACME_EMAIL=admin@your-domain.com   # 可选；推荐用于 Let's Encrypt
```

`OPENVIKING_PUBLIC_BASE_URL` 同时被 OpenViking 容器（发布在 OAuth 元数据和
`WWW-Authenticate` 头中）和 Caddy（作为 HTTPS 站点地址）读取。

### 2. 在 `Caddyfile` 追加域名块

在已有的 `:1934` 块下面添加：

```caddyfile
{$OPENVIKING_PUBLIC_BASE_URL} {
    @console path /console /console/*
    handle @console {
        reverse_proxy openviking:8020
    }
    handle {
        reverse_proxy openviking:1933
    }
    # 绑定 ACME 注册邮箱（可选）：
    # tls {$OV_ACME_EMAIL}
}
```

`:1934` 块保留 — 继续为本地/内网提供 HTTP 访问。新块在 443 端口为公网域名
提供 HTTPS。

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
```

## 使用自己的反向代理

如果你已有 nginx、Traefik 或其他 TLS 终止代理，直接指向 1934 端口，不用分别
处理 1933 + 8020。1934 内部已经做好了路径路由。

### nginx（TLS 在 nginx 终止，HTTP 到 1934）

```nginx
server {
    listen 443 ssl http2;
    server_name ov.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/ov.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:1934;
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

这种方案下，Caddyfile 只保留 `:1934` 块，不加域名块。

### Caddy（宿主机运行，不走 compose）

```caddyfile
ov.your-domain.com {
    reverse_proxy 127.0.0.1:1934
}
```

### Cloudflare / CDN

CDN 源站指向 `http://your-server-ip:1934`。设置
`OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com` 让服务端知道自己的公网
地址。确保 CDN 转发 `Host`、`X-Forwarded-Proto`、`X-Forwarded-Host` 头。

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

## HTTPS 与 OAuth

OAuth 2.1（以及 MCP SDK）对非 localhost 的 issuer **强制要求 HTTPS**。
如果服务端发布的 origin 是非回环地址的 `http://`，MCP 客户端会拒绝连接并报
"Issuer URL must be HTTPS"。

这是协议层面的要求，不是 OpenViking 的限制。本地测试时
`http://127.0.0.1:1934` 无需 HTTPS。其他场景请按上面的方式配置 TLS。

非 OAuth 的 API 访问（API Key 认证）在 HTTP 下也能正常工作 — 协议本身不强制
bearer token 走 TLS，用户自行评估风险。

## 不用 Docker

如果直接运行 `openviking-server` 和 console（systemd、裸机等），在宿主机装
Caddy 并使用相同的 Caddyfile 模式，上游改为 `127.0.0.1`：

```caddyfile
:1934 {
    @console path /console /console/*
    handle @console {
        reverse_proxy 127.0.0.1:8020
    }
    handle {
        reverse_proxy 127.0.0.1:1933
    }
}

# 需要 HTTPS 时追加域名块：
# ov.your-domain.com { ... }
```

或者 nginx：

```nginx
server {
    listen 1934;

    location /console {
        proxy_pass http://127.0.0.1:8020;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }

    location / {
        proxy_pass http://127.0.0.1:1933;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }
}
```

## 相关文档

- [部署指南](03-deployment.md) — Docker、systemd、Kubernetes
- [OAuth 指南](11-oauth.md) — OAuth 2.1 配置与客户端接入
- [认证](04-authentication.md) — API Key 管理
