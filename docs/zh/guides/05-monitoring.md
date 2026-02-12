# 监控与健康检查

OpenViking Server 提供了用于监控系统健康状态和组件状态的端点。

## 健康检查

`/health` 端点提供简单的存活检查，不需要认证。

```bash
curl http://localhost:1933/health
```

```json
{"status": "ok"}
```

## 系统状态

### 整体系统健康状态

**Python SDK (Embedded / HTTP)**

```python
status = client.get_status()
print(f"Healthy: {status['is_healthy']}")
print(f"Errors: {status['errors']}")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "errors": [],
    "components": {
      "queue": {"name": "queue", "is_healthy": true, "has_errors": false},
      "vikingdb": {"name": "vikingdb", "is_healthy": true, "has_errors": false},
      "vlm": {"name": "vlm", "is_healthy": true, "has_errors": false}
    }
  }
}
```

### 组件状态

检查各个组件的状态：

| 端点 | 组件 | 描述 |
|------|------|------|
| `GET /api/v1/observer/queue` | Queue | 处理队列状态 |
| `GET /api/v1/observer/vikingdb` | VikingDB | 向量数据库状态 |
| `GET /api/v1/observer/vlm` | VLM | 视觉语言模型状态 |

### 快速健康检查

**Python SDK (Embedded / HTTP)**

```python
if client.is_healthy():
    print("System OK")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/debug/health \
  -H "X-API-Key: your-key"
```

```json
{"status": "ok", "result": {"healthy": true}}
```

## 响应时间

每个 API 响应都包含一个 `X-Process-Time` 请求头，其中包含服务端处理时间（单位为秒）：

```bash
curl -v http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key" 2>&1 | grep X-Process-Time
# < X-Process-Time: 0.0023
```

## 相关文档

- [部署](03-deployment.md) - 服务器设置
- [系统 API](../api/07-system.md) - 系统 API 参考
