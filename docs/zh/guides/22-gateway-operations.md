---
description: 用 Docker Compose、Helm 或自建反向代理部署 OpenViking 网关，在 Studio 中管理网关并排查问题。
---

# OpenViking 网关部署与运维

本页写给负责运行 OpenViking 网关的人，内容包括部署网关，在 Studio 中管理上游、上下文配置和密钥，保护数据，日常运维，以及排查问题。网关能做什么、客户端怎么接入，见[OpenViking 网关](15-gateway.md)；整体架构见其中的[一张图看懂架构](15-gateway.md#一张图看懂架构)。

网关目前处于 Beta 阶段，设置和接口可能随版本调整，升级前请先阅读发布说明。

网关是独立于 OpenViking Server 的进程，默认监听 1935 端口。客户端把模型请求发给网关；管理网关则在 Studio 中进行，Studio 由 OpenViking Server 提供，管理操作经 OpenViking Server 转发给网关。两者之间只走 HTTP：网关调用 OpenViking 的公开接口搜索记忆、保存对话和执行工具，OpenViking Server 则调用网关的管理接口。下图是对外只开一个 HTTPS 地址时的请求路径：

```text
                       客户端
                         |
                         v
          反向代理 (https://ov.example.com)
             |                                  |
             | /v1/*                            | 其他路径：
             | /api/v3/*                        | /studio、/api/v1、
             | /api/compatible/v1/*             | /mcp、/health 等
             | /gateway/uploads                 |
             v                                  v
      OpenViking 网关 :1935 <----- 管理 ---- OpenViking Server :1933
         |        |                                  ^
         |        +-------- 搜索、保存、工具 --------+
         v
      模型服务商 (上游)
```

## 环境要求

- **OpenViking Server 0.4.16 或更高版本，并运行在 API Key 模式**（`server.auth_mode: "api_key"`，同时配置 `root_api_key`）。网关用每个人自己的 OpenViking 密钥，以这个人的身份访问 OpenViking。dev 模式下任何密钥都按 root 身份处理，而网关不接受 root 身份，所以一个网关密钥也签发不了。
- **网关本身**：在 Python 3.10 或更高版本上安装 `openviking[gateway]` 可选依赖，或者使用官方 OpenViking Docker 镜像，镜像里已经包含 `openviking-gateway` 命令。
- **用于 Studio 的账号管理员密钥。** 你在 Studio 中配置的内容，都属于登录所用密钥所在的账号。root key 也能用，但它管理的是它解析到的那个账号，所以优先使用账号管理员的密钥。
- **每位领取网关密钥的人都要是同一账号中的 OpenViking 用户**，普通用户和管理员都可以，root 不行。签发时由 OpenViking Server 读取这个用户的 OpenViking 密钥；如果它只保存密钥哈希，就需要这个用户自己提供密钥。
- **模型服务商的 API Key。** 订阅登录会被拒绝；Coding Plan 密钥默认也会被拒绝，除非你明确允许。
- **单台主机上的本地磁盘**，用于网关存储（默认 `~/.openviking/gateway`）。不支持网络文件系统，也不支持在多台主机之间共享存储。同一时间只运行一个网关实例；需要更多处理能力时增加工作进程，见[扩展](#扩展)。
- **加密密钥和管理令牌**，见下一节。

## 加密密钥和管理令牌

| 名称 | 环境变量 | 谁需要 | 作用 |
| --- | --- | --- | --- |
| 加密密钥 | `OPENVIKING_GATEWAY_ENCRYPTION_KEY` | 网关 | 加密网关存储的全部数据：上游 API Key 和请求头、绑定到网关密钥的 OpenViking 密钥、对话状态和请求日志。必须是 Fernet 密钥，即 32 字节随机数的 URL 安全 base64 编码。 |
| 管理令牌 | `OPENVIKING_GATEWAY_ADMIN_TOKEN` | 网关和 OpenViking Server | 你在 Studio 中操作时，OpenViking Server 用它向网关的管理接口证明身份。至少 32 个字符。 |

两者各生成一次：

```bash
# 加密密钥（格式与 cryptography 包中 Fernet.generate_key() 的输出相同）
python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'

# 管理令牌
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

把它们放进密钥管理系统或部署环境，不要写进 `ov.conf`，也不要提交到代码仓库。如果要从其他名称的环境变量读取，在 `gateway` 中设置 `encryption_key_env` 和 `admin_token_env`。

- **加密密钥不能更换。** 网关不会重新加密已存储的数据，换了密钥就读不出原来的内容。请把密钥和网关存储一起备份。密钥丢失后，只能把 `storage_path` 指向一个空目录，重新配置上游、上下文配置和密钥。
- **管理令牌可以轮换。** 给两个进程设置相同的新值，然后都重启。它只保护管理接口，不加密任何数据。
- 缺少有效的加密密钥或管理令牌时，网关在启动阶段就会退出。OpenViking Server 没有管理令牌也能启动，但 Studio 的 OpenViking 网关页面会提示缺少管理令牌。

## 部署

### 部署方式

网关和 OpenViking 之间只走 HTTP，所以可以装在同一台机器、放在同一个 Pod，也可以分开部署。无论哪种形态，都要满足[环境要求](#环境要求)：OpenViking 0.4.16 或更高版本并开启 API Key 认证，网关存储放在一台主机的本地磁盘上，同一时间只运行一个网关实例，网关持有加密密钥，两个进程配置同一个管理令牌。负载均衡、故障转移和限流交给接在网关后面的 LiteLLM 或 new-api。

| 形态 | 适合 | 怎么部署 | 要留意 |
| --- | --- | --- | --- |
| 单机 | 个人试用 | 安装 `openviking[gateway]`，运行 `openviking-gateway`，和 OpenViking 读取同一份 `ov.conf`，步骤见[快速开始](15-gateway.md#快速开始)。 | 只在本机使用时可以不配反向代理。 |
| [Docker Compose](#docker-compose) | 小团队、单台服务器 | 网关是单独的容器，自带的 Caddy 在 1934 端口按路径分流。 | 网关端口不映射到主机；加密密钥只传给网关容器。 |
| [Helm](#helm) | Kubernetes | 网关作为 OpenViking Pod 里的第二个容器，共用存储卷和 `ov.conf`。 | 保持一个副本；Chart 不支持把网关拆成单独的 Pod。 |
| [分开部署](#分开部署) | 网关和 OpenViking 在不同的机器或 Pod | 自己编写 Deployment 或服务定义，两边的 `ov.conf` 放同一份 `gateway` 配置。 | 两边互相要能访问；链路上传输用户的 OpenViking 密钥；网络延迟会占用召回的等待时间。 |

两个进程读取同一个 `ov.conf` 中的 `gateway` 部分。OpenViking Server 用它找到网关，供 Studio 和数据删除使用；网关则用到整个部分。这一部分有几条规则：

- 网关读取 `--config` 指定的文件，没有指定时读取 `OPENVIKING_CONFIG_FILE`，再没有就读取 `~/.openviking/ov.conf`。与 OpenViking Server 不同，它不会回退到 `/etc/openviking/ov.conf`。
- 网关不展开 `$VAR` 或 `${VAR}` 占位符。`gateway` 中请写字面值，密钥和令牌放在上面的环境变量里。
- 未知的键会报错。OpenViking Server 报告 `Unknown config field 'gateway.<name>'`，网关则拒绝启动。
- 修改这一部分后，要重启 OpenViking Server。

其中三个地址容易混淆：

| 设置 | 谁使用 | 常见取值 |
| --- | --- | --- |
| `url` | OpenViking Server，用来访问网关的管理接口 | `http://127.0.0.1:1935`；Docker Compose 中为 `http://gateway:1935` |
| `openviking_url` | 网关，用来访问 OpenViking Server | `http://127.0.0.1:1933`；Docker Compose 中为 `http://openviking:1933` |
| `public_url` | 客户端。Studio 的接入说明显示这个地址，OpenViking 工具也用它生成上传链接。 | `https://ov.example.com` |

### 路由

让 OpenViking Server 和网关共用同一个公网 HTTPS 地址，由反向代理按路径分流：

| 路径 | 转发到 | 用途 |
| --- | --- | --- |
| `/v1/*` | 网关 | Anthropic Messages、Chat Completions、Responses 和模型列表 |
| `/api/v3/*` | 网关 | 方舟路径（火山方舟和 BytePlus 方舟）下的 Chat Completions、Responses 和模型列表 |
| `/api/compatible/v1/*` | 网关 | 方舟的 Anthropic 兼容路径 |
| `/gateway/uploads` | 网关 | OpenViking 工具的一次性文件上传 |
| 其他所有路径 | OpenViking Server | Studio、REST API、MCP、OAuth、`/health` |

按这种布局，`public_url` 就填这个公网地址，例如 `https://ov.example.com`。Anthropic 客户端直接使用这个地址，OpenAI 风格的客户端在后面加上 `/v1`。

> **安全**：不要从公网入口转发网关的 `/admin/*` 路径，也不要直接暴露网关端口。网关的管理接口只认管理令牌，持有令牌就能管理任何账号。Studio 经由 OpenViking Server 访问管理接口，OpenViking Server 会确认你是账号管理员，并把每次调用限制在你自己的账号内。只转发上面四组路径，1933 和 1935 端口都留在代理后面，不对外开放。

不管用哪种代理，网关路径都要按长时间的流式模型调用来配置：

- **关闭响应缓冲**，流式回复才能边生成边送达客户端。
- **请求体上限至少 32 MiB**（`max_body_bytes`）。带工具输出的长对话请求体很大，nginx 默认的 1 MB 会让这些请求失败。
- **读超时至少 600 秒**（`upstream_timeout_seconds`）。
- **`/gateway/uploads` 的访问日志不记录查询字符串**，因为里面带有一次性上传令牌。网关本身不写访问日志。

### Docker Compose

仓库自带的 `docker-compose.yml` 在 `gateway` profile 下定义了 `gateway` 服务。它与 OpenViking Server 使用同一个镜像、同一个 `~/.openviking` 挂载和同一个 `ov.conf`。它的 1935 端口只在 Compose 网络内可达，客户端通过自带的 Caddy 访问网关，Caddy 已经在 1934 端口把网关路径转发过去。

1. **把加密密钥和管理令牌写进 `.env`**，放在 `docker-compose.yml` 旁边：

   ```dotenv
   OPENVIKING_GATEWAY_ENCRYPTION_KEY=<encryption-key>
   OPENVIKING_GATEWAY_ADMIN_TOKEN=<admin-token>
   ```

   Compose 把管理令牌传给两个容器，加密密钥只传给网关。其中任何一个为空，网关都会退出，Compose 则会不停地重启它。

2. **把以下设置合并进 `~/.openviking/ov.conf`：**

   ```json
   {
     "server": {
       "auth_mode": "api_key",
       "root_api_key": "<root-key>"
     },
     "gateway": {
       "enabled": true,
       "host": "0.0.0.0",
       "url": "http://gateway:1935",
       "openviking_url": "http://openviking:1933",
       "public_url": "http://<your-host>:1934"
     }
   }
   ```

   客户端还在访问自带 Caddy 的端口时，使用 `http://<your-host>:1934`；配好 HTTPS 地址之后改用它。`storage_path` 可以保持默认：在容器内它解析为 `/app/.openviking/gateway`，正好位于挂载卷中。

3. **带上 profile 启动：**

   ```bash
   docker compose --profile gateway up -d
   ```

   之后执行 `up` 时也要带上 `--profile gateway`。修改 `ov.conf` 后，用 `docker compose --profile gateway restart openviking gateway` 重启两个容器。

4. **检查路由。** 不带密钥的请求应该到达网关并被拒绝：

   ```bash
   curl -s http://127.0.0.1:1934/v1/models
   # {"detail":"Invalid or revoked OpenViking Gateway key"}
   ```

   然后打开 Studio，确认“概览”标签页显示 OpenViking 已连接。启动错误可以用 `docker compose logs gateway` 查看。

如果网关启动时 OpenViking Server 还没有就绪，网关会先在不带记忆的状态下运行，直到下一次检查 OpenViking，最多等 60 秒（`health_interval_seconds`）。这段时间里发出的消息不带记忆直接发给模型。

**HTTPS。** 按[公网访问与反向代理](12-public-access.md#添加-https公网访问)中的方式 A 设置 `OPENVIKING_PUBLIC_BASE_URL`、开放 80 和 443 端口并添加 Caddy 卷，但 `Caddyfile` 中的域名块改用下面这段，让网关路径转发到网关：

```caddyfile
{$OPENVIKING_PUBLIC_BASE_URL} {
    @gateway path /v1/* /api/v3/* /api/compatible/v1/* /gateway/uploads
    handle @gateway {
        reverse_proxy gateway:1935 {
            flush_interval -1
        }
    }
    handle {
        reverse_proxy openviking:{$OPENVIKING_SERVER_PORT:1933}
    }
}
```

然后把 `gateway.public_url` 设为同一个地址，并写成字面值（例如 `https://ov.example.com`），再重启。

### Helm

Chart 把网关作为 OpenViking Pod 中的第二个容器运行，与 OpenViking 共用存储卷和 `ov.conf`。

1. **创建 Secret。** 键名 `encryption-key` 和 `admin-token` 是固定的：

   ```bash
   kubectl create secret generic openviking-gateway \
     --from-literal=encryption-key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
     --from-literal=admin-token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
   ```

2. **在 values 中启用网关。** 下面的 NGINX Ingress 注解是针对流式响应和大请求的推荐配置，Chart 不会自动设置：

   ```yaml
   gateway:
     enabled: true
     existingSecret: openviking-gateway
     workers: 2

   config:
     server:
       auth_mode: api_key
       # 按 Chart README 的说明从 Secret 传入 root_api_key。
     gateway:
       public_url: https://ov.example.com

   ingress:
     enabled: true
     className: nginx
     annotations:
       nginx.ingress.kubernetes.io/proxy-buffering: "off"
       nginx.ingress.kubernetes.io/proxy-request-buffering: "off"
       nginx.ingress.kubernetes.io/proxy-body-size: 32m
       nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
       nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
     hosts:
       - host: ov.example.com
         paths:
           - path: /
             pathType: Prefix
     tls:
       - secretName: ov-example-com-tls
         hosts:
           - ov.example.com
   ```

values 中的 `gateway.enabled` 为 true 时，Chart 会：

- 生成 `gateway` 部分的其余设置：`enabled`、`host: 0.0.0.0`、`port`、`workers`、`url: http://127.0.0.1:<port>`、`openviking_url: http://127.0.0.1:<config.server.port>` 和 `storage_path: <persistence.mountPath>/gateway`。你在 `config.gateway` 下写的值优先。`public_url` 就在那里设置，并写成字面值。
- 把管理令牌传给两个容器，把加密密钥传给网关容器。网关容器不接收 `extraEnv`。
- 在 Service 上添加 `gateway` 端口。启用 Ingress 时，把 `/v1`、`/api/v3`、`/api/compatible/v1` 和 `/gateway/uploads` 路由到这个端口，并排在你自己的路径之前。
- 为网关的 `/health` 添加就绪探针。`gateway.resources` 设置网关容器的资源请求和限制。

保持 `replicaCount: 1`。网关的存储位于 ReadWriteOnce 卷上，只能由一台主机使用；需要更多处理能力时，调大 `gateway.workers`。如何从 Secret 传入 root key 和模型密钥，见 [Chart README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md)。

### 分开部署

网关和 OpenViking 运行在不同的机器或 Pod 上时，Chart 和 Compose 文件不再适用，需要自己编写部署定义。两边的 `ov.conf` 放同一份 `gateway` 配置，并注意以下几点：

- **两个方向的地址都要填。** 网关通过 `openviking_url` 访问 OpenViking；OpenViking Server 通过 `url` 访问网关，Studio 的管理操作和删除用户数据都经过这个地址。两个地址都应该是内网地址，网关的 `host` 要监听对方能访问的网卡。
- **保护两者之间的链路。** 网关调用 OpenViking 时带着用户自己的 OpenViking 密钥，管理请求也带着管理令牌，所以这段链路要走内网或 TLS。
- **留意延迟。** 每条新消息的召回最多等上下文配置中的**超时时间**（默认 2 秒），网关到 OpenViking 的网络延迟也算在里面。延迟较高时，召回更容易超时，消息就不带记忆发给模型。
- **注意版本匹配。** 两边分别升级时，先升级 OpenViking，再升级网关，见[升级](#升级)。

### 自建反向代理

nginx 与两个进程在同一台主机上时，把网关路径写在兜底的 location 之前：

```nginx
server {
    listen 443 ssl;
    http2 on;  # nginx < 1.25.1：删掉这一行，改用 `listen 443 ssl http2;`
    server_name ov.example.com;

    ssl_certificate     /etc/letsencrypt/live/ov.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.example.com/privkey.pem;

    # 模型 API：流式回复、很长的历史、响应慢的模型。
    location ~ ^/(v1|api/v3|api/compatible/v1)/ {
        proxy_pass http://127.0.0.1:1935;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_request_buffering off;
        client_max_body_size 32m;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 一次性文件上传。查询字符串里带着上传令牌。
    location = /gateway/uploads {
        proxy_pass http://127.0.0.1:1935;
        proxy_http_version 1.1;
        proxy_request_buffering off;
        client_max_body_size 32m;
        proxy_read_timeout 120s;
        access_log off;
    }

    # Studio、REST API、MCP 和其他所有路径。
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
```

如果调大了 `max_body_bytes` 或 `upstream_timeout_seconds`，`client_max_body_size` 和各项超时也要一起调大。其他代理同样按这四组路径和[路由](#路由)一节列出的设置来配置。

### 扩展

- **工作进程。** `workers`（1–64；默认 1，Helm Chart 中为 2）决定主机上运行几个网关进程。它们共用存储文件，每个进程还会在后台保存对话。
- **单台主机。** 不支持在多台主机上运行网关，也不支持网络文件系统。使用 Helm 时保持一个副本。
- **变更传播。** 吊销密钥或编辑上游后，其他工作进程大约 2 秒内跟上。
- **健康状态按进程计算。** 每个工作进程独立检查 OpenViking，所以“概览”标签页上的 OpenViking 状态来自恰好响应这次查询的那个进程。
- **没有负载均衡和限流。** 每段对话固定在一个上游上，网关不会换一个上游重试，也不限流；服务商返回的 429 原样传给客户端。需要负载均衡、故障转移或配额时，在网关后面接 LiteLLM 或 new-api，再把它添加为上游。

## 在 Studio 中管理网关

在 OpenViking Server 上打开 `/studio`，进入**连接设置**，把账号管理员密钥填入**管理员 API 密钥**。侧边栏的“设置”分组里随即出现**OpenViking 网关**，只有账号管理员和 root 能看到它。这些页面上的所有内容都属于该密钥所在的账号。页面顶部显示客户端应该使用的网关地址，即 `public_url`；`public_url` 为空时显示 `url`，“接入”标签页会提示客户端可能连不上这个地址。

如果网关没有配置好或者无法访问，页面会显示一张设置卡片，指出要修改哪项配置，见 [Studio 显示设置卡片](#studio-显示设置卡片)。否则页面有六个标签页：**概览**、**上游**、**上下文配置**、**密钥**、**请求日志**和**接入**。初次设置就按这个顺序：添加上游，创建上下文配置，签发网关密钥，最后按“接入”标签页的说明接入客户端。

### 上游

上游是模型服务商的一个接口，只使用一种 API：Anthropic Messages、Chat Completions 或 Responses。网关不在它们之间做转换，所以客户端用到几种 API，就要添加几个上游，即使它们来自同一个服务商：Claude Code 用 Anthropic Messages，Codex CLI 用 Responses，大多数聊天应用和 SDK 用 Chat Completions。

各服务商提供的协议如下。在编辑页选择服务商后，会自动填入它的默认 Base URL：

| 服务商 | 协议 | 默认 Base URL |
| --- | --- | --- |
| 通用 | 三种都有 | 无，需要自己填写 |
| Anthropic | Anthropic Messages | `https://api.anthropic.com` |
| OpenAI | Chat Completions、Responses | `https://api.openai.com/v1` |
| DeepSeek | 三种都有 | `https://api.deepseek.com`；Anthropic Messages 为 `https://api.deepseek.com/anthropic` |
| 火山方舟 | 三种都有 | `https://ark.cn-beijing.volces.com` |
| BytePlus 方舟（海外站） | 三种都有 | `https://ark.ap-southeast.bytepluses.com` |

上游编辑页分为四部分。

**接口。**

- **服务商**决定可以选哪些协议，并让网关适配各家服务商的差异。编辑页只提供服务商支持的协议，见上表。列表里没有的服务商、LiteLLM 和 new-api 这类兼容代理，以及 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 这类把订阅账号包装成 API 的反向代理，都选*通用*（见[自定义上游](15-gateway.md#自定义上游)；用订阅额度时是否合规请自行确认）。
  - *通用*、*Anthropic* 和 *OpenAI* 转发请求的方式相同。
  - *DeepSeek*：请求带工具时，DeepSeek 要求历史里之前每条回复都带着推理内容，而很多聊天应用不会把推理内容发回来。DeepSeek 上游默认开启**补全推理内容回传**（见下文“路由”），所以请求保持思考模式也能得到 OpenViking 工具。关闭这项设置后，只有请求关闭了思考模式（`"thinking": {"type": "disabled"}`）时才提供 OpenViking 工具；标准的 Responses 请求没有 `thinking` 字段，这时也得不到工具。记忆照常补充。
  - *火山方舟*和它的海外站 *BytePlus 方舟*行为相同：请求发往方舟自己的路径。Chat Completions 和 Responses 的每段对话都使用一个固定的 `prompt_cache_key`，方舟的前缀缓存就能跟着对话走。如果请求的模型、思考模式、采样参数、系统提示词或工具与对话的第一个请求不同，就会被标记为**缓存参数有变化**（`ark_cache_parameters_changed`），因为这些参数一变，方舟就会重新建立缓存。
- **协议**决定上游能处理哪些客户端请求，也决定网关如何发送密钥：Anthropic Messages 用 `x-api-key`，另外两种用 `Authorization: Bearer`。
- **Base URL。** 选择服务商后，编辑页会填入上表中的默认地址。切换服务商或协议时，只有地址为空或仍是之前的默认地址，才会换成新的默认地址，自己填写的地址不会被覆盖；地址与默认地址不同时，点输入框下方的**使用默认地址**即可恢复。网关把客户端的请求路径拼接在后面，并去掉重复的 `/v1`，所以 `https://api.openai.com/v1` 和 `https://api.anthropic.com` 都能用。编辑页会显示请求实际发往的地址。火山方舟和 BytePlus 方舟请填写不带路径的地址，例如 `https://ark.cn-beijing.volces.com`，它适用于全部三种协议；以 `/api/v3` 结尾的地址只适用于 Chat Completions 和 Responses，以 `/api/compatible/v1` 结尾的只适用于 Anthropic Messages。API 路径不以 `/v1` 结尾的服务商（例如 `…/api/paas/v4`）不能直接使用，需要在中间加一个 LiteLLM 之类的兼容代理。

**凭证。**

- **API Key 由谁提供**：
  - **由网关保管 API Key**（默认）：网关发送上游的 API Key，客户端只需要网关密钥。
  - **每个客户端自带 API Key**：每个请求除了网关密钥，还要在 `X-OpenViking-Upstream-Key` 请求头里带上客户端自己的服务商 API Key。缺少这个请求头的请求返回 401 "Upstream API key is missing"。网关按服务商惯用的请求头把这个 API Key 传过去，`X-OpenViking-*` 请求头本身从不转发。
- **API Key** 和**额外请求头**只能写入，不能读回。编辑上游时，API Key 留空表示保留已保存的值。额外请求头只显示名称：值留空表示保留，删除这一行表示删除这个请求头。已保存的 API Key 无法清空；不想再用它时，改为“每个客户端自带 API Key”，或者删除这个上游。
- **额外请求头**会加到发往这个上游的每个请求上，例如服务商的版本或组织请求头，并覆盖客户端发送的同名请求头。不允许设置 `Host`、`Content-Length`、`Transfer-Encoding` 和 `Connection`。
- **这是 Coding Plan 或订阅密钥。** 服务商通常只允许 Coding Plan 这类订阅密钥用在自家的编程工具里，通过共享网关使用可能导致订阅被封禁。勾选后，路由到这个上游的每个请求都会被拒绝，返回 403 "Coding Plan upstreams are disabled; configure a model API key"，除非同时勾选**仍然允许**。网关无法从密钥本身分辨它是不是订阅密钥，需要你自己标记。Claude 订阅令牌（`sk-ant-oat…`）始终会被拒绝。

**模型。**

- **模型**列出这个上游提供的模型名。留空表示接受任何名称。只有列出的名称和别名会出现在客户端可以请求的模型列表（`/v1/models`）中。
- **模型别名**把客户端使用的名称映射为发给上游的模型，例如把 Claude Code 请求的 Claude 模型名映射到服务商提供的模型。别名会出现在模型列表中，请求日志显示的是客户端使用的名称。
- **上下文窗口**以 token 为单位记录各个模型的上下文窗口（至少 1,024），键是经过别名映射、实际发给上游的模型名。长对话按这个窗口的比例触发压缩。没有列在这里的模型使用上下文配置的**默认上下文窗口**，那里也没有设置时按 1,000,000 token 计算，所以窗口更小的模型都要列出来，见[长对话](#长对话)。

**路由。**

- **优先级。** 对每个请求，网关从密钥绑定的已启用上游中挑出使用这种 API、并且提供所请求模型的那些，再选优先级最高的一个。只要原来的上游还能服务，对话就一直留在它开始时的上游上，所以修改优先级只影响新对话。
- **启用。** 停用的上游不接收请求，也不出现在模型列表里。原本使用它的对话会转到下一个候选上游，这会造成一次服务商缓存未命中，并丢掉之前的 Claude 思考块；这些请求会被标记为**上游已切换**（`upstream_changed`）。想保留恢复的余地，就用停用代替删除。
- **允许 OpenViking 工具**决定能否通过这个上游提供 OpenViking 工具。关闭后立即生效，已经带工具的对话也会受影响。
- **补全推理内容回传。** 客户端回传历史时，常常丢掉模型之前回复里的推理内容。开启后，网关记下经它转发的每条回复的推理内容，在后续请求里放回原来的回复上：Chat Completions 补回 `reasoning_content`，Anthropic Messages 把 thinking 块放回原位，Responses 把带明文内容的 reasoning 项放回对应条目之前。每轮补回的内容逐字节相同，所以服务商的提示词缓存照常命中；补回的内容计入输入 token。客户端自己回传了推理内容的回复保持不变。DeepSeek、火山方舟和 BytePlus 方舟默认开启，其他服务商默认关闭。DeepSeek 在带工具的请求里缺少推理内容会报错，所以关闭后，DeepSeek 上游只在请求关闭思考模式时提供 OpenViking 工具。即使开启，有些请求也补不回推理内容，这些请求同样得不到工具：对话换了上游（`upstream_changed`）、Claude 对话丢了记忆记录（`missing_injection_record`），或者客户端把思考内容当作正文发回。
- **最小可缓存长度**（仅火山方舟和 BytePlus 方舟）是模型能缓存的最短提示词长度，只影响请求日志如何报告缓存资格。

其他上游设置都立即生效，对进行中的对话也一样。网关没有故障转移：选中的上游出错时，客户端会收到这个错误；服务商无法访问时返回 502 "Model upstream is unavailable"。

列表中的**测试**和编辑页的**测试连接**会用上游的凭证和请求头请求服务商的模型列表（`/v1/models`，火山方舟和 BytePlus 方舟为 `/api/v3/models`），最多等待 10 秒。测试通过说明主机可达，并且服务商接受这个 API Key。测试不检查模型名，也不检查 Coding Plan 设置。没有模型列表接口、或者访问这个接口需要额外请求头的服务商，可能测试失败而实际请求正常。“每个客户端自带 API Key”的上游无法测试。

有密钥在使用的上游不能删除；先吊销这些密钥，或者改为停用这个上游。

### 上下文配置

上下文配置是一组有名字的记忆设置。每个网关密钥使用一份上下文配置，一份上下文配置可以供多个密钥使用。比如可以准备一份“Coding”配置，召回记忆、资源和技能；再准备一份“Chat”配置，只召回记忆，预算也更小。

“上下文配置”标签页为每份配置显示一张卡片，概括四个部分的设置和使用它的密钥数。标签页还是空的时候，**使用推荐设置创建**一键创建一份名为“默认”的配置，**自定义**打开编辑页；之后用**新建配置**打开编辑页。有密钥在使用的配置不能删除；想做一份变体，用**复制一份**最快。

> **注意**：保存的修改只对之后开始的对话生效。对话在第一个请求时记下密钥当时的上下文配置并一直沿用，因为在对话中途改变模型看到的内容会破坏服务商缓存。每次聊天都新开对话的客户端很快就能用上新设置。Claude Code、Codex 以及发送固定会话请求头的客户端会一直沿用旧设置，直到开始新对话，或者这段对话闲置满 30 天（`session_ttl_days`）。

各部分都有自己的开关，长对话部分的两项功能各有一个：

| 部分 | 作用 | 主要设置和默认值 |
| --- | --- | --- |
| 召回记忆 | 用每条新的用户消息搜索 OpenViking，把相关内容附加到这条消息上。 | 检索范围：记忆、资源、技能 · 单条消息预算：1,600 token · 单个上下文窗口预算：30,000 token · 相关度阈值：0.35 · 超时时间：2 秒 |
| 保存对话 | 把已完成的轮次写入 OpenViking 会话并提交，供 OpenViking 提取记忆。 | 最新回复等待时长：600 秒 · 提交阈值：20,000 token · 保留最近消息：10 |
| 长对话 | 对话接近模型的上下文窗口时压缩：由同一个模型写摘要，用摘要替换之前的消息。Agent 自管上下文窗口是实验性的另一种方式。 | 压缩：开启 · 压缩时机：上下文窗口的 0.9 · 摘要长度上限：8,000 token · Agent 自管上下文窗口：关闭 |
| OpenViking 工具 | 让模型在回答时使用 OpenViking 的工具。支持 Chat Completions、完整历史的 Responses 和 Anthropic Messages，新建的配置默认开启。 | 默认勾选只读工具，会修改数据的工具不勾选 |

召回设置的实际效果：

- **会话开头提供用户画像**独立于召回。启用读取工具后，还会提供记忆和技能目录。**开头内容预算**默认限制为 4,000 token，其中技能最多占四分之一；设为 0 时全部省略。这些内容不占召回预算。
- 召回采用 OpenViking 在指定预算内选取和排版的内容，也包括只提供 URI、供模型之后读取的条目。

- 检索文本是去掉客户端噪声后的用户消息，最多截取**检索文本长度**个字符（8,000）。少于 3 个字符的消息不搜索。
- 每条消息最多补充**单条消息预算**与**单个上下文窗口预算**剩余额度中较小的那个；剩余额度不足 64 token 时跳过搜索。无论是网关还是客户端压缩了对话，单个上下文窗口预算都会重新计算。预算设为 0 就关闭了召回。这里的 token 数是偏保守的估算，不是服务商的计费数。
- OpenViking 在**超时时间**内没有返回时，这条消息不带记忆直接发给模型，之后也不会补上，客户端重试也一样。
- **按分类限量**（在“高级设置”中）可以给事件、实体、偏好、经验、资源和技能六个分类分别设置条数上限，只搜索上限大于 0 的分类。关闭时，所有来源放在一起排序。

**召回摘要。** 开启**显示召回摘要**（默认关闭）后，每条新用户消息的回复都以一段简短的摘要开头，说明网关给这条消息补充了什么：

```text
> OpenViking context: user profile, memory index, skill list, earlier sessions
> OpenViking recall: 4 items (3 memories, 1 resource) — booking_duplicate_handling, user_lang_pref, +2 more
```

`context` 行只出现在对话的第一条回复里，并且只列出开头实际提供的内容：用户画像、记忆目录、技能目录，以及这段对话较早部分在 OpenViking 中的保存位置。`recall` 行按类别统计召回的条目数，并列出最多三个条目名。搜索失败时，这一行改为说明原因，例如 `> OpenViking recall failed: OpenViking unavailable`。没有找到相关内容、召回已关闭或预算已用完时不显示 `recall` 行，因此在第一条回复之后，这样的消息没有摘要。工具步骤等后续请求从不显示摘要；客户端重试同一条消息时，摘要和第一次相同。在 Anthropic Messages 中，摘要单独占一个文本块；在 Responses 中单独占一条助手消息；在 Chat Completions 中是回复文本的开头。要求结构化输出（JSON 格式）的请求，以及 `n` 大于 1 的 Chat Completions 请求，不显示摘要。无论是否开启 OpenViking 工具，这项设置都有效。模型看不到摘要：客户端随下一条消息把回复发回来时，网关会先删掉摘要再转发请求，也不会把它保存到 OpenViking。和其他上下文配置一样，改动只影响新对话。

保存设置的实际效果：

- 下一条用户消息到达时，上一轮就会保存。**最新回复等待时长**决定网关等多久之后把最后一轮也保存下来并提交会话，这样短对话也能被提交。
- **提交阈值**：会话中待提交的内容达到这么多 token 时就提交一次。每次提交后，OpenViking 在后台提取记忆。
- **保留最近消息**：提交后，会话中保留最新的若干完整轮次，至少包含这么多条消息。
- 召回和保存相互独立：只保存不召回、或者只召回不保存的配置都有效。

[配置参考](#配置参考)列出了每项设置的 API 名称和取值范围。

### 网关密钥

网关密钥（`ovgw_…`）是客户端用来代替服务商 API Key 的凭证。每个密钥属于一个 OpenViking 用户，使用一份上下文配置和一组上游。

在“密钥”标签页选择**签发密钥**，然后填写：

- **名称**：方便日后辨认，例如“alice · Claude Code”。
- **OpenViking 用户**：本账号中的一个用户或管理员。网关以这个用户的身份搜索和保存记忆。签发时，OpenViking Server 读取这个用户的 OpenViking 密钥并交给网关，网关加密保存；密钥不经过浏览器，之后也不再显示。如果 OpenViking Server 只保存密钥的哈希（启用了 `encryption.api_key_hashing.enabled`），或者因为其他原因读不到某个用户的密钥，这个用户在列表里不可选。这时选择**粘贴 OpenViking 密钥**，填入该用户自己的密钥。用 root key 登录 Studio 时没有用户列表可选：root key 解析到的账号可能不是 Studio 当前显示的账号，所以要粘贴用户的密钥。粘贴的密钥不能是 root key。
- **上下文配置**。
- **上游**：至少一个。这个密钥只能访问选中的上游。
- **允许的模型**（可选）：留空表示允许这些上游提供的所有模型。这里填客户端发送的名称，包括别名。请求其他模型会返回 403 "Model is not allowed by this key"，模型列表也只显示允许的名称。

签发前，网关会向 OpenViking 核实这个用户的 OpenViking 密钥。如果粘贴的是 root key、其他账号的密钥或无效的密钥，或者 OpenViking 无法访问，签发就会失败，见[签发密钥失败](#签发密钥失败)。

签发成功后，**复制网关密钥**对话框显示完整密钥。这是唯一一次显示：网关只保存密钥的哈希和开头几个字符。对话框里还有 Claude Code、Codex CLI 和聊天客户端的配置，已经填好真实密钥和网关地址，可以直接粘贴。密钥丢了就重新签发一个。

- **密钥不能编辑。** 要更换密钥的上下文配置、上游、允许的模型或 OpenViking 用户，就签发一个新密钥，再吊销旧的。
- **对话属于用户，不属于密钥。** 只要客户端发送相同的会话 ID，同一 OpenViking 用户的新密钥就会接着原来的对话，所以换密钥不会打断对话。
- **吊销**会立即切断客户端的访问（其他工作进程约 2 秒内跟上），正在进行的请求会正常完成。最后使用这个密钥的对话里还没保存的轮次会被丢弃，对话和记忆本身都保留。想换密钥又不丢轮次，就先把客户端切到新密钥，再吊销旧的。
- **OpenViking 密钥变了怎么办。** 网关保存的是签发时这个用户的 OpenViking 密钥。如果该密钥被重新生成或删除，网关密钥仍能通过客户端认证，但记忆搜索会失败，保存也会因 `openviking_http_401` 暂停。为该用户重新签发网关密钥（Studio 会读取新的 OpenViking 密钥），切换客户端，再吊销旧的网关密钥。
- **每人一个密钥。** 使用同一个密钥的人共享其 OpenViking 用户的记忆。请给每人签发一个密钥；想让不同客户端使用不同的上下文配置，就按客户端再分开签发。

**删除该用户的网关数据…**（在密钥的**更多操作**菜单中）会吊销该 OpenViking 用户的所有网关密钥，并删除网关为该用户保存的对话状态。OpenViking 中的会话和记忆不受影响，需要在 OpenViking 中删除。在 OpenViking 中删除用户或账号时，网关会自动做同样的清理，见[安全与数据](#安全与数据)。

### 请求日志和概览

**概览**标签页汇总日志保留期（默认 30 天）内最新的 10,000 条请求日志：

- 第一个请求到达之前，显示**快速开始**清单。
- **请求数**：模型请求的数量、最近一次请求的时间，以及这些请求用掉的输出 token。
- **首次调用缓存命中率**：每条新用户消息的第一次模型调用中，命中服务商提示词缓存的输入 token 所占的比例。这是判断网关是否健康最重要的指标，应该接近不经过网关时服务商能达到的水平；明显下降通常说明网关发送的历史和上一次请求对不上了，见[首次调用缓存命中率下降](#首次调用缓存命中率下降)。
- **轮内缓存命中率**：同一轮里后续调用的缓存命中率，例如工具步骤和 OpenViking 工具的往返。这个值通常都很高。
- **召回的记忆条数**：补充到新消息中的记忆条数，以及每次检索 OpenViking 的平均耗时（只统计实际检索了的消息）。
- **OpenViking**：显示已连接、异常或启动中，附带版本和认证模式，异常时还会给出原因。OpenViking 无法访问期间，请求照常发给模型，只是不带记忆。卡片中的**保存对话**一栏还会统计正在重试或已暂停保存的对话数。
- **降级请求**：每种异常出现的次数和说明，异常类型见[故障排查中的表格](#降级请求的异常)。
- **最近请求**，以及前往“请求日志”标签页的链接。

**请求日志**标签页按时间倒序列出最新的 1,000 条记录，停留在第一页时每 30 秒刷新一次。可以按**全部**、**新消息**、**工具步骤**或**异常**筛选，也可以按**请求类型**筛选，或者按模型、对话或密钥搜索。各列显示类型、模型（客户端发送的名称）、服务商返回的 HTTP 状态、token（输入、缓存占比、输出）、**记忆**（`+3` 表示这次搜索补充的条数，附带搜索耗时；`↺ 4` 表示之前 4 条消息补充的记忆被原样放回）、**保存**状态和**异常**。展开一行可以看到对话 ID、上游、密钥、耗时、token 明细、记忆搜索结果、上下文窗口（用了多少，以及是否压缩或开启了新窗口）、保存状态和下次重试时间、OpenViking 工具详情；有异常的请求还会说明发生了什么、该怎么处理。

只有到达模型服务商的请求才会被记录。网关先行拒绝的请求不会记录，例如密钥无效、模型不在密钥的允许范围内、没有匹配的上游、请求体超过上限，这些情况下客户端会直接收到错误。

| 类型 | 含义 | 记忆 |
| --- | --- | --- |
| 新消息 | 最后一条是新用户消息的请求 | 搜索一次，并放回之前的记忆；这一轮会被保存 |
| 工具步骤 | 同一轮的延续，例如带着工具结果继续 | 放回之前的记忆；随所在轮次一起保存 |
| 辅助请求 | 生成标题、摘要、压缩、建议和权限检查的请求 | 放回之前的记忆；从不保存 |
| 子 Agent | 客户端的子 Agent 发出的请求 | 放回之前的记忆；从不保存 |
| Token 计数 | token 计数请求（`/v1/messages/count_tokens`） | 放回之前的记忆，计数才和实际发送的内容一致 |
| 直接转发 | 不做改动、不带记忆直接转发的请求：其他接口、依赖服务商端状态的 Responses 请求、压缩过或无法解析的请求体 | 无 |
| 记忆同步 | 后台保存事件，不是模型请求 | — |

**保存**列显示每段对话的保存状态：

- **正常**：保存正常进行。
- **已关闭**：这段对话的上下文配置不保存对话。
- **重试中**：OpenViking 拒绝了保存，或者无法访问。网关会在 10、20、40、80 秒后依次重试。
- **已暂停**：连续 5 次失败。网关之后每 5 分钟重试一次，直到成功。未保存的轮次会一直保留，直到保存成功或者对话过期。

展开的行会显示原因，例如 OpenViking 拒绝了密钥（`openviking_http_401`，即用户的 OpenViking 密钥已失效），或者 OpenViking 无法访问（`openviking_unavailable`）。提示对话历史有改动（`history_changed`）是正常的：客户端编辑、重新生成或压缩了对话，网关用当前历史在新的 OpenViking 会话中继续保存。

展开行中的**重新同步对话…**会为这段对话新建一个 OpenViking 会话。用户下次发消息时，网关把对话的完整历史写进这个会话。已经保存的内容留在旧会话里，所以 OpenViking 可能会把部分记忆提取两次。修复了原因但对话仍然暂停，或者想要一份干净的对话副本时，可以用它。下一条消息到达之前，什么都不会发生。重新同步需要记录上显示的那个密钥；如果它已被吊销，请选同一段对话里更新的一条记录。

## OpenViking 工具

开启 OpenViking 工具后，模型可以在回答过程中使用 OpenViking 服务提供的工具，访问范围受该用户的权限限制。工具调用由网关执行，客户端会持续收到回答，直到本次回复结束。显示的 token 用量包含期间所有模型调用。这项功能对各类客户端意味着什么、模型能用哪些工具、用户在回复里看到什么，见 OpenViking 网关指南中的[让任意客户端拥有 Agentic 记忆](15-gateway.md#让任意客户端拥有-agentic-记忆)；本节说明客户端要求、设置和上限。

三种协议都支持流式和非流式请求：

| 协议 | 客户端要求 |
| --- | --- |
| Chat Completions | 回传完整 `messages` 历史。 |
| Responses | 使用 `store: false` 和完整 `input` 历史；下一轮回传本轮返回的全部 `output` 内容，包括 reasoning 和客户端工具调用。带 `previous_response_id`、`conversation` 或 `background` 的请求继续普通转发，不提供网关工具；`item_reference` 无法还原完整历史。 |
| Anthropic Messages | 回传完整 `messages` 历史，保留 thinking/signature 块。计数请求使用相同工具定义，但不执行工具。 |

模型在同一条回复中同时调用 OpenViking 和客户端工具时，网关会处理 OpenViking 调用，客户端照常执行自己的工具并回传结果。Responses 支持客户端的 function 和 custom 工具，Anthropic Messages 支持客户端的 tool_use 调用。

这项功能面向无法连接 OpenViking MCP 服务器的聊天应用和 API 应用。支持 MCP 或有插件的客户端，例如 Claude Code 和 Codex，用 MCP 或插件更合适，因为客户端会在那里显示每次工具调用及其结果，并在调用前请求确认。

新建的上下文配置默认打开 **OpenViking 工具**，“使用推荐设置创建”也一样。**工具**清单来自你的 OpenViking 服务，默认只勾选只读工具：`find`、`search`、`grep`、`glob`、`list`、`tree`、`read`、`list_watches`、`get_acl`、`list_users`、`list_groups` 和 `health`。会修改数据的工具默认不勾选：`remember`、`write`、`edit`、`add_resource`、`add_skill`、`forget`、`set_acl` 和 `cancel_watch`。比如想让模型保存记忆，就勾选 `remember`；想让它导入网页或附件，就勾选 `add_resource`。已有的上下文配置保留原来的设置。还没有网关密钥时，先签发一个密钥再加载清单。OpenViking 后续新增的工具也会默认可用，已有对话仍沿用开始时的工具清单。

每个上游的**允许 OpenViking 工具**默认开启。两个开关都开启后，新对话会获得选中的工具，名称加上 `openviking_` 前缀，例如 `openviking_find`、`openviking_read`、`openviking_grep` 和 `openviking_glob`。Studio 中的说明介绍各个工具的用途；只有 OpenViking 提供了相应信息时，页面才显示只读标记。提供给模型的工具定义会随对话中的每个请求发送，OpenViking 的全部工具约占 3,500 个输入 token，所以取消勾选用不到的工具也能节省 token。

> **注意**：所有选中的工具都直接执行，不经过客户端的权限确认，包括写入和删除数据的工具。勾选会修改数据的工具之前，先确认可以接受这一点；给聊天应用用的上下文配置，建议不要勾选 `forget` 和 `set_acl`。调用提示用于说明已经发生的操作，不是权限确认。

**工具调用提示。** 开启**显示工具调用**（默认开启）时，网关每执行一次 OpenViking 工具调用，就在回复中调用发生的位置加一行提示：

```text
> OpenViking find: "release date" — done
```

提示写明工具名；能够识别调用对象时，还会显示：检索的查询词，读取、列出或写入的 URI，或者正在导入的文件或技能。流式回复中，调用一开始提示就会出现，所以导入这类耗时的调用进行时，用户也能看到网关在工作；非流式回复会在最终消息里带上同样的提示。调用结束后再补上结果：`done` 表示完成，`failed` 表示调用返回了错误，`skipped` 表示网关拒绝了这次调用，没有执行，例如工具轮数已达上限或 token 预算已经用完。每条提示单独成段。在 Chat Completions 中，提示是回复文本的一部分；在 Anthropic Messages 中，同一轮调用的提示放在同一个文本块里；在 Responses 中，同一轮调用的提示放在同一条助手消息里。模型看不到这些提示，它收到的是真实的调用和结果；网关也不会把提示保存到 OpenViking。要隐藏提示，在上下文配置的 **OpenViking 工具**部分关闭**显示工具调用**，回复里就只有模型自己的输出。和其他工具设置一样，改动只影响新对话。

对话是否带工具，在它的第一个请求时就决定了，之后保持不变，所以修改上下文配置只影响新对话。请求没有带工具时，请求日志的详情会说明原因：

| 原因 | 说明 |
| --- | --- |
| `tools_require_full_history` | Responses 工具需要 `store: false` 和完整 input 历史，不能使用 `item_reference`。 |
| `upstream_tools_disabled` | 上游关闭了**允许 OpenViking 工具**。 |
| `tools_multiple_choices` | 请求要求返回多个候选（`n` 大于 1）。 |
| `tools_structured_output` | 请求要求结构化输出（`response_format`、`text.format` 或 `output_config.format`）。 |
| `tools_non_function` | Chat Completions 客户端传入了 function 以外类型的工具。 |
| `tools_forced_choice` | `tool_choice` 为 `required`，或者指定了某个工具。 |
| `deepseek_reasoning_history_required` | 上游的服务商是 DeepSeek，请求没有关闭思考模式（`"thinking": {"type": "disabled"}`），而之前的回复发到上游时会缺少推理内容：关闭了**补全推理内容回传**，或者这个请求补不回推理内容，因为对话换了上游、丢了记忆记录，或者思考内容被当作正文发回。标准的 Responses 请求没有 `thinking` 字段，按开启思考模式处理。 |
| `tool_name_collision` | 客户端定义了与某个 `openviking_*` 工具同名的工具。 |
| `tools_unavailable` | 无法从 OpenViking 加载工具清单，也没有之前加载成功的清单。连接恢复后请开始新对话。 |
| `tools_not_selected_at_session_start` | 上下文配置开启了工具，但对话的第一个请求因为上面某个原因用不了工具，所以整段对话都没有工具。 |

辅助请求、子 Agent 请求和检测到 OpenViking 插件的对话都不带 OpenViking 工具。这类请求如果回传了模型用过这些工具的历史，网关仍会附上工具定义，让这段历史保持有效，但会拒绝新的 OpenViking 调用。客户端自己的工具在每个请求里都照常可用。

**导入文件和技能。** OpenViking 服务提供导入工具、且配置中勾选了它们时即可使用。通过 URL 导入不需要 shell 工具，本地文件则需要以下上传方式之一：

- **有 shell 工具**（名称类似 bash、shell、exec_command、terminal 或 run_command 的客户端工具）：导入工具返回一个一次性上传链接，模型再用客户端自己的 shell 工具上传文件，这一步会经过客户端的权限确认。技能目录会先打包成 zip。链接指向 `public_url` 加上 `/gateway/uploads`，所以 `public_url` 必须是客户端能访问的地址，代理也要把这个路径转发给网关。没有设置 `public_url` 时，导入会失败并提示 "Set gateway.public_url for client uploads"。
- **有附件**：没有 shell 工具时，模型可以导入用户消息里附带的文件，文件可以是 base64 数据，也可以是附件文本，支持 Responses `input_file` 和 Anthropic `document.source` 内嵌的 `base64`、`text` 数据。Open WebUI 通常只发送提取出的文本，这些文本会作为文本文件导入。附件中的 URL 和服务商的文件 ID 不会被抓取；导入支持的远程资源时，请使用导入工具的 URL 参数。

上传大小受 `max_body_bytes` 限制（默认 32 MiB）。

上下文配置中 **OpenViking 工具**部分的**高级设置**里有以下上限：

| 设置 | API 名称 | 默认值 | 范围 | 达到上限时 |
| --- | --- | --- | --- | --- |
| 每次请求的工具轮数 | `tool_max_rounds` | 不限 | 1 及以上 | 拒绝后续的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。 |
| 单次调用超时 | `tool_timeout_seconds` | 30 秒 | 最多 120 秒 | 这次调用向模型返回错误。 |
| 结果大小上限 | `tool_result_bytes` | 65,536 字节 | 1,024–1,048,576 | 结果被截断。 |
| 总时长 | `tool_total_seconds` | 不限 | 大于 0 秒 | 请求失败，返回 504 "Hidden tool request timed out"。 |
| Token 预算 | `tool_total_tokens` | 不限 | 1,024 及以上 | 拒绝后续的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。这不是计费上限，最终回答可能超出它。 |

轮数、总时长和 Token 预算默认都不限，和 Agent 自己的工具循环一样：模型会一直使用 OpenViking 工具直到完成。流式请求中，用户可以随时中断回复，客户端断开后网关也会停止。非流式请求在客户端断开后仍会继续执行，所以如果客户端发送非流式请求，请设置**总时长**。字段留空即表示不限。这项默认值修改之前保存的上下文配置仍保留原来的值（5 轮、120 秒、100,000 Token），清空这几项即可取消限制。

Token 预算用于模型使用 OpenViking 工具时新增的调用、结果和后续生成内容，客户端已有的对话、工具定义和图片不计入。网关保留客户端设置的回答长度上限（`max_tokens`、`max_completion_tokens` 或 `max_output_tokens`）。预算用完后，网关会拒绝之后的 OpenViking 调用，模型利用已有结果和客户端自己的工具继续回答。

单次工具调用失败时，模型会收到错误结果，并可据此继续回答。如果整个回答失败，例如服务商拒绝了后续模型请求，或者 OpenViking 调用被拒绝后模型仍继续调用，客户端会收到错误，请求日志中会显示 **OpenViking 工具调用失败**（`hidden_tool_loop_failed`）。流式 Responses 请求以 `response.failed` 事件结束；Chat Completions 和 Anthropic Messages 返回 `gateway_tool_error` 错误。

如果某个上游反复失败，可以关闭它的**允许 OpenViking 工具**。已使用过这些工具的 Claude 对话可能同时出现 **工具历史无法继续使用**（`hidden_tool_history_unavailable`）：之前的思考内容无法继续使用，网关已将其去掉。可根据请求详情中的工具停用原因恢复原设置，或开始一段新对话。重试的工具调用会复用第一次调用的结果，中断的写入操作不会自动重做。

## 长对话

模型的上下文窗口限制了一段对话能有多长。开启**压缩**（默认开启）后，网关会在对话撑满窗口之前，把较早的部分换成一份摘要。

**何时压缩。** 每收到一条新的用户消息或一个工具步骤，网关都会估算上下文窗口用了多少：取服务商为这段对话上一次回复报告的 token 用量，再加上之后新增消息的估算值。没有这份用量时（例如流式 Chat Completions 没有设置 `stream_options.include_usage`），就估算整个请求。估算值达到**压缩时机**（窗口的 0.9）时，网关开始压缩。辅助请求、子 Agent 请求和 token 计数请求不会触发压缩。

上下文窗口取自上游**上下文窗口**中该模型的条目（按发给上游的模型名查找），没有时取上下文配置的**默认上下文窗口**。两者都没有，网关按 1,000,000 token 计算。窗口更小的模型请设置其中一项，否则还没等到压缩，服务商就会以对话过长为由拒绝请求。

**摘要怎么生成。** 网关向同一个上游、同一个模型额外发一次非流式请求，客户端的系统提示词、工具和思考设置都保持不变，所以大部分内容能命中服务商的提示词缓存。纯文本摘要无法遵守的设置会去掉：强制的工具选择改为不调用工具，结构化输出格式和停止序列也不保留。这个请求包含压缩位置之前的对话，以及一条要求模型为自己写摘要的指令。摘要要覆盖：用户的目标和最新请求的原文、关键决定和当前进度、文件、路径和标识符、出过的错误和修正、未完成的事项，以及最近几次工具结果里的要点。之前的摘要会并进新摘要。摘要是纯文本，最多**摘要长度上限**个 token（8,000）。思考内容也计入输出上限，而有些模型（如 DeepSeek V4.1）即使请求里没有思考设置也会默认思考，所以网关总是在摘要长度上限之外再留 16,000 token 的输出余量；客户端手动指定了思考预算时，改为加上这份预算。这次请求和其他请求一样由服务商计费。

**之后模型收到什么。** 压缩位置取决于请求类型：

- 新的用户消息：压缩位置就在这条消息之前。模型收到 system 和 developer 消息、作为一条用户消息的摘要，以及这条新消息。
- 工具步骤：压缩位置在最近一次工具结果之后，模型从摘要出发继续完成任务。

摘要消息开头说明网关替换了对话较早的部分、这段内容不是用户写的，结尾附上这段对话开头的网关说明和用户画像，所以压缩后它们仍然保留。之后凡是重新发送这段历史的请求，包括辅助请求和 token 计数请求，都会做同样的替换；子 Agent 通常发送自己单独的历史，不受影响。每次压缩会让服务商缓存在压缩位置失效一次，之后缓存照常命中。

**压缩位置之前的内容不保留原文**，最近几轮也不例外。模型之前的回复和其中的思考内容都依赖被替换掉的历史，只保留其中一部分会破坏 Claude 的思考签名。压缩位置之后生成的内容照常发送，思考内容也一样。

**查找被压缩的内容。** 对话本身仍然保存在 OpenViking 里。开启了**保存对话**、并且这段对话提供了 `openviking_grep` 和 `openviking_read` 工具时，摘要后面会附上查找方法：这段对话对应的 OpenViking 会话（`viking://user/<user_id>/sessions/<session_id>/`）、会话里消息的存放位置，以及检索步骤：先用具体的关键词 grep 会话，拿到行号，再读取匹配位置前后的内容。如果压缩发生在一轮对话中间，压缩位置之前的部分会立即保存到 OpenViking，模型在这一轮里就能检索到。

**压缩失败时。** 如果摘要请求失败，或者回复里调用了工具、内容为空、因长度上限被截断，网关就改为发送完整历史，并且 60 秒内不会在这段对话里再次尝试。请求日志中这条请求的详情会显示**压缩失败**及原因。如果摘要写完后上下文仍达到**压缩时机**，比如系统提示词和工具本身就占了窗口的大部分，网关会保留这份摘要，但同样等 60 秒再尝试压缩，原因显示为 `still_over_threshold`。

**不再使用 Working Memory。** 摘要由对话所用的模型生成，不再使用 OpenViking 的会话摘要。网关新建的 OpenViking 会话都关闭了 Working Memory：提交时仍会归档消息，OpenViking 也照常提取记忆，只是不再生成会话摘要。

**记忆预算。** 每压缩一次，**单个上下文窗口预算**就重新计算，压缩位置之前补充过的记忆也可能再次补充。

**客户端自己的压缩。** 网关压缩之后，客户端仍会发送完整历史，网关在每个请求里把压缩位置之前的部分替换掉。客户端看到的用量很小，按 token 用量决定是否压缩的客户端很少再自己压缩，所以特别长的会话最终可能超过 `max_body_bytes`。客户端压缩或编辑历史后，网关把新历史保存到新的 OpenViking 会话；检索工具可用时，新历史开头的网关说明会告诉模型之前的会话在哪里。

请求日志中每条请求的详情会显示上下文窗口用了多少、较早的历史是否被替换，以及新生成摘要的大小和耗时。

### 实验性：Agent 自管上下文窗口

开启 **Agent 自管上下文窗口**（默认关闭）后，由模型自己决定何时开启新的上下文窗口，并自己写交接笔记，不必等网关生成摘要。这项设置只在能使用 [OpenViking 工具](#openviking-工具)的对话中生效，其他对话不受影响，压缩照上文所述进行。开启压缩时，如果模型一直不开新窗口，压缩仍会兜底。

模型会看到：

- 在 OpenViking 工具之外多出两个工具。它们不在上下文配置的**工具**清单里，只由这项设置控制。
  - `openviking_new_context(reason, notes, next_steps)`：开启新窗口，`next_steps` 可省略。它必须在一轮里单独调用；和其他工具在同一轮调用时，这一轮的每个调用都返回错误，窗口不会重置。如果网关在本次请求里已经在同一位置替换过上下文，比如刚压缩过，它同样返回错误。
  - `openviking_context_remaining()`：返回窗口序号、已用和剩余 token 的估算值、本窗口内的用户轮数、距用户上一条消息的时间，以及一句建议。
- 开头的网关说明里多一行，告诉模型它用这两个工具自己管理上下文窗口。
- 每条新的用户消息之后，记忆块末尾有一行状态：`[context-status] window wN · ~X/Y tokens (P%) · T since your previous message`。
- 窗口用到**软提醒时机**（0.7）和**硬提醒时机**（0.85）时，网关分别发出软提醒和硬提醒。软提醒每个窗口只发一次，要求模型做完当前这一步就开启新窗口，并在交接笔记里记下之后要用到的确切路径、行号和取值。硬提醒要求模型立即开启新窗口，此后每一步都会重复，直到模型开启新窗口或压缩接手。提醒附在新的用户消息上，工具步骤里则附在工具结果上。

模型调用 `openviking_new_context` 后，网关把目前为止的对话换成一个窗口头，模型在同一条回复里接着工作。窗口头先说明这是模型开启的第 N 个窗口、内容不是用户写的，然后依次是 reason、notes 和 next_steps、用户最新一条消息的原文、检索之前窗口的方法（条件和压缩时相同），以及这段对话开头的网关说明。之后的请求会根据客户端的历史重建同一个窗口。重置之前的内容会立即保存到 OpenViking，之后仍可检索。reason 和 notes 只出现在窗口头里，不会保存到 OpenViking。每次重置会让服务商缓存失效一次，但不需要摘要请求。

请求日志中每条请求的详情会显示窗口序号、模型是否开启了新窗口，以及收到了哪种提醒。效果取决于所用的模型，后续版本中的行为也可能调整。

## 安全与数据

**按用户隔离。** 每个网关密钥绑定一位 OpenViking 用户。网关用这位用户自己的 OpenViking 密钥搜索记忆、保存对话和调用工具，所以权限不会超出用户本人；各用户的对话状态也互相隔离。会话和记忆的正本始终在 OpenViking，网关只保存让对话继续所需的状态。

**网关存储什么。** 网关在 `storage_path` 下保存两个 SQLite 数据库：

- `management.sqlite3`：上游、上下文配置、网关密钥、请求日志，以及 Responses ID 到上游的映射。上游 API Key、请求头的值，以及绑定到网关密钥的 OpenViking 密钥都加密存储。网关密钥本身只保存哈希和一段短前缀。
- `kernel.sqlite3`：每段对话的状态，包括补充到消息中的记忆文本（原样放回需要它）、OpenViking 工具的往返记录、摘要，以及等待保存的对话文本。

存储的值都用加密密钥加密。文件只有运行网关的用户能读取。

**请求日志包含什么。** 只有元数据：请求类型、模型、状态、上游、对话 ID 的哈希、token 数、记忆条数和耗时、保存状态和异常。从不包含消息文本、召回的记忆、工具参数和结果，也不包含任何密钥。网关不写 HTTP 访问日志，错误信息也从不回显提交的值。

**服务商能看到什么。** 每个上游都会收到完整的模型输入，包括网关补充的记忆和 OpenViking 工具的结果。只添加你放心交出这些数据的服务商。

**保留期限。** 清理任务每小时运行一次：

| 数据 | 保留时长 | 设置 |
| --- | --- | --- |
| 对话状态 | 最后一次使用后 30 天 | `session_ttl_days` |
| 请求日志 | 30 天 | `log_retention_days` |
| Responses ID 映射 | 30 天 | `response_ttl_seconds` |
| OpenViking 中的会话和记忆 | 遵循 OpenViking 自己的规则 | — |

**删除数据。**

- 在 Studio 中删除某个用户的网关数据（密钥的**更多操作**菜单 → **删除该用户的网关数据…**），或者用账号管理员密钥调用 OpenViking Server 的 `DELETE /api/v1/admin/gateway/users/{user_id}/data`。这会吊销该用户的网关密钥，并删除其对话状态。请求日志中的元数据按保留期限自然过期。
- 在 OpenViking 中删除用户时，网关会自动执行同样的操作。删除账号时，还会从网关中删除这个账号的上游、上下文配置、密钥和请求日志。如果当时网关无法访问，OpenViking 会持续重试。不再使用网关时，把 `gateway.enabled` 设为 `false`，删除操作就不会再等待网关。
- OpenViking 中的会话和记忆要通过 OpenViking 删除，不经过网关。
- 在 OpenViking 中删除一条记忆，不会抹掉已经补充进对话的副本：网关为了原样放回，在对话状态里保留了补充过的记忆文本，直到对话过期。要立即清除，就删除该用户的网关数据。这会同时吊销他的全部网关密钥，之后要重新签发。

**访问控制。**

- 客户端用网关密钥认证，放在 `Authorization: Bearer` 或 `x-api-key` 中。密钥可以随时吊销。
- 网关用每个用户自己的 OpenViking 密钥做记忆搜索、保存和工具调用，所以它能做的事不会超出这个用户的权限。
- 管理令牌只应由 OpenViking Server 持有，网关的 `/admin/*` 路径必须保持私有（见[路由](#路由)）。Studio 用户由 OpenViking Server 校验：只有账号管理员和 root 能访问，并且只能管理自己的账号。管理令牌则不受账号限制，持有它就能管理所有账号，所以网关端口不能对公网开放。
- 账号管理员能为本账号的任何用户签发网关密钥（OpenViking 只保存密钥哈希时，需要用户本人提供密钥）。拿到网关密钥就能以该用户的身份召回记忆、调用工具，所以签发权实际上等于读取本账号所有用户记忆的权限，只交给可信的管理员。
- `/gateway/uploads` 不需要网关密钥。每次上传由 OpenViking 签发的一次性令牌授权，网关只把它转发到 OpenViking 的上传接口。不要让它的查询字符串出现在代理日志里。
- `X-OpenViking-*` 请求头、客户端凭证和 Cookie 从不转发给上游。

**备份。** 备份 `storage_path`，包括两个数据库及其 `-wal` 和 `-shm` 文件，并和加密密钥放在一起。要得到一致的副本，先停止网关，或者使用 SQLite 的在线备份（`sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"`，`management.sqlite3` 同理）。各种丢失的后果如下：

- **丢失 `kernel.sqlite3`**：之前补充的记忆无法放回。每段进行中的对话会有一次服务商缓存未命中，Claude 对话会丢失一次之前的思考内容（**记忆记录缺失**，`missing_injection_record`），尚未保存的轮次也会丢失。
- **丢失 `management.sqlite3`**：上游、上下文配置、密钥和请求日志全部丢失。需要重新配置，并签发新密钥。
- **丢失加密密钥**：两个数据库都无法读取，只能从空的 `storage_path` 重新开始。

## 日常运维

OpenViking 网关页面只对账号管理员和 root 开放。初次设置按“上游 → 上下文配置 → 签发密钥 → 接入”的顺序进行，之后的日常工作集中在下面几件事上。

- **盯住首次调用缓存命中率。** “概览”标签页上的**首次调用缓存命中率**反映跨轮次的提示缓存，应该接近不经过网关时服务商能达到的水平；**轮内缓存命中率**覆盖工具步骤，正常时很高。明显下降时，见[首次调用缓存命中率下降](#首次调用缓存命中率下降)。
- **用请求日志定位单个请求。** 按新消息、工具步骤或异常筛选，展开一行就能看到召回结果、保存状态和 OpenViking 工具的停用原因，见[请求日志和概览](#请求日志和概览)。
- **设置对外地址。** 共享部署必须设置 `public_url`。不设置时，Studio 的接入说明显示内部地址，OpenViking 工具的上传链接也无法使用。
- **定期备份。** 备份网关存储目录和加密密钥，方法和丢失的后果见[安全与数据](#安全与数据)中的“备份”。

### 升级

- OpenViking Server 的版本不能低于 `min_server_version`（0.4.16），否则记忆搜索和保存都会停止，也无法签发密钥。网关和 OpenViking 分开升级时，先升级 OpenViking，再升级网关。
- 升级前阅读发布说明。新版本的网关如果不能读取旧的存储格式，会在启动时报 `Unsupported gateway schema`，见[网关无法启动](#网关无法启动)。

### 观测

网关不导出 Prometheus 之类的监控指标，也不写 HTTP 访问日志。运行状态通过 Studio 的“概览”和“请求日志”标签页，或者 OpenViking Server 上的管理 API（`/api/v1/admin/gateway/overview` 和 `logs`）查看；进程是否存活可以用网关的 `/health` 检查。

## 设计取舍

下表说明网关几项关键设计的原因和代价，帮助你判断它是否适合自己的场景：

| 设计 | 选择及原因 | 代价 |
| --- | --- | --- |
| 接入位置 | 接在模型调用路径上，而不是给每个 Agent 安装插件：装不了插件的客户端也能用，服务商密钥集中管理。 | 看不到工作目录，按项目隔离记忆要给每个项目使用不同的 OpenViking 用户。 |
| 工具由谁执行 | 网关在回复过程中代为执行 OpenViking 工具，并把工具往返存下来，下一轮原样放回：没有工具、不支持 MCP 的客户端也能让模型主动使用记忆。 | 没有逐次确认；客户端必须每轮回传完整历史；需要管理员收紧工具清单和上限。 |
| 记忆放在哪 | 附加在最新的用户消息末尾，而不是系统提示词里：系统提示词改动一个字，整份缓存都会失效；附加在末尾并在之后原样放回，前缀就始终不变。 | 网关必须可靠地记下每处补充的原文，记录丢失时缓存会失效一次。 |
| 保存时机 | 晚一轮保存：下一条消息带回上一轮，才能确认用户保留了它。 | 最后一轮要等**最新回复等待时长**（默认 10 分钟）之后才保存。 |
| 长对话 | 由网关让同一个模型写摘要：只有网关知道模型实际收到了什么，摘要请求也大部分命中缓存。 | 触发压缩的那一轮多一次模型调用和一次计费；压缩位置之前的原文不再发给模型。 |
| 本地状态 | 单台主机上的 SQLite，不引入 Postgres、Redis 这类依赖，适合在单台主机上自部署。 | 网关不能多机互备，是模型调用路径上的单点。 |
| 客户端身份 | 网关签发自己的密钥，而不是直接使用 OpenViking 密钥：可以单独吊销，同一个人的不同客户端可以绑定不同的上下文配置。 | 网关要加密保管用户的 OpenViking 密钥；网关密钥泄露，等于该用户的记忆泄露。 |

## 配置参考

`ov.conf` 中的 `gateway` 部分，所有键都取默认值：

```jsonc
{
  "gateway": {
    "enabled": false,                                  // 设为 true 之前，网关拒绝启动
    "host": "127.0.0.1",                               // 监听地址
    "port": 1935,
    "workers": 1,                                      // 本机运行 1–64 个进程
    "url": "http://127.0.0.1:1935",                    // OpenViking Server 访问网关的地址
    "openviking_url": "http://127.0.0.1:1933",         // 网关访问 OpenViking Server 的地址
    "public_url": "",                                  // 客户端访问网关的地址
    "storage_path": "~/.openviking/gateway",   // 只能是本地磁盘
    "encryption_key_env": "OPENVIKING_GATEWAY_ENCRYPTION_KEY",
    "admin_token_env": "OPENVIKING_GATEWAY_ADMIN_TOKEN",
    "min_server_version": "0.4.16",
    "session_ttl_days": 30,
    "response_ttl_seconds": 2592000,
    "log_retention_days": 30,
    "max_body_bytes": 33554432,
    "upstream_timeout_seconds": 600,
    "health_interval_seconds": 60
  }
}
```

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 开启网关。为 false 时网关拒绝启动，Studio 的 OpenViking 网关页面显示设置卡片。 |
| `host` | `127.0.0.1` | 监听地址。OpenViking 运行在 dev 模式时，必须是回环地址。 |
| `port` | `1935` | 网关端口。 |
| `workers` | `1` | 本机上的网关进程数，1–64。 |
| `url` | `http://127.0.0.1:1935` | OpenViking Server 用来管理网关和删除数据的地址。`public_url` 为空时，Studio 也显示这个地址。 |
| `openviking_url` | `http://127.0.0.1:1933` | 网关访问 OpenViking Server 的地址。 |
| `public_url` | 空 | 客户端使用的地址。Studio 的接入说明显示它，OpenViking 工具的上传链接也用它。所有共享部署都应设置。 |
| `storage_path` | `~/.openviking/gateway` | 两个数据库所在的目录，必须位于本地磁盘。 |
| `encryption_key_env` | `OPENVIKING_GATEWAY_ENCRYPTION_KEY` | 保存加密密钥的环境变量。 |
| `admin_token_env` | `OPENVIKING_GATEWAY_ADMIN_TOKEN` | 保存管理令牌的环境变量。 |
| `min_server_version` | `0.4.16` | 网关支持的最低 OpenViking Server 版本。版本更低或无法解析时，记忆搜索和保存都会停止，也无法签发密钥。 |
| `session_ttl_days` | `30` | 对话闲置多少天后删除它的状态。 |
| `response_ttl_seconds` | `2592000` | Responses ID 到上游的映射保留多久（至少 60）。 |
| `log_retention_days` | `30` | 请求日志的保留天数。 |
| `max_body_bytes` | `33554432` | 请求体和上传的最大字节数（至少 1,024）。超出时返回 413。 |
| `upstream_timeout_seconds` | `600` | 单次调用服务商的总时限。 |
| `health_interval_seconds` | `60` | 网关检查 OpenViking 的间隔（至少 1）。 |

URL 类设置必须是普通的 `http` 或 `https` 地址，不能包含账号密码、查询参数或片段；末尾的 `/` 会被去掉。

**环境变量：**

| 变量 | 读取方 | 用途 |
| --- | --- | --- |
| `OPENVIKING_GATEWAY_ENCRYPTION_KEY` | 网关 | 加密密钥（见[加密密钥和管理令牌](#加密密钥和管理令牌)）。 |
| `OPENVIKING_GATEWAY_ADMIN_TOKEN` | 网关和 OpenViking Server | 管理令牌，至少 32 个字符。 |
| `OPENVIKING_CONFIG_FILE` | 网关和 OpenViking Server | 没有指定 `--config` 时使用的 `ov.conf` 路径。 |

**上下文配置的设置。** 先列 Studio 中的名称，再列管理 API 中的名称（管理 API 把上下文配置称为 `policies`）：

| Studio 名称 | API 名称 | 默认值 | 取值范围 | 含义 |
| --- | --- | --- | --- | --- |
| 名称 | `name` | `Default` | | 显示名称。 |
| 召回记忆 | `recall` | `true` | | 为每条新的用户消息搜索记忆。 |
| 会话开头提供用户画像 | `profile` | `true` | | 新对话开始时提供画像，独立于召回。 |
| 开头内容预算 | `profile_max_tokens` | `4000` | 0–32,000 | 画像和目录的独立预算；0 表示省略。目录需要启用读取工具。 |
| 显示召回摘要 | `show_recall` | `false` | | 在回复开头用一行说明 OpenViking 补充了什么，或召回失败的原因。模型看不到这一行。 |
| 检索范围 | `context_types` | `memory`、`resource`、`skill` | 至少一个 | 搜索范围：记忆、资源、技能。 |
| 单条消息预算 | `max_tokens` | `1600` | 64–32,000 | 一条消息最多补充的 token 数。 |
| 单个上下文窗口预算 | `session_max_tokens` | `30000` | ≥ 0 | 一个上下文窗口内最多补充的 token 数，每次压缩后重新计算；0 表示关闭召回。 |
| 相关度阈值 | `score_threshold` | `0.35` | 0–1 | 接受的最低相关度分数。 |
| 超时时间 | `recall_timeout` | `2`（秒） | 最多 30 | 搜索最多等待多久，超时后消息不带记忆继续发送。 |
| 检索文本长度 | `query_max_chars` | `8000` | 3–32,000 | 用作检索文本的消息字符数。 |
| 按分类限量 | `quotas` | `{}`（关闭） | 键：`events`、`entities`、`preferences`、`experiences`、`resources`、`skills` | 每个分类最多取的条数；只搜索大于 0 的分类。 |
| 保存对话 | `capture` | `true` | | 把已完成的轮次保存到 OpenViking。 |
| 最新回复等待时长 | `idle_seconds` | `600`（秒） | ≥ 1 | 停顿多久之后保存最后一轮并提交会话。 |
| 提交阈值 | `commit_tokens` | `20000` | ≥ 1 | 会话中待提交的 token 数达到这个值时提交。 |
| 保留最近消息 | `keep_recent_messages` | `10` | 0–1,000 | 提交后会话中保留的消息数。 |
| 压缩 | `compaction` | `true` | | 接近上下文窗口时，用摘要替换对话较早的部分。 |
| 压缩时机 | `compaction_threshold` | `0.9` | 0.5–0.98 | 上下文窗口用到多大比例时压缩。 |
| 摘要长度上限 | `summary_max_tokens` | `8000` | 1,000–32,000 | 一份摘要最多多少 token。 |
| 默认上下文窗口 | `context_window` | 未设置（按 1,000,000 计算） | ≥ 1,024 | 上游没有列出该模型时使用的窗口。 |
| Agent 自管上下文窗口 | `agent_windows` | `false` | | 实验性。让模型自己开启新的上下文窗口；需要 OpenViking 工具。 |
| 软提醒时机 | `window_soft_ratio` | `0.7` | 0.3–0.95，须低于 `window_hard_ratio` | 上下文窗口用到这个比例时，提醒模型尽快开启新窗口。 |
| 硬提醒时机 | `window_hard_ratio` | `0.85` | 0.4–0.97 | 上下文窗口用到这个比例时，要求模型立即开启新窗口。 |
| OpenViking 工具 | `gateway_tools` | `true` | | 为 Chat、完整历史的 Responses 和 Anthropic Messages 提供 OpenViking 工具。 |
| 取消勾选的工具 | `disabled_tools` | 会修改数据的 8 个工具：`remember`、`write`、`edit`、`add_resource`、`add_skill`、`forget`、`set_acl` 和 `cancel_watch` | OpenViking 工具原名，不带 `openviking_` | 新对话不提供这些工具，其余可用工具都会提供，包括以后新增的。API 请求省略这个字段时使用默认列表；列表为空且总开关开启时，提供全部可用工具。 |
| 显示工具调用 | `show_tool_calls` | `true` | | 每次 OpenViking 工具调用都在回复里加一行提示。 |
| 每次请求的工具轮数 | `tool_max_rounds` | `null`（不限） | 1 及以上 | 见 [OpenViking 工具](#openviking-工具)。 |
| 单次调用超时 | `tool_timeout_seconds` | `30` | 最多 120 | |
| 结果大小上限 | `tool_result_bytes` | `65536` | 1,024–1,048,576 | |
| 总时长 | `tool_total_seconds` | `null`（不限） | 大于 0 | |
| Token 预算 | `tool_total_tokens` | `null`（不限） | 1,024 及以上 | |

**上游设置：**

| Studio 名称 | API 名称 | 默认值 | 含义 |
| --- | --- | --- | --- |
| 名称 | `name` | | 显示名称。 |
| 协议 | `protocol` | | `anthropic`（Anthropic Messages）、`chat`（Chat Completions）或 `responses`（Responses）。 |
| 服务商 | `vendor` | `generic` | `generic`、`anthropic`、`openai`、`deepseek`、`ark`（火山方舟）或 `byteplus`（BytePlus 方舟）。Studio 只提供服务商支持的协议，API 接受任意组合。 |
| Base URL | `base_url` | | 服务商地址；各服务商的默认地址和路径规则见[上游](#上游)。 |
| API Key 由谁提供 | `auth_mode` | `managed` | `managed`（由网关保管 API Key）或 `passthrough`（每个客户端自带 API Key）。 |
| API Key | `api_key` | | 只能写入。编辑时留空表示保留已保存的密钥。 |
| 额外请求头 | `headers` | `{}` | 值只能写入，名称可见。 |
| 模型 | `models` | `[]` | 提供的模型名；留空表示接受任何名称。 |
| 模型别名 | `aliases` | `{}` | 客户端使用的名称 → 发给上游的模型。 |
| 上下文窗口 | `context_windows` | `{}` | 模型 → token 数，每项至少 1,024。 |
| 优先级 | `priority` | `0` | 数值越大，新对话越优先使用。 |
| 启用 | `enabled` | `true` | 停用的上游不接收请求。 |
| 允许 OpenViking 工具 | `allow_gateway_tools` | `true` | 能否通过这个上游提供 OpenViking 工具。 |
| 补全推理内容回传 | `replay_reasoning` | `null`（跟随服务商） | 把客户端丢掉的推理内容补回之前的回复。`null` 时 `deepseek`、`ark` 和 `byteplus` 开启，其他服务商关闭；`true` 或 `false` 覆盖服务商默认值。Studio 中开关与服务商默认值一致时保存为 `null`。 |
| 这是 Coding Plan 或订阅密钥 | `coding_plan` | `false` | 除非另外允许，否则拒绝发往这个上游的请求。 |
| 仍然允许 | `allow_coding_plan` | `false` | 允许使用被标记为 Coding Plan 的密钥。 |
| 最小可缓存长度 | `cache_min_tokens` | `1024` | 仅火山方舟和 BytePlus 方舟；影响缓存资格的报告。 |

**网关密钥字段：** 名称（`name`）、OpenViking 密钥（`openviking_key`）、上下文配置（`policy_id`）、上游（`upstream_ids`，至少一个）和允许的模型（`models`）。通过 OpenViking Server 签发时，可以用本账号用户的 `user_id` 代替 `openviking_key`，由 OpenViking Server 读取这个用户的密钥；两者只能提供一个。

Studio 通过 OpenViking Server 上 `/api/v1/admin/gateway/` 下的管理 API 操作网关，资源包括 `overview`、`logs`、`guides`、`upstreams`、`policies`、`keys` 和 `users/{user_id}/data`。脚本也可以用账号管理员密钥调用这些路径。

## 故障排查

### 常见现象

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 某段对话没有召回，也没有保存 | 检测到 OpenViking 插件或名为 `openviking` 的 MCP 服务器，网关让出了这段对话，召回、保存和 OpenViking 工具都交给插件。 | 正常行为，避免同一份内容补充两次、保存两次，见[网关还是插件](15-gateway.md#网关还是插件)。 |
| 保存显示“重试中”，之后变成“已暂停” | OpenViking 拒绝保存或无法访问，常见原因是用户的 OpenViking 密钥被重新生成过。 | 修好原因后网关会自动恢复；密钥失效时重新签发网关密钥，见[OpenViking 中看不到对话](#openviking-中看不到对话)。 |
| 上下文配置开启了工具，对话里却没有 | 对话的第一个请求不满足工具条件，或者客户端没有回传完整历史。 | 在请求日志里查看停用原因（见 [OpenViking 工具](#openviking-工具)），修正后开始新对话。 |
| 请求不带记忆 | OpenViking 无法访问或超时、预算用完、没有相关内容等。 | 见[没有补充记忆](#没有补充记忆)。 |
| 长对话被服务商以过长为由拒绝 | 网关不知道模型的实际窗口，压缩得太晚。 | 见[长对话超出上下文长度](#长对话超出上下文长度)。 |

### 网关无法启动

| 提示信息 | 原因和处理 |
| --- | --- |
| `configure a Fernet encryption key and an admin token of at least 32 characters` | 网关的环境里缺少加密密钥或管理令牌，或者长度不够。把两者（见[加密密钥和管理令牌](#加密密钥和管理令牌)）加载到启动网关的 shell、`.env` 或 Secret 中。 |
| `Set gateway.enabled=true in ov.conf` | 缺少这一部分、没有开启，或者网关读到了别的文件。网关依次读取 `--config`、`OPENVIKING_CONFIG_FILE`、`~/.openviking/ov.conf`，不会读取其他位置。 |
| `Missing OpenViking Gateway dependencies: …` | 安装可选依赖：`pip install "openviking[gateway]"`。 |
| 某个 `gateway` 字段的校验错误 | 有未知的键、拼写错误或无效的值。`${VAR}` 占位符不会展开，所以写在数字或 URL 字段里的占位符同样会报错。 |
| `OpenViking Gateway must bind to loopback when OpenViking uses dev authentication` | OpenViking 运行在 dev 模式，而 `host` 不是回环地址。把 OpenViking 切换到 API Key 模式。 |
| `Unsupported gateway schema; configure a fresh storage_path` | 存储由不兼容的网关版本写入。把 `storage_path` 指向一个空目录，重新设置网关。 |

在 Docker Compose 中，网关反复重启通常是因为 `.env` 里的加密密钥或管理令牌为空；`docker compose logs gateway` 会显示具体信息。

### Studio 显示设置卡片

OpenViking Server 无法使用网关时，OpenViking 网关页面会显示一张设置卡片，而不是各个标签页：

- **OpenViking 网关未开启**：OpenViking Server 的 `ov.conf` 中没有 `gateway.enabled: true`。加上之后重启 OpenViking Server。
- **缺少管理令牌**：OpenViking Server 的环境里没有管理令牌，或者它短于 32 个字符。设置好之后重启 OpenViking Server；Docker Compose 通过 `.env` 设置，Helm 通过 `gateway.existingSecret` 设置。
- **OpenViking 连不上网关**：OpenViking Server 访问不到 `gateway.url`。检查网关是否在运行，以及 `url` 是否是 OpenViking Server 能访问的地址，在 Docker Compose 中应为 `http://gateway:1935`（Helm 由 Chart 自动设置）。

如果两个进程都在运行，页面却提示“密钥被拒绝，请检查连接设置”，而同一个管理员密钥在**用户与权限**页面能正常使用，就说明两个进程的管理令牌不一致，网关返回的是 401 "Invalid gateway management credential"。给两者设置相同的令牌，然后都重启。

如果侧边栏里没有**OpenViking 网关**，说明 Studio 没有管理员权限：在**连接设置**中把账号管理员或 root 的密钥填入**管理员 API 密钥**。OpenViking 运行在 dev 模式时，Studio 会隐藏管理页面。

### 签发密钥失败

**签发网关密钥**对话框会显示签发失败的原因。括号里的代码是管理 API 返回的原因值。

| Studio 中的提示 | 原因和处理 |
| --- | --- |
| 服务端读不到这个用户的 OpenViking 密钥，请改为粘贴。 | OpenViking Server 读不到所选用户的密钥，通常是因为它只保存密钥的哈希（`encryption.api_key_hashing.enabled`）。选择**粘贴 OpenViking 密钥**，填入该用户自己的密钥。 |
| 这是 Root 密钥。（`root_key_not_allowed`） | OpenViking 密钥是 root key，或者 OpenViking 运行在 dev 模式，此时任何密钥都按 root 身份处理。请使用用户自己的密钥，并启用 API Key 模式。 |
| 这个 OpenViking 密钥属于其他账号。 | 密钥所属的账号不是你在 Studio 中管理的账号。 |
| OpenViking 拒绝了这个密钥。（`openviking_http_401`） | 密钥不完整、已被重新生成，或者它的用户已被删除。请使用该用户当前的密钥。 |
| OpenViking 无法识别这个密钥所属的用户（`openviking_identity_missing`） | OpenViking 没有返回这个密钥对应的用户。请使用本账号中某个用户的密钥。 |
| 网关连不上 OpenViking，无法校验这个密钥。（`openviking_unavailable`） | 网关访问不到 `openviking_url`。 |
| OpenViking 版本低于网关的要求。（`openviking_version_mismatch`） | OpenViking Server 的版本低于 `min_server_version`（0.4.16），请升级。从源码仓库安装、没有版本标签的服务可能报告 `0.1.dev123` 这样的开发版本号；请安装正式版本，或者把 `min_server_version` 改成与之匹配的值。 |

### 客户端收到网关返回的错误

这些拒绝都发生在请求到达服务商之前，所以不会出现在请求日志里。

| 状态码和提示 | 原因和处理 |
| --- | --- |
| 401 "Invalid or revoked OpenViking Gateway key" | 客户端没有发送网关密钥、发送了已吊销的密钥，或者发送的是服务商的 API Key。检查客户端实际发送的是哪个密钥。 |
| 403 "Claude subscription OAuth credentials are not supported" | 客户端发送了 Claude 订阅令牌，例如 Claude Code 用订阅账号登录且没有设置 `ANTHROPIC_AUTH_TOKEN`；或者上游保存的就是订阅令牌。请使用 API Key。 |
| 403 "Model is not allowed by this key" | 模型不在密钥允许的范围内。签发一个允许这个模型的密钥。 |
| 404 "No allowed upstream matches this protocol and model" | 密钥绑定的已启用上游中，没有一个使用客户端调用的 API 并提供所请求的模型。检查上游的协议、模型和别名、是否启用，以及密钥绑定了哪些上游。 |
| 401 "Upstream API key is missing" | 上游要求每个客户端在 `X-OpenViking-Upstream-Key` 中自带 API Key，或者由网关保管 API Key 的上游没有填写。 |
| 403 "Coding Plan upstreams are disabled; configure a model API key" | 上游被标记为 Coding Plan 密钥。请改用模型 API Key，或者有意勾选**仍然允许**。 |
| 404 "Unknown response for this key"、403 "Response upstream is no longer allowed" | 查询的 Responses 响应不是这个密钥创建的，或者它的上游已被停用、不再绑定到这个密钥。 |
| 413 | 请求体超过了 `max_body_bytes`，或者超过了代理自己的上限。 |
| 426 | 对 Responses API 发起了 WebSocket 连接。这是预期行为，Codex 会改用 HTTP。 |
| 502 "Model upstream is unavailable" | 网关连不上服务商，或者调用超过了 `upstream_timeout_seconds`。在上游上运行**测试连接**。 |
| 503 "Dev authentication requires a loopback gateway" | OpenViking 运行在 dev 模式。把它切换到 API Key 模式。 |
| 504 "Hidden tool request timed out" | OpenViking 工具的往返超过了上下文配置中工具的**总时长**；未设置**总时长**时，是工具往返中的某次模型请求超过了 `upstream_timeout_seconds`。 |

### 没有补充记忆

先在请求日志里找到这个请求。找不到，说明客户端没有经过网关，或者请求被网关拒绝了（见上一节）。找到了，就展开它查看记忆搜索结果：

- **没有找到相关内容**（`empty`）：新用户通常如此。只有对话被提交、OpenViking 提取之后才会有记忆。
- **召回已关闭或预算已用完**（`disabled`）：上下文配置关闭了召回，这段对话的预算已经用完，或者消息短于 3 个字符。
- **OpenViking 无法访问或响应超时**（`openviking_unavailable`）：查看“概览”标签页上的 OpenViking 卡片。如果 OpenViking 在运行但响应慢，可以考虑调长**超时时间**。
- **OpenViking 拒绝了这个密钥**（`openviking_http_401`）：用户的 OpenViking 密钥已失效。用当前的 OpenViking 密钥签发新的网关密钥。
- **OpenViking 版本低于网关的要求**（`openviking_version_mismatch`）：见[签发密钥失败](#签发密钥失败)。
- **检测到 OpenViking 插件**（`plugin_present`）：网关发现了 OpenViking 插件，并让出了这段对话。
- **类型不是新消息**：工具步骤、辅助请求和子 Agent 请求从不搜索，它们放回之前的消息得到的记忆。

搜索失败的消息之后也不会补上记忆；下一条消息会重新搜索。

### OpenViking 中看不到对话

- **保存晚一轮。** 下一条消息到达时，上一轮才会出现；最后一轮要等**最新回复等待时长**过去（默认 10 分钟）。
- **上下文配置不保存对话。** 这时**保存**列显示“已关闭”。
- **保存正在重试或已暂停。** 展开的行会显示原因。修复原因（例如 OpenViking 密钥失效）后，网关会在 5 分钟内重试。如果对话一直暂停，使用**重新同步对话…**。
- **检测到 OpenViking 插件**（`plugin_present`）：网关不会为这段对话保存任何内容。
- **你用其他用户的身份查看。** 会话属于网关密钥背后的 OpenViking 用户，名称为 `gateway-…`。
- **记忆比会话出现得晚。** OpenViking 只在提交之后才在后台提取记忆。

### 首次调用缓存命中率下降

先和不经过网关时服务商能达到的命中率比较，再查看最近请求上标出的异常：

- **记忆记录缺失**（`missing_injection_record`）：网关存储丢失、从旧备份恢复，或者记录已过期，之前补充的记忆无法放回。进行中的对话在一次未命中之后就会恢复。
- **上游已切换**（`upstream_changed`）：对话原来的上游被停用、没有绑定到客户端当前使用的密钥，或者不再提供这个模型，于是对话转到了另一个上游。
- **缓存参数有变化**（`ark_cache_parameters_changed`）：在火山方舟或 BytePlus 方舟上，客户端在同一段对话里改变了模型、思考模式、采样参数、系统提示词或工具。
- 没有标出异常：客户端本身可能每轮都在改动之前的消息或系统提示词，例如插入当前时间，网关无法修正这种情况。每次压缩、模型每次开启新的上下文窗口，也都会造成一次未命中，这是预期内的。

### 长对话超出上下文长度

如果服务商以对话过长为由拒绝长对话的请求：

- **网关不知道模型的窗口大小。** 上游的**上下文窗口**里没有这个模型，上下文配置也没有设置**默认上下文窗口**时，网关按 1,000,000 token 计算，对窗口更小的模型来说压缩得太晚。请补上模型的窗口大小。
- **对话所用的上下文配置关闭了压缩。** 修改配置只对新对话生效。
- **压缩失败。** 请求详情会显示原因，网关 60 秒后再次尝试。如果摘要总因长度上限被截断，就调大**摘要长度上限**。原因是 `still_over_threshold` 时，说明系统提示词、工具和摘要本身就占满了大部分窗口：调小**摘要长度上限**，或者检查模型的上下文窗口设置。

### 文件导入失败

- "Set gateway.public_url for client uploads"：把 `public_url` 设为客户端能访问的地址。
- 上传链接无法访问，或者返回 404：在代理中把 `/gateway/uploads` 转发给网关。
- 413：文件超过了 `max_body_bytes` 或代理的上限。
- 502 "OpenViking upload is unavailable"：网关连不上 OpenViking Server。
- 没有提供导入工具：OpenViking 服务未提供该工具、上下文配置中取消了勾选，或当前请求无法使用网关工具。请检查工具清单和请求详情。

### 降级请求的异常

“请求日志”和“概览”标签页会标出网关没能完整处理的请求。Studio 显示异常名称，括号里的代码是管理 API 中对应的值：

| 异常 | 发生了什么 | 怎么处理 |
| --- | --- | --- |
| 记忆不可用（`memory_store_failure`） | 网关无法读写自己的存储，或者该用户的网关数据刚被删除。请求不带记忆发给了模型；对 Claude 来说，之前的思考内容被去掉了。 | 检查磁盘空间、权限和网关日志。刚删除过该用户的网关数据时出现这一项是正常的。 |
| 原样转发（`unsafe_json`） | 请求中有无法精确重新编码的数字或重复的键，所以网关逐字节原样转发，没有补充记忆。 | 通常只与某个客户端有关，网关这边无需调整。 |
| 上游已切换（`upstream_changed`） | 对话原来的上游无法再服务，转到了另一个上游，造成一次缓存未命中，之前的 Claude 思考内容也被去掉。 | 重新启用这个上游，或者让客户端改用包含它的密钥；也可以接受这次切换。 |
| 记忆记录缺失（`missing_injection_record`） | Claude 对话中，之前补充的记忆已经没有记录，所以之前的思考内容被去掉了一次。 | 存储丢失后，或者在很久以前的对话里出现，都在预期之内。反复出现时，开始一段新对话。 |
| 检测到 OpenViking 插件（`plugin_present`） | 检测到了 OpenViking 插件，这段对话不再使用网关记忆。 | 使用插件时出现这一项是正常的。同一个客户端在插件和网关之间二选一。 |
| 缓存参数有变化（`ark_cache_parameters_changed`） | 在火山方舟或 BytePlus 方舟上，影响缓存的参数与对话的第一个请求不同，提示词缓存很可能没有命中。 | 在同一段对话里保持模型、思考模式、采样参数、系统提示词和工具不变。 |
| 工具历史无法继续使用（`hidden_tool_history_unavailable`） | Claude 对话曾使用 OpenViking 工具，但当前请求无法使用这些工具，之前的思考内容已被去掉。 | 查看请求详情中的工具停用原因，恢复原设置，或开始一段新对话。 |
| OpenViking 工具调用失败（`hidden_tool_loop_failed`） | 模型使用 OpenViking 工具时未能完成回答，客户端收到了错误。 | 见 [OpenViking 工具](#openviking-工具)。 |
| 回复未保存（`capture_parse_failure`） | 流式回复无法解析，这条回复没有保存。客户端不受影响。 | 无需处理，仅供了解。 |
