# 系统与监控

OpenViking 提供系统健康检查、可观测性和调试 API，用于监控各组件状态。

## API 参考

### health

#### 1. API 实现介绍

基础健康检查端点，无需认证。返回服务版本号和健康状态。如果提供认证信息，还会返回认证模式和身份信息。

**代码入口**:
- `openviking/server/routers/system.py:health_check` - HTTP 路由
- `openviking_cli/client/sync_http.py:SyncHTTPClient.health` - SDK 入口
- `crates/ov_cli/src/commands/system.rs` - CLI 命令

#### 2. 接口和参数说明

无参数。

#### 3. 使用示例

**HTTP API**

```
GET /health
```

```bash
curl -X GET http://localhost:1933/health
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

healthy = client.health()
print(f"Healthy: {healthy}")
```

**CLI**

```bash
ov system health
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

### reindex()

对已经存储在 OpenViking 中的现有内容，重新构建语义产物和/或向量索引。这是一个运维维护接口，适用于 embedding 模型更换、VLM 更换、向量库重刷、版本升级后修复历史索引等场景。

这个接口面向已有的 `viking://...` 内容，不负责导入新文件。常规导入请使用 [Resources](02-resources.md)。

**认证**

- HTTP 端点：在开启认证时要求 root/admin 权限；root key 请求必须带 `X-OpenViking-Account`
- Python embedded 模式：使用当前 service context
- Python HTTP client / CLI：使用当前认证身份发起请求

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要重新索引的 Viking URI |
| mode | str | 否 | `vectors_only` | 重建模式：`vectors_only` 或 `semantic_and_vectors` |
| wait | bool | 否 | `true` | 是否等待任务完成 |

HTTP 请求体不接受未知字段。`uri` 可以使用其他 content API 支持的 OpenViking 路径变量，服务端会先解析再校验。

**支持的 URI 范围**

- `viking://`
- `viking://user`
- `viking://user/<user_id>`
- `viking://agent`
- `viking://agent/<agent_id>`
- `viking://resources`
- `viking://resources/...`
- `viking://user/<user_id>/memories/...`
- `viking://agent/<agent_id>/memories/...`
- `viking://agent/<agent_id>/skills`
- `viking://agent/<agent_id>/skills/<skill_name>`

`reindex()` 不支持 `viking://session/...`。

**模式说明**

- `vectors_only`：基于当前仍可恢复的源数据重建向量库记录，不会重写 `.abstract.md` 和 `.overview.md`
- `semantic_and_vectors`：先重新生成语义产物，再基于新的语义结果重建向量

对于 `resource` 和 `skill`，`semantic_and_vectors` 会刷新目录/文件语义产物，包括 `.abstract.md` 和 `.overview.md`。对于 `memory`，它会重建当前已持久化 memory 子树的语义和向量，但不会回放历史记忆抽取顺序。

对于 `semantic_and_vectors`，语义刷新和向量重建由 reindex executor 串行编排。语义刷新阶段不会再额外向后台 embedding queue 投递自己的向量化任务；向量由 reindex 阶段统一重建，因此 `wait=true` 表示等待 reindex 操作本身完成。

**Python SDK (Embedded / HTTP)**

```python
result = client.reindex(
    uri="viking://resources",
    mode="vectors_only",
    wait=True,
)
print(result)
```

```python
result = client.reindex(
    uri="viking://agent/default/skills",
    mode="semantic_and_vectors",
    wait=False,
)
print(result["status"])
```

**HTTP API**

```
POST /api/v1/content/reindex
```

不存在 `/api/v1/maintenance/reindex` 端点。请使用 `/api/v1/content/reindex`。

```bash
curl -X POST http://localhost:1933/api/v1/content/reindex \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -d '{
    "uri": "viking://resources",
    "mode": "vectors_only",
    "wait": true
  }'
```

**CLI**

```bash
openviking reindex viking://resources --mode vectors_only
```

```bash
openviking reindex viking://agent/default/skills --mode semantic_and_vectors --wait false
```

**同步响应（`wait=true`）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "status": "completed",
    "object_type": "resource",
    "scanned_records": 120,
    "rebuilt_records": 118,
    "unsupported_records": 2,
    "failed_records": 0,
    "duration_ms": 1284,
    "warnings": []
  },
  "time": 0.1
}
```

**异步响应（`wait=false`）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "object_type": "resource",
    "status": "accepted",
    "task_id": "task_xxx"
  },
  "time": 0.1
}
```

使用返回的 task 查询后台任务：

```bash
curl -X GET http://localhost:1933/api/v1/tasks/task_xxx \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default"
```

Reindex 后台任务的 `task_type` 为 `admin_reindex`，`resource_id` 等于请求中的 `uri`，也可以这样列出：

```text
GET /api/v1/tasks?task_type=admin_reindex&resource_id=viking://resources
```

任务记录保存在内存中，可能过期，也会在服务重启后丢失。

**结果字段**

| 字段 | 说明 |
|------|------|
| status | 同步完成时为 `completed`，后台执行时为 `accepted` |
| uri | 解析路径变量后的请求 URI |
| object_type | 推断出的目标类型，例如 `resource`、`skill`、`memory`、`user_namespace`、`agent_namespace`、`skill_namespace` 或 `global_namespace` |
| mode | 实际执行的 reindex 模式 |
| scanned_records | 被检查的记录或语义源数量 |
| rebuilt_records | 成功重建的向量记录数量 |
| unsupported_records | 因没有可用向量来源而跳过的记录数量 |
| failed_records | 重建失败的记录数量 |
| duration_ms | 同步执行耗时，单位毫秒 |
| warnings | 可恢复的单条记录级 warning |
| task_id | 后台任务 ID，仅 `wait=false` 时返回 |

**行为说明**

- Reindex 是非破坏式的，采用重建/覆盖写入，不需要先 drop 向量集合。
- 对 `viking://` 发起 reindex 时，会向下分发到支持的顶层命名空间，并显式排除 `session`。
- 命名空间级 reindex，例如 `viking://user` 或 `viking://agent/default`，会继续传播到其支持的子内容类型。
- 如果只是 embedding 模型或向量索引需要刷新，应使用 `vectors_only`。
- 如果语义产物本身也需要重建，再做重向量化，应使用 `semantic_and_vectors`。
- 同一个 URI 和 owner 同时只能运行一个 reindex 任务。对同一目标的并发请求会返回 conflict。
- 对 resource 文件，文本文件在没有 summary 时可以使用文件正文；非文本文件需要已生成的 summary 或已有向量记录 fallback，否则会计为 unsupported。

**当前限制**

- Reindex 会使用当前系统中“尽可能可恢复”的输入进行重建，不保证所有场景都能逐字节回放历史当时的 embedding 输入。
- Memory 的 semantic reindex 基于当前已持久化的 memory 树，不会重建最初按时间顺序执行的记忆抽取流水线。

---

## Observer API

Observer API 提供详细的组件级监控。

### observer.queue

#### 1. API 实现介绍

获取队列系统状态（embedding 和语义处理队列）。显示各队列的待处理、进行中、已完成和错误数量。

**代码入口**:
- `openviking/server/routers/observer.py:observer_queue` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.queue` - 核心实现
- `openviking/storage/observers/queue_observer.py` - 队列观察者
- `crates/ov_cli/src/commands/observer.rs` - CLI 命令

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
print(client.observer.queue)
# 输出:
# [queue] (healthy)
# Queue                 Pending  In Progress  Processed  Errors  Total
# Embedding             0        0            10         0       10
# Semantic              0        0            10         0       10
# TOTAL                 0        0            20         0       20
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
  },
  "time": 0.1
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
print(client.observer.vikingdb())
# 输出:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

# 访问特定属性
print(client.observer.vikingdb().is_healthy)  # True
print(client.observer.vikingdb().status)      # 状态表字符串
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
  },
  "time": 0.1
}
```

---

### observer.models

#### 1. API 实现介绍

获取模型子系统的聚合状态（VLM、embedding、rerank）。检查各模型提供者是否健康可用。

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
print(client.observer.models)
# 输出:
# [models] (healthy)
# provider_model         healthy  detail
# dense_embedding        yes      ...
# rerank                 yes      ...
# vlm                    yes      ...
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
    "status": "provider_model         healthy  detail\ndense_embedding        yes      ...\nrerank                 yes      ...\nvlm                    yes      ..."
  },
  "time": 0.1
}
```

---

### observer.lock

#### 1. API 实现介绍

获取分布式锁系统状态。

**代码入口**:
- `openviking/server/routers/observer.py:observer_lock` - HTTP 路由
- `openviking/service/debug_service.py:ObserverService.lock` - 核心实现
- `openviking/storage/observers/lock_observer.py` - 锁观察者
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

**Python SDK**

```python
print(client.observer.lock)
```

**CLI**

```bash
ov observer transaction
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "name": "lock",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  },
  "time": 0.1
}
```

---

### observer.retrieval

#### 1. API 实现介绍

获取检索质量指标。

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

**Python SDK**

```python
print(client.observer.retrieval)
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
  },
  "time": 0.1
}
```

---

### observer.system

#### 1. API 实现介绍

获取整体系统状态，包括所有组件（queue、vikingdb、models、lock、retrieval）。

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
print(client.observer.system())
# 输出:
# [queue] (healthy)
# ...
#
# [vikingdb] (healthy)
# ...
#
# [models] (healthy)
# ...
#
# [system] (healthy)
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
      }
    }
  },
  "time": 0.1
}
```

---

## 相关文档

- [Resources](02-resources.md) - 资源管理
- [Retrieval](06-retrieval.md) - 搜索与检索
- [Sessions](05-sessions.md) - 会话管理
