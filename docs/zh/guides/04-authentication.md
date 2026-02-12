# 认证

OpenViking Server 支持 API Key 认证以保护访问安全。

## API Key 认证

### 设置（服务端）

在 `ov.conf` 配置文件中设置 `server.api_key`：

```json
{
  "server": {
    "api_key": "your-secret-key"
  }
}
```

启动服务时通过 `--config` 指定配置文件：

```bash
python -m openviking serve
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

**Python SDK (HTTP)**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-secret-key"
)
```

**CLI**

CLI 从 `ovcli.conf` 配置文件读取连接信息。在 `~/.openviking/ovcli.conf` 中配置 `api_key`：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key"
}
```

## 开发模式

当 `ov.conf` 中未配置 `server.api_key` 时，认证功能将被禁用。所有请求无需凭证即可被接受。

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933
  }
}
```

```bash
# 未配置 api_key = 禁用认证
python -m openviking serve
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
