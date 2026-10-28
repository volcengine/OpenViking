# 运行观测

Observer API 提供队列、向量库、模型、锁、检索和文件系统等组件的即时状态。

## Observer API

以下 Python 示例使用 `SyncHTTPClient`。Observer 接口是返回字典的属性，使用 `client.observer.queue`，不加括号。每次访问都会请求服务端；需要读取多个字段时，先保存返回值。

### observer.queue

#### 1. API 实现介绍

获取队列系统状态，包括 embedding、语义处理和会话提交队列。显示各队列的待处理、进行中、已处理投递、重新入队和投递错误数量。

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

### 排查会话提交失败

队列计数描述投递，不代表所属任务的最终结果。`Processed` 包括重新入队和取消的投递；`Errors` 记录投递失败。例如，恢复带有 `.failed.json` 标记的归档时，`SessionCommit` 可以在将任务标记为 `failed` 后成功结算这次投递。因此，`Errors: 0` 或 `is_healthy: true` 都不能证明所有归档已成功产出记忆。

通过已有的 [Task API](17-tasks.md) 或 CLI 查询失败任务：

```bash
ov task list --task-type session_commit --status failed
```

需要查询多页时，在 HTTP 任务列表中传入 `task_type=session_commit`、`status=failed` 和 `pagination=cursor`。当 `has_more` 为 true 时，保持筛选条件，将返回的 `next_cursor` 作为 `cursor` 继续查询。单页结果数不是失败总数。任务查询受调用者访问范围和任务保留期约束，不会统计磁盘上的全部归档标记。

已有的 `openviking_task_failed{task_type="session_commit"}` Gauge 可辅助排查，但需结合[指标说明](../concepts/12-metrics.md)中的任务存储快照限制解读。

### GET /api/v1/observer/session-archives

统计当前用户会话命名空间中的持久归档标记，不受任务列表分页、任务记录保留期或进程内任务缓存影响。ROOT 调用者同样使用当前账号和用户的命名空间；这不是跨账号或全部用户的统计。

```bash
curl http://localhost:1933/api/v1/observer/session-archives \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "total": 65,
    "completed": 10,
    "failed": 54,
    "pending": 1
  }
}
```

每个现存的 `archive_NNN` 目录只计一次：存在 `.done` 为 `completed`；否则存在 `.failed.json` 为 `failed`；两种标记均无则为 `pending`。两种标记同时存在时，与会话恢复一致，优先按 `.done` 计入。任务记录过期后，失败归档仍可被统计；归档被删除后则移出计数。完成标记不衡量记忆质量或实际提取的记忆数量。

该接口按需读取全部可见归档，历史较多时成本可能较高。并发提交或删除期间，结果不是事务快照。存储异常会导致请求失败，不会返回误导性的零失败结果。可使用 `result.failed` 进行归档失败告警；队列投递计数及其健康状态语义保持不变。

### observer.vikingdb

#### 1. API 实现介绍

获取 VikingDB 状态。结构化响应还会在后端能够从已加载索引中确定时，返回实际向量 metric 和纯 dense 分数尺度。

**代码入口**:
- `openviking/server/routers/observer.py:observer_vikingdb` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.account_vikingdb` - 核心实现
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

#### 2. 接口和参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `format` | string | 否 | `table` | `table` 返回原有可读状态；`json` 返回结构化运行时详情。 |

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/observer/vikingdb?format=json
```

```bash
curl -X GET 'http://localhost:1933/api/v1/observer/vikingdb?format=json' \
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
    "status": {
      "backend": "local",
      "collection": "context",
      "index": "default",
      "dimension": 1024,
      "vector_count": 55,
      "distance_metric": "cosine",
      "pure_dense_score_scale": "cosine_affine_0_1"
    }
  }
}
```

对于 local 后端，cosine、IP 和 L2 对应的 `pure_dense_score_scale` 分别为 `cosine_affine_0_1`、`inner_product` 和 `one_minus_squared_l2`。其他后端返回 `backend_defined`；如果已加载的元数据没有暴露 metric，`distance_metric` 为 `null`。

该字段只描述纯 dense 向量分数。稀疏融合、时间衰减、rerank 等检索阶段可能产生不同尺度的最终 `score`。从 v0.4.22 起，local 纯 dense cosine 使用 `clamp((cosine_similarity + 1) / 2, 0, 1)`，已有索引也适用。

---

### observer.models

#### 1. API 实现介绍

获取当前账号的 VLM、Embedding 配置和 token 用量信息。观察器解析账号模型信息时未发生错误，`is_healthy` 才为 true；它不会逐一探测提供者是否可达。Embedding 连通性可查看 `/ready`，其他模型应结合实际请求错误排查。

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
    "status": "Account: default\nEmbedding dimension: 1024\nNo model usage data available."
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
