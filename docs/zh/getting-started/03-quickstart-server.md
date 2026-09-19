# 快速开始：服务端模式

将 OpenViking 作为独立 HTTP 服务运行，并从任意客户端连接。

## 前置要求

- 已安装 OpenViking（`uv tool install openviking --upgrade`）
- 模型配置已就绪（参见 [快速开始](02-quickstart.md) 了解配置方法）

> Python 3.14 说明（适用于火山方舟 / Volcengine Ark）：
> 如果你的 `ov.conf` 中使用了 `provider = "volcengine"` / Ark runtime，当前建议优先使用 Python 3.13 及以下版本运行 `openviking-server`。
> 这是因为 `volcengine-python-sdk[ark]` 仍会在 Python 3.14 下输出 Pydantic V1 兼容性警告，服务通常仍可运行，但启动命令和 `--version` 等输出会带有噪声，直到上游 SDK 去掉这层兼容逻辑。

## 启动服务

确保 `ov.conf` 已配置好存储路径和模型信息（参见 [快速开始](02-quickstart.md)），然后启动服务：

如果是首次配置，建议先运行 `openviking-server init`。

启动前建议先做本地校验：

```bash
openviking-server doctor
```

`openviking-server doctor` 会校验当前本地配置是否可用，包括各 provider 需要的鉴权状态。

```bash
# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
openviking-server

# 配置文件在其他位置时，通过 --config 指定
openviking-server --config /path/to/ov.conf

# 使用其他端口
openviking-server --port 8000
```

你应该看到：

```
INFO:     Uvicorn running on http://127.0.0.1:1933
```

## 验证

```bash
curl http://localhost:1933/health
# {"status": "ok"}
```

`openviking-server doctor` 用于校验本地配置、模型访问和鉴权状态；`curl /health` 只表示服务进程已经启动。

Web Studio 也会在 `http://localhost:1933/studio` 提供（自 v0.3.21 起 pip/pipx 安装即自带，无需 Docker）。

## 使用 Python SDK 连接

在客户端的 Python 环境中安装独立 SDK：

```bash
python -m pip install --upgrade openviking-sdk
```

`uv tool` 或 pipx 安装的服务端使用独立环境。也可以将脚本保存后，通过 `uv run --with openviking-sdk python example.py` 运行。

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933")
```

### 启用认证

服务端启用认证后，需要传入 `api_key`。OpenViking 使用两层 API Key 体系，请根据场景选择：

**常规数据访问：使用 `user_key` 或 `admin_key`**

大多数场景应使用 `user_key`，也可以使用绑定到管理员用户的 `admin_key`。两者都可直接调用 `add_resource`、`find`、`ls` 等租户级 API：

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
)
```

> `user_key` / `admin_key` 通过 Admin API 创建（参见 [认证文档](../guides/04-authentication.md)），服务端可自动识别其所属租户和用户。

**管理操作：使用 `root_key`**

`root_key` 只适用于管理操作（创建账户、系统状态等）和少量 system/monitoring API。常规数据访问不要使用 `root_key`，也不要通过 `account` / `user` header 模拟某个用户；数据面应使用对应的 `user_key` 或 `admin_key`。

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<root-key>",
)
```

> 如果确实需要由上游网关注入 `account` / `user` 身份，应使用 `trusted` 模式；在默认 `api_key` 模式下，只有绑定了用户身份的 user/admin key 能访问自己的数据空间。

更多认证细节（trusted 模式、CLI 配置等）请参见 [认证文档](../guides/04-authentication.md)。

**完整示例：**

连接启用认证的服务时，先将 `OPENVIKING_API_KEY` 设置为 user/admin key。SDK 会自动读取这个环境变量。

```python
import time

from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933")

try:
    client.initialize()

    # Add a resource
    result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
    )

    task_id = result["task_id"]
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

    # Search
    results = client.find(
        query="what is openviking",
        target_uri=root_uri,
    )
    for result in results.get("resources", []):
        print(f"  {result['uri']} (score: {result.get('score', 0.0):.4f})")

finally:
    client.close()
```

## 使用 CLI 连接

默认本地服务可省略 `api_key`。启用认证时，将下方的 `your-key` 替换为 user/admin key。

创建 CLI 连接配置文件 `~/.openviking/ovcli.conf`：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

然后直接使用 CLI 命令：

```bash
# Check system health
openviking observer system

# Add a resource to memory
openviking add-resource https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md

# List all synchronized resources
openviking ls viking://resources

# Query
openviking find "what is openviking"
```

如果配置文件在其他位置，通过环境变量指定：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## 使用 curl 连接

下面的示例使用默认本地服务。启用认证时，为每个请求增加 `-H "X-API-Key: $OPENVIKING_API_KEY"`，使用 user/admin key。资源导入为异步操作：根据返回的 `task_id` 轮询 `GET /api/v1/tasks/{task_id}`，完成后再检索。

远端 URL 可以直接放在 `path` 里。本地文件需要先调用 `POST /api/v1/resources/temp_upload` 上传，再用返回的 `temp_file_id` 调目标 API。裸 HTTP 如果导入本地目录，需要先把目录打成 `.zip` 再上传。

```bash
# Add a resource
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{"path": "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"}'

# List resources
curl "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/"

# Semantic search
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -d '{"query": "what is openviking"}'
```

## 部署到云服务器

使用火山引擎 ECS 时，在 [官方 ECS 控制台](https://console.volcengine.com/ecs/) 创建实例。持久化存储、Docker 和服务管理请参见 [部署指南](../guides/03-deployment.md)。

本地服务默认监听 `127.0.0.1`。允许远程客户端访问前，需要配置 [身份认证](../guides/04-authentication.md)、可访问的监听地址和必要的网络访问规则。TLS 与反向代理配置请参见 [公网访问](../guides/12-public-access.md)。数据访问使用绑定租户和用户身份的 user/admin key。

## 下一步

- [服务部署](../guides/03-deployment.md) - 配置、认证和部署选项
- [API 概览](../api/01-overview.md) - 完整 API 参考
- [认证](../guides/04-authentication.md) - 使用 API Key 保护你的服务
