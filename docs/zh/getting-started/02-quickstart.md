# 快速开始

启动本地服务，导入一份资源，然后完成第一次检索。

## 前置要求

在开始使用 OpenViking 之前，请确保您的环境满足以下要求：

- **Python 版本**：3.10 或更高版本
- **操作系统**：Linux、macOS、Windows
- **网络连接**：需要稳定的网络连接（用于下载依赖包和访问模型服务）

## 安装与启动

选择一种服务端安装方式：Python 包或 Docker。Python SDK 通过 HTTP 连接这两种方式启动的服务。

### 方式一：安装服务端和 CLI

选择你常用的 Python 包管理工具安装 OpenViking：

::: code-group

```bash [uv（推荐）]
uv tool install openviking --upgrade
```

```bash [pip]
python -m pip install openviking --upgrade
```

```bash [pipx]
# 安装
pipx install openviking

# 更新
pipx upgrade openviking
```

:::

安装完成后，可以使用客户端命令 `ov`（`openviking` 是其别名）和服务端命令 `openviking-server`。

### 方式二：通过 Docker 启动 (作为独立服务)

如果你希望将 OpenViking 作为独立的服务运行，推荐使用 Docker。

1. **准备配置目录**
   在宿主机上创建 OpenViking 目录，并准备好 `ov.conf` 配置文件（配置项参考下方“配置环境”章节）。所有持久化状态 —— 配置和工作区数据 —— 都放在这一个目录下：
   ```bash
   mkdir -p ~/.openviking
   ```

   启动容器前，参照下方手动配置模板，将完整 JSON 写入 `~/.openviking/ov.conf`。空文件不是有效配置。

2. **使用 Docker Compose 启动**
   创建 `docker-compose.yml` 文件：
   ```yaml
   services:
     openviking:
       # 推荐优先使用 ghcr.io；如果访问有问题，可改用 openviking-cn-beijing.cr.volces.com/volcengine/openviking:latest
       image: ghcr.io/volcengine/openviking:latest
       container_name: openviking
       ports:
         - "127.0.0.1:1933:1933"
       volumes:
         - ~/.openviking:/app/.openviking
       restart: unless-stopped
   ```
   然后在同目录下执行启动命令：
   ```bash
   docker compose up -d
   ```

   默认情况下，容器会启动 OpenViking API 服务（`1933`，同时在 `/studio` 提供 Web Studio 前端）以及内置的 `vikingbot` gateway。如果你需要关闭 `vikingbot`，可以在 Compose 里增加 `command: ["--without-bot"]`，或者设置 `environment: ["OPENVIKING_WITH_BOT=0"]`。

   如果运行平台不支持 bind mount，可以通过 `OPENVIKING_CONF_CONTENT` 环境变量传入完整的配置 JSON，或在容器启动后 `docker exec` 进去执行 `openviking-server init`。详见 [部署指南](../guides/03-deployment.md#无法使用-docker--v-时)。

容器入口脚本在容器内监听 `0.0.0.0`。上述端口映射使宿主机（包括 macOS）可通过 `http://localhost:1933` 访问服务。如需允许其他机器访问，请参见 [身份认证](../guides/04-authentication.md) 和 [公网访问](../guides/12-public-access.md)。

## 模型准备

OpenViking 需要以下模型能力：
- **VLM 模型**：用于图像和内容理解
- **Embedding 模型**：用于向量化和语义检索

OpenViking 支持多种模型服务：
- **火山引擎（豆包模型）**：推荐使用，成本低、性能好，新用户有免费额度。如需购买和开通，请参考：[火山引擎购买指南](../guides/02-volcengine-purchase-guide.md)
- **OpenAI 模型**：通过 OpenAI API 使用支持视觉理解的模型和 Embedding 模型
- **OpenAI Codex**：支持通过 ChatGPT/Codex OAuth 使用 Codex 作为 VLM
- **其他自定义模型服务**：支持兼容 OpenAI API 格式的模型服务

## 配置环境

### 配置文件模版

通过 Python 包安装时，使用配置向导：

```bash
openviking-server init
openviking-server doctor
```

使用 Docker，或希望手动配置时，编写 `~/.openviking/ov.conf`：

```json
{
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",
      "api_key"  : "<your-api-key>",
      "provider" : "<provider-type>",
      "dimension": 1024,
      "model"    : "<model-name>"
    }
  },
  "vlm": {
    "api_base" : "<api-endpoint>",
    "api_key"  : "<your-api-key>",
    "provider" : "<provider-type>",
    "model"    : "<model-name>"
  }
}
```

`provider`、`model`、`api_base` 和 `api_key` 取决于你选择的 VLM 服务；部分 provider 可能会使用本地 OAuth 状态，而不是手动填写 API key。

各模型服务的完整配置示例请参见 [配置指南 - 配置示例](../guides/01-configuration.md#配置示例)。

首次配置建议优先使用 `openviking-server init`，它会帮助你选择 provider，并生成对应场景可直接使用的配置模板。

### 设置环境变量

配置文件放在默认路径 `~/.openviking/ov.conf` 时，无需额外设置，OpenViking 会自动加载。

如果配置文件放在其他位置，需要通过环境变量指定：

```bash
export OPENVIKING_CONFIG_FILE=/path/to/your/ov.conf
```

## 启动本地服务

通过 Python 包安装并完成配置后，启动服务。如果 Docker 已运行，跳过此步骤：

```bash
openviking-server
```

保持服务运行，然后打开另一个终端执行下面的 Python SDK 示例。
如果使用自定义配置路径，通过 `openviking-server --config /path/to/ov.conf` 启动。

默认本地模式不需要 API Key；连接启用鉴权的 Server 时，先设置
`OPENVIKING_API_KEY`。

## 运行第一个示例

### 创建 Python 脚本

创建 `example.py`：

```python
import time

from openviking_sdk import SyncHTTPClient

# 连接本地 OpenViking Server
client = SyncHTTPClient(url="http://localhost:1933")

try:
    # 检查连接
    client.initialize()

    # Add resource (supports URL, file, or directory)
    # Local directory scans respect .gitignore by default.
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
    )

    task_id = add_result["task_id"]
    print(f"Import task: {task_id}")
    while True:
        task = client.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} is no longer available")
        if task["status"] == "completed":
            break
        if task["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Import task {task_id}: {task['status']} ({task.get('error')})")
        time.sleep(2)
    root_uri = task["result"]["root_uri"]

    # Explore the resource tree structure
    ls_result = client.ls(uri=root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Use glob to find markdown files
    glob_result = client.glob(pattern="**/*.md", uri=root_uri)
    if glob_result['matches']:
        content = client.read(uri=glob_result["matches"][0])
        print(f"Content preview: {content[:200]}...\n")

    # Get abstract and overview of the resource
    abstract = client.abstract(uri=root_uri)
    overview = client.overview(uri=root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Perform semantic search
    results = client.find(
        query="what is openviking",
        target_uri=root_uri,
    )
    print("Search results:")
    for result in results.get("resources", []):
        print(f"  {result['uri']} (score: {result.get('score', 0.0):.4f})")

finally:
    client.close()
```

### 运行脚本

SDK 需要安装在运行脚本的 Python 环境中。[`uv tool install`](https://docs.astral.sh/uv/guides/tools/#installing-tools) 和 `pipx install` 会隔离服务端依赖，不会把 SDK 安装到当前 Python 环境；Docker 也不会在宿主机安装客户端。

::: code-group

```bash [uv]
uv run --with openviking-sdk python example.py
```

```bash [pip（在你的虚拟环境中）]
python -m pip install --upgrade openviking-sdk
python example.py
```

:::

### 预期输出

```
Import task: ...

Directory structure:
...

Content preview: ...

Abstract:
...

Overview:
...

Search results:
  viking://resources/... (score: 0.8523)
  ...
```

恭喜！你已成功运行 OpenViking。

## 服务端模式

想要将 OpenViking 作为共享服务运行？请参见 [快速开始：服务端模式](03-quickstart-server.md)。

## 下一步

- [配置详解](../guides/01-configuration.md) - 详细配置选项
- [API 概览](../api/01-overview.md) - API 参考
- [资源管理](../api/02-resources.md) - 资源管理 API
