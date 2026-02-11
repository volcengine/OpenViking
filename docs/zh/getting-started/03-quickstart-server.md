# 快速开始：服务端模式

将 OpenViking 作为独立 HTTP 服务运行，并从任意客户端连接。

## 前置要求

- 已安装 OpenViking（`pip install openviking`）
- 模型配置已就绪（参见 [快速开始](02-quickstart.md) 了解配置方法）

## 启动服务

确保 `ov.conf` 已配置好存储路径和模型信息（参见 [快速开始](02-quickstart.md)），然后启动服务：

```bash
# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
python -m openviking serve

# 配置文件在其他位置时，通过 --config 指定
python -m openviking serve --config /path/to/ov.conf

# 覆盖 host/port
python -m openviking serve --port 8000
```

你应该看到：

```
INFO:     Uvicorn running on http://0.0.0.0:1933
```

## 验证

```bash
curl http://localhost:1933/health
# {"status": "ok"}
```

## 使用 Python SDK 连接

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933")
```

如果服务端启用了认证，需要传入 `api_key`：

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933", api_key="your-key")
```

**完整示例：**

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933")

try:
    client.initialize()

    # Add a resource
    result = client.add_resource(
        "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = result["root_uri"]

    # Wait for processing
    client.wait_processed()

    # Search
    results = client.find("what is openviking", target_uri=root_uri)
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

finally:
    client.close()
```

## 使用 CLI 连接

创建 CLI 连接配置文件 `~/.openviking/ovcli.conf`：

```json
{
  "url": "http://localhost:1933"
}
```

然后直接使用 CLI 命令：

```bash
python -m openviking health
python -m openviking find "what is openviking"
```

如果配置文件在其他位置，通过环境变量指定：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## 使用 curl 连接

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

## 下一步

- [服务部署](../guides/03-deployment.md) - 配置、认证和部署选项
- [API 概览](../api/01-overview.md) - 完整 API 参考
- [认证](../guides/04-authentication.md) - 使用 API Key 保护你的服务
