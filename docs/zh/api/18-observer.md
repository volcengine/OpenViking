# 运行观测

Observer API 提供队列、向量库、模型、锁、检索和文件系统等组件的即时状态。

## Observer API

以下 Python 示例使用 `SyncHTTPClient`。Observer 接口是返回字典的属性，使用 `client.observer.queue`，不加括号。每次访问都会请求服务端；需要读取多个字段时，先保存返回值。

### observer.queue

#### 1. API 实现介绍

获取队列系统状态（embedding 和语义处理队列）。显示各队列的待处理、进行中、已完成和错误数量。

**代码入口**:
- `openviking/server/routers/observer.py:observer_queue` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.queue` - 核心实现
- `openviking/storage/observers/queue_observer.py` - 队列观察者

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/queue
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/queue \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
status = client.observer.queue
print(status["is_healthy"])
print(status["status"])
```

**TypeScript SDK**

```typescript
console.log(await client.queueStatus());
```

**Go SDK**

```go
status, err := client.QueueStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer queue
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "queue",
    "is_healthy": true,
    "has_errors": false,
    "status": "Queue                 Pending  In Progress  Processed  Errors  Total\nEmbedding             0        0            10         0       10\nSemantic              0        0            10         0       10\nTOTAL                 0        0            20         0       20"
  }
}
```

---

### observer.vikingdb

#### 1. API 实现介绍

获取 VikingDB 状态（集合、索引、向量数量）。

**代码入口**:
- `openviking/server/routers/observer.py:observer_vikingdb` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.vikingdb` - 核心实现
- `openviking/storage/observers/vikingdb_observer.py` - VikingDB 观察者
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/vikingdb
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/vikingdb \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
status = client.observer.vikingdb
print(status["is_healthy"])
print(status["status"])
```

**TypeScript SDK**

```typescript
console.log(await client.vikingDBStatus());
```

**Go SDK**

```go
status, err := client.VikingDBStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer vikingdb
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "vikingdb",
    "is_healthy": true,
    "has_errors": false,
    "status": "Collection  Index Count  Vector Count  Status\ncontext     1            55            OK\nTOTAL       1            55"
  }
}
```

---

### observer.models

#### 1. API 实现介绍

获取 VLM、Embedding 和 Rerank 的模型实例及 token 用量信息。`is_healthy` 表示至少存在一个模型实例，不会逐一探测提供者是否可达。Embedding 连通性可查看 `/ready`，其他模型应结合实际请求错误排查。

**代码入口**:
- `openviking/server/routers/observer.py:observer_models` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.models` - 核心实现
- `openviking/storage/observers/models_observer.py` - 模型观察者
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/models
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/models \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
status = client.observer.models
print(status["is_healthy"])
print(status["status"])
```

**TypeScript SDK**

```typescript
console.log(await client.modelsStatus());
```

**Go SDK**

```go
status, err := client.ModelsStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer models
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "models",
    "is_healthy": true,
    "has_errors": false,
    "status": "No model usage data available."
  }
}
```

---

### observer.lock

#### 1. API 实现介绍

获取分布式锁系统状态。

**代码入口**:
- `openviking/server/routers/observer.py:observer_lock` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.lock` - 核心实现
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/lock
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/lock \
  -H "X-API-Key: your-key"
```

公开 SDK 和 CLI 目前没有单独的 lock observer 方法。请使用 HTTP API 查询该组件；`ov observer system` 会在汇总状态中包含它。

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "lock",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  }
}
```

---

### observer.retrieval

#### 1. API 实现介绍

获取已记录的查询次数、结果数、分数、Rerank 使用情况和延迟。这些数据用于诊断，不能直接衡量结果相关性；空结果也是有效结果，不会使该组件被判定为不健康。

**代码入口**:
- `openviking/server/routers/observer.py:observer_retrieval` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.retrieval` - 核心实现
- `openviking/storage/observers/retrieval_observer.py` - 检索观察者
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/retrieval
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/retrieval \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov observer retrieval
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "retrieval",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  }
}
```

---

### observer.filesystem

#### 1. API 实现介绍

获取文件系统操作指标。

**代码入口**:
- `openviking/server/routers/observer.py:observer_filesystem` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.filesystem` - 核心实现
- `openviking/storage/observers/filesystem_observer.py` - 文件系统观察者
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/filesystem
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/filesystem \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov observer filesystem
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "filesystem",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  }
}
```

---

### observer.system

#### 1. API 实现介绍

获取整体系统状态，包括所有组件（queue、vikingdb、models、lock、retrieval、filesystem）。

**代码入口**:
- `openviking/server/routers/observer.py:observer_system` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.system` - 核心实现
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/system
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
status = client.observer.system
print(status["is_healthy"])
print(status["components"])
```

**TypeScript SDK**

```typescript
console.log(await client.getStatus());
```

**Go SDK**

```go
status, err := client.GetStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer system
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "errors": [],
    "components": {
      "queue": {
        "name": "queue",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "vikingdb": {
        "name": "vikingdb",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "models": {
        "name": "models",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "lock": {
        "name": "lock",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "retrieval": {
        "name": "retrieval",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "filesystem": {
        "name": "filesystem",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      }
    }
  }
}
```

---

## 相关文档

- [Metrics](09-metrics.md) - Prometheus 指标抓取
- [系统状态](07-system.md) - 健康检查和一致性检查
