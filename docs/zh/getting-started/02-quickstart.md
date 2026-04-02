# 快速开始

5 分钟上手 OpenViking。

## 前置要求

在开始使用 OpenViking 之前，请确保您的环境满足以下要求：

- **Python 版本**：3.10 或更高版本
- **操作系统**：Linux、macOS、Windows
- **网络连接**：需要稳定的网络连接（用于下载依赖包和访问模型服务）

## 安装与启动

OpenViking 支持通过 Python Package 安装作为本地库使用，也支持通过 Docker 快速启动独立服务。

### 方式一：通过 pip 安装 (作为本地库)

```bash
pip install openviking --upgrade --force-reinstall
```

### 方式二：通过 Docker 启动 (作为独立服务)

如果你希望将 OpenViking 作为独立的服务运行，推荐使用 Docker。

1. **准备配置文件与数据目录**
   在宿主机上创建数据目录，并准备好 `ov.conf` 配置文件（配置项参考下方“配置环境”章节）：
   ```bash
   mkdir -p ~/.openviking/data
   touch ~/.openviking/ov.conf
   ```

2. **使用 Docker Compose 启动**
   创建 `docker-compose.yml` 文件：
   ```yaml
   services:
     openviking:
       image: ghcr.io/volcengine/openviking:latest
       container_name: openviking
       ports:
         - "1933:1933"
         - "8020:8020"
       volumes:
         - ~/.openviking/ov.conf:/app/ov.conf
         - ~/.openviking/data:/app/data
       restart: unless-stopped
   ```
   然后在同目录下执行启动命令：
   ```bash
   docker-compose up -d
   ```

   默认情况下，容器会同时启动 OpenViking API 服务（`1933`）、Console 界面（`8020`）以及内置的 `vikingbot` gateway。如果你需要关闭 `vikingbot`，可以在 Compose 里增加 `command: ["--without-bot"]`，或者设置 `environment: ["OPENVIKING_WITH_BOT=0"]`。

> **💡 Mac 本地网络访问提示 (Connection reset 报错)：**
>
> 默认情况下，OpenViking 为了安全仅监听 `127.0.0.1`。如果你在 Mac 上使用 Docker，宿主机可能无法直接通过 `localhost:1933` 访问。
> 
> **推荐解决方案：使用 socat 端口转发（无需修改配置）：**
> 在你的 `docker-compose.yml` 中覆盖默认启动命令，利用 `socat` 在容器内部进行端口转发：
> ```yaml
> services:
>   openviking:
>     image: ghcr.io/volcengine/openviking:latest
>     ports:
>       - "8020:8020"
>       - "1933:1934" # 将宿主机 1933 映射到容器 1934
>     volumes:
>       - ~/.openviking/ov.conf:/app/ov.conf
>       - ~/.openviking/data:/app/data
>     command: /bin/sh -c "apt-get update && apt-get install -y socat && socat TCP-LISTEN:1934,fork,reuseaddr TCP:127.0.0.1:1933 & openviking-server"
> ```
> 这样即可完美解决 Mac 宿主机的访问问题。

## 模型准备

OpenViking 需要以下模型能力：
- **VLM 模型**：用于图像和内容理解
- **Embedding 模型**：用于向量化和语义检索

OpenViking 支持多种模型服务：
- **火山引擎（豆包模型）**：推荐使用，成本低、性能好，新用户有免费额度。如需购买和开通，请参考：[火山引擎购买指南](../guides/02-volcengine-purchase-guide.md)
- **OpenAI 模型**：支持 GPT-4V 等 VLM 模型和 OpenAI Embedding 模型
- **其他自定义模型服务**：支持兼容 OpenAI API 格式的模型服务

## 配置环境

### 配置文件模版

创建配置文件 `~/.openviking/ov.conf`：

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

各模型服务的完整配置示例请参见 [配置指南 - 配置示例](../guides/01-configuration.md#配置示例)。

### 设置环境变量

配置文件放在默认路径 `~/.openviking/ov.conf` 时，无需额外设置，OpenViking 会自动加载。

如果配置文件放在其他位置，需要通过环境变量指定：

```bash
export OPENVIKING_CONFIG_FILE=/path/to/your/ov.conf
```

## 运行第一个示例

### 创建 Python 脚本

创建 `example.py`：

```python
import openviking as ov

# Initialize OpenViking client with data directory
client = ov.OpenViking(path="./data")

try:
    # Initialize the client
    client.initialize()

    # Add resource (supports URL, file, or directory)
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = add_result['root_uri']

    # Explore the resource tree structure
    ls_result = client.ls(root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Use glob to find markdown files
    glob_result = client.glob(pattern="**/*.md", uri=root_uri)
    if glob_result['matches']:
        content = client.read(glob_result['matches'][0])
        print(f"Content preview: {content[:200]}...\n")

    # Wait for semantic processing to complete
    print("Wait for semantic processing...")
    client.wait_processed()

    # Get abstract and overview of the resource
    abstract = client.abstract(root_uri)
    overview = client.overview(root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Perform semantic search
    results = client.find("what is openviking", target_uri=root_uri)
    print("Search results:")
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

    # Close the client
    client.close()

except Exception as e:
    print(f"Error: {e}")
```

### 运行脚本

```bash
python example.py
```

### 预期输出

```
Directory structure:
...

Content preview: ...

Wait for semantic processing...
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
