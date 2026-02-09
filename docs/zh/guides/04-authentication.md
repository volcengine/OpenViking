# 认证

OpenViking Server 支持 API Key 认证以保护访问安全。

## API Key 认证

### 设置（服务端）

**方式一：命令行**

```bash
python -m openviking serve --path ./data --api-key "your-secret-key"
```

**方式二：环境变量**

```bash
export OPENVIKING_API_KEY="your-secret-key"
python -m openviking serve --path ./data
```

**方式三：配置文件**（通过 `OPENVIKING_CONFIG_FILE`）

```json
{
  "server": {
    "api_key": "your-secret-key"
  }
}
```

### 使用 API Key（客户端）

OpenViking 通过以下两种请求头接受 API Key：

**X-API-Key 请求头**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-secret-key"
```

**Authorization: Bearer 请求头**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "Authorization: Bearer your-secret-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(
    url="http://localhost:1933",
    api_key="your-secret-key"
)
```

或使用 `OPENVIKING_API_KEY` 环境变量：

```bash
export OPENVIKING_URL="http://localhost:1933"
export OPENVIKING_API_KEY="your-secret-key"
```

```python
import openviking as ov

# api_key 自动从 OPENVIKING_API_KEY 环境变量读取
client = ov.OpenViking()
```

## 开发模式

当未配置 API Key 时，认证功能将被禁用。所有请求无需凭证即可被接受。

```bash
# 不指定 --api-key 参数 = 禁用认证
python -m openviking serve --path ./data
```

## 无需认证的端点

`/health` 端点无论配置如何，都不需要认证。这允许负载均衡器和监控工具检查服务器健康状态。

```bash
curl http://localhost:1933/health
# 始终可用，无需 API Key
```

## 相关文档

- [部署](03-deployment.md) - 服务器设置
- [API 概览](../api/01-overview.md) - API 参考
