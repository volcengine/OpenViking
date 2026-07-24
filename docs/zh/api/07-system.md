# 系统状态

OpenViking 系统 API 提供健康检查、就绪检查、一致性检查和多写后端同步状态。组件级观测和 Prometheus 指标分别提供独立文档。

## API 参考

### health

#### 1. API 实现介绍

基础健康检查端点，无需认证。返回服务版本号和健康状态。如果提供认证信息，还会返回认证模式和身份信息。

**代码入口**:
- `openviking/server/routers/system.py:health_check` - HTTP 路由
- `openviking_cli/client/sync_http.py:SyncHTTPClient.health` - SDK 入口
- `crates/ov_cli/src/commands/system.rs` - CLI 命令

#### 2. 接口和参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| profile | string | 否 | - | 传 `1`、`true`、`yes` 或 `on` 时，为本次请求开启 `cProfile`，并在 JSON 响应里追加 `profile` 字段 |

**profile 行为说明**:
- `profile` 是 HTTP middleware 级能力，对任意返回 JSON 的 OpenViking 接口都生效，不限于 `/health`。
- 仅当服务端在 `ov.conf` 中开启 `server.profile_enabled = true` 时，请求里的 `profile=1` 才会生效；否则服务端会忽略该参数。
- `profile` 仅对当前请求生效，请求结束后自动关闭；后续请求默认不会继承这次 profile 状态。
- 仅 JSON 响应会追加 `profile` 字段；纯文本、文件、流式响应不会被改写。
- `profile` 的返回值是 `list[string]`，每个元素对应一行格式化后的 `pstats` 输出，便于浏览器直接查看和前端按行渲染。
- `ov` CLI 会显示返回的 `profile`；Python HTTP client 可以通过 `ovcli.conf.profile = true` 触发服务端 profile，但大多数 SDK 方法默认只返回业务 `result`，不会把顶层 `profile` 一并暴露给调用方。

**profile 表头字段说明**:
- `ncalls`: 调用次数。若显示为 `总调用次数/原始调用次数`，前者是总调用数，后者是 primitive calls。
- `tottime`: 函数自身耗时，总时间，不包含其调用的子函数耗时。
- `percall`（第一列）: `tottime / ncalls`，即函数自身平均每次调用耗时。
- `cumtime`: 累计耗时，包含当前函数及其所有子调用耗时。
- `percall`（第二列）: `cumtime / primitive calls`，即按原始调用计算的平均累计耗时。
- `filename:lineno(function)`: 函数定义位置。普通 Python 代码会显示为裁剪后的模块路径；`~:0(...)` 这类条目通常表示 builtin 或 C 扩展调用。

#### 3. 使用示例

**HTTP API**

```
GET /health
```

```bash
curl -X GET http://localhost:1933/health
```

```bash
curl -G http://localhost:1933/health \
  --data-urlencode "profile=1"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

healthy = client.health()
print(f"Healthy: {healthy}")
```

**TypeScript SDK**

```typescript
console.log(await client.health());
```

**Go SDK**

```go
healthy, err := client.Health(ctx)
if err != nil {
    return err
}
fmt.Println(healthy)
```

**CLI**

```bash
ov system health
```

```bash
ov --profile health
```

**响应示例**

```json
{
  "status": "ok",
  "healthy": true,
  "version": "0.1.x",
  "auth_mode": "api_key"
}
```

**带 profile 的响应示例**

```json
{
  "status": "ok",
  "healthy": true,
  "version": "0.1.x",
  "profile": [
    "         325 function calls (310 primitive calls) in 0.004 seconds",
    "",
    "   Ordered by: cumulative time",
    "   List reduced from 87 to 87 due to restriction <100>",
    "",
    "   ncalls  tottime  percall  cumtime  percall filename:lineno(function)",
    "        1    0.000    0.000    0.003    0.003 starlette/middleware/base.py:112(call_next)",
    "        1    0.000    0.000    0.001    0.001 openviking/server/routers/system.py:39(health_check)",
    "        3    0.000    0.000    0.000    0.000 ~:0(<method 'read' of 'builtins.RAGFSBindingClient' objects>)"
  ]
}
```

---

### ready

#### 1. API 实现介绍

部署环境使用的就绪探针。检查 AGFS、VectorDB、APIKeyManager 和 Ollama（如配置）的状态。当所有配置的子系统都准备完成时返回 200，否则返回 503。无需认证（专为 Kubernetes 探针设计）。

**代码入口**:
- `openviking/server/routers/system.py:readiness_check` - HTTP 路由

#### 2. 接口和参数说明

无参数。

**检查项说明**:
- `agfs`: Viking 文件系统是否可访问
- `vectordb`: 向量数据库是否健康
- `api_key_manager`: API 密钥管理器是否已加载
- `ollama`: Ollama 服务是否可达（仅当配置时）

#### 3. 使用示例

**HTTP API**

```
GET /ready
```

```bash
curl -X GET http://localhost:1933/ready
```

**响应示例**

```json
{
  "status": "ready",
  "checks": {
    "agfs": "ok",
    "vectordb": "ok",
    "api_key_manager": "ok",
    "ollama": "not_configured"
  }
}
```

---

### status

#### 1. API 实现介绍

获取系统状态，包括初始化状态和当前认证用户信息。`result.user` 是认证请求的 `user_id`（来自 API 密钥或请求头），而非进程级服务默认值，客户端可用于解析多租户路径。

**代码入口**:
- `openviking/server/routers/system.py:system_status` - HTTP 路由
- `openviking_cli/client/sync_http.py:SyncHTTPClient.get_status` - SDK 入口
- `crates/ov_cli/src/commands/system.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/system/status
```

```bash
curl -X GET http://localhost:1933/api/v1/system/status \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
status = client.get_status()
print(status)
```

**TypeScript SDK**

```typescript
console.log(await client.getStatus());
```

**CLI**

```bash
ov system status
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "initialized": true,
    "user": "alice"
  },
  "time": 0.1
}
```

---

### consistency

#### 1. API 实现介绍

检查指定 URI 子树的文件系统内容和向量索引是否一致，用于调试索引缺失、向量快照导出失败等问题。该能力是通用数据一致性检查，不属于 OVPack 私有接口；`ov export --include-vectors` 和 `ov backup --include-vectors` 会复用同一检查。

响应只返回摘要和缺失项，不返回完整 expected 列表。`missing_records` 最多返回前 20 条；如果还有更多缺失项，`missing_records_truncated` 为 `true`。

**代码入口**:
- `openviking/server/routers/system.py:check_consistency` - HTTP 路由
- `openviking_cli/client/sync_http.py:SyncHTTPClient.check_consistency` - SDK 入口
- `crates/ov_cli/src/commands/system.rs:consistency` - CLI 命令

#### 2. 接口和参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | string | 是 | - | 要检查的 Viking URI 子树 |

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/system/consistency
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/system/consistency \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/my-project"}'
```

**Python SDK**

```python
report = client.check_consistency("viking://resources/my-project")
print(report["ok"])
print(report["missing_records"])
```

**TypeScript SDK**

```typescript
console.log(await client.checkConsistency("viking://resources/"));
```

**Go SDK**

```go
report, err := client.CheckConsistency(ctx, "viking://resources/my-project")
if err != nil {
    return err
}
fmt.Println(report["ok"])
```

**CLI**

```bash
ov system consistency viking://resources/my-project
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
	    "ok": false,
	    "expected_count": 3,
	    "missing_record_count": 1,
	    "missing_records_truncated": false,
	    "missing_records": [
      {
        "uri": "viking://resources/my-project/README.md",
        "path": "README.md",
        "level": 2,
        "key": "README.md#level=2"
      }
    ]
  }
}
```

---

### wait_processed

#### 1. API 实现介绍

等待所有异步处理（embedding、语义生成）完成。该方法会阻塞直到所有队列中的任务处理完毕或超时。

**代码入口**:
- `openviking/server/routers/system.py:wait_processed` - HTTP 路由
- `openviking_cli/client/sync_http.py:SyncHTTPClient.wait_processed` - SDK 入口
- `crates/ov_cli/src/commands/system.rs` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| timeout | float | 否 | None | 超时时间（秒），None 表示无限等待 |

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/system/wait
```

```bash
curl -X POST http://localhost:1933/api/v1/system/wait \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "timeout": 60.0
  }'
```

**Python SDK**

```python
# 添加资源
client.add_resource("./docs/")

# 等待所有处理完成
status = client.wait_processed(timeout=60.0)
print(f"Processing complete: {status}")
```

**TypeScript SDK**

```typescript
console.log(await client.waitProcessed(60));
```

**Go SDK**

```go
status, err := client.WaitProcessed(ctx, &openviking.WaitProcessedOptions{
    Timeout: openviking.Float64(60),
})
if err != nil {
    return err
}
fmt.Println(status)
```

**CLI**

```bash
ov system wait --timeout 60
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "Embedding": {
      "processed": 10,
      "requeue_count": 0,
      "error_count": 0,
      "errors": []
    },
    "Semantic": {
      "processed": 10,
      "requeue_count": 0,
      "error_count": 0,
      "errors": []
    }
  },
  "time": 0.1
}
```

---

### backend_sync_status()

查询指定 Viking URI 子树在多写存储后端之间的同步状态。该接口要求 ROOT 或 ADMIN 权限。

**HTTP API**

```http
POST /api/v1/system/backend/sync-status
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/system/backend/sync-status \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources"}'
```

也可以使用 URI 路径形式：

```http
GET /api/v1/system/sync/{sync_path}
```

**CLI**

```bash
ov system backend sync-status viking://resources
```

### backend_sync_retry()

重试指定 URI 子树中尚未完成的多写后端同步工作。该接口要求 ROOT 或 ADMIN 权限。

**HTTP API**

```http
POST /api/v1/system/backend/sync-retry
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/system/backend/sync-retry \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources"}'
```

URI 路径形式为：

```http
POST /api/v1/system/sync/{sync_path}/retry
```

**CLI**

```bash
ov system backend sync-retry viking://resources
```

公共 Python、TypeScript 和 Go SDK 当前没有多写后端同步方法，因此以上小节只展示 HTTP 和 CLI Tab。

---

<a id="reindex"></a><a id="observer-api"></a>

## 相关文档

- [Resources](02-resources.md) - 资源管理
- [Retrieval](06-retrieval.md) - 搜索与检索
- [Sessions](05-sessions.md) - 会话管理
- [运行观测](18-observer.md) - 组件即时状态
- [Metrics](09-metrics.md) - Prometheus 指标
