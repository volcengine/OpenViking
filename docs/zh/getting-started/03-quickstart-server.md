# 快速开始：服务端模式

将 OpenViking 作为独立 HTTP 服务运行，并从任意客户端连接。

## 前置要求

- 已安装 OpenViking（`pip install openviking`）
- 模型配置已就绪（参见 [快速开始](02-quickstart.md) 了解配置方法）

## 启动服务

```bash
python -m openviking serve --path ./data
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

或使用环境变量：

```bash
export OPENVIKING_URL="http://localhost:1933"
export OPENVIKING_API_KEY="your-key"  # 如果启用了认证
```

```python
import openviking as ov

# url 和 api_key 自动从环境变量读取
client = ov.OpenViking()
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
