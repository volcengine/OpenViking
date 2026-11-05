---
description: 把 OpenViking 网关嵌入 Server、用 Docker Compose 或 Helm 部署，或者作为独立进程运行；再对外暴露、做好保护，并在升级中保持稳定运行。
---

# OpenViking 网关部署与运维

本页写给把 OpenViking 网关作为服务来运行的人：按合适的方式部署、放到反向代理后面、控制谁能修改配置，以及在升级和迁移中保持服务正常。你需要一台使用 API 密钥认证或本地 dev 模式的 OpenViking Server，完整条件见[环境要求](#requirements)。第一次接触网关，请先读[OpenViking 网关](15-gateway.md)。

> **注意**：网关目前处于 Beta 阶段，设置和接口可能随版本调整，升级前请先读发布说明。

**要找别的内容？** 本页只讲运行网关，其他内容见：

- Studio 标签页和各项设置：[在 Studio 中管理 OpenViking 网关](../gateway/06-manage-in-studio.md)，以及[上游](../gateway/07-upstreams.md)、[上下文配置](../gateway/08-context-profiles.md)、[网关密钥](../gateway/09-gateway-keys.md)和[网关概览与请求日志](../gateway/10-overview-and-requests.md)。
- 报错、告警或 Studio 设置卡片：[OpenViking 网关排障](../gateway/12-troubleshooting.md)。
- 配置字段或环境变量：[配置参考](../gateway/13-configuration.md)。
- 存了什么、谁能看到、保留多久、怎么备份：[安全与数据](../gateway/11-security-and-data.md)。
- OpenViking 工具或长对话：[网关中的 OpenViking 工具](../gateway/04-openviking-tools.md)和[网关中的长对话](../gateway/05-long-conversations.md)。
- 某个客户端怎么配置：[把客户端接入 OpenViking 网关](../gateway/03-connect-clients.md)。

## 环境要求 {#requirements}

| 条件 | 说明 |
| --- | --- |
| OpenViking 认证方式 | `api_key` 或本地 dev 模式。网关不支持 trusted 认证。 |
| OpenViking 版本 | 嵌入运行时和 Server 同版本发布，无需额外检查；独立运行时要求 OpenViking Server 0.4.16 或更高版本。 |
| 安装 | 网关包含在 OpenViking 核心包中。可选的 `gateway-fast` extra（`pip install "openviking[gateway-fast]"`）装上更快的事件循环和 HTTP 解析器。 |
| 登录身份 | 远程部署需要 OpenViking API 密钥，普通用户、账号管理员和 root 都能登录 Studio 的网关页面。本地 dev 模式不需要密钥，固定使用 `default/default` 身份。 |
| 存储 | 单机使用本地磁盘上的 SQLite，多副本使用共享 Redis。Redis 需要 `pip install "openviking[expiry]"`，官方镜像已包含。 |

## 选择部署方式 {#deployment-options}

网关可以运行在 OpenViking Server 内部（嵌入），也可以作为单独的进程运行（独立）。两种方式都在 `/gateway` 下提供相同的模型路径和管理路径。

| 方式 | 适用场景 | 怎么开启 | 客户端地址 |
| --- | --- | --- | --- |
| 嵌入 Server（包括 Compose 和 Helm） | OpenViking Server 由你自己运行。不需要第二个进程，也不需要按路径分流的反向代理。 | 在 `ov.conf` 中开启 `gateway.enabled` | Server 地址加 `/gateway`，默认 `http://127.0.0.1:1933/gateway` |
| 独立运行 | 不想改动 OpenViking 服务的配置，例如使用 OpenViking 商业版服务。 | 先 `openviking-gateway init`，再 `openviking-gateway` | 网关自己的地址加 `/gateway`，默认 `http://127.0.0.1:1935/gateway` |

选好之后，按你的部署方式阅读对应小节：[在 Server 中启用](#enable-it-in-the-server)、[Docker Compose](#docker-compose)、[Helm](#helm)或[独立部署](#standalone-deployment)。

## 在 Server 中启用 {#enable-it-in-the-server}

嵌入网关运行在 OpenViking Server 内部、和 Server 共用端口，默认关闭（`gateway.enabled` 为 `false`）。开启之前，Studio 的 **OpenViking 网关**页面会显示**这台 OpenViking 没有开启网关**。开启方式有三种。

**用 `openviking-server init`。** 在 **Enable OpenViking Gateway on the same port?** 处回答 yes，然后启动或重启服务。已有配置时，在菜单中选择 **Update gateway**。远程部署还会询问是否允许用户自行配置上游，见[网关管理密钥](#gateway-management-key)。

**改 `ov.conf`。** 把下面的内容合并进 `ov.conf`，再重启服务：

```json
{
  "gateway": {
    "enabled": true
  }
}
```

**在 Studio 中开启。** 使用 API 密钥的服务端上，root 可以在 Studio 的 OpenViking 网关页面点**开启网关**，Studio 会把 `gateway.enabled: true` 写入 `ov.conf` 并重启服务。确认对话框还可以顺便生成网关管理密钥，见[网关管理密钥](#gateway-management-key)。几种例外情况：

- 从 Studio 重启要求服务端只有一个 worker，否则 Studio 只保存文件，需要你自行重启。
- 设置了 `OPENVIKING_GATEWAY_ENABLED` 时，它会覆盖 `ov.conf` 中的 `gateway.enabled`，值为 false 时重启后网关仍不开启。
- `ov.conf` 只读时（例如由 Helm ConfigMap 挂载），Studio 改不了它。请在 Helm values 中设置 `gateway.enabled: true`，或者设置 `OPENVIKING_GATEWAY_ENABLED=true`，然后重启服务。

**数据存放位置。** 未设置 `gateway.storage_path`（或 `OPENVIKING_GATEWAY_STORAGE_PATH`）时，嵌入网关把数据库放在 `<storage.workspace>/gateway`。如果该目录没有网关数据库，而旧的默认目录 `~/.openviking/gateway` 有，服务端继续使用旧目录并打印警告；把目录复制到新位置或设置 `storage_path` 即可消除警告。独立网关仍默认使用 `~/.openviking/gateway`。

**确认已经开启。** 打开服务端的 `/studio/`，在侧边栏选择 **OpenViking 网关**，应看到网关的各个标签页，而不是未开启的提示卡片。本地 dev 模式下，如果默认账号还没有上游，网关启动时会导入服务端可用的 VLM 凭证作为上游，并创建 `default` 上下文配置。导入的 Coding Plan 上游需要确认后才能使用；没有可导入的凭证时，请手动添加上游（见[添加上游](../gateway/07-upstreams.md#add-an-upstream)）。

**让 Agent 修改配置。** 持有 root API 密钥的 Agent 可以通过[运行时配置接口](../api/08-admin.md#runtime-configuration)修改 `ov.conf` 并重启服务。独立网关不提供这组接口。

## Docker Compose {#docker-compose}

用 Docker Compose 部署时，网关在 `openviking` 服务内运行，不需要单独的服务或 profile。

1. 在 `.env` 中设置 `OPENVIKING_GATEWAY_ENABLED=true`，或者在挂载的 `ov.conf` 里开启 `gateway.enabled`。设置了这个变量时，以变量为准。
2. 确认挂载的 `ov.conf` 使用 API 密钥认证：容器内 Server 监听 `0.0.0.0`。
3. 运行 `docker compose up -d`，打开 1933 端口上的 `/studio/`，选择 **OpenViking 网关**，应看到网关的各个标签页。服务没起来时，用 `docker compose logs openviking` 查看启动错误。

不用 Compose 时，`docker run` 加上 `-e OPENVIKING_GATEWAY_ENABLED=true` 即可：

```bash
docker run -d -p 1933:1933 -v ~/.openviking:/app/.openviking \
  -e OPENVIKING_GATEWAY_ENABLED=true \
  ghcr.io/volcengine/openviking:latest
```

- **管理密钥**：在 `.env` 中设置 `OPENVIKING_GATEWAY_MANAGEMENT_KEY`，自带的 Compose 文件会把它传进容器；也可以写进挂载的 `ov.conf` 的 `gateway.management_key`。Compose 文件还会传入 `OPENVIKING_GATEWAY_ENCRYPTION_KEY`，它只在迁移 v0.5 数据时读取一次，见[迁移](#migration)。
- **对外地址**：设置 `OPENVIKING_PUBLIC_BASE_URL`（自带的 Compose 文件会传入）即可，网关没有单独配置 `public_url` 时沿用它。见[对外地址](#public-address)。
- **修改配置后**：改了挂载的 `ov.conf`，执行 `docker compose restart openviking`；改了 `.env`，执行 `docker compose up -d`，因为 `restart` 沿用原来的容器，读不到 `.env` 的新值。

自带的 Caddy 把所有路径转发到 `openviking:1933`。可以继续用兼容端口 1934，也可以直接访问 Server 的 1933。配置 HTTPS 时，一条 `reverse_proxy openviking:1933` 就能承载 Studio、管理接口和模型流量，见[公网访问](12-public-access.md)。

独立容器需要挂载 `gateway.conf` 并使用网关命令。在文件中设置 `host: "0.0.0.0"`，以及启用 API key 认证的 `openviking_url`。选择包含所配置功能的镜像：

```bash
docker run -d --no-healthcheck -p 1935:1935 \
  -v "$PWD/gateway.conf:/etc/openviking/gateway.conf:ro" \
  -v gateway-data:/app/.openviking \
  ghcr.io/volcengine/openviking:'<gateway-image-tag>' openviking-gateway
```

`--no-healthcheck` 关闭镜像自带的 1933 端口 Server 探针。独立网关改用 `http://localhost:1935/health` 探测。

## Helm {#helm}

用嵌入式 `deploy/helm/openviking` chart 部署时，在 chart values 中设置 `gateway.enabled: true`。网关嵌入 server 容器，数据保存在 `<persistence.mountPath>/gateway`，Service 和 Ingress 都使用 Server 端口。保持 `replicaCount: 1`。

```yaml
gateway:
  enabled: true
  existingSecret: ""  # 可选：Secret 名称，字段 management-key
config:
  gateway:
    public_url: https://ov.example.com
```

- 网关和 Server 一样，按 chart 的 `config.encryption` 设置加密数据。
- `existingSecret` 指定的 Secret 中有 `management-key` 字段时，修改上游和上下文配置需要这把管理密钥。
- 模型请求是流式的：Ingress 要关闭响应缓冲，允许至少 32 MiB 的请求体和 600 秒超时（见[反向代理和 HTTPS](#reverse-proxy-and-https)）。

NGINX annotations、创建 Secret 的命令和从 sidecar 版本升级的说明见 [chart README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md#openviking-gateway)。

独立部署使用 `deploy/helm/openviking-gateway`。它有自己的 80 端口 Service，转发到 1935，并运行 `openviking-gateway`。`config` 值是独立的 `gateway.conf` 对象，chart 固定 `host`、`port` 和 `storage_path`。

在包含这些功能的版本发布之前，需要把 `image.tag` 设为包含它们的构建，见 [chart README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md#standalone-openviking-gateway)。

```bash
helm upgrade --install gateway ./deploy/helm/openviking-gateway \
  --set-string image.tag='<gateway-image-tag>' \
  --set-string config.openviking_url=https://ov.example.com
```

默认使用单副本 SQLite、持久卷和 `Recreate` 更新策略。集群必须有默认 StorageClass，或配置 `persistence.storageClass`/`persistence.existingClaim`。设置 `persistence.enabled: false` 后，更换 Pod 会丢失 SQLite 状态，Helm NOTES 会显示警告。卸载会保留 PVC。扩容前必须把两个 provider 都改成 Redis，见[扩展](#scaling)。

chart 设置了 `session_ttl_seconds: 604800` 和 `response_ttl_seconds: 604800`，比网关 30 天的默认值短；需要其他值时覆盖 `config.*`。每个 Pod 请求 500m CPU 和 1Gi 内存，上限为 2 CPU 和 4Gi；可按负载设置 `resources`。

独立 chart 的 `existingSecret` 接受 `management-key`、`redis-password` 和 `root-key`。前两项分别注入 `OPENVIKING_GATEWAY_MANAGEMENT_KEY` 和 `OPENVIKING_GATEWAY_REDIS_PASSWORD`；`config.storage.redis.password_env` 指向后者。local 和 KMS 加密把 `root-key` 只读挂载到 `/etc/openviking-gateway/keys/root-key`，并将 provider 的 `key_file` 指向它。安装前先按[加密](#encryption)生成密钥。上游凭证和 KMS AK/SK 在 `config` 中写 `$VAR`，通过 `extraEnv` 的 `secretKeyRef` 注入，不要将明文凭证写入 ConfigMap。

`extraVolumes` 和 `extraVolumeMounts` 支持私有 CA 文件；`extraEnv`、`podLabels`、`podAnnotations`、`nodeSelector`、`affinity`、`tolerations`、`imagePullSecrets` 用于部署环境的其他设置。ConfigMap 变化通过 `checksum/config` 触发滚动更新。外部 Secret 改动后需要重启 Deployment，让所有副本加载一致的凭证。

chart 不创建 Ingress。把代理指向它的 Service，并保持 `fullnameOverride`、`nameOverride` 和 release 名不变：Service 和 Deployment 同名，selector 是 `app.kubernetes.io/name` 与 `app.kubernetes.io/instance`。直接对外开放时，配置 `service.type: LoadBalancer` 和 `service.loadBalancerSourceRanges`。可选的 `networkPolicy.enabled` 将 1935 端口入站限制为 `networkPolicy.ingressFrom`，空列表会拒绝入站。放行代理实际的来源网段或 Pod selector，并在所用 CNI 上验证 kubelet 探针。

## 独立部署 {#standalone-deployment}

独立网关不读取 `ov.conf`，只需要知道 OpenViking 的地址，已有的开源版或商业版服务不用改配置。

1. 运行 `openviking-gateway init`。它依次询问 OpenViking 地址（会实际访问一次，并显示服务端的认证模式和版本）、监听地址（默认 `127.0.0.1:1935`），以及是否要求[管理密钥](#gateway-management-key)。结果写入 `~/.openviking/gateway.conf`，或者 `--config` 指定的文件，只有你自己能读：

   ```json
   {
     "openviking_url": "https://ov.example.com",
     "host": "127.0.0.1",
     "port": 1935
   }
   ```

2. 运行 `openviking-gateway`。它先完成[启动检查](#startup-checks)，然后在你选的地址上监听。
3. 打开网关的 `/studio/`，用 OpenViking 用户、账号管理员或 root 的 API 密钥登录。应看到网关页面，以及显示网关所连 OpenViking 地址的连接页。
4. 手动添加上游。独立网关读不到远端服务的 VLM 配置，所以不能导入模型配置。

之后想修改设置，再运行一次 `openviking-gateway init` 即可：它把当前值作为默认值，已有的密钥不改就原样保留。

### 设置从哪里来 {#standalone-config-sources}

命令行只有 `--config` 一个参数。设置了 `--config` 或 `OPENVIKING_GATEWAY_CONFIG_FILE` 时，它必须指向已存在的文件；两者都没设置时，网关读取 `~/.openviking/gateway.conf` 和 `/etc/openviking/gateway.conf` 中第一个存在的文件。环境变量可以覆盖其中任何字段，所以容器里可以完全不用文件：设置 `OPENVIKING_GATEWAY_OPENVIKING_URL`，再按需加上 `OPENVIKING_GATEWAY_HOST=0.0.0.0` 之类的变量。

两处都没有 OpenViking 地址时，网关退出，并提示运行 `openviking-gateway init` 或设置 `OPENVIKING_GATEWAY_OPENVIKING_URL`。全部字段和覆盖规则见[配置参考](../gateway/13-configuration.md#configuration-reference)。

### 启动检查 {#startup-checks}

选择 Redis 后端时，启动 PING 必须成功。地址、凭据错误或 Redis 不可达会使启动失败，Kubernetes 会重启容器；滚动发布使用 `maxUnavailable: 0`。运行时 Redis 故障不会改变 `/health`；鉴权阶段存储失败返回纯文本 HTTP 500，并在 stderr 输出异常栈。

独立网关开始监听前会先检查 OpenViking 的认证模式：

- OpenViking 使用 trusted 认证时，网关拒绝启动。
- 绑定非回环地址时，必须确认 OpenViking 使用 `api_key` 认证才会启动。连接失败或 HTTP 5xx 响应会输出告警，并在 1、2、4、8、16、30 秒后依次重试，后续等待上限为 30 秒。dev 模式、未知认证模式以及 HTTP 404 等其他错误仍会立即退出。
- 回环地址（`127.0.0.1`、`::1`、`localhost`）只探测一次，OpenViking 不可达时也能启动；仍然拒绝 trusted 认证。
- 跨主机连接 OpenViking 时，请使用 TLS 或私有网络。

等待期间 HTTP 端口尚未监听。Kubernetes 的启动探针此时仍会失败，应按预期的 OpenViking 中断时长设置 `startupProbe` 预算（`periodSeconds × failureThreshold`）。例如 `periodSeconds: 2`、`failureThreshold: 150` 允许等待约 300 秒，之后 kubelet 会重启容器。网关自身不另设启动等待期限。

### 连接公共 OpenViking 服务 {#public-openviking-service}

独立网关连接公共 OpenViking 服务 `https://api.vikingdb.cn-beijing.volces.com/openviking`、又没有设置管理密钥时，会对该服务的所有用户开放。启动日志会显示 `public_openviking_without_management_key` 告警，Studio 会显示**这台网关对所有 OpenViking 商业版用户开放**：任何持有该服务 API 密钥的人都能登录这台网关，并修改所在账号的上游。

网关只给你自己或团队使用时，设置 `OPENVIKING_GATEWAY_MANAGEMENT_KEY` 后重启即可限制修改。这不限制谁能登录，也不限制他们给自己签发网关密钥。

## 路由 {#routes}

两种部署方式下，网关的所有 URL 都在 `/gateway` 下。嵌入部署中，Server 把整个 `/gateway` 前缀交给网关，其他路径仍由 Server 自己处理：

| 路径前缀 | 用途 |
| --- | --- |
| `/gateway/v1/` | 模型 API 和模型列表 |
| `/gateway/api/v3/` | 方舟兼容客户端 |
| `/gateway/api/compatible/v1/` | 方舟 Anthropic 兼容客户端 |
| `/gateway/uploads` | 工具上传 |
| `/gateway/downloads` | 工具下载 |
| `/gateway/admin/` | 管理 API，需要认证 |

因此客户端使用下面的 Base URL，把 `http://127.0.0.1:1933` 换成你的 Server 地址即可。各客户端的具体设置见[把客户端接入 OpenViking 网关](../gateway/03-connect-clients.md#connect-clients)。

| 客户端 | Base URL |
| --- | --- |
| Claude Code 和 Anthropic SDK | `http://127.0.0.1:1933/gateway` |
| OpenAI 兼容的客户端和 SDK，包括 Codex、OpenCode、pi 和 Open WebUI | `http://127.0.0.1:1933/gateway/v1` |
| 火山方舟和 BytePlus 方舟 SDK | `http://127.0.0.1:1933/gateway/api/v3` |

`/health` 仍是 Server 自己的健康检查，嵌入网关不单独提供 `/health`。独立网关在自己的端口上提供同样的 `/gateway` 路径，另外在根路径提供两个地址：

- `/` 跳转到 `/studio/`，Studio 只包含网关和连接页面。
- `/health` 返回 `service: "openviking-gateway"`、`auth_mode` 和 `openviking_url`；请求带有效的 OpenViking 凭证时，还返回调用方身份。

### 对外地址 {#public-address}

对外地址 `public_url` 是客户端访问 Server（或独立网关）时使用的地址，不带 `/gateway`，网关会自己加上。它默认为空，此时嵌入网关沿用 Server 的对外地址（`OPENVIKING_PUBLIC_BASE_URL` 或 `server.public_base_url`）。共享部署都应设置它，前面有反向代理时更是必须设置：

- **嵌入**：在 `ov.conf` 中设置 `gateway.public_url`，然后重启服务。
- **独立**：设置 `gateway.conf` 中的 `public_url` 或环境变量 `OPENVIKING_GATEWAY_PUBLIC_URL`，然后重启网关。

有两样东西依赖它：

- **Studio 的接入指南**：Anthropic 客户端使用 `<public_url>/gateway`，OpenAI 风格的客户端使用 `<public_url>/gateway/v1`。
- **OpenViking 工具的上传和下载链接**，除非 `mcp_file_links` 设为 `direct`，见[导入文件和技能](../gateway/04-openviking-tools.md#import-files-and-skills)。

一个对外地址都没有设置时，指南用的是请求到达服务器时的协议和主机。经过反向代理时，这可能是内部地址或 `http://` 地址，**接入**标签页会提示**客户端可能连不上这个地址**；如果是 `127.0.0.1` 这类回环地址，则提示**只有网关所在的机器能使用这个地址**。

## 反向代理和 HTTPS {#reverse-proxy-and-https}

把整个站点转发到 Server（嵌入）或网关（独立），不需要按路径分流。以 Caddy 为例：

```caddyfile
ov.example.com {
    reverse_proxy 127.0.0.1:1933 {
        flush_interval -1
    }
}
```

无论用哪种代理，都要满足下面几项：

| 设置 | 取值 | 原因 |
| --- | --- | --- |
| 响应缓冲 | 关闭 | 流式输出才能实时到达 |
| 请求体大小 | 至少 32 MiB | 这是网关自身对请求和上传的默认上限；长对话每次都发送完整历史（见[副作用](../gateway/05-long-conversations.md#side-effects)） |
| 读取超时 | 至少 600 秒 | 网关默认最多等待服务商 600 秒 |
| `/gateway/uploads` 和 `/gateway/downloads` 的日志 | 不记录查询字符串 | 其中带有一次性令牌 |

独立网关的 `keep_alive_seconds`（默认 75）应大于负载均衡器的后端空闲超时。

响应头发出后，空闲事件流每 15 秒发送一条 SSE 注释（`: keep-alive`），包括执行 OpenViking 工具期间。SSE 客户端会忽略注释，模型事件和捕获的回复不受影响。代理的空闲超时应大于 15 秒，并关闭响应缓冲。心跳只在完整事件的边界发送，上游压缩流禁用心跳，因此仍需保留上面的较长读取超时，以覆盖事件传到一半或响应头尚未到达时的等待。心跳间隔固定，不会延长服务商超时或工具的总时间限制。

对外发布前，先配置好认证和[对外地址](#public-address)，见[公网访问](12-public-access.md)。

## 密钥和凭证 {#keys-and-credentials}

网关涉及四种密钥，各管一件事：

| 密钥 | 用途 | 在哪里使用 |
| --- | --- | --- |
| OpenViking API 密钥 | 登录 Studio；决定以哪个账号、哪个用户的身份读写记忆 | 管理请求的 `X-API-Key` 或 `Authorization: Bearer` |
| 网关密钥（`ovgw_…`） | 客户端接入；每把绑定一位用户、一份上下文配置和一组上游（见[网关密钥](../gateway/09-gateway-keys.md#gateway-keys)） | 客户端的 API Key 输入框 |
| 网关管理密钥（可选） | 限制谁能修改上游和上下文配置 | `gateway.management_key`；请求头 `X-OpenViking-Gateway-Management-Key` |
| 上游 API Key | 调用模型服务商（见[凭证](../gateway/07-upstreams.md#credentials)） | 上游编辑页 |

网关没有自己的加密密钥，本地存储按 OpenViking 的加密配置加密，见[加密](#encryption)。

### 网关管理密钥 {#gateway-management-key}

网关管理密钥是一项可选的部署密钥，用来限制谁能修改[上游和上下文配置](15-gateway.md#key-terms)。它默认不设置，此时账号内每个登录的用户都能添加和修改本账号的上游和上下文配置。设置之后，只有带上这把密钥的请求能修改它们，账号管理员和 root 也不例外。它不限制谁能登录，也不限制用户给自己签发网关密钥。

只读用户在 Studio 里还能看到什么、做什么，见[谁能修改什么](../gateway/06-manage-in-studio.md#who-can-change-what)；包括管理 API 在内的完整规则见[权限矩阵](../api/25-gateway.md#credentials-and-account-scope)。

**像部署密钥一样保管它。** 管理密钥本身就能当凭证用，不需要 OpenViking API 密钥：带上 `account_id`，它可以查看、创建、修改和删除任意账号的上游和上下文配置，包括把上游指向另一个服务商地址。但它读不了记忆和日志，也不能签发网关密钥。只把它交给维护上游的人。

设置方法：

- **`openviking-server init`**（嵌入）：远程部署回答 **Allow users to configure their own gateway upstreams?** 时选 no，init 会生成一个以 `ovgm_` 开头的值，打印出来并写入 `ov.conf` 的 `gateway.management_key`。
- **`openviking-gateway init`**（独立）：回答 **Require a management key to change upstreams and profiles?** 时选 yes，init 会生成一个 `ovgm_` 开头的值，只打印一次，并写入 `gateway.conf` 的 `management_key`。
- **Studio**（嵌入，root）：点**开启网关**后弹出的确认对话框里有一个复选框**只有持有网关管理密钥的人才能修改上游和上下文配置**，默认不勾选。勾选后，Studio 会生成一个 `ovgm_` 开头的值，写入 `ov.conf` 的 `gateway.management_key`（同时写入 `gateway.enabled: true`），并且只显示一次。
- **手动设置**：值至少 32 个字符，可以用 `openssl rand -base64 32` 生成。写进 `management_key`，或者设置环境变量 `OPENVIKING_GATEWAY_MANAGEMENT_KEY`；两种部署下环境变量都优先于配置文件，见[配置参考](../gateway/13-configuration.md#configuration-reference)。
- **Compose 和 Helm**：Compose 会把 `OPENVIKING_GATEWAY_MANAGEMENT_KEY` 传进容器；Helm 从 `gateway.existingSecret`（嵌入）或 `existingSecret`（独立）指定的 Secret 中读取 `management-key` 字段。见 [Docker Compose](#docker-compose) 和 [Helm](#helm)。

管理密钥不提供命令行参数，因为命令行参数会出现在进程列表和 shell 历史里。设置、更换或去掉管理密钥后需要重启，这不会吊销已签发的网关密钥，也不会改动已有的上游和上下文配置。

#### 忘记网关管理密钥 {#lost-the-management-key}

没人记得[网关管理密钥](#gateway-management-key)时，按网关读取它的顺序查找：

1. 环境变量 `OPENVIKING_GATEWAY_MANAGEMENT_KEY`，它优先于配置文件。Compose 部署一般写在 `docker-compose.yml` 旁边的 `.env` 里；Helm 从 `gateway.existingSecret`（嵌入）或 `existingSecret`（独立）指定的 Secret 中读取 `management-key` 字段。
2. 没有设置环境变量时，看 `ov.conf` 的 `gateway.management_key`（嵌入）或 `gateway.conf` 的 `management_key`（独立）。值写成 `$VAR` 时，密钥在对应的环境变量里。

嵌入部署的 root 还可以在 Studio 的**设置** → **服务端配置** → **文件编辑**中查看 `ov.conf`。`openviking-server init` 和 Studio **开启网关**生成的密钥都以明文保存在这里。

实在找不回来，就换一把新的：

1. 生成一个至少 32 个字符的新值，例如用 `openssl rand -base64 32`。
2. 写到原来的位置。把环境变量设为空字符串不会清除文件里的值，需要删掉这个变量。
3. 重启。Compose 部署改了 `.env` 之后，要运行 `docker compose up -d` 才会生效，`docker compose restart` 读不到新值。
4. 在 Studio 中点**输入网关管理密钥**，粘贴新值。已签发的网关密钥、上游和上下文配置都不受影响。

## 加密 {#encryption}

网关用 OpenViking 自己的加密功能保护已存的凭证和对话状态（见[加密指南](08-encryption.md)）。加密关闭或没有配置时，网关以明文存储，依靠存储访问控制保护。

- **嵌入**：网关沿用 Server 在 `ov.conf` 中的 `encryption` 段，不需要为网关单独设置。
- **独立**：在 `gateway.conf` 中加一个 `encryption` 块，字段和 `ov.conf` 的 `encryption` 段完全相同，例如：

  ```json
  {
    "openviking_url": "https://ov.example.com",
    "encryption": {
      "enabled": true,
      "provider": "local",
      "local": { "key_file": "~/.openviking/master.key" }
    }
  }
  ```

共享部署的每个副本必须只读挂载同一份 root key。local provider 使用**十六进制文本**，不要生成原始字节：

```bash
umask 077
openssl rand -hex 32 > gateway-root-key
kubectl create secret generic gateway-credentials --from-file=root-key=gateway-root-key
```

设置 `existingSecret: gateway-credentials`、`config.encryption.enabled: true` 和 `config.encryption.provider: local`。需要时，在同一个 Secret 中添加 `management-key` 和 `redis-password`。

使用 `provider: volcengine_kms` 时，`root-key` 是 **KMS 密文文件**，不是十六进制文本。在受保护的本地可写目录中，把 `encryption.volcengine_kms.key_file` 设为绝对路径，通过环境变量引用提供 KMS 凭证，启动一次网关。启动成功后停止，将生成的文件放入 Secret 的 `root-key`。各副本使用一致的 KMS key ID 和凭证。文件不存在时，provider 会在首次使用时生成；只读 Secret 挂载下生成失败，网关会拒绝启动。Vault 将加密 root key 保存在 Vault 中，不使用这个文件挂载。

之后修改加密设置时：

- **开启加密**不会出问题：已有的明文值照常可读，新写入的值会加密。
- **关闭加密或换一把加密根密钥**后，已加密的值就读不出来了，所以要保持写入时的加密设置。
- **丢失加密根密钥**后，网关的数据库就无法读取。请按加密指南备份加密根密钥。

v0.5 网关用自己的密钥加密数据，这些数据会在首次启动时转换，见[迁移](#migration)。

## 扩展 {#scaling}

SQLite 供一台主机上的一套部署使用，不要跨主机共享目录，也不要让嵌入网关和独立网关共用。独立进程支持 `workers: 1–64`，Kubernetes 中建议 `workers: 1`，通过增加 Pod 扩容。嵌入网关遵循 Server 的 worker 和副本限制。

独立网关使用多副本时，必须把 **`storage.kernel` 和 `storage.management` 都设为 `redis`**，共享同一份 `storage.redis` 连接与 `storage.key_prefix`，并只读挂载同一份加密 root key。任一 provider 是 SQLite 时，chart 会拒绝多副本。下面是双副本的起点，需要合并你的 URL 和 Secret 设置：

```yaml
replicaCount: 2
existingSecret: gateway-credentials
config:
  capture_concurrency: 4
  storage:
    kernel: redis
    management: redis
    key_prefix: "ovgw:"
    redis:
      mode: standalone
      endpoints: ["redis://redis:6379"]
      password_env: OPENVIKING_GATEWAY_REDIS_PASSWORD
  encryption:
    enabled: true
    provider: local
```

无管理面、按密钥隔离的部署再加上 `management_api: false`、`openviking_keys: true`、`tenancy: key`、`default_upstreams` 和 `request_log_sink: stdout`，见[直接使用 OpenViking API 密钥](../gateway/09-gateway-keys.md#openviking-keys)和[部署默认上游](../gateway/07-upstreams.md#deployment-default-upstreams)。没有管理 API 时，在 OpenViking 中删除用户不会通知网关，该用户的网关数据按[保留期限](../gateway/11-security-and-data.md#retention)过期。部署在它前面的服务遵循[宿主集成约定](#host-integration-contract)。

Redis `mode: standalone` 用于单个端点，包括托管代理；`mode: cluster` 用 Redis Cluster 端点且数据库必须为 0。TLS 用 `rediss://`，端点 URL 中不要写凭证。`pool_size` 限制每个进程同时执行的 Redis 命令数，超出的命令等待空闲连接。保持 `pool_size >= capture_concurrency + 8`。Redis 位于鉴权路径，整体不可用时请求返回 HTTP 500；需要高可用时使用主从和自动切换，内存和持久化按 [Redis 内存策略](#redis-memory-policy)配置。

Redis 部署使用 `RollingUpdate`，配置 `maxUnavailable: 0`、`maxSurge: 1`；多副本时创建 `maxUnavailable: 1` 的 PDB。通过 `affinity` 把副本分散到不同节点。各进程缓存管理记录，所以修改管理配置或吊销签发密钥后，其他副本最多 **2 秒**后生效。

所有探针都不带凭证访问 `/health`，只返回本地状态，不调用 OpenViking 或 Redis。startupProbe 预算为 300 秒（`2 × 150`）。启动完成后，OpenViking 故障不会使 Pod 摘流。

停机预算是 `15s preStop + upstream_timeout_seconds + 最多 100s capture 排空`。chart 默认的 `terminationGracePeriodSeconds: 660` 覆盖 preStop 和一条持续到默认 600 秒上游超时的流；capture 尚未排空就被 Kubernetes 终止时，其他副本会在 lease 到期后接手。要让 capture 在 Pod 被终止前排空，把它调到 `15 + upstream_timeout_seconds + 100`（默认超时下为 715）。

### 宿主集成约定 {#host-integration-contract}

部署在无管理面网关前面的服务（例如为每个请求选择模型端点的服务）转发客户端请求时，设置这些请求头：

| 请求头 | 取值 |
| --- | --- |
| `X-API-Key` 或 `Authorization: Bearer` | 客户端的 OpenViking API 密钥，原样透传 |
| `X-OpenViking-Upstream-URL` | 选中的端点，必须与某个 `default_upstreams` 条目的 `base_url` 相同；其他值返回 403，且不发出任何请求。见[按请求指定上游](../gateway/07-upstreams.md#upstream-url-header) |
| `X-OpenViking-Upstream-Key` | 服务商 API Key，用于 `auth_mode: passthrough` 的上游 |
| 其他请求头 | 客户端自己的请求头，例如 `X-OpenViking-Session` 和 `X-OpenViking-Context`，原样透传 |

网关把请求转发给服务商之前，会去掉所有 `X-OpenViking-*` 请求头、客户端凭证和逐跳请求头。

## Redis 内存策略 {#redis-memory-policy}

为网关单独准备 Redis 实例，不要与 OpenViking 的 `expiry_redis_host` 定时器存储共用。从下面的 `redis.conf` 开始，按负载设置 `maxmemory`：

```conf
maxmemory 2gb
maxmemory-policy volatile-lru
appendonly yes
appendfsync everysec
```

`volatile-lru` 只驱逐本来就会过期的数据：对话状态、replay、待投递 capture 和 OpenViking 密钥记录。每类数据丢失的影响见 [Redis 对话状态](../gateway/11-security-and-data.md#redis-conversation-state)。不要使用 `allkeys-*`：它也会驱逐没有 TTL 的数据，导致签发的密钥、上游和上下文配置消失，已删除的对话状态可能重新出现，待投递 capture 不再被调度。`noeviction` 也能运行，但内存满后写入失败，请求无法获得记忆增强。

监控 `evicted_keys` 的增速和内存用量；容量充足时不会发生驱逐。网关不检查驱逐策略或其他任何 Redis 设置。观察 capture 积压可用 `ZCARD <key_prefix>v1:k:{cap}:ready`，它统计已调度或正在处理的 capture，其中可能有少量稍后才清理的过期条目。Redis Cluster 中所有 capture 数据位于同一个分片，应为 OpenViking 故障期间的积压预留该分片容量。capture lease 使用 Redis 时钟，调度时间则来自网关宿主，各宿主应通过 NTP 保持时钟同步。

未启用持久化时，Redis 重启会删除网关的全部管理数据：已签发的网关密钥失效，上游和上下文配置需要重新设置。AOF 让这些数据、待投递 capture 和删除操作在重启后保留。在 Redis 7.2 上，禁用的 `default` 用户也要保留命令权限（例如 `user default off ~* &* +@all`）：AOF 回放事务时会检查这些权限，缺少时回放的写入会因 `NOPERM` 失败。`appendfsync everysec` 仍可能丢失约一秒的写入，异步主从切换也可能丢失最近的写入。AOF 重写和快照期间需为写时复制预留内存。备份中可能保留其生成之后才删除的数据。

## 观测与例行检查 {#monitoring}

网关不导出 Prometheus 之类的监控指标。请求日志默认写入存储，可通过 Studio 的[概览和请求日志](../gateway/10-overview-and-requests.md#overview)标签页查看，或者调用管理 API 的 `/gateway/admin/overview` 和 `/gateway/admin/logs`。

在网关配置中设置 `request_log_sink: "stdout"`，或设置 `OPENVIKING_GATEWAY_REQUEST_LOG_SINK=stdout`，重启后新记录只写到 stdout。每个完成的模型请求和后台保存状态变更各产生一行 JSON，即使 stdout 接到管道也会立即刷新。每条记录包含 `time`（写入时的 Unix 秒数）、`account`、`user_id`（旧保存状态可能为 `null`），以及位于顶层的现有请求指标，如 `kind`、`status`、`input_tokens` 和 `cached_tokens`。不包含凭证、提示词或回复。采集系统负责这些记录的保留期限和访问权限；`log_retention_days` 只作用于存储中的日志。

当 `management_api: false` 关闭 Studio 和管理接口时，配置 stdout sink 并采集进程或容器日志进行观测。管理面开启但选择 stdout sink 时也采用同样流程：请求日志页和概览中的请求统计不会收到新记录。之前已存储的记录仍可见，直到过期。

从请求日志的顶层字段统计 reason 和首调缓存结果。对 capture 暂停（`kind: capture`）、队列增长、`openviking_http_429`、Redis 内存/驱逐和 HTTP 错误率设置告警。identify 拒绝发生在代理请求记录之前，所以从代理访问日志统计 401/402/403 和 Redis 故障引发的 500，具体 traceback 看网关 stderr。代理并发连接数可以近似表示在途流数。确认平台实际采集 stdout；只挂一个目录不会把它转成文件日志。

### 例行检查 {#day-to-day-operations}

- **进程存活：** `/health`。嵌入部署用的是 Server 的健康检查，独立部署用的是网关自己的。
- **网关健康：** **概览**标签页上的**首次调用缓存命中率**（[含义](../gateway/10-overview-and-requests.md#overview)），或从 stdout 指标计算：在同一账号和时间窗口内，筛选 `kind: "user"` 的记录，将 `first_upstream_cached_tokens` 的总和除以 `first_upstream_input_tokens` 的总和。某个字段缺失时，用该条记录的 `cached_tokens` 或 `input_tokens` 代替（未报告的用量按 0 计）。分母为零表示没有报告输入用量。这些字段位于顶层，不在嵌套的 `metrics` 对象中。命中率明显下降时，见[首次调用缓存命中率下降](../gateway/12-troubleshooting.md#the-first-call-cache-hit-rate-dropped)。
- **单个请求：** 在**请求日志**标签页展开对应的行，或按 `account`、`user_id` 和 `request_id` 查找 stdout 记录；见[请求日志](../gateway/10-overview-and-requests.md#requests)。后台保存事件的 `kind` 为 `capture`，通过哈希后的 `session` 标识对话。
- **对外地址：** `public_url` 仍是客户端访问的地址，见[对外地址](#public-address)。
- **备份：** SQLite 目录或 Redis 持久化/备份；开启加密时还有加密根密钥，见[备份与恢复](../gateway/11-security-and-data.md#backups)。

## 升级 {#upgrades}

- 升级前读发布说明。新版本的网关读不了旧的存储格式时，启动会报 `Unsupported gateway schema`，见[网关无法启动](../gateway/12-troubleshooting.md#the-gateway-does-not-start)。
- 独立网关要求 OpenViking Server 不低于 `min_server_version`（0.4.16），否则无法签发密钥，保存重试也会以 `openviking_version_mismatch` 失败；记忆检索也可能失败，显示为 `openviking_http_<status>`。两者分开升级时，先升级 OpenViking，再升级网关。嵌入网关和 Server 同版本发布，不做这项检查。
- 从 sidecar 或旧版独立网关升级，见[迁移](#migration)。

- SQLite 切换到 Redis 后，会话缓存从空开始。management 存储中的已签发 `ovgw_` 密钥、上游和上下文配置需要重建，没有提供迁移命令。使用 OpenViking key 时，可以通过部署默认值提供上游和上下文配置。
- Redis 键包含布局版本（`<storage.key_prefix>v1:`）。布局相同时，回滚可以继续读现有状态。将来布局版本变化时从空开始：会话数据按缓存处理，OpenViking key 记录在访问时重建，使用签发密钥的部署需要重新配置。不要假设存在跨版本数据迁移。
- 紧急停用时移除代理路由，或将网关 Deployment 缩到零。客户端可以把 Base URL 改回模型服务商，继续使用原模型凭证，此时没有网关记忆能力。`helm rollback` 的目标构建必须兼容当前配置字段和 Redis 布局。

## 从早期版本迁移 {#migration}

以下步骤适用于从早期版本的 sidecar 或独立网关迁移到嵌入网关。第 4 步和第 7 步的细节见列表后面的两张表。

1. **先备份。** 备份 `storage_path`（两个数据库，以及存在的话 `encryption.key`）。停止旧的网关进程或 sidecar，再启用嵌入网关。
2. **删掉旧字段。** 从 `ov.conf` 的 `gateway` 段删除 `host`、`port`、`workers`、`url`、`openviking_url`、`admin_token_env` 和 `encryption_key_env`。Server 会忽略这些字段并在启动时打印警告，旧的 `ov.conf` 仍能启动，但还是应该清理掉；`openviking-server init` → **Update gateway** 会自动删除它们。

   想继续独立运行网关，就运行 `openviking-gateway init`，或者把设置移到单独的 `gateway.conf`，去掉 `gateway` 外层和 `enabled`。独立网关的命令不再接受 `--openviking-url`、`--host`、`--port`、`--workers` 和 `--storage-path`，这些设置改写到 `gateway.conf` 或环境变量中。
3. **改对外地址。** 把 `public_url` 改为 Server 的对外地址（不带 `/gateway`），或者删掉。旧版快速开始把它设成了 `http://127.0.0.1:1935`，嵌入网关不再监听这个端口，接入指南和上传链接都会指向一个不存在的地址。`openviking-server init` → **Update gateway** 发现 `public_url` 指向别的端口时，会询问是否清除。
4. **改客户端地址和部署配置。** 网关的所有 URL 现在都在 `/gateway` 下，所以客户端要同时改端口和路径，旧路径不再提供服务，见[新地址](#new-addresses)。移除旧的 1935 Service 和 Ingress 路由、Compose 的 `gateway` 服务和 profile，以及 Helm 的 sidecar 配置 `gateway.workers`、`gateway.port`、`gateway.resources`。
5. **首次启动会转换已存数据。** v0.5 网关用自己的密钥加密数据。新版网关（嵌入或独立）首次启动时，用这把旧密钥解密，再按当前的[加密](#encryption)设置重新写入；加密关闭时写成明文，所以想让数据继续加密，要在这次启动前开启 OpenViking 加密。

   旧密钥从环境变量 `OPENVIKING_GATEWAY_ENCRYPTION_KEY` 读取，或者从 `storage_path` 下的 `encryption.key` 读取。`encryption_key_env` 会被忽略：如果你曾用它指向别的环境变量，这次启动时把密钥导出为 `OPENVIKING_GATEWAY_ENCRYPTION_KEY`。存在旧的加密数据、两处却都没有密钥时，网关拒绝启动，错误信息会写明这两个位置。用嵌入式 Helm chart 时，只在这次升级的版本中保留 `gateway.existingSecret` 里的 `encryption-key` 字段。独立 chart 在这次升级时通过 `extraEnv` 注入迁移变量。启动成功后旧密钥不会再被读取，可以删掉这个环境变量、Secret 字段和 `encryption.key`。
6. **移除共享 admin token。** `OPENVIKING_GATEWAY_ADMIN_TOKEN` **不会**被当作管理密钥。需要限制谁能修改上游和上下文配置时，显式设置 `management_key`，见[网关管理密钥](#gateway-management-key)。
7. **核对上游地址。** 首次启动时，网关会改写一次已存上游的 Base URL，让每个上游继续请求升级前的同一个地址，见[已存上游地址的变化](#upstream-address-rewrite)。在**上游**标签页对每个上游点**测试**，确认它仍然可用。
8. **了解日志和吊销的变化。** 请求日志新增用户列，没有用户的旧记录只出现在账号管理员和 root 的视图中。吊销密钥不再删除用户数据，显式删除和保留期照常生效。

### 新地址 {#new-addresses}

把 `host` 换成你的 Server 地址：

| 对象 | 迁移前 | 迁移后 |
| --- | --- | --- |
| OpenAI 兼容客户端 | `http://host:1935/v1` | `http://host:1933/gateway/v1` |
| Claude Code 的 `ANTHROPIC_BASE_URL` | `http://host:1935` | `http://host:1933/gateway` |
| 火山方舟和 BytePlus 方舟 SDK | 旧的网关地址 | `http://host:1933/gateway/api/v3` |
| 调用管理 API 的脚本 | `/api/v1/admin/gateway/...` | `/gateway/admin/...` |

1935 仍是独立运行的默认端口，独立网关也使用同样的 `/gateway` 路径。

### 已存上游地址的变化 {#upstream-address-rewrite}

| 上游 | 已存的 Base URL | 首次启动后 |
| --- | --- | --- |
| 火山方舟和 BytePlus 方舟的 Chat Completions、Responses 上游 | 只有域名 | 补上 `/api/v3` |
| 其他 Chat Completions、Responses 上游 | 路径不以 `/v1`、`/v4` 这类版本段结尾，包括纯域名和带路径前缀的地址 | 补上 `/v1`：`https://api.deepseek.com` → `https://api.deepseek.com/v1`，`https://openrouter.ai/api` → `https://openrouter.ai/api/v1`，或者挂在 `/litellm` 下的自建代理 |
| 火山方舟和 BytePlus 方舟的 Anthropic 上游 | 只有域名 | 补上 `/api/compatible` |
| Anthropic 上游 | 以 `/v1` 结尾 | 去掉 `/v1` |
| 任意上游 | 已经以其他版本段结尾，例如智谱的 `https://open.bigmodel.cn/api/paas/v4` | 保持不变。升级前它会被拼成 `…/v4/v1` 而请求失败，升级后按填写的地址直接可用。 |

新建和修改的上游按 [Base URL 的拼接规则](../gateway/07-upstreams.md#base-url)拼接，以 `/v1` 结尾的 Anthropic Base URL 会被拒绝。

## 下一步 {#next-steps}

- [在 Studio 中管理 OpenViking 网关](../gateway/06-manage-in-studio.md)：添加上游、创建上下文配置，签发第一批网关密钥。
- [安全与数据](../gateway/11-security-and-data.md)：了解网关存了什么、谁能访问，以及怎么备份和删除数据。
- [OpenViking 网关排障](../gateway/12-troubleshooting.md)：根据启动错误、设置卡片或失败的请求找到解决办法。
