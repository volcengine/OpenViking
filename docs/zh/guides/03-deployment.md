# 服务端部署

OpenViking 以 HTTP 服务运行。安装服务端之前，先选择由谁运行服务：

| 服务方式 | 你需要准备什么 |
| --- | --- |
| [火山引擎托管 OpenViking](https://www.volcengine.com/product/openviking-service) | 在[控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)获取 API Key，用独立 CLI 连接，无需本地服务端或模型配置。 |
| 团队已有服务或他人部署 | 向管理员获取服务地址和 user/admin key。 |
| 自建 OpenViking | 按下文安装、配置和运行服务端。 |

托管及已有服务用户可直接从 [CLI 快速开始](../getting-started/02-quickstart.md)进入。托管服务的可用范围、套餐和额度见[官方服务文档](https://docs.volcengine.com/docs/84313/2374478)。

本页后续内容适用于自建服务。

容量规划应使用有代表性的数据集和预期并发量测量内存、磁盘占用，为原始文件、索引和快照预留空间。扩大工作负载前，通过[可观测性](../guides/05-observability.md)检查容量余量。

## 快速开始

先按[自建服务快速开始](../getting-started/02-quickstart.md)安装服务端。Python 3.14 下使用火山引擎 Ark 时，上游 Python SDK 可能输出 Pydantic V1 兼容性警告；此场景可使用 Python 3.13 避免该警告。

```bash
# 使用初始化向导创建或刷新 ~/.openviking/ov.conf
openviking-server init

# 如果你在向导中选择 OpenAI Codex，init 会帮你处理 Codex 登录/导入

# 启动前校验本地配置、模型访问和鉴权状态
openviking-server doctor

# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
openviking-server

# 配置文件在其他位置时，通过 --config 指定
openviking-server --config /path/to/ov.conf

# 在另一终端按快速开始配置 ov，连接本服务后验证
ov health
```

## 命令行选项

| 选项 | 描述 | 默认值 |
|------|------|--------|
| `--config` | 配置文件路径 | `~/.openviking/ov.conf` |
| `--host` | 绑定的主机地址 | `127.0.0.1` |
| `--port` | 绑定的端口 | `1933` |

**示例**

```bash
# 使用默认配置
openviking-server

# 使用自定义端口
openviking-server --port 8000

# 指定配置文件、主机地址和端口
openviking-server --config /path/to/ov.conf --host 127.0.0.1 --port 8000
```

## 配置

服务端从 `ov.conf` 读取所有配置。配置文件各段详情见 [配置指南](01-configuration.md)。

`ov.conf` 中的 `server` 段控制服务端行为：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "your-secret-root-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
  }
}
```

## 部署模式

### 独立模式（嵌入存储）

服务器管理本地 RAGFS 和 VectorDB。在 `ov.conf` 中配置本地存储路径：

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
  }
}
```

```bash
openviking-server
```

## 使用 Systemd 部署服务（推荐）

对于 Linux 系统，可以使用 Systemd 服务来管理 OpenViking，实现自动重启、开机自启等功能。首先，你应该已经成功安装并配置了 OpenViking 服务器，确保它可以正常运行，再进行服务化部署。

### 创建 Systemd 服务文件

创建 `/etc/systemd/system/openviking.service` 文件：

```ini
[Unit]
Description=OpenViking HTTP Server
After=network.target

[Service]
Type=simple
# 替换为运行 OpenViking 的用户
User=your-username
# 替换为用户组
Group=your-group
# 替换为工作目录
WorkingDirectory=/var/lib/openviking
# 使用实际安装位置中的绝对路径
ExecStart=/path/to/your/python/bin/openviking-server
Restart=always
RestartSec=5
# 配置文件路径
Environment="OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf"

[Install]
WantedBy=multi-user.target
```

启动前替换所有占位值。以服务用户运行 `command -v openviking-server` 确认可执行文件路径，创建工作目录，并让该用户能够访问配置、workspace 及本地加密密钥。此 unit 使用 `/etc/openviking/ov.conf`，不会自动使用你个人 home 目录下生成的配置。系统服务省略 `User` 会以 root 运行。

### 管理服务

创建好服务文件后，使用以下命令管理 OpenViking 服务：

```bash
# 重载 systemd 配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start openviking.service

# 设置开机自启
sudo systemctl enable openviking.service

# 查看服务状态
sudo systemctl status openviking.service

# 查看服务日志
sudo journalctl -u openviking.service -f
```

## 连接客户端

### Python SDK

```bash
python -m pip install --upgrade openviking-sdk
```

```python
import openviking_sdk as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find(query="how to use openviking")
client.close()
```

### CLI

CLI 从 `ovcli.conf` 读取连接配置。在 `~/.openviking/ovcli.conf` 中配置：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

也可通过 `OPENVIKING_CLI_CONFIG_FILE` 环境变量指定配置文件路径：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## 云原生部署

### Docker

OpenViking 提供预构建的 Docker 镜像，发布在 GitHub Container Registry。运行目录为 `/app/.openviking`，因此默认的 `storage.workspace`（`./data`）解析为 `/app/.openviking/data`。默认工作区、`ov.conf` 和 `ovcli.conf` 共用一个持久卷。若将工作区配置为此目录以外的绝对路径，需要另外挂载该路径：

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

> 推荐优先使用 `ghcr.io` 镜像；如果访问有问题，可改用 `openviking-cn-beijing.cr.volces.com/volcengine/openviking:latest`。本节后续命令同理。

Docker 镜像默认会同时启动：
- OpenViking HTTP 服务，端口 `1933`（绑定 `0.0.0.0`），同时在 `/studio` 提供 Web Studio 前端
- `vikingbot` gateway

由于容器内服务绑定 `0.0.0.0`（Docker 端口映射所必需），你**必须**在 `ov.conf` 中设置 `root_api_key`：

```json
{
  "server": {
    "root_api_key": "your-secret-root-key"
  }
}
```

未设置时服务将拒绝启动。如需自定义绑定地址，可通过环境变量 `OPENVIKING_SERVER_HOST` 覆盖。

**从旧镜像升级：** 运行目录为 `/app` 的旧镜像会把 `./data` 解析为默认挂载之外的 `/app/data`。删除旧容器前，先停止容器并备份其实际工作区（例如 `docker cp openviking:/app/data ./openviking-data-backup`）。启动替换容器前，将备份恢复到挂载的宿主机工作区，通常为 `~/.openviking/data`。若目标工作区已存在，先确认要保留的数据，不要直接覆盖。绝对工作区路径不变；其他相对路径现在以 `/app/.openviking` 为基准。

升级容器的方式
```bash
docker stop openviking
docker pull ghcr.io/volcengine/openviking:latest
docker rm -f openviking
# 然后重新 docker run ...
```

如果你希望本次容器启动时关闭 `vikingbot`，可以使用下面任一方式：

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest \
  --without-bot
```

```bash
docker run -d \
  --name openviking \
  -e OPENVIKING_WITH_BOT=0 \
  -p 1933:1933 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

#### 无法使用 `docker -v` 时

部分托管平台（如 Railway、Fly.io、Heroku 这类 PaaS）不支持把宿主机目录绑定挂载进容器。这种环境下，如果容器启动时找不到 `ov.conf`，entrypoint 不会崩溃 —— 它会打印一段修复指引并阻塞等待文件出现。你可以选用以下两种方式之一：

**方案 A：通过 `OPENVIKING_CONF_CONTENT` 注入完整配置内容。** entrypoint 会在启动 server 前把这个环境变量的值写入到 `OPENVIKING_CONFIG_FILE`（默认 `/app/.openviking/ov.conf`）：

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -e OPENVIKING_CONF_CONTENT="$(cat ~/.openviking/ov.conf)" \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

**方案 B：容器起来之后再 `docker exec` 进去用向导配置。** 容器在等待 `ov.conf` 期间是存活的，`exec` 进去运行 setup wizard，它会按 `OPENVIKING_CONFIG_FILE` 写到 server 正在监听的位置：

```bash
docker exec -it openviking openviking-server init
```

`ov.conf` 出现后，entrypoint 会自动恢复并启动 server。

也可以使用 Docker Compose，项目根目录提供了 `docker-compose.yml`：

```bash
docker compose up -d
```

启动后可以访问：
- API 服务：`http://localhost:1933`
- Web Studio：`http://localhost:1933/studio`（与 API 同源）
- 兼容入口：`http://localhost:1934`（Caddy 反代到 1933，仅为已有部署保留）

### 部署到 Railway

点击下方按钮一键部署到 Railway：

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/openviking)

#### 预置资源与环境

- **服务镜像**：拉取官方镜像 `ghcr.io/volcengine/openviking:latest`，监听 1933 端口，Railway 自动分配 HTTPS 域名。
- **持久化存储**：挂载持久卷至 `/app/.openviking`，`storage.workspace` 位于该卷上，保证重新部署后数据与配置不丢失。
- **默认配置**：默认采用 OpenAI 预设。部署时仅需填写 `OPENAI_API_KEY`；管理员密钥 `OPENVIKING_ROOT_API_KEY` 自动生成，部署完成后可在 Railway 的 **Variables** 标签页查看。

#### 快速初始化（Web Studio）

部署完成后，在浏览器中即可完成首次初始化：

1. **配置管理员密钥**：从 Railway 服务变量中复制 `OPENVIKING_ROOT_API_KEY`，访问 `https://<你的域名>/studio/settings` 并保存。
2. **创建用户**：进入 `https://<你的域名>/studio/users` 创建首个账户与用户，系统将展示该用户的 API Key。
3. **开始使用**：后续访问 Web Studio、`ov` CLI 或 SDK 时，均使用上述用户的 API Key。

#### 配置管理

- **首次生成配置**：模板在 `OPENVIKING_CONF_CONTENT` 中预置了完整配置并引用 `${OPENAI_API_KEY}`。该变量仅在首次启动且 `ov.conf` 尚不存在时生效。
- **后续修改配置**：首次启动后，请通过 `railway ssh` 或 `railway service files upload --overwrite` 直接修改持久卷上的 `ov.conf`；也可以删除 `ov.conf` 后重新部署，让服务按当前 `OPENVIKING_CONF_CONTENT` 重新生成配置文件。

#### 资源与费用参考

- **推荐配置**：长期运行建议选择 **Hobby** 计划（$5/月，包含 $5 用量抵扣）。以 ~0.5 GB 常驻内存估算，月均成本通常在 $5–$7 左右。
- **免费额度说明**：Railway Free 计划（$1/月额度）不足以支持服务常驻运行；Trial 赠金适合短期体验评估，额度到期 30 天后持久卷将被清理，请注意按需备份数据。

> **安全提示**：服务部署后默认监听并暴露于公网。请妥善保管 `OPENVIKING_ROOT_API_KEY`，在对外开放前请阅读[公网访问安全指南](12-public-access.md)。

### 多实例部署注意事项

使用本地向量后端（`local` 或 `cuvs`）时，OpenViking 默认通过操作系统文件锁独占 `storage.workspace`。`.openviking.lock` 文件会保留在磁盘上，文件存在不代表服务正在运行；正常关闭或进程终止后，操作系统会释放锁。不要手动删除运行中服务的锁文件。

远程向量后端（`http`、`volcengine`、`vikingdb`）不会获取此 workspace 锁，包括文件存放在共享 NAS 上的情况，无需设置 `storage.skip_process_lock=true`。把本地向量数据库放在 NAS 上，并不会使它支持多进程共享。

使用本地向量后端从 `.openviking.pid` 旧版本升级时，必须先停止所有使用该 workspace 的旧版服务，再启动新版。新版不再根据遗留 PID 判断目录是否被占用，新旧锁机制不支持混用。

多实例部署时，通常建议注意这几项配置：

- 把 `server.temp_upload.default_mode` 设为 `"shared"`，这样临时上传文件可以被其他副本消费。
- 共享存储应使用远程向量后端。保留的 `storage.skip_process_lock` 开关只关闭本地后端的启动保护，不会使本地向量存储支持多进程共享。
- 对 QueueFS，建议通过 `storage.agfs.queuefs.db_path` 显式指定实例本地的 SQLite 路径。如果启用了 usage audit，建议通过 `server.observability.usage_audit.sqlite_path` 显式指定实例本地的 SQLite 路径，不要默认和共享 workspace 卷混用。

示例：

```json
{
  "server": {
    "temp_upload": {
      "default_mode": "shared"
    }
  },
  "storage": {
    "vectordb": {
      "backend": "http",
      "url": "http://vector-db:5000"
    }
  }
}
```

这个示例使用远程 HTTP 向量服务。请将 URL 替换为实际的向量服务地址，或配置 `volcengine`、`vikingdb` 后端。

如果你还需要为 QueueFS 和 usage audit 显式指定本地 SQLite 路径，可以参考：

```json
{
  "server": {
    "temp_upload": {
      "default_mode": "shared"
    },
    "observability": {
      "usage_audit": {
        "sqlite_path": "/var/lib/openviking-local/usage_audit.sqlite3"
      }
    }
  },
  "storage": {
    "vectordb": {
      "backend": "http",
      "url": "http://vector-db:5000"
    },
    "agfs": {
      "queuefs": {
        "db_path": "/var/lib/openviking-local/queue.db"
      }
    }
  }
}
```

这个变体适用于多个实例共享同一个 `workspace`，但 QueueFS 和 usage audit 的 SQLite 文件仍然放在各实例本地路径的场景。

如需公网 HTTPS 访问，请参考 [公网访问指南](12-public-access.md)。

如需自行构建镜像，请显式传入 OpenViking 版本：
`docker build --build-arg OPENVIKING_VERSION=0.3.12 -t openviking:latest .`

### Kubernetes + Helm

项目提供了 Helm chart，位于 `examples/k8s-helm/`：

```bash
helm install openviking ./examples/k8s-helm \
  --set openviking.config.embedding.dense.api_key="YOUR_API_KEY" \
  --set openviking.config.vlm.api_key="YOUR_API_KEY"
```

详细的云上部署指南（包括火山引擎 TOS + VikingDB + 方舟配置）请参考 [云上部署指南](https://github.com/volcengine/OpenViking/blob/main/examples/cloud/GUIDE.md)。

## 健康检查

| 端点 | 认证 | 用途 |
|------|------|------|
| `GET /health` | 否 | 存活探针 — 立即返回 `{"status": "ok"}` |
| `GET /ready` | 否 | 就绪探针 — 检查 AGFS、VectorDB、APIKeyManager、Embedding、Ollama |

```bash
# 存活探针
curl http://localhost:1933/health

# 就绪探针
curl http://localhost:1933/ready
# {"status": "ready", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok", "embedding": "ok", "ollama": "ok"}}
```

在 Kubernetes 中，使用 `/health` 作为存活探针，`/ready` 作为就绪探针。

## 相关文档

- [公网访问与反向代理](12-public-access.md) - HTTPS、Caddy、nginx
- [认证](04-authentication.md) - API Key 设置
- [OAuth 接入指南](11-oauth.md) - 面向 MCP 客户端的 OAuth 2.1
- [可观测性与排障](05-observability.md) - 健康检查、追踪与排障
- [API 概览](../api/01-overview.md) - 完整 API 参考
