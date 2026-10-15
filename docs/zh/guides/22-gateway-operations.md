---
description: 用 Docker Compose、Helm 或独立进程部署 OpenViking 网关，在 Studio 中管理网关并排查问题。
---

# OpenViking 网关部署与运维

本页写给运行 OpenViking 网关的人：怎么部署，怎么在 Studio 中管理上游、上下文配置和密钥，数据存在哪里、怎么备份，以及出了问题怎么查。网关能做什么、客户端怎么接入，见[OpenViking 网关](15-gateway.md)；整体结构见[一张图看懂架构](15-gateway.md#一张图看懂架构)。

网关目前处于 Beta 阶段，设置和接口可能随版本调整，升级前请先读发布说明。

## 环境要求

- **OpenViking 的认证方式**：`api_key` 或本地 dev 模式。网关不支持 trusted 认证。
- **OpenViking 版本**：嵌入运行时和 Server 同版本发布，无需额外检查；独立运行时要求 OpenViking Server 0.4.16 或更高版本。
- **安装**：网关包含在 OpenViking 核心包中。可选的 `gateway-fast` extra（`pip install "openviking[gateway-fast]"`）装上更快的事件循环和 HTTP 解析器。
- **登录身份**：远程部署需要 OpenViking API 密钥，普通用户、账号管理员和 root 都能登录 Studio 的网关页面。本地 dev 模式不需要密钥，固定使用 `default/default` 身份。
- **存储**：本地磁盘。网关的 SQLite 数据库只供一台主机使用，不支持网络文件系统或跨主机共享存储。

## 密钥和凭证

网关涉及四种密钥，各管一件事：

| 密钥 | 用途 | 在哪里使用 |
| --- | --- | --- |
| OpenViking API 密钥 | 登录 Studio；决定以哪个账号、哪个用户的身份读写记忆 | 管理请求的 `X-API-Key` 或 `Authorization: Bearer` |
| 网关密钥（`ovgw_…`） | 客户端接入；每把绑定一位用户、一份上下文配置和一组上游 | 客户端的 API Key 输入框 |
| 网关管理密钥（可选） | 限制谁能修改上游和上下文配置 | `gateway.management_key`；请求头 `X-OpenViking-Gateway-Management-Key` |
| 上游 API Key | 调用模型服务商 | 上游编辑页 |

网关没有自己的加密密钥，本地存储按 OpenViking 的加密配置加密，见[加密](#加密)。

### 网关管理密钥

不设置管理密钥时，账号内每个登录的用户都能添加和修改本账号的上游和上下文配置。设置之后，只有带上这把密钥的请求能修改它们，OpenViking 的 admin 和 root 也不例外；其他人只能查看、测试，并在签发密钥时选用。管理密钥不限制谁能登录，也不限制用户给自己签发网关密钥。完整的权限规则见[权限矩阵](../api/25-gateway.md#凭证和账号范围)。

设置方法：

- **`openviking-server init`**（嵌入）：远程部署回答 **Allow users to configure their own gateway upstreams?** 时选 no，init 会生成一个以 `ovgm_` 开头的值，打印出来并写入 `ov.conf` 的 `gateway.management_key`。
- **`openviking-gateway init`**（独立）：回答 **Require a management key to change upstreams and profiles?** 时选 yes，init 会生成一个 `ovgm_` 开头的值，只打印一次，并写入 `gateway.conf` 的 `management_key`。
- **手动设置**：值至少 32 个字符，可以用 `openssl rand -base64 32` 生成。写进 `management_key`，或者设置环境变量 `OPENVIKING_GATEWAY_MANAGEMENT_KEY`；两种部署下环境变量都优先于配置文件，见[配置参考](#配置参考)。
- **Compose 和 Helm**：Compose 会把 `OPENVIKING_GATEWAY_MANAGEMENT_KEY` 传进容器；Helm 从 `gateway.existingSecret` 指定的 Secret 中读取 `management-key` 字段。见 [Docker Compose](#docker-compose) 和 [Helm](#helm)。

管理密钥不提供命令行参数，因为命令行参数会出现在进程列表和 shell 历史里。设置、更换或去掉管理密钥后需要重启，这不会吊销已签发的网关密钥，也不会改动已有的上游和上下文配置。

### 加密

网关用 OpenViking 自己的加密功能保护已存的凭证和对话状态（见[加密指南](08-encryption.md)）：

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

加密关闭或没有这个块时，网关以明文存储，只靠文件权限保护。之后再开启加密不会出问题：已有的明文值照常可读，新写入的值会加密。反过来，关闭加密或换一把根密钥后，已加密的值就读不出来了，所以要保持写入时的加密设置。按加密指南备份根密钥，丢了它，网关的数据库就无法读取。

v0.5 网关用自己的密钥加密数据，这些数据会在首次启动时转换，见[迁移](#迁移)。

## 部署

### 部署方式

| 方式 | 怎么开启 | 客户端地址 |
| --- | --- | --- |
| 嵌入 Server（包括 Compose 和 Helm） | 在 `ov.conf` 中开启 `gateway.enabled` | Server 地址加 `/gateway`，默认 `http://127.0.0.1:1933/gateway` |
| 独立运行 | 先 `openviking-gateway init`，再 `openviking-gateway` | 网关自己的地址加 `/gateway`，默认 `http://127.0.0.1:1935/gateway` |

两种方式都在 `/gateway` 下提供相同的模型路径和管理路径。嵌入部署不需要第二个进程，也不需要按路径分流的反向代理；独立部署不用改动已有 OpenViking 服务的配置。

### 在 Server 中启用

运行 `openviking-server init`，在 **Enable OpenViking Gateway on the same port?** 处回答 yes，然后启动或重启服务。已有配置时，在菜单中选择 **Update gateway**。远程部署还会询问是否允许用户自行配置上游，见[网关管理密钥](#网关管理密钥)。

也可以手动把下面的内容合并进 `ov.conf`，再重启：

```json
{
  "gateway": {
    "enabled": true
  }
}
```

使用 API 密钥的服务端上，root 也可以在 Studio 的 OpenViking 网关页面点**开启网关**：Studio 会把 `gateway.enabled: true` 写入 `ov.conf` 并重启服务（从 Studio 重启要求服务端只有一个 worker，否则 Studio 只保存文件，需要你自行重启）。

打开服务端的 `/studio/`，在侧边栏选择 **OpenViking 网关**。dev 模式下，如果默认账号还没有上游，网关启动时会导入服务端可用的 VLM 凭证作为上游，并创建 `default` 上下文配置。导入的 Coding Plan 上游需要确认后才能使用；没有可导入的凭证时，请手动添加上游。

**让 Agent 修改配置。** 持有 root API 密钥的 Agent 可以通过[运行时配置接口](../api/08-admin.md#runtime-configuration)修改 `ov.conf` 并重启服务。独立网关不提供这组接口。

### 路由

两种部署方式下，网关的所有 URL 都在 `/gateway` 下。嵌入部署中，Server 把整个 `/gateway` 前缀交给网关，其他路径仍由 Server 自己处理：

| 路径前缀 | 用途 |
| --- | --- |
| `/gateway/v1/` | 模型 API 和模型列表 |
| `/gateway/api/v3/` | 方舟兼容客户端 |
| `/gateway/api/compatible/v1/` | 方舟 Anthropic 兼容客户端 |
| `/gateway/uploads` | 工具上传 |
| `/gateway/admin/` | 管理 API，需要认证 |

因此客户端使用下面的 Base URL，把 `http://127.0.0.1:1933` 换成你的 Server 地址即可：

| 客户端 | Base URL |
| --- | --- |
| Claude Code 和 Anthropic SDK | `http://127.0.0.1:1933/gateway` |
| OpenAI 兼容的客户端和 SDK，包括 Codex、OpenCode、pi 和 Open WebUI | `http://127.0.0.1:1933/gateway/v1` |
| 火山方舟和 BytePlus 方舟 SDK | `http://127.0.0.1:1933/gateway/api/v3` |

`/health` 仍是 Server 自己的健康检查，嵌入网关不单独提供 `/health`。

独立网关在自己的端口上提供同样的 `/gateway` 路径，另外在根路径提供：

- `/` 跳转到 `/studio/`，Studio 只包含网关和连接页面。
- `/health` 返回 `service: "openviking-gateway"`、`auth_mode` 和 `openviking_url`；请求带有效的 OpenViking 凭证时，还返回调用方身份。

**对外地址。** 共享部署要把 `public_url` 设为客户端访问 Server（或独立网关）的地址，不带 `/gateway`，网关会自己加上。嵌入网关不设置时，沿用 Server 的对外地址（`OPENVIKING_PUBLIC_BASE_URL` 或 `server.public_base_url`）。Studio 的接入指南用它生成配置：Anthropic 客户端使用 `<public_url>/gateway`，OpenAI 风格的客户端使用 `<public_url>/gateway/v1`。不设置时，指南使用浏览器访问 Studio 的地址；但文件导入的上传链接必须依赖 `public_url`，见[导入文件和技能](#导入文件和技能)。

### Docker Compose

在 `.env` 中设置 `OPENVIKING_GATEWAY_ENABLED=true`，然后运行 `docker compose up -d`；也可以在挂载的 `ov.conf` 里开启 `gateway.enabled`。网关在 `openviking` 服务内运行，不需要单独的服务或 profile。不用 Compose 时，`docker run` 加上 `-e OPENVIKING_GATEWAY_ENABLED=true` 即可：

```bash
docker run -d -p 1933:1933 -v ~/.openviking:/app/.openviking \
  -e OPENVIKING_GATEWAY_ENABLED=true \
  ghcr.io/volcengine/openviking:latest
```

容器内 Server 监听 `0.0.0.0`，所以 `ov.conf` 要使用 API 密钥认证。

- **管理密钥**：在 `.env` 中设置 `OPENVIKING_GATEWAY_MANAGEMENT_KEY`，自带的 Compose 文件会把它传进容器；也可以写进挂载的 `ov.conf` 的 `gateway.management_key`。Compose 文件还会传入 `OPENVIKING_GATEWAY_ENCRYPTION_KEY`，它只在迁移 v0.5 数据时读取一次，见[迁移](#迁移)。
- **对外地址**：设置 `OPENVIKING_PUBLIC_BASE_URL`（自带的 Compose 文件会传入）即可，网关没有单独配置 `public_url` 时沿用它。
- **修改配置后**：执行 `docker compose restart openviking`；启动错误用 `docker compose logs openviking` 查看。

自带的 Caddy 把所有路径转发到 `openviking:1933`。可以继续用兼容端口 1934，也可以直接访问 Server 的 1933。配置 HTTPS 时，一条 `reverse_proxy openviking:1933` 就能承载 Studio、管理接口和模型流量，见[公网访问](12-public-access.md)。

### Helm

在 chart values 中设置 `gateway.enabled: true`。网关嵌入 server 容器，数据保存在 `<persistence.mountPath>/gateway`，Service 和 Ingress 都使用 Server 端口。保持 `replicaCount: 1`。

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
- 模型请求是流式的：Ingress 要关闭响应缓冲，允许至少 32 MiB 的请求体和 600 秒超时。

NGINX annotations、创建 Secret 的命令和从 sidecar 版本升级的说明见 [chart README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md#openviking-gateway)。

### 独立部署

独立网关不读取 `ov.conf`，只需要知道 OpenViking 的地址，已有的开源版或商业版服务不用改配置：

```bash
openviking-gateway init   # 写入 ~/.openviking/gateway.conf
openviking-gateway
```

`openviking-gateway init` 依次询问 OpenViking 地址（会实际访问一次，并显示服务端的认证模式和版本）、监听地址（默认 `127.0.0.1:1935`），以及是否要求[管理密钥](#网关管理密钥)。结果写入 `~/.openviking/gateway.conf`，或者 `--config` 指定的文件，只有你自己能读。想修改设置就再运行一次：它把当前值作为默认值，已有的密钥不改就原样保留。生成的文件类似这样：

```json
{
  "openviking_url": "https://ov.example.com",
  "host": "127.0.0.1",
  "port": 1935
}
```

打开网关的 `/studio/`，用 OpenViking 用户、账号管理员或 root 的 API 密钥登录。连接页会显示网关连接的 OpenViking 地址。独立网关读不到远端服务的 VLM 配置，所以不能导入模型配置，上游要手动添加。

**设置从哪里来。** 命令行只有 `--config` 一个参数。设置了 `--config` 或 `OPENVIKING_GATEWAY_CONFIG_FILE` 时，它必须指向已存在的文件；两者都没设置时，网关读取 `~/.openviking/gateway.conf` 和 `/etc/openviking/gateway.conf` 中第一个存在的文件。环境变量可以覆盖其中任何字段。容器里可以完全不用文件：设置 `OPENVIKING_GATEWAY_OPENVIKING_URL`，再按需加上 `OPENVIKING_GATEWAY_HOST=0.0.0.0` 之类的变量。两处都没有 OpenViking 地址时，网关退出，并提示运行 `openviking-gateway init` 或设置 `OPENVIKING_GATEWAY_OPENVIKING_URL`。全部字段和覆盖规则见[配置参考](#配置参考)。

**启动检查。** 独立网关启动前会先访问一次 OpenViking：

- OpenViking 使用 trusted 认证时，网关拒绝启动。
- 绑定非回环地址时，必须确认 OpenViking 使用 `api_key` 认证才会启动；OpenViking 连不上或处于 dev 模式时，网关直接退出。请先启动 OpenViking，或者只绑定 `127.0.0.1`。
- 跨主机连接 OpenViking 时，请使用 TLS 或私有网络。

**连接公共 OpenViking 服务。** 连接 `https://api.vikingdb.cn-beijing.volces.com/openviking` 且没有设置管理密钥时，启动日志和 Studio 都会显示 `public_openviking_without_management_key` 告警：任何持有该服务 API 密钥的人都能登录这台网关，并修改所在账号的上游。网关只给你自己或团队使用时，设置 `OPENVIKING_GATEWAY_MANAGEMENT_KEY` 后重启即可限制修改；这不限制谁能登录，也不限制他们给自己签发网关密钥。

### 反向代理和 HTTPS

把整个站点转发到 Server（嵌入）或网关（独立），不需要按路径分流。以 Caddy 为例：

```caddyfile
ov.example.com {
    reverse_proxy 127.0.0.1:1933 {
        flush_interval -1
    }
}
```

无论用哪种代理，都要满足三点：关闭响应缓冲，流式输出才能实时到达；允许至少 32 MiB 的请求体；读取超时至少 600 秒。另外，不要在代理日志中记录 `/gateway/uploads` 的查询字符串，其中带有一次性上传令牌。对外发布前先配置好认证，见[公网访问](12-public-access.md)。

### 扩展

一个存储目录只供一台主机上的一套部署使用，嵌入网关和独立网关不能共用同一目录。嵌入网关跟随 Server 的 worker 设置；独立网关用 `workers` 设置，取值 1–64。需要在服务商之间负载均衡、故障转移或限流时，把 LiteLLM 这类服务添加为上游。

## 在 Studio 中管理网关

用 OpenViking API 密钥连接 Studio 后，在侧边栏打开 **OpenViking 网关**。上游和上下文配置由整个账号共享，网关密钥和请求日志属于各个用户：

- **没有设置管理密钥**：每个登录的用户都能添加和修改本账号的上游和上下文配置。
- **设置了管理密钥**：“上游”和“上下文配置”标签页变为只读，OpenViking 的 admin 和 root 也一样。只读时仍能查看上游（看不到 API Key 和请求头），并在给自己签发密钥时选用；模型测试会真实请求服务商并可能计费，因此需要写权限。点**输入网关管理密钥**解锁编辑和测试，页头会显示“可修改上游和上下文配置”；解锁状态保持到点击**锁定**或关闭浏览器标签页。管理密钥被更换后，Studio 会自动锁定。
- **网关密钥**：每个人都能给自己签发；账号管理员可以给本账号的用户签发；root 先在侧边栏切换账号。见[网关密钥](#网关密钥)。

上游和上下文配置保存后，立即对使用它们的密钥生效（上下文配置只影响之后开始的对话）。还没有可用的上游或上下文配置、自己又不能修改时，请联系部署网关的人。

### 上游

上游是模型服务的一个接口，只使用一种 API：Anthropic Messages、Chat Completions 或 Responses。网关不在 API 之间转换，所以客户端用到几种 API，就要添加几个上游，即使它们来自同一家服务商：Claude Code 用 Anthropic Messages，Codex CLI 用 Responses，大多数聊天应用和 SDK 用 Chat Completions。

在编辑页选择服务商后，会自动填入它的默认 Base URL：

| 服务商 | 协议 | 默认 Base URL |
| --- | --- | --- |
| 通用 | 三种都有 | 无，自己填写 |
| Anthropic | Anthropic Messages | `https://api.anthropic.com` |
| OpenAI | Chat Completions、Responses | `https://api.openai.com/v1` |
| DeepSeek | 三种都有 | `https://api.deepseek.com`；Anthropic Messages 为 `https://api.deepseek.com/anthropic` |
| 火山方舟 | 三种都有 | Chat Completions 和 Responses 为 `https://ark.cn-beijing.volces.com/api/v3`；Anthropic Messages 为 `https://ark.cn-beijing.volces.com/api/compatible` |
| BytePlus 方舟（海外站） | 三种都有 | Chat Completions 和 Responses 为 `https://ark.ap-southeast.bytepluses.com/api/v3`；Anthropic Messages 为 `https://ark.ap-southeast.bytepluses.com/api/compatible` |

#### 接口

- **服务商**决定可选的协议，并让网关适配各家的差异。列表里没有的服务商、LiteLLM 和 new-api 这类兼容代理，以及 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 这类把订阅账号包装成 API 的反向代理，都选*通用*（见[自定义上游](15-gateway.md#自定义上游)；用订阅额度是否合规请自行确认）。
  - *通用*、*Anthropic* 和 *OpenAI* 转发请求的方式相同。
  - *DeepSeek*：请求带工具时，DeepSeek 要求历史中之前每条回复都带推理内容，而很多聊天应用不回传它。DeepSeek 上游默认开启**补全推理内容回传**（见下文“路由和开关”），所以请求保持思考模式也能用 OpenViking 工具。关闭这项后，只有请求关闭了思考模式（`"thinking": {"type": "disabled"}`）才提供 OpenViking 工具；标准 Responses 请求没有 `thinking` 字段，这时也得不到工具。记忆照常补充。
  - *火山方舟*和 *BytePlus 方舟*行为相同：Chat Completions 和 Responses 的每段对话使用一个固定的 `prompt_cache_key`，方舟的前缀缓存就能跟着对话走。请求的模型、思考模式、采样参数、系统提示词或工具和对话的第一个请求不同时，方舟会重建缓存，请求被标为**缓存参数有变化**（`ark_cache_parameters_changed`）。
- **协议**决定上游接受哪种 API。Anthropic 上游同时发送 `x-api-key` 和 `Authorization: Bearer`，Chat Completions 和 Responses 上游只发送 Bearer。
- **Base URL** 按服务商 SDK 文档填写，网关在后面拼接端点：
  - Chat Completions 和 Responses 拼接 `/chat/completions` 或 `/responses`。只填域名时不会自动补 `/v1`，OpenAI 要写 `https://api.openai.com/v1`。
  - Anthropic Messages 拼接 `/v1/messages`，所以 Base URL 不能以 `/v1` 结尾，否则保存时被拒绝。
  - 末尾的斜杠会被去掉。`/api/paas/v4` 这类带版本路径的地址可以直接使用。
  - 客户端用方舟路径（`/gateway/api/v3/`、`/gateway/api/compatible/v1/`）发来的请求，按同样的规则拼接。
  - 一个上游只有一个 Base URL。同一服务商的不同协议地址不同时，要分别建上游：例如智谱 Chat Completions 用 `https://open.bigmodel.cn/api/paas/v4`，Responses 用 `https://open.bigmodel.cn/api/v1`。方舟 Coding Plan 的 Chat Completions 和 Responses 用 `…/api/coding/v3`，Anthropic Messages 用 `…/api/coding`。
- **拼接协议接口路径**默认开启。如果 Base URL 已经是完整的请求地址，可以关闭它；关闭后，网关的所有上游请求都直接使用该 URL，不再拼接协议路径。

  编辑页会实时预览最终的请求地址；OpenAI、方舟这类地址漏掉路径，或者 Anthropic 地址多了 `/v1` 时，会给出提示和一键修正。

#### 凭证

- **API Key 由谁提供**：
  - **由网关保管 API Key**（默认）：网关发送上游的 API Key，客户端只需要网关密钥。
  - **每个客户端自带 API Key**：每个请求除了网关密钥，还要在 `X-OpenViking-Upstream-Key` 请求头里带上客户端自己的服务商 API Key，缺少时返回 401 "Upstream API key is missing"。网关按服务商惯用的请求头转发这个 API Key，`X-OpenViking-*` 请求头本身从不转发。
- **API Key 和额外请求头只写不读**：留空表示保留已存的值，删除请求头行就删除这个请求头。**修改 Base URL 会清除已存的 API Key 和请求头值**，所以每次改地址都要重新填写，已存的密钥不会被发往新地址。
- **额外请求头**加在发往这个上游的每个请求上，例如服务商的版本或组织请求头，并覆盖客户端发送的同名请求头。不能设置 `Host`、`Content-Length`、`Transfer-Encoding` 和 `Connection`。
- **这是 Coding Plan 或订阅密钥**：服务商通常只允许 Coding Plan 这类订阅密钥用在自家的编程工具里，通过共享网关使用可能导致订阅被封禁。勾选后，发往这个上游的请求一律被拒绝，返回 403 "Coding Plan upstreams are disabled; configure a model API key"，除非同时勾选**允许通过网关使用这个套餐**。网关无法从密钥本身分辨它是不是订阅密钥，需要你自己标记。Claude 订阅令牌（`sk-ant-oat…`）始终被拒绝。

#### 模型

- **模型**列出这个上游提供的模型名，留空表示接受任何名称。只有列出的名称和别名会出现在模型列表（`/gateway/v1/models`）里。
- **模型别名**把客户端使用的名称映射为发给上游的模型，例如把 Claude Code 请求的 Claude 模型名映射到服务商提供的模型。别名出现在模型列表中，请求日志显示客户端使用的名称。
- **上下文窗口**按模型记录窗口大小（token，至少 1,024），键是映射之后、实际发给上游的模型名。长对话按窗口的比例触发压缩，窗口小于 1,000,000 token 的模型都要列出来，见[长对话](#长对话)。

#### 路由和开关

- **优先级**：对每个请求，网关在密钥绑定的已启用上游中，找出使用这种 API 并提供所请求模型的上游，选优先级最高的一个。对话一旦开始，只要原来的上游还能服务就一直用它，所以修改优先级只影响新对话。
- **启用**：停用的上游不接收请求，也不出现在模型列表里。原本使用它的对话会转到下一个候选上游，造成一次服务商缓存未命中，并丢掉之前的 Claude 思考块，这些请求被标为**上游已切换**（`upstream_changed`）。想保留恢复的余地，就停用而不是删除。
- **允许 OpenViking 工具**：决定能否通过这个上游提供 OpenViking 工具。关闭后立即生效，已经带工具的对话也受影响。
- **补全推理内容回传**：客户端回传历史时常常丢掉模型之前回复里的推理内容。开启后，网关记下经它转发的每条回复的推理内容，在后续请求中放回原处：Chat Completions 补回 `reasoning_content`，Anthropic Messages 放回 thinking 块，Responses 把带明文内容的 reasoning 项放回对应条目之前。补回的内容每次逐字节相同，服务商的提示缓存照常命中，但计入输入 token。客户端自己回传了推理内容的回复保持不变。DeepSeek、火山方舟和 BytePlus 方舟默认开启，其他服务商默认关闭。有些请求即使开启也补不回推理内容：对话换了上游（`upstream_changed`）、Claude 对话丢了记忆记录（`missing_injection_record`），或者客户端把思考内容当作正文发回；DeepSeek 上游的这类请求同样得不到 OpenViking 工具。
- **最小可缓存长度**（仅火山方舟和 BytePlus 方舟）：模型能缓存的最短提示词长度，只影响请求日志如何报告缓存资格。

除了优先级，其他上游设置都立即生效，进行中的对话也一样。网关不做故障转移：选中的上游出错时，客户端收到这个错误；服务商无法访问时返回 502 "Model upstream is unavailable"。

#### 测试和删除

有写权限时，在上游列表里点**测试**，网关先检查已保存配置的连接。连接成功后，再选择或输入模型名运行模型测试。网关使用已存的凭证发送一次很小的生成请求，验证接口地址、密钥、模型名和协议能否一起正常工作。上游允许 Gateway 工具时会带上 OpenViking 工具，reasoning effort 使用模型默认值。服务商可能对模型请求计费。Coding Plan 上游在授权网关使用前仍会阻止模型测试。各种结果的含义见[上游测试](../api/25-gateway.md#上游测试)。

有密钥在用的上游不能删除：先吊销这些密钥，或者改为停用这个上游。

### 导入 OpenViking 模型配置

嵌入部署中，**使用 OpenViking 的模型配置**（上游页头的**从 OpenViking 导入**）把服务端的 VLM 凭证导入为当前账号的上游。没有设置管理密钥时只有 root（包括本地 dev）能导入；设置了管理密钥时需要先解锁。

- 结果分三组显示：已导入、需要确认、未导入。路径中带 `coding` 或 `plan` 的上游被标为 Coding Plan，确认服务商条款允许后，勾选**允许通过网关使用这个套餐**才能使用。
- 重复导入不会产生重复的上游。再次导入时，地址、API Key、请求头、协议和服务商按服务端配置刷新；你在网关里改过的名称、启用状态、优先级、模型、别名、上下文窗口和 Coding Plan 确认都保留。
- Codex OAuth 和 Azure 凭证、缺少 `api_base` 的 LiteLLM 凭证，以及不使用 OpenAI Chat Completions API 的 LiteLLM 路由会被跳过，原因见[导入 VLM 配置](../api/25-gateway.md#导入-vlm-配置)。

### 上下文配置

上下文配置是一组有名字的记忆设置。每个网关密钥使用一份，一份可以供多个密钥使用。比如准备一份“Coding”配置，召回记忆、资源和技能；再准备一份“Chat”配置，只召回记忆，预算也小一些。

“上下文配置”标签页为每份配置显示一张卡片，概括各部分的设置和使用它的密钥数。标签页为空时，**使用推荐设置创建**一键创建一份名为“默认”的配置，**自定义**打开编辑页；之后用**新建配置**。有密钥在用的配置不能删除；想做一份变体，用**复制一份**最快。

> **注意**：保存的修改只对之后开始的对话生效。对话在第一个请求时记下当时的上下文配置并一直沿用，因为中途改变模型看到的内容会破坏服务商缓存。每次聊天都新开对话的客户端很快就能用上新设置；Claude Code、Codex 以及发送固定会话请求头的客户端会一直沿用旧设置，直到开始新对话，或者这段对话闲置满 30 天（`session_ttl_days`）。

| 部分 | 作用 | 主要设置和默认值 |
| --- | --- | --- |
| 召回记忆 | 用每条新的用户消息检索 OpenViking，把相关内容附加到这条消息上。 | 检索范围：记忆、资源、技能 · 单条消息预算：1,600 token · 单个上下文窗口预算：30,000 token · 相关度阈值：0.35 · 超时时间：2 秒 |
| 保存对话 | 把完成的轮次写入 OpenViking 会话并提交，供 OpenViking 提取记忆。 | 最新回复等待时长：600 秒 · 提交阈值：20,000 token · 保留最近消息：10 |
| 长对话 | 对话接近上下文窗口时，让同一个模型写摘要，替换之前的消息。 | 压缩：开启 · 压缩时机：窗口的 0.9 · 摘要长度上限：8,000 token · Agent 自管上下文窗口：关闭 |
| OpenViking 工具 | 让模型在回答时使用 OpenViking 的工具。 | 开启 · 只勾选只读工具，见 [OpenViking 工具](#openviking-工具) |

每个部分都有自己的开关，长对话部分的压缩和 Agent 自管上下文窗口各有一个。召回和保存相互独立，只召回不保存、只保存不召回都可以。

**召回的细节：**

- **会话开头提供用户画像**独立于召回。提供 `read` 工具时，开头还提供记忆和技能目录。**开头内容预算**默认 4,000 token，技能最多占四分之一，设为 0 时全部省略；这部分不占召回预算。
- 召回内容由 OpenViking 在预算内选取和排版，也可能只给出 URI，供模型之后读取。
- 检索文本是去掉客户端噪声后的用户消息，最多取**检索文本长度**（8,000）个字符。少于 3 个字符的消息不检索。
- 每条消息最多补充**单条消息预算**和**单个上下文窗口预算**剩余额度中较小的那个；剩余额度不足 64 token 时跳过检索。无论网关还是客户端压缩了对话，单个上下文窗口预算都重新计算。预算设为 0 就等于关闭召回。这里的 token 数是偏保守的估算，不是服务商的计费数。
- OpenViking 在**超时时间**内没有返回时，这条消息不带记忆发给模型，之后也不补，客户端重试也一样。
- **按分类限量**（在“高级设置”中）可以给事件、实体、偏好、经验、资源和技能六类分别设条数上限，只检索上限大于 0 的类别。关闭时，所有来源放在一起排序。

**召回摘要。** 开启**显示召回摘要**（默认关闭）后，每条新用户消息的回复以一段简短摘要开头，说明网关补充了什么：

```text
> OpenViking context: user profile, memory index, skill list, earlier sessions
> OpenViking recall: 4 items (3 memories, 1 resource) — booking_duplicate_handling, user_lang_pref, +2 more
```

- `context` 行只出现在对话的第一条回复里，列出开头实际提供的内容：用户画像、记忆目录、技能目录，以及这段对话较早部分在 OpenViking 中的位置。
- `recall` 行按类别统计召回条数，最多列出三个条目名。检索失败时改为说明原因，例如 `> OpenViking recall failed: OpenViking unavailable`。没有找到内容、召回已关闭或预算用完时不显示，所以第一条回复之后，这类消息没有摘要。
- 工具步骤等后续请求不显示摘要；客户端重试同一条消息时，摘要和第一次相同。
- 摘要在 Anthropic Messages 中单独占一个文本块，在 Responses 中单独占一条助手消息，在 Chat Completions 中位于回复文本开头。要求结构化输出的请求，以及 `n` 大于 1 的 Chat Completions 请求，不显示摘要。
- 模型看不到摘要：客户端随下一条消息把回复发回来时，网关先删掉摘要再转发，也不把它保存到 OpenViking。

**保存的细节：**

- 下一条用户消息到达时，上一轮就会保存。**最新回复等待时长**决定网关等多久之后保存最后一轮并提交会话，这样短对话也能被提交。
- **提交阈值**：会话中待提交的内容达到这么多 token 时提交一次。每次提交后，OpenViking 在后台提取记忆。
- **保留最近消息**：提交后，会话中保留最新的若干完整轮次，至少包含这么多条消息。

每项设置的 API 名称和取值范围见[配置参考](#配置参考)。

### 网关密钥

网关密钥（`ovgw_…`）是客户端用来代替服务商 API Key 的凭证。每把密钥属于一位 OpenViking 用户，使用一份上下文配置和一组上游。

在“密钥”标签页点**签发密钥**，依次填写：

- **名称**：方便日后辨认，例如“alice · Claude Code”。
- **给谁用**：普通用户只能给自己签发；账号管理员可以选本账号的用户；root 先在侧边栏切换账号。网关会从 OpenViking 读取所选用户的 API 密钥；OpenViking 只保存密钥哈希时读不出来，Studio 会显示**粘贴用户 API 密钥**输入框，填入该用户当前的密钥。root API 密钥不能绑定到网关密钥。本地 dev 模式下，密钥以 `default` 用户的身份读写记忆。
- **上下文配置**。
- **上游**：至少一个，密钥只能访问选中的上游。
- **允许的模型**（可选）：留空表示允许这些上游的全部模型。这里填客户端发送的名称，包括别名。请求其他模型返回 403 "Model is not allowed by this key"，模型列表也只显示允许的名称。

签发前，网关会向 OpenViking 核实这位用户的密钥。粘贴的是 root 密钥、其他账号的密钥或无效密钥，或者 OpenViking 连不上时，签发失败，见[签发密钥失败](#签发密钥失败)。

签发成功后，**复制网关密钥**对话框显示完整密钥，这是唯一一次：网关只保存它的哈希和开头几个字符。对话框里还有 Claude Code、Codex CLI 和聊天客户端的配置，已填好真实密钥和网关地址，可以直接粘贴。密钥丢了就重新签发。

- **密钥不能编辑。** 要更换上下文配置、上游、允许的模型或用户，就签发新密钥，再吊销旧的。
- **对话属于用户，不属于密钥。** 客户端发送相同的会话 ID 时，同一用户的新密钥会接着原来的对话，换密钥不会打断对话。
- **吊销**后，这把密钥立即不能再用；用户的对话状态和日志保留，直到被显式删除或过期。
- **OpenViking 密钥变了。** 用户的 OpenViking API 密钥重新生成或用户被删除后，OpenViking 会拒绝网关保存的旧密钥。网关在随后的请求中发现这一点（最多延迟约 30 秒），删除绑定它的网关密钥，对话状态和日志保留。请用当前的 OpenViking 密钥重新签发。OpenViking 暂时连不上或出错时，网关不删除密钥，请求照常发给模型，只是不带记忆。
- **每人一把密钥。** 共用一把密钥的人共享其 OpenViking 用户的记忆。请给每人签发一把；想让不同客户端使用不同的上下文配置，再按客户端分开签发。

账号管理员和 root 可以在密钥页使用**删除该用户的网关数据…**：吊销该用户的全部网关密钥，删除网关为该用户保存的对话状态（包括尚未保存到 OpenViking 的内容）和请求日志。已存入 OpenViking 的记忆不受影响，见[安全与数据](#安全与数据)。

### 请求日志和概览

普通用户只看到自己的请求，账号管理员和 root 看到整个账号的请求。

**概览**标签页汇总日志保留期（默认 30 天）内最新的 10,000 条请求：

- 第一个请求到达之前，显示**快速开始**清单，顺序是“上游 → 上下文配置 → 签发密钥 → 接入”。
- **请求数**：模型请求的数量、最近一次请求的时间，以及这些请求的输出 token。
- **首次调用缓存命中率**：每条新用户消息的第一次模型调用中，命中服务商提示缓存的输入 token 所占比例。这是判断网关是否健康最重要的指标，应该接近不经过网关时服务商能达到的水平；明显下降通常说明网关发送的历史和上一次对不上了，见[首次调用缓存命中率下降](#首次调用缓存命中率下降)。
- **轮内缓存命中率**：同一轮里后续调用的命中率，例如工具步骤和 OpenViking 工具的往返，通常很高。
- **召回的记忆条数**：补充到新消息中的条数，以及每次检索 OpenViking 的平均耗时（只统计实际检索了的消息）。
- **OpenViking**：已连接、异常或启动中，附带版本；异常时给出原因。OpenViking 连不上期间，请求照常发给模型，只是不带记忆。卡片中的**保存对话**一栏统计正在重试或已暂停保存的对话数。
- **降级请求**：每种异常出现的次数和说明，见[降级请求的异常](#降级请求的异常)。
- **最近请求**，以及前往“请求日志”标签页的链接。

**请求日志**标签页按时间倒序列出最新的 1,000 条记录，停留在第一页时每 30 秒刷新一次。可以按**全部**、**新消息**、**工具步骤**或**异常**筛选，也可以按请求类型筛选，或者按模型、对话或密钥搜索。

各列依次是类型、模型（客户端发送的名称）、服务商返回的 HTTP 状态、token（输入、缓存占比、输出）、**记忆**、**保存**状态和**异常**。**记忆**列中，`+3` 表示这次检索补充了 3 条，并附检索耗时；`↺ 4` 表示之前 4 条消息补充的记忆被原样放回。展开一行可以看到对话 ID、上游、密钥、耗时、token 明细、记忆检索结果、上下文窗口用量（以及是否压缩或开启了新窗口）、保存状态和下次重试时间、OpenViking 工具详情；有异常的请求还会说明发生了什么、怎么处理。

只有到达模型服务商的请求才会记录。被网关先行拒绝的请求不记录，例如密钥无效、模型不在允许范围内、没有匹配的上游、请求体超限，这些情况下客户端直接收到错误。

| 类型 | 含义 | 记忆 |
| --- | --- | --- |
| 新消息 | 最后一条是新用户消息的请求 | 检索一次，并放回之前的记忆；这一轮会被保存 |
| 工具步骤 | 同一轮的延续，例如带着工具结果继续 | 放回之前的记忆；随所在轮次一起保存 |
| 辅助请求 | 生成标题、摘要、压缩、建议和权限检查的请求 | 放回之前的记忆；从不保存 |
| 子 Agent | 客户端的子 Agent 发出的请求 | 放回之前的记忆；从不保存 |
| Token 计数 | token 计数请求（`/gateway/v1/messages/count_tokens`） | 放回之前的记忆，计数才和实际发送的内容一致 |
| 直接转发 | 原样转发、不带记忆的请求：其他接口、依赖服务商端状态的 Responses 请求、压缩过或无法解析的请求体 | 无 |
| 记忆同步 | 后台保存事件，不是模型请求 | — |

**保存**列显示每段对话的保存状态：

- **正常**：保存正常进行。
- **已关闭**：这段对话的上下文配置不保存对话。
- **重试中**：OpenViking 拒绝了保存或无法访问。网关在 10、20、40、80 秒后依次重试。
- **已暂停**：连续失败 5 次。网关之后每 5 分钟重试一次，直到成功；未保存的轮次一直保留，直到保存成功或对话过期。
- **已暂停**，原因为 `gateway_key_revoked`：这段对话的网关密钥已被吊销或删除，网关不再重试。用户之后用有效密钥在这段对话里发请求时，保存自动恢复。

展开的行会显示原因，例如 OpenViking 拒绝了密钥（`openviking_http_401`，即用户的 OpenViking 密钥已失效），或者 OpenViking 无法访问（`openviking_unavailable`）。对话历史有改动（`history_changed`）是正常的：客户端编辑、重新生成或压缩了对话，网关用当前历史在新的 OpenViking 会话中继续保存。

展开行中的**重新同步对话…**为这段对话新建一个 OpenViking 会话，用户下次发消息时，网关把完整历史写进去。已经保存的内容留在旧会话里，所以部分记忆可能被提取两次。原因已经修复、对话却仍然暂停，或者想要一份干净的对话副本时，可以用它。下一条消息到达之前什么都不会发生。重新同步需要记录上显示的那把密钥；它已被吊销时，请选同一段对话里更新的一条记录。

## OpenViking 工具

开启 OpenViking 工具后，模型能在回答过程中使用 OpenViking 服务提供的工具，范围受该用户自己的 OpenViking 权限限制。调用由网关执行，客户端持续收到回答直到本次回复结束，显示的 token 用量包含期间所有模型调用。这项功能对各类客户端意味着什么、用户在回复里看到什么，见[让任意客户端拥有 Agentic 记忆](15-gateway.md#让任意客户端拥有-agentic-记忆)；本节说明客户端要求、设置和上限。

这项功能面向接不了 OpenViking MCP 服务器的聊天应用和 API 应用。Claude Code、Codex 这类支持 MCP 或有插件的客户端，用 MCP 或插件更合适，因为它们会显示每次工具调用和结果，并在调用前请求确认。

### 客户端要求

三种协议都支持流式和非流式请求：

| 协议 | 客户端要求 |
| --- | --- |
| Chat Completions | 回传完整的 `messages` 历史。 |
| Responses | 使用 `store: false` 和完整的 `input` 历史；下一轮回传本轮返回的全部 `output` 内容，包括 reasoning 和客户端工具调用。带 `previous_response_id`、`conversation` 或 `background` 的请求照常转发，不提供网关工具；`item_reference` 无法还原完整历史。 |
| Anthropic Messages | 回传完整的 `messages` 历史，保留 thinking 和 signature 块。token 计数请求带相同的工具定义，但不执行工具。 |

模型在同一条回复中同时调用 OpenViking 工具和客户端工具时，网关处理 OpenViking 调用，客户端照常执行自己的工具并回传结果。Responses 支持客户端的 function 和 custom 工具，Anthropic Messages 支持客户端的 tool_use 调用。

### 选择工具

新建的上下文配置默认开启 **OpenViking 工具**，“使用推荐设置创建”也一样。**工具**清单来自你的 OpenViking 服务，默认只勾选只读工具：

- **默认勾选**：`find`、`search`、`grep`、`glob`、`list`、`tree`、`read`、`list_watches`、`get_acl`、`list_users`、`list_groups` 和 `health`。
- **默认不勾选**（会修改数据）：`remember`、`write`、`edit`、`add_resource`、`add_skill`、`forget`、`set_acl` 和 `cancel_watch`。想让模型保存记忆就勾选 `remember`，想让它导入网页或附件就勾选 `add_resource`。

OpenViking 以后新增的工具会默认可用，已有对话仍沿用开始时的工具清单。已有的上下文配置保留原来的勾选。

每个上游的**允许 OpenViking 工具**默认开启。两个开关都开启时，新对话获得选中的工具，名称加上 `openviking_` 前缀，例如 `openviking_find`、`openviking_read`。Studio 会说明每个工具的用途；只有 OpenViking 提供了相应信息时，才显示只读标记。工具定义随对话中的每个请求发送，全部工具约占 3,500 个输入 token，所以取消用不到的工具也能省 token。

> **注意**：选中的工具直接执行，不经过客户端的权限确认，写入和删除数据的工具也一样。勾选会修改数据的工具前，先确认能接受这一点；给聊天应用用的配置，建议不勾选 `forget` 和 `set_acl`。调用提示只说明已经发生的操作，不是权限确认。

### 工具调用提示

开启**显示工具调用**（默认开启）时，网关每执行一次 OpenViking 工具调用，就在回复中调用发生的位置加一行提示：

```text
> OpenViking find: "release date" — done
```

- 提示写明工具名；能识别调用对象时，还显示检索的查询词，读取、列出或写入的 URI，或者正在导入的文件或技能。
- 流式回复中，调用一开始提示就出现，导入这类耗时的调用进行时，用户也能看到网关在工作；非流式回复在最终消息里带上同样的提示。
- 调用结束后补上结果：`done` 表示完成，`failed` 表示调用返回了错误，`skipped` 表示网关拒绝了这次调用、没有执行，例如工具轮数或 token 预算已经用完。
- 每条提示单独成段。在 Chat Completions 中，提示是回复文本的一部分；在 Anthropic Messages 中，同一轮调用的提示放在同一个文本块里；在 Responses 中，放在同一条助手消息里。
- 模型看不到这些提示，它收到的是真实的调用和结果；网关也不把提示保存到 OpenViking。

要隐藏提示，在上下文配置的 **OpenViking 工具**部分关闭**显示工具调用**。和其他工具设置一样，改动只影响新对话。

### 什么时候不提供工具

对话是否带工具，由它第一个能用工具的请求决定（生成标题这类辅助请求不算），之后保持不变，所以修改上下文配置只影响新对话。唯一的例外是关闭：模型还没调用过网关工具时，客户端出现了自己的 OpenViking 工具，例如 MCP 服务器在第一轮之后才连上。

辅助请求、子 Agent 请求和检测到 OpenViking 插件的对话都不带 OpenViking 工具。这类请求如果回传了模型用过这些工具的历史，网关仍附上工具定义，让这段历史保持有效，但拒绝新的 OpenViking 调用。客户端自己的工具在每个请求里都照常可用。

请求没有带工具时，请求日志的详情会说明原因：

| 原因 | 说明 |
| --- | --- |
| `tools_require_full_history` | Responses 请求需要 `store: false` 和完整 input 历史，不能使用 `item_reference`。 |
| `upstream_tools_disabled` | 上游关闭了**允许 OpenViking 工具**。 |
| `tools_multiple_choices` | 请求要求返回多个候选（`n` 大于 1）。 |
| `tools_structured_output` | 请求要求结构化输出（`response_format`、`text.format` 或 `output_config.format`）。 |
| `tools_non_function` | Chat Completions 客户端传入了 function 以外类型的工具。 |
| `tools_forced_choice` | `tool_choice` 为 `required`，或者指定了某个工具。 |
| `deepseek_reasoning_history_required` | 上游是 DeepSeek，请求没有关闭思考模式（`"thinking": {"type": "disabled"}`），而之前的回复发到上游时会缺推理内容：关闭了**补全推理内容回传**，或者这个请求补不回推理内容（对话换了上游、丢了记忆记录，或者思考内容被当作正文发回）。标准 Responses 请求没有 `thinking` 字段，按开启思考模式处理。 |
| `tool_name_collision` | 客户端已经接了 OpenViking 工具，网关在这段对话剩下的部分不再提供自己的工具（模型已经调用过网关工具的分支除外）。判断条件见下文。 |
| `tools_unavailable` | 无法从 OpenViking 加载工具清单，也没有之前加载成功的清单。连接恢复后开始新对话。 |
| `tools_not_selected_at_session_start` | 上下文配置开启了工具，但这段对话的工具列表为空：配置里一个工具都没选，或者对话在网关升级前就已开始。开始新对话即可。 |

满足以下任一条件，网关就认为客户端已经接了 OpenViking 工具（`tool_name_collision`）：

- MCP 服务名（`server_label`、`mcp_server_name`、`mcp_servers[].name`）含单词 `openviking`。
- 以 OpenViking 工具名结尾的工具名中也含这个单词，例如 `mcp__claude_ai_OpenViking__search`；连接器的 `authenticate` 工具不算。独占一行列出的延迟加载工具名也算。
- 带前缀、以 OpenViking 工具名结尾的工具（例如 `mcp__ov__read`），定义中出现 `viking://`。
- 任一请求中，客户端工具和网关工具同名，因为上游会拒绝重名工具。

误判同样会持续整段对话，开始新对话即可。

### 导入文件和技能

OpenViking 服务提供导入工具、上下文配置也勾选了它们时，模型就能导入资料。按 URL 导入不需要额外条件；导入本地文件需要以下两种方式之一：

- **客户端有 shell 工具**（名称类似 bash、shell、exec_command、terminal 或 run_command）：导入工具返回一个一次性上传链接，模型再用客户端的 shell 工具上传文件，这一步经过客户端的权限确认。技能目录会先打包成 zip。链接指向 `public_url` 加 `/gateway/uploads`，所以 `public_url` 必须是客户端能访问的地址，前面的代理也要像其他路径一样转发它。没有设置 `public_url` 时，导入失败并提示 "Set gateway.public_url for client uploads"。
- **用户消息带附件**：没有 shell 工具时，模型可以导入用户消息里附带的文件，文件可以是 base64 数据，也可以是附件文本，支持 Responses `input_file` 和 Anthropic `document.source` 中内嵌的 `base64`、`text` 数据。Open WebUI 通常只发送提取出的文本，这些文本作为文本文件导入。附件中的 URL 和服务商的文件 ID 不会被抓取；要导入远程资源，请用导入工具的 URL 参数。

上传大小受 `max_body_bytes` 限制（默认 32 MiB）。

### 上限

上下文配置中 **OpenViking 工具**部分的**高级设置**里有以下上限：

| 设置 | API 名称 | 默认值 | 范围 | 达到上限时 |
| --- | --- | --- | --- | --- |
| 每次请求的工具轮数 | `tool_max_rounds` | 不限 | 1 及以上 | 拒绝之后的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。 |
| 单次调用超时 | `tool_timeout_seconds` | 30 秒 | 最多 120 秒 | 这次调用向模型返回错误。 |
| 结果大小上限 | `tool_result_bytes` | 65,536 字节 | 1,024–1,048,576 | 结果被截断。 |
| 总时长 | `tool_total_seconds` | 不限 | 大于 0 秒 | 请求失败，返回 504 "Hidden tool request timed out"。 |
| Token 预算 | `tool_total_tokens` | 不限 | 1,024 及以上 | 拒绝之后的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。这不是计费上限，最终回答可能超出它。 |

轮数、总时长和 Token 预算默认都不限，和 Agent 自己的工具循环一样，模型会一直使用 OpenViking 工具直到完成。流式请求中，用户可以随时中断回复，客户端断开后网关也会停止。**非流式请求在客户端断开后仍会继续执行，所以客户端发送非流式请求时，请设置总时长。**字段留空表示不限。这几项默认值改为“不限”之前保存的上下文配置仍保留原来的值（5 轮、120 秒、100,000 token），清空即可取消限制。

Token 预算只计算模型使用 OpenViking 工具时新增的调用、结果和后续生成内容，客户端已有的对话、工具定义和图片不计入。网关保留客户端设置的回答长度上限（`max_tokens`、`max_completion_tokens` 或 `max_output_tokens`）。

### 失败处理

- **单次调用失败**：模型收到错误结果，可以据此继续回答。
- **整个回答失败**：例如服务商拒绝了后续的模型请求，或者 OpenViking 调用被拒绝后模型仍坚持调用。客户端收到错误，请求日志显示 **OpenViking 工具调用失败**（`hidden_tool_loop_failed`）。流式 Responses 请求以 `response.failed` 事件结束；Chat Completions 和 Anthropic Messages 返回 `gateway_tool_error` 错误。
- **某个上游反复失败**：关闭它的**允许 OpenViking 工具**。用过这些工具的 Claude 对话可能随之出现**工具历史无法继续使用**（`hidden_tool_history_unavailable`）：之前的思考内容无法继续使用，网关已将其去掉。可以按请求详情中的停用原因恢复设置，或者开始新对话。
- **重试**：重试的工具调用复用第一次调用的结果，中断的写入操作不会自动重做。

## 长对话

模型的上下文窗口限制了一段对话的长度。开启**压缩**（默认开启）后，网关会在对话撑满窗口之前，把较早的部分换成一份摘要。

**何时压缩。** 每收到一条新的用户消息或一个工具步骤，网关都估算一次窗口用量：取服务商为这段对话上一次回复报告的 token 用量，加上之后新增消息的估算值。没有这份用量时（例如流式 Chat Completions 没有设置 `stream_options.include_usage`），就估算整个请求。估算值达到**压缩时机**（窗口的 0.9）时开始压缩。辅助请求、子 Agent 请求和 token 计数请求不触发压缩。

**窗口大小从哪来。** 网关先查上游**上下文窗口**中该模型的条目（按发给上游的模型名查找），没有时用上下文配置的**默认上下文窗口**，两者都没有时按 1,000,000 token 计算。使用窗口更小的模型时请设置其中一项，否则还没等到压缩，服务商就会以对话过长为由拒绝请求。

**摘要怎么生成。** 网关向同一个上游、同一个模型额外发一次非流式请求，客户端的系统提示词、工具和思考设置保持不变，所以大部分内容能命中服务商的提示缓存。纯文本摘要做不到的设置会被去掉：强制的工具选择改为不调用工具，结构化输出格式和停止序列也不保留。这次请求包含压缩位置之前的对话，以及一条让模型为自己写摘要的指令，摘要要覆盖：

- 用户的目标和最新请求的原文；
- 关键决定和当前进度；
- 文件、路径和标识符；
- 出过的错误和修正；
- 未完成的事项；
- 最近几次工具结果里的要点。

之前的摘要会并入新摘要。摘要是纯文本，最长**摘要长度上限**（8,000）个 token。思考内容也计入输出上限，而有些模型（如 DeepSeek V4.1）即使请求里没有思考设置也会默认思考，所以网关总在摘要长度上限之外再留 16,000 token 的输出余量；客户端手动指定了思考预算时，改为加上这份预算。这次请求和其他请求一样由服务商计费。

**压缩之后模型收到什么。** 压缩位置取决于请求类型：

- 新的用户消息：压缩位置就在这条消息之前。模型收到 system 和 developer 消息、作为一条用户消息的摘要，以及这条新消息。
- 工具步骤：压缩位置在最近一次工具结果之后，模型从摘要出发继续完成任务。

摘要消息开头说明网关替换了对话较早的部分、这段内容不是用户写的；结尾附上这段对话开头的网关说明和用户画像，压缩后它们仍然保留。之后凡是重发这段历史的请求，包括辅助请求和 token 计数请求，都做同样的替换；子 Agent 通常发送自己单独的历史，不受影响。每次压缩让服务商缓存在压缩位置失效一次，之后照常命中。

**压缩位置之前的原文一概不保留**，最近几轮也一样。模型之前的回复和其中的思考内容都依赖被替换掉的历史，只保留一部分会破坏 Claude 的思考签名。压缩位置之后生成的内容照常发送，包括思考内容。

**查找被压缩的内容。** 对话本身仍保存在 OpenViking 里。开启了**保存对话**、这段对话也提供了 `openviking_grep` 和 `openviking_read` 工具时，摘要后面会附上查找方法：这段对话对应的 OpenViking 会话（`viking://user/<user_id>/sessions/<session_id>/`）、会话中消息的存放位置，以及检索步骤：先用具体关键词 grep 会话拿到行号，再读取匹配位置前后的内容。压缩发生在一轮中间时，压缩位置之前的部分会立即保存到 OpenViking，模型在这一轮里就能检索到。

**压缩失败时。** 摘要请求失败，或者回复里调用了工具、内容为空、因长度上限被截断时，网关改为发送完整历史，并在 60 秒内不再为这段对话尝试压缩。请求详情会显示**压缩失败**及原因。如果摘要写完后用量仍达到**压缩时机**（例如系统提示词和工具本身就占了窗口的大部分），网关保留这份摘要，同样 60 秒后再尝试，原因显示为 `still_over_threshold`。

**不再生成 Working Memory 摘要。** 摘要由对话所用的模型生成，不使用 OpenViking 的会话摘要。网关新建的 OpenViking 会话都关闭了 Working Memory：提交时照常归档消息、提取记忆，只是不生成会话摘要。

**记忆预算重新计算。** 每压缩一次，**单个上下文窗口预算**就重新计算，压缩位置之前补充过的记忆可能再次补充。

**客户端仍发送完整历史。** 网关压缩后，客户端还是发送完整历史，网关在每个请求里替换压缩位置之前的部分。客户端看到的用量很小，按用量决定何时压缩的客户端很少再自己压缩，所以特别长的会话最终可能超过 `max_body_bytes`。客户端压缩或编辑历史后，网关把新历史保存到新的 OpenViking 会话；检索工具可用时，新历史开头的网关说明会告诉模型之前的会话在哪里。

请求日志中每条请求的详情会显示上下文窗口用量、较早的历史是否被替换，以及新摘要的大小和耗时。

### 实验性：Agent 自管上下文窗口

开启 **Agent 自管上下文窗口**（默认关闭）后，由模型自己决定何时开启新的上下文窗口，并自己写交接笔记，不必等网关生成摘要。这项设置只对能使用 [OpenViking 工具](#openviking-工具)的对话生效，其他对话照常压缩。开启压缩时，如果模型一直不开新窗口，压缩仍会兜底。

模型会看到：

- **两个额外的工具**。它们不在上下文配置的**工具**清单里，只由这项设置控制：
  - `openviking_new_context(reason, notes, next_steps)`：开启新窗口，`next_steps` 可省略。它必须在一轮里单独调用；和其他工具同一轮调用时，这一轮的每个调用都返回错误，窗口不重置。网关在本次请求里已经在同一位置替换过上下文（例如刚压缩过）时，它同样返回错误。
  - `openviking_context_remaining()`：返回窗口序号、已用和剩余 token 的估算值、本窗口内的用户轮数、距用户上一条消息的时间，以及一句建议。
- **开头的网关说明里多一行**，告诉模型它用这两个工具自己管理上下文窗口。
- **一行状态**：每条新的用户消息之后，记忆块末尾附上 `[context-status] window wN · ~X/Y tokens (P%) · T since your previous message`。
- **两级提醒**：窗口用到**软提醒时机**（0.7）时，网关提醒一次，要求模型做完当前这一步就开启新窗口，并在交接笔记里记下之后要用的确切路径、行号和取值。用到**硬提醒时机**（0.85）时，要求模型立即开启新窗口，此后每一步都重复，直到模型开启新窗口或压缩接手。提醒附在新的用户消息上，工具步骤里则附在工具结果上。

模型调用 `openviking_new_context` 后，网关把目前为止的对话换成一个窗口头，模型在同一条回复里接着工作。窗口头依次包含：这是模型开启的第 N 个窗口、内容不是用户写的；reason、notes 和 next_steps；用户最新一条消息的原文；检索之前窗口的方法（条件和压缩时相同）；这段对话开头的网关说明。之后的请求会根据客户端的历史重建同一个窗口。重置之前的内容会立即保存到 OpenViking，之后仍可检索；reason 和 notes 只出现在窗口头里，不保存到 OpenViking。每次重置让服务商缓存失效一次，但不需要摘要请求。

请求日志中每条请求的详情会显示窗口序号、模型是否开启了新窗口，以及收到了哪种提醒。效果取决于所用的模型，后续版本中的行为也可能调整。

## 安全与数据

**按用户隔离。** 每把网关密钥绑定一位 OpenViking 用户。网关用这位用户自己的 OpenViking 密钥检索记忆、保存对话和调用工具，所以能做的事不会超出用户本人的权限；各用户的对话状态互相隔离。会话和记忆的正本始终在 OpenViking，网关只保存让对话继续所需的状态。

**网关存储什么。** `storage_path` 下有两个 SQLite 数据库（嵌入网关默认在 `storage.workspace/gateway`，见[配置参考](#配置参考)），文件只有运行网关的系统用户能读取；开启[加密](#加密)后，存储的每个值也都会加密：

- `management.sqlite3`：上游、上下文配置、网关密钥、请求日志，以及每个 Responses 响应由哪个上游创建。其中包括上游 API Key、请求头的值，以及网关密钥绑定的 OpenViking 密钥，加密关闭时它们以明文存储；网关密钥本身只存哈希和一段短前缀。
- `kernel.sqlite3`：每段对话的状态，包括补充到消息中的记忆原文（原样放回需要它）、OpenViking 工具的往返记录、摘要，以及等待保存的对话文本。

**请求日志记什么。** 只有元数据：请求类型、模型、状态、上游、对话 ID 的哈希、token 数、记忆条数和耗时、保存状态和异常。不记录消息文本、召回的记忆、工具参数和结果，也不记录任何密钥。网关不写 HTTP 访问日志，错误信息也不回显提交的值。

**服务商能看到什么。** 每个上游都收到完整的模型输入，包括网关补充的记忆和 OpenViking 工具的结果。只添加你放心交出这些数据的服务商。

**保留期限。** 清理任务每小时运行一次：

| 数据 | 保留时长 | 设置 |
| --- | --- | --- |
| 对话状态 | 最后一次使用后 30 天 | `session_ttl_days` |
| 请求日志 | 30 天 | `log_retention_days` |
| Responses 响应与上游的对应关系 | 30 天 | `response_ttl_seconds` |
| OpenViking 中的会话和记忆 | 遵循 OpenViking 自己的规则 | — |

**删除数据。** 吊销网关密钥、或者它绑定的 OpenViking 密钥失效，都不会删除用户数据。要删除，有三种途径：

- 账号管理员或 root 在 Studio 中用**删除该用户的网关数据…**，或者调用 `DELETE /gateway/admin/users/{user_id}/data`：吊销该用户的网关密钥，删除对话状态和日志，包括尚未保存的内容。
- root 调用 `DELETE /gateway/admin/account/data?account_id=<account>`，清除整个账号的网关数据。
- 嵌入部署中，在 OpenViking 里删除用户或账号时，网关自动删除对应的网关数据。独立部署没有这一步，数据保留到显式删除或过期。

已存入 OpenViking 的记忆和会话不受这些操作影响。反过来，在 OpenViking 中删除一条记忆后，网关为进行中的对话保存的那份记忆副本仍会被原样放回，直到对话过期；要立即清除，删除该用户的网关数据。

**访问控制。**

- 客户端用网关密钥认证，放在 `Authorization: Bearer` 或 `x-api-key` 中，密钥可以随时吊销。
- 管理密钥只控制上游和上下文配置的修改，不授予读取记忆、日志或签发密钥的权限，见[权限矩阵](../api/25-gateway.md#凭证和账号范围)。
- 账号管理员能给本账号的任何用户签发网关密钥（OpenViking 只存密钥哈希时，需要用户本人提供密钥）。拿到网关密钥就能以该用户的身份召回记忆、调用工具，所以签发权实际上等于读取本账号所有用户记忆的权限，只交给可信的管理员。
- `/gateway/uploads` 不需要网关密钥。每次上传由 OpenViking 签发的一次性令牌授权，网关只把它转发到 OpenViking 的上传接口。不要让它的查询字符串出现在代理日志里。
- `X-OpenViking-*` 请求头、客户端凭证和 Cookie 从不转发给上游。

**备份。** 备份 `storage_path`，包括两个数据库及其 `-wal`、`-shm` 文件；开启加密时，还要保管好 OpenViking 根密钥的备份。要得到一致的副本，先停止网关，或者用 SQLite 的在线备份（`sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"`，`management.sqlite3` 同理）。丢失不同文件的后果：

- **丢失 `kernel.sqlite3`**：之前补充的记忆无法放回。每段进行中的对话会有一次服务商缓存未命中，Claude 对话会丢掉一次之前的思考内容（**记忆记录缺失**，`missing_injection_record`），尚未保存的轮次也会丢失。
- **丢失 `management.sqlite3`**：上游、上下文配置、密钥和请求日志全部丢失，需要重新配置并签发新密钥。
- **丢失根密钥**（开启加密时）：两个数据库中已加密的值都无法读取，只能从空的 `storage_path` 重新开始。

## 日常运维

- **盯住首次调用缓存命中率。** 它反映跨轮次的提示缓存，应该接近不经过网关时服务商能达到的水平；**轮内缓存命中率**覆盖工具步骤，正常时很高。明显下降时，见[首次调用缓存命中率下降](#首次调用缓存命中率下降)。
- **用请求日志定位单个请求。** 按新消息、工具步骤或异常筛选，展开一行就能看到召回结果、保存状态和 OpenViking 工具的停用原因，见[请求日志和概览](#请求日志和概览)。
- **设置对外地址。** 把 `public_url` 设为客户端访问的地址（不带 `/gateway`），反向代理后的客户端配置和上传链接才能保持一致。
- **定期备份。** 备份网关存储目录；开启加密时还要备份 OpenViking 根密钥，见[安全与数据](#安全与数据)中的“备份”。

### 升级

- 升级前读发布说明。新版本的网关读不了旧的存储格式时，启动会报 `Unsupported gateway schema`，见[网关无法启动](#网关无法启动)。
- 独立网关要求 OpenViking Server 不低于 `min_server_version`（0.4.16），否则记忆检索和保存都会停止，也无法签发密钥。两者分开升级时，先升级 OpenViking，再升级网关。嵌入网关和 Server 同版本发布，不做这项检查。
- 从 sidecar 或旧版独立网关升级，见[迁移](#迁移)。

### 观测

网关不导出 Prometheus 之类的监控指标，也不写 HTTP 访问日志。运行状态通过 Studio 的“概览”和“请求日志”标签页查看，或者调用管理 API 的 `/gateway/admin/overview` 和 `/gateway/admin/logs`。进程存活用 `/health` 检查：嵌入部署用 Server 的健康检查，独立部署用网关自己的。

## 设计取舍

下表说明网关几项关键设计的原因和代价，帮你判断它是否适合自己的场景：

| 设计 | 选择及原因 | 代价 |
| --- | --- | --- |
| 接入位置 | 接在模型调用路径上，而不是给每个 Agent 装插件：装不了插件的客户端也能用，服务商密钥集中管理。 | 看不到工作目录，按项目隔离记忆要给每个项目用不同的 OpenViking 用户。 |
| 工具由谁执行 | 网关在回复过程中代为执行 OpenViking 工具，存下往返并在下一轮原样放回：没有工具、不支持 MCP 的客户端也能让模型主动使用记忆。 | 没有逐次确认；客户端必须每轮回传完整历史；需要管理员收紧工具清单和上限。 |
| 记忆放在哪 | 附加在最新用户消息末尾，而不是系统提示词里：系统提示词改一个字整份缓存就失效；附加在末尾并在之后原样放回，前缀始终不变。 | 网关必须可靠地记下每处补充的原文，记录丢失时缓存失效一次。 |
| 保存时机 | 晚一轮保存：下一条消息带回上一轮，才能确认用户保留了它。 | 最后一轮要等**最新回复等待时长**（默认 10 分钟）之后才保存。 |
| 长对话 | 由网关让同一个模型写摘要：只有网关知道模型实际收到了什么，摘要请求也大部分命中缓存。 | 触发压缩的那一轮多一次模型调用和计费；压缩位置之前的原文不再发给模型。 |
| 本地状态 | 单台主机上的 SQLite，不引入 Postgres、Redis 这类依赖，适合自部署。 | 网关不能多机互备，是模型调用路径上的单点。 |
| 客户端身份 | 网关签发自己的密钥，而不是直接用 OpenViking 密钥：可以单独吊销，同一个人的不同客户端可以绑定不同的上下文配置。 | 网关要保存用户的 OpenViking 密钥，因此应开启加密；网关密钥泄露，等于该用户的记忆泄露。 |

## 配置参考

两种部署用同一套字段，区别只在配置文件放在哪、用什么命令生成：

- **嵌入**：写在 `ov.conf` 的 `gateway` 段，由 `openviking-server init` 生成。host、port 和 workers 跟随 Server。
- **独立**：写在 `gateway.conf` 的根层，没有 `gateway` 外层，由 `openviking-gateway init` 生成。设置了 `--config` 或 `OPENVIKING_GATEWAY_CONFIG_FILE` 时，它必须指向已存在的文件；两者都没设置时，网关读取 `~/.openviking/gateway.conf` 和 `/etc/openviking/gateway.conf` 中第一个存在的文件。`OPENVIKING_CONFIG_FILE` 只用来选择 Server 的配置。

| 字段 | 适用于 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `enabled` | 嵌入 | `false` | 在 Server 中开启网关 |
| `openviking_url` | 独立 | —（必填） | OpenViking Server 的地址 |
| `host` | 独立 | `127.0.0.1` | 监听地址 |
| `port` | 独立 | `1935` | 监听端口 |
| `workers` | 独立 | `1` | worker 进程数，1–64 |
| `encryption` | 独立 | 不设置（明文） | 字段同 `ov.conf` 的 `encryption` 段，见[加密](#加密)；嵌入网关沿用 Server 的设置 |
| `public_url` | 两者 | 空 | 客户端访问的对外地址，不带 `/gateway`（网关自己加）；嵌入时为空则沿用 Server 的对外地址；都没有时接入指南用请求来源地址，文件上传不可用 |
| `storage_path` | 两者 | 嵌入：`storage.workspace` 下的 `gateway`；独立：`~/.openviking/gateway` | 本地存储目录。嵌入网关不设置时跟随 Server 的数据目录，容器只把卷挂在数据目录上也不会丢；`~/.openviking/gateway` 里已经有网关数据时继续用它，并在启动日志里提示 |
| `management_key` | 两者 | 空 | 可选的网关管理密钥，至少 32 个字符 |
| `min_server_version` | 两者 | `0.4.16` | 独立网关要求的 OpenViking Server 最低版本 |
| `session_ttl_days` | 两者 | `30` | 对话闲置多少天后过期，最小 1 |
| `response_ttl_seconds` | 两者 | `2592000` | Responses 响应与上游的对应关系保留多少秒，最小 60 |
| `log_retention_days` | 两者 | `30` | 请求日志保留天数，最小 1 |
| `max_body_bytes` | 两者 | `33554432` | 请求和上传的大小上限，至少 1,024 字节 |
| `upstream_timeout_seconds` | 两者 | `600` | 服务商请求超时，必须大于 0 |
| `health_interval_seconds` | 两者 | `60` | 检查 OpenViking 状态的间隔秒数，最小 1 |

**环境变量优先于配置文件。** 两种部署规则相同：非空的 `OPENVIKING_GATEWAY_<字段名大写>` 环境变量（例如 `OPENVIKING_GATEWAY_PUBLIC_URL`、`OPENVIKING_GATEWAY_MANAGEMENT_KEY`）覆盖文件中的值，值为空等于没有设置。文件中的值也可以写成 `"$VAR"` 引用环境变量。`encryption` 块只能写在文件里。容器通常不需要配置文件，用环境变量设置需要的字段即可。

URL 必须是 HTTP(S)，不能含凭证、查询参数或 fragment。未知字段会导致校验失败；已删除的字段除外，它们会被忽略并在启动时打印警告，包括 `encryption_key_env`，以及 `ov.conf` 中[迁移](#迁移)列出的 v0.5 字段。

**上下文配置的设置。** 先列 Studio 中的名称，再列管理 API 中的名称（管理 API 把上下文配置称为 `policies`）：

| Studio 名称 | API 名称 | 默认值 | 取值范围 | 含义 |
| --- | --- | --- | --- | --- |
| 名称 | `name` | `Default` | | 显示名称。 |
| 召回记忆 | `recall` | `true` | | 为每条新的用户消息检索记忆。 |
| 会话开头提供用户画像 | `profile` | `true` | | 新对话开始时提供画像，独立于召回。 |
| 开头内容预算 | `profile_max_tokens` | `4000` | 0–32,000 | 画像和目录的独立预算；0 表示省略。目录需要提供 `read` 工具。 |
| 显示召回摘要 | `show_recall` | `false` | | 在回复开头说明 OpenViking 补充了什么，或召回失败的原因。模型看不到。 |
| 检索范围 | `context_types` | `memory`、`resource`、`skill` | 至少一个 | 检索哪些内容：记忆、资源、技能。 |
| 单条消息预算 | `max_tokens` | `1600` | 64–32,000 | 一条消息最多补充的 token 数。 |
| 单个上下文窗口预算 | `session_max_tokens` | `30000` | ≥ 0 | 一个上下文窗口内最多补充的 token 数，每次压缩后重新计算；0 表示关闭召回。 |
| 相关度阈值 | `score_threshold` | `0.35` | 0–1 | 接受的最低相关度分数。 |
| 超时时间 | `recall_timeout` | `2`（秒） | 最多 30 | 检索最多等多久，超时后消息不带记忆继续发送。 |
| 检索文本长度 | `query_max_chars` | `8000` | 3–32,000 | 取消息的多少个字符作为检索文本。 |
| 按分类限量 | `quotas` | `{}`（关闭） | 键：`events`、`entities`、`preferences`、`experiences`、`resources`、`skills` | 每个分类最多取的条数；只检索大于 0 的分类。 |
| 保存对话 | `capture` | `true` | | 把完成的轮次保存到 OpenViking。 |
| 最新回复等待时长 | `idle_seconds` | `600`（秒） | ≥ 1 | 停顿多久之后保存最后一轮并提交会话。 |
| 提交阈值 | `commit_tokens` | `20000` | ≥ 1 | 会话中待提交的 token 数达到这个值时提交。 |
| 保留最近消息 | `keep_recent_messages` | `10` | 0–1,000 | 提交后会话中保留的消息数。 |
| 压缩 | `compaction` | `true` | | 接近上下文窗口时，用摘要替换对话较早的部分。 |
| 压缩时机 | `compaction_threshold` | `0.9` | 0.5–0.98 | 上下文窗口用到多大比例时压缩。 |
| 摘要长度上限 | `summary_max_tokens` | `8000` | 1,000–32,000 | 一份摘要最多多少 token。 |
| 默认上下文窗口 | `context_window` | 未设置（按 1,000,000 计算） | ≥ 1,024 | 上游没有列出该模型时使用的窗口。 |
| Agent 自管上下文窗口 | `agent_windows` | `false` | | 实验性。让模型自己开启新的上下文窗口；需要 OpenViking 工具。 |
| 软提醒时机 | `window_soft_ratio` | `0.7` | 0.3–0.95，须低于 `window_hard_ratio` | 窗口用到这个比例时，提醒模型尽快开启新窗口。 |
| 硬提醒时机 | `window_hard_ratio` | `0.85` | 0.4–0.97 | 窗口用到这个比例时，要求模型立即开启新窗口。 |
| OpenViking 工具 | `gateway_tools` | `true` | | 为 Chat Completions、完整历史的 Responses 和 Anthropic Messages 提供 OpenViking 工具。 |
| 取消勾选的工具 | `disabled_tools` | 会修改数据的 8 个工具，见[选择工具](#选择工具) | OpenViking 工具原名，不带 `openviking_` | 新对话不提供这些工具，其余可用工具都提供，包括以后新增的。API 请求省略这个字段时使用默认列表；列表为空且总开关开启时，提供全部可用工具。 |
| 显示工具调用 | `show_tool_calls` | `true` | | 每次 OpenViking 工具调用都在回复里加一行提示。 |
| 每次请求的工具轮数 | `tool_max_rounds` | `null`（不限） | 1 及以上 | 见[上限](#上限)。 |
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
| Base URL | `base_url` | | 服务商地址；默认地址和拼接规则见[上游](#上游)。 |
| 拼接协议接口路径 | `append_endpoint_path` | `true` | 拼接所选协议的接口路径；关闭时把 `base_url` 直接作为完整请求地址。 |
| API Key 由谁提供 | `auth_mode` | `managed` | `managed`（由网关保管 API Key）或 `passthrough`（每个客户端自带 API Key）。 |
| API Key | `api_key` | | 只写，见[凭证](#凭证)。 |
| 额外请求头 | `headers` | `{}` | 只写，见[凭证](#凭证)。 |
| 模型 | `models` | `[]` | 提供的模型名；留空表示接受任何名称。 |
| 模型别名 | `aliases` | `{}` | 客户端使用的名称 → 发给上游的模型。 |
| 上下文窗口 | `context_windows` | `{}` | 模型 → token 数，每项至少 1,024。 |
| 优先级 | `priority` | `0` | 数值越大，新对话越优先使用。 |
| 启用 | `enabled` | `true` | 停用的上游不接收请求。 |
| 允许 OpenViking 工具 | `allow_gateway_tools` | `true` | 能否通过这个上游提供 OpenViking 工具。 |
| 补全推理内容回传 | `replay_reasoning` | `null`（跟随服务商） | 把客户端丢掉的推理内容补回之前的回复。`null` 时 `deepseek`、`ark` 和 `byteplus` 开启，其他服务商关闭；`true` 或 `false` 覆盖服务商默认值。Studio 中开关与服务商默认值一致时保存为 `null`。 |
| 这是 Coding Plan 或订阅密钥 | `coding_plan` | `false` | 除非另外允许，否则拒绝发往这个上游的请求。 |
| 允许通过网关使用这个套餐 | `allow_coding_plan` | `false` | 允许使用被标记为 Coding Plan 的密钥。 |
| 最小可缓存长度 | `cache_min_tokens` | `1024` | 仅火山方舟和 BytePlus 方舟；影响缓存资格的报告。 |

网关密钥的字段、全部管理操作、权限规则和错误原因见[管理 API](../api/25-gateway.md)。

## 迁移

以下步骤适用于从早期版本的 sidecar 或独立网关迁移到嵌入网关。

1. **先备份。** 备份 `storage_path`（两个数据库，以及存在的话 `encryption.key`）。停止旧的网关进程或 sidecar，再启用嵌入网关。
2. **删掉旧字段。** 从 `ov.conf` 的 `gateway` 段删除 `host`、`port`、`workers`、`url`、`openviking_url`、`admin_token_env` 和 `encryption_key_env`。Server 会忽略这些字段并在启动时打印警告，旧的 `ov.conf` 仍能启动，但还是应该清理掉。`openviking-server init` → **Update gateway** 会自动删除它们。想继续独立运行网关，就运行 `openviking-gateway init`，或者把设置移到单独的 `gateway.conf`，去掉 `gateway` 外层和 `enabled`。独立网关的命令不再接受 `--openviking-url`、`--host`、`--port`、`--workers` 和 `--storage-path`，这些设置改写到 `gateway.conf` 或环境变量中。
3. **改对外地址。** 把 `public_url` 改为 Server 的对外地址（不带 `/gateway`），或者删掉。旧版快速开始把它设成了 `http://127.0.0.1:1935`，嵌入网关不再监听这个端口，接入指南和上传链接都会指向一个不存在的地址。`openviking-server init` → **Update gateway** 发现 `public_url` 指向别的端口时，会询问是否清除。
4. **改客户端地址和部署配置。** 网关的所有 URL 现在都在 `/gateway` 下，所以客户端要同时改端口和路径：`http://host:1935/v1` 改为 `http://host:1933/gateway/v1`，Claude Code 的 `ANTHROPIC_BASE_URL` 从 `http://host:1935` 改为 `http://host:1933/gateway`，方舟 SDK 改用 `http://host:1933/gateway/api/v3`。旧路径不再提供服务。调用管理 API 的脚本把 `/api/v1/admin/gateway/...` 改为 `/gateway/admin/...`。移除旧的 1935 Service 和 Ingress 路由、Compose 的 `gateway` 服务和 profile，以及 Helm 的 sidecar 配置 `gateway.workers`、`gateway.port`、`gateway.resources`。1935 仍是独立运行的默认端口，独立网关也使用同样的 `/gateway` 路径。
5. **首次启动会转换已存数据。** v0.5 网关用自己的密钥加密数据。新版网关（嵌入或独立）首次启动时，用这把旧密钥解密，再按当前的[加密](#加密)设置重新写入；加密关闭时写成明文，所以想让数据继续加密，要在这次启动前开启 OpenViking 加密。旧密钥从环境变量 `OPENVIKING_GATEWAY_ENCRYPTION_KEY` 读取，或者从 `storage_path` 下的 `encryption.key` 读取。`encryption_key_env` 会被忽略：如果你曾用它指向别的环境变量，这次启动时把密钥导出为 `OPENVIKING_GATEWAY_ENCRYPTION_KEY`。存在旧的加密数据、两处却都没有密钥时，网关拒绝启动，错误信息会写明这两个位置。用 Helm 时，只在这次升级的版本中保留 `existingSecret` 里的 `encryption-key` 字段。启动成功后旧密钥不会再被读取，可以删掉这个环境变量、Secret 字段和 `encryption.key`。
6. **移除共享 admin token。** `OPENVIKING_GATEWAY_ADMIN_TOKEN` **不会**被当作管理密钥。需要限制谁能修改上游和上下文配置时，显式设置 `management_key`。
7. **核对上游地址。** 首次启动时，网关会改写一次已存上游的 Base URL，让每个上游继续请求升级前的同一个地址：
   - 火山方舟和 BytePlus 方舟的 Chat Completions、Responses 上游，只有域名的补上 `/api/v3`。
   - 其他 Chat Completions、Responses 上游，路径不以 `/v1`、`/v4` 这类版本段结尾的补上 `/v1`。这包括纯域名（`https://api.deepseek.com` → `https://api.deepseek.com/v1`），也包括带路径前缀的地址（`https://openrouter.ai/api` → `https://openrouter.ai/api/v1`，或者挂在 `/litellm` 下的自建代理）。
   - 方舟和 BytePlus 方舟的 Anthropic 上游，只有域名的补上 `/api/compatible`。
   - 以 `/v1` 结尾的 Anthropic Base URL 去掉 `/v1`。

   已经以其他版本段结尾的地址保持不变，例如智谱的 `https://open.bigmodel.cn/api/paas/v4`：升级前它会被拼成 `…/v4/v1` 而请求失败，升级后按填写的地址直接可用。新建和修改的上游按[上游](#上游)中的规则拼接，以 `/v1` 结尾的 Anthropic Base URL 会被拒绝。
8. **了解日志和吊销的变化。** 请求日志新增用户列，没有用户的旧记录只出现在账号管理员和 root 的视图中。吊销密钥不再删除用户数据，显式删除和保留期照常生效。

## 故障排查

### 常见现象

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 某段对话没有召回，也没有保存 | 检测到 OpenViking 插件，网关把召回、保存和工具都让给了插件。 | 正常行为，避免同一份内容被补充、保存两次，见[网关还是插件](15-gateway.md#网关还是插件)。 |
| 保存显示“重试中”，之后变成“已暂停” | OpenViking 拒绝保存或无法访问；或者这段对话的网关密钥已被吊销（`gateway_key_revoked`），例如用户的 OpenViking 密钥被重新生成过。 | OpenViking 恢复后网关会自动恢复保存。密钥被吊销时，重新签发网关密钥，用户的下一个请求会恢复保存。见[OpenViking 中看不到对话](#openviking-中看不到对话)。 |
| 上下文配置开启了工具，对话里却没有 | 还没有能用工具的请求，第一个能用的请求加载工具清单失败，客户端没有回传完整历史，或者客户端自己接了 OpenViking 工具。 | 在请求日志里查看原因（见[什么时候不提供工具](#什么时候不提供工具)）。客户端接了 OpenViking MCP 服务器时出现 `tool_name_collision` 是正常的；其他原因修正后开始新对话。 |
| 请求不带记忆 | OpenViking 无法访问或超时、预算用完、没有相关内容等。 | 见[没有补充记忆](#没有补充记忆)。 |
| 长对话被服务商以过长为由拒绝 | 网关不知道模型的实际窗口，压缩得太晚。 | 见[长对话超出上下文长度](#长对话超出上下文长度)。 |

### 网关无法启动

嵌入网关无法启动时，整个 OpenViking Server 都会停止。请在 Server 日志中查看启动错误（Compose 中用 `docker compose logs openviking`）。

- **v0.5 数据缺少旧密钥**：存储目录里有 v0.5 网关加密的数据，但既没有 `OPENVIKING_GATEWAY_ENCRYPTION_KEY`，`storage_path` 下也没有 `encryption.key`。提供原来的密钥，见[迁移](#迁移)第 5 步。
- **旧字段**：删除[迁移](#迁移)第 2 步列出的字段。
- **管理密钥太短**：至少 32 个字符；用 `$VAR` 引用的环境变量没有设置时，也会报这个错。
- **加密设置**：Server 的 `encryption` 段（嵌入）或 `gateway.conf` 的 `encryption` 块（独立）不合法，或者加载不了根密钥。见[加密指南](08-encryption.md)。
- **存储格式不支持**（`Unsupported gateway schema`）：存储目录由不兼容的网关版本写入（更旧的版本，或降级前更新的版本）。换一个空的 `storage_path`。
- **独立运行缺少 OpenViking 地址**：运行 `openviking-gateway init`，或者设置 `OPENVIKING_GATEWAY_OPENVIKING_URL`。
- **独立运行找不到配置文件**：`--config` 或 `OPENVIKING_GATEWAY_CONFIG_FILE` 指定的文件不存在。改正路径，或者用 `openviking-gateway init --config <路径>` 生成它。
- **独立运行绑定非回环地址**：启动检查必须连上 OpenViking 并确认它使用 `api_key` 认证，否则网关退出。先启动 OpenViking（在容器中要等它就绪），或者绑定 `127.0.0.1`。

### Studio 显示设置卡片

| 卡片 | 处理 |
| --- | --- |
| 这台 OpenViking 没有开启网关（404） | 运行 `openviking-server init` → **Update gateway**，或者在 `ov.conf` 中设置 `gateway.enabled: true` 后重启。使用 API 密钥的服务端上，root 也可以直接在这个页面点**开启网关**。账号管理员和普通用户看到的卡片只提示联系部署者开启网关。 |
| 先连接 OpenViking，或者 OpenViking API 密钥无效（401） | 打开连接设置，填入当前的 OpenViking API 密钥。 |
| 网关不支持 Trusted 模式（`trusted_mode_unsupported`） | 把 Server 改为 `api_key` 认证。 |
| 网关连不上 OpenViking（`openviking_unreachable`） | 只出现在独立网关：检查 `openviking_url`（`gateway.conf` 或 `OPENVIKING_GATEWAY_OPENVIKING_URL`）和网络。 |

“上游”和“上下文配置”标签页只读，是因为部署者设置了网关管理密钥：点**输入网关管理密钥**解锁。OpenViking 的 root 或 admin API 密钥不能解锁编辑；管理密钥被更换后（`management_key_invalid`），Studio 会重新锁定。

### 签发密钥失败

**签发密钥**对话框会显示失败原因，括号里是管理 API 返回的 `reason`。

| Studio 中的提示 | 原因和处理 |
| --- | --- |
| 服务端读不到这个用户的用户 API 密钥，请改为粘贴（`user_key_unreadable`） | OpenViking Server 只保存了密钥哈希（`encryption.api_key_hashing.enabled`）。在**粘贴用户 API 密钥**中填入该用户自己的密钥。 |
| 不接受 Root API 密钥，请使用本账号下的用户 API 密钥（`root_key_not_allowed`） | root API 密钥不能绑定到网关密钥，改用某个用户或账号管理员的 API 密钥。本地 dev 模式的 `default/default` 不受此限。 |
| 这个 OpenViking API 密钥属于其他账号（`other_account`） | 密钥所属的账号不是你在 Studio 中管理的账号。 |
| OpenViking 拒绝了这个密钥（`openviking_http_401`） | 密钥不完整、已被重新生成，或者用户已被删除。使用该用户当前的密钥。 |
| OpenViking 无法识别这个密钥所属的用户（`openviking_identity_missing`） | OpenViking 没有返回这个密钥对应的用户。使用本账号中某个用户的密钥。 |
| 网关连不上 OpenViking，无法校验这个密钥（`openviking_unavailable`） | 网关访问不到 OpenViking；独立网关检查 `openviking_url`。 |
| OpenViking 版本低于网关的要求（`openviking_version_mismatch`） | 仅独立网关：OpenViking Server 低于 `min_server_version`（0.4.16），请升级。从源码安装、没有版本标签的服务可能报告 `0.1.dev123` 这样的开发版本号；请安装正式版本，或者把 `min_server_version` 改成与之匹配的值。 |

### 客户端收到网关返回的错误

这些拒绝都发生在请求到达服务商之前，所以不会出现在请求日志里。

| 状态码和提示 | 原因和处理 |
| --- | --- |
| 401 "Invalid gateway key. It may have been revoked, or removed because its OpenViking API key was regenerated…" | 客户端没有发送网关密钥，发送了已吊销的密钥或服务商的 API Key，或者用户的 OpenViking API 密钥重新生成后，网关删除了这把密钥。检查客户端实际发送的密钥，必要时重新签发。 |
| 403 "Claude subscription OAuth credentials are not supported" | 客户端发送了 Claude 订阅令牌，例如 Claude Code 用订阅账号登录且没有设置 `ANTHROPIC_AUTH_TOKEN`；或者上游保存的就是订阅令牌。请改用 API Key。 |
| 403 "Model is not allowed by this key" | 模型不在密钥允许的范围内。签发一把允许这个模型的密钥。 |
| 404 "No allowed upstream matches this protocol and model" | 密钥绑定的已启用上游中，没有一个使用客户端调用的 API 并提供所请求的模型。检查上游的协议、模型和别名、是否启用，以及密钥绑定了哪些上游。 |
| 401 "Upstream API key is missing" | 上游要求每个客户端在 `X-OpenViking-Upstream-Key` 中自带 API Key，或者由网关保管 API Key 的上游没有填写。 |
| 403 "Coding Plan upstreams are disabled; configure a model API key" | 上游被标记为 Coding Plan 密钥。改用模型 API Key，或者有意勾选**允许通过网关使用这个套餐**。 |
| 404 "Unknown response for this key"、403 "Response upstream is no longer allowed" | 查询的 Responses 响应不是这把密钥创建的，或者创建它的上游已停用、不再绑定到这把密钥。 |
| 413 | 请求体超过了 `max_body_bytes`，或者超过了代理自己的上限。 |
| 426 | 对 Responses API 发起了 WebSocket 连接。这是预期行为，Codex 会改用 HTTP。 |
| 502 "Model upstream is unavailable" | 网关连不上服务商，或者调用超过了 `upstream_timeout_seconds`。在上游上点**测试**。 |
| 504 "Hidden tool request timed out" | OpenViking 工具的往返超过了上下文配置中工具的**总时长**；没有设置总时长时，是往返中某次模型请求超过了 `upstream_timeout_seconds`。 |

### 没有补充记忆

先在请求日志里找到这个请求。找不到，说明客户端没有经过网关，或者请求被网关拒绝了（见上一节）。找到后展开它，查看记忆检索结果：

- **没有找到相关内容**（`empty`）：新用户通常如此。对话提交、OpenViking 提取之后才会有记忆。
- **召回已关闭或预算已用完**（`disabled`）：上下文配置关闭了召回，这段对话的预算已经用完，或者消息短于 3 个字符。
- **OpenViking 无法访问或响应超时**（`openviking_unavailable`）：查看“概览”标签页上的 OpenViking 卡片。OpenViking 在运行但响应慢时，可以调长**超时时间**。
- **OpenViking 拒绝了这个密钥**（`openviking_http_401`）：用户的 OpenViking 密钥已失效。用当前的密钥签发新的网关密钥。
- **OpenViking 版本低于网关的要求**（`openviking_version_mismatch`）：见[签发密钥失败](#签发密钥失败)。
- **检测到 OpenViking 插件**（`plugin_present`）：网关发现了 OpenViking 插件，让出了这段对话。
- **类型不是新消息**：工具步骤、辅助请求和子 Agent 请求从不检索，它们放回之前的消息得到的记忆。

检索失败的消息之后也不会补上记忆；下一条消息会重新检索。

### OpenViking 中看不到对话

- **保存晚一轮。** 下一条消息到达时，上一轮才会出现；最后一轮要等**最新回复等待时长**（默认 10 分钟）。
- **上下文配置不保存对话。** 这时**保存**列显示“已关闭”。
- **保存正在重试或已暂停。** 展开的行会显示原因。原因修复后（例如 OpenViking 恢复访问），网关会在 5 分钟内重试。原因为 `gateway_key_revoked` 时，重新签发网关密钥，下一个请求会恢复保存。对话一直暂停时，用**重新同步对话…**。
- **检测到 OpenViking 插件**（`plugin_present`）：网关不为这段对话保存任何内容。
- **你用的是其他用户的身份。** 会话属于网关密钥背后的 OpenViking 用户，名称形如 `gateway-…`。
- **记忆比会话出现得晚。** OpenViking 在提交之后才在后台提取记忆。

### 首次调用缓存命中率下降

先和不经过网关时服务商能达到的命中率比较，再看最近请求上标出的异常：

- **记忆记录缺失**（`missing_injection_record`）：网关存储丢失、从旧备份恢复，或者记录已过期，之前补充的记忆无法放回。进行中的对话在一次未命中之后就会恢复。
- **上游已切换**（`upstream_changed`）：对话原来的上游被停用、没有绑定到客户端当前用的密钥，或者不再提供这个模型，于是对话转到了另一个上游。
- **缓存参数有变化**（`ark_cache_parameters_changed`）：在火山方舟或 BytePlus 方舟上，客户端在同一段对话里改变了模型、思考模式、采样参数、系统提示词或工具。
- **没有标出异常**：客户端本身可能每轮都在改动之前的消息或系统提示词，例如插入当前时间，网关无法修正。每次压缩、模型每次开启新的上下文窗口，也都会造成一次未命中，这是预期内的。

### 长对话超出上下文长度

服务商以对话过长为由拒绝长对话的请求时，通常是下面三种原因之一：

- **网关不知道模型的窗口大小。** 上游的**上下文窗口**里没有这个模型，上下文配置也没有设置**默认上下文窗口**，网关就按 1,000,000 token 计算，对窗口更小的模型压缩得太晚。补上模型的窗口大小。
- **对话所用的上下文配置关闭了压缩。** 修改配置只对新对话生效。
- **压缩失败。** 请求详情会显示原因，网关 60 秒后再试。摘要总因长度上限被截断时，调大**摘要长度上限**。原因是 `still_over_threshold` 时，说明系统提示词、工具和摘要本身就占满了大部分窗口：调小**摘要长度上限**，或者检查模型的上下文窗口设置。

### 文件导入失败

- "Set gateway.public_url for client uploads"：把 `public_url` 设为客户端能访问的地址。
- 上传链接无法访问或返回 404：确认 `public_url` 不是旧的 `:1935` 地址，也没有以 `/gateway` 结尾，并且代理像其他路径一样转发 `/gateway/uploads`。
- 413：文件超过了 `max_body_bytes` 或代理的上限。
- 502 "OpenViking upload is unavailable"：网关连不上 OpenViking Server。
- 模型没有导入工具：OpenViking 服务没有提供该工具，上下文配置没有勾选，或者当前请求不能使用网关工具。检查工具清单和请求详情。

### 降级请求的异常

“请求日志”和“概览”标签页会标出网关没能完整处理的请求。Studio 显示异常名称，括号里是管理 API 中对应的值：

| 异常 | 发生了什么 | 怎么处理 |
| --- | --- | --- |
| 记忆不可用（`memory_store_failure`） | 网关无法读写自己的存储，或者该用户的网关数据刚被删除。请求不带记忆发给了模型；Claude 对话中之前的思考内容被去掉。 | 检查磁盘空间、权限和网关日志。刚删除过该用户的网关数据时出现属于正常。 |
| 原样转发（`unsafe_json`） | 请求中有无法精确重新编码的数字或重复的键，网关逐字节原样转发，没有补充记忆。 | 通常只与某个客户端有关，网关这边无需调整。 |
| 上游已切换（`upstream_changed`） | 对话原来的上游无法再服务，转到了另一个上游，造成一次缓存未命中，之前的 Claude 思考内容也被去掉。 | 重新启用这个上游，或者让客户端改用包含它的密钥；也可以接受这次切换。 |
| 记忆记录缺失（`missing_injection_record`） | Claude 对话中，之前补充的记忆已经没有记录，之前的思考内容被去掉一次。 | 存储丢失后，或者在很久以前的对话里出现，都在预期之内。反复出现时，开始新对话。 |
| 检测到 OpenViking 插件（`plugin_present`） | 检测到 OpenViking 插件，这段对话不再使用网关记忆。 | 使用插件时出现属于正常。同一个客户端在插件和网关之间二选一。 |
| 缓存参数有变化（`ark_cache_parameters_changed`） | 在火山方舟或 BytePlus 方舟上，影响缓存的参数与对话的第一个请求不同，提示缓存很可能没有命中。 | 在同一段对话里保持模型、思考模式、采样参数、系统提示词和工具不变。 |
| 工具历史无法继续使用（`hidden_tool_history_unavailable`） | Claude 对话用过 OpenViking 工具，但当前请求不能使用这些工具，之前的思考内容已被去掉。 | 查看请求详情中的工具停用原因，恢复原设置，或者开始新对话。 |
| OpenViking 工具调用失败（`hidden_tool_loop_failed`） | 模型使用 OpenViking 工具时未能完成回答，客户端收到错误。 | 见[失败处理](#失败处理)。 |
| 回复未保存（`capture_parse_failure`） | 流式回复无法解析，这条回复没有保存，客户端不受影响。 | 无需处理，仅供了解。 |
| 密钥未校验（`revocation_check_unavailable`） | OpenViking 无法确认这把网关密钥，请求在没有检查是否已吊销的情况下照常处理。 | 检查 OpenViking 是否可达、运行是否正常。 |
