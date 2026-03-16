# 操作级 Telemetry 设计

## 1. 背景与目标

OpenViking 需要一套统一的 telemetry 机制，用来描述一次操作在执行过程中的关键观测信息。当前已经落地的是操作级 telemetry，主要覆盖：

- 请求耗时统计
- token 消耗统计
- 检索、队列、内存提取等阶段的摘要指标

这里统一使用 `telemetry`，而不是 `trace`，原因是这套抽象未来不只服务于“单次操作链路”，还要能承载非操作级数据，例如：

- 服务整体 token 消耗
- 各类后端能力的延迟与错误率
- 存储、向量库、队列等组件级指标
- 基于 OpenTelemetry 的 exporter / backend 对接

当前实现只对“操作级 telemetry”提供正式接口，但抽象命名和结构已经为后续扩展预留空间。

## 2. 设计原则

### 2.1 详细信息显式按需返回

详细 telemetry 由调用方通过 `telemetry` 参数显式请求，当前对外协议只返回结构化 summary，不返回事件流。

### 2.3 字段名直接面向用户

内部打点名与对外 summary 字段名保持一致，避免额外的“内部名 -> 外部名”转换层。

### 2.4 缺失分组不返回

如果某类操作天然不会产出某个 summary 分组，则该分组直接省略，不返回空对象或全 `null` 字段。

例如：

- `resources.add_resource` 不一定有 `memory`
- `session.commit` 一般没有 `semantic_nodes`
- 某些操作没有向量检索，就不返回 `vector`

## 3. 当前支持范围

### 3.1 HTTP 接口

当前已接入 operation telemetry 的接口：

- `POST /api/v1/search/find`
- `POST /api/v1/search/search`
- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/sessions/{session_id}/commit`

说明：

- `session.commit` 仅在 `wait=true` 的同步模式下支持返回 telemetry
- `wait=false` 的异步任务模式当前不支持 telemetry，请求时会返回 `INVALID_ARGUMENT`

### 3.2 SDK 接口

当前已接入 operation telemetry 的 SDK 方法：

- `add_resource`
- `add_skill`
- `find`
- `search`
- `commit_session`

本地嵌入式 client 和 HTTP client 都遵循同一套 telemetry 请求语义。

## 4. 响应模型

服务端仍使用统一响应包裹结构：

```json
{
  "status": "ok",
  "result": { "...": "..." },
  "time": 0.031,
  "telemetry": {
    "id": "tm_9f6f4d6b0d0c4f4d93ce5adf82e71c18",
    "summary": {
      "operation": "search.find",
      "status": "ok",
      "duration_ms": 31.224,
      "tokens": {
        "total": 24,
        "llm": {
          "input": 12,
          "output": 6,
          "total": 18
        },
        "embedding": {
          "total": 6
        }
      },
      "vector": {
        "searches": 3,
        "scored": 26,
        "passed": 8,
        "returned": 5,
        "scanned": 26,
        "scan_reason": ""
      }
    }
  }
}
```

说明：

- `telemetry` 只在调用方显式请求时返回
- `telemetry.id` 是不透明标识，只用于关联，不要求调用方解析语义

## 5. telemetry 请求语义

`telemetry` 字段支持两种形态：

### 5.1 布尔形态

```json
{
  "telemetry": true
}
```

语义：

- 返回 `telemetry.id + telemetry.summary`

### 5.2 对象形态

```json
{
  "telemetry": {
    "summary": true
  }
}
```

语义：

- `summary` 默认值为 `true`
- 适合只看结构化摘要

当前支持的合法组合如下：

| 请求值 | 语义 |
| --- | --- |
| `false` | 不返回 `telemetry` |
| `true` | 返回 `id + summary` |
| `{"summary": true}` | 返回 `id + summary` |
| `{"summary": false}` | 不返回 `telemetry` |

以下请求非法：

```json
{
  "telemetry": {
    "events": true
  }
}
```

原因是当前对外 telemetry 已收敛为 summary-only，不再接受事件流选择参数。

## 6. telemetry 的职责划分

### 6.1 `telemetry.summary`

`summary` 是结构化的操作摘要，用于：

- 调试
- 排障
- 离线分析
- 上报到外部观测系统

当前 summary 的核心字段包括：

- `operation`
- `status`
- `duration_ms`
- `tokens`
- `queue`
- `vector`
- `semantic_nodes`
- `memory`
- `errors`

其中：

- `tokens` 始终存在
- 其余分组按是否有产出决定是否返回

### 6.3 `telemetry.id`

`telemetry.id` 是请求级关联标识，用于把一次操作的 summary 与内部异步链路统计关联起来。

## 7. summary 字段约定

### 7.1 顶层公共字段

所有 summary 至少包含：

- `operation`
- `status`
- `duration_ms`
- `tokens`

### 7.2 tokens

示例：

```json
{
  "tokens": {
    "total": 19,
    "llm": {
      "input": 11,
      "output": 7,
      "total": 18
    },
    "embedding": {
      "total": 1
    }
  }
}
```

说明：

- `llm` 统计输入、输出与总量
- `embedding` 当前只统计总量

### 7.3 queue

队列相关摘要示例：

```json
{
  "queue": {
    "semantic": {
      "processed": 1,
      "error_count": 0
    },
    "embedding": {
      "processed": 1,
      "error_count": 0
    }
  }
}
```

### 7.4 vector

向量检索摘要示例：

```json
{
  "vector": {
    "searches": 2,
    "scored": 5,
    "passed": 3,
    "returned": 2,
    "scanned": 5,
    "scan_reason": ""
  }
}
```

### 7.5 semantic_nodes

语义检索 DAG / 节点级摘要示例：

```json
{
  "semantic_nodes": {
    "total": 4,
    "done": 3,
    "pending": 1,
    "running": 0
  }
}
```

### 7.6 memory

会话提交等内存提取类操作示例：

```json
{
  "memory": {
    "extracted": 4
  }
}
```

### 7.7 errors

发生错误时可返回：

```json
{
  "errors": {
    "stage": "resource_processor.parse",
    "error_code": "PROCESSING_ERROR",
    "message": "..."
  }
}
```

无错误时，该分组可以省略。

## 8. 缺失字段裁剪策略

summary 采用“按分组裁剪”的策略，而不是固定返回整套字段。

这样做有几个直接收益：

- 避免返回大量与当前操作无关的空字段
- 降低调用方理解成本
- 更适合未来扩展新的 telemetry 分组

示例：

### 8.1 `resources.add_resource`

可能返回：

```json
{
  "operation": "resources.add_resource",
  "status": "ok",
  "duration_ms": 152.3,
  "tokens": { "...": "..." },
  "semantic_nodes": { "...": "..." },
  "queue": { "...": "..." }
}
```

这里不应强行返回 `memory`。

### 8.2 `session.commit`

可能返回：

```json
{
  "operation": "session.commit",
  "status": "ok",
  "duration_ms": 48.1,
  "tokens": { "...": "..." },
  "memory": {
    "extracted": 4
  }
}
```

这里不应强行返回 `semantic_nodes`。

## 9. 成本模型

当前 collector 只采集 summary 所需的数据：

- 采集 counters / gauges
- 记录 error 状态
- 构造最终 summary
- 不保留事件列表

## 10. 实现结构

### 10.1 核心类型

核心实现位于：

- `openviking/telemetry/operation.py`
- `openviking/telemetry/request.py`
- `openviking/telemetry/context.py`
- `openviking/telemetry/registry.py`

主要对象包括：

- `OperationTelemetry`
- `TelemetrySnapshot`
- `TelemetrySelection`

### 10.2 请求解析

`openviking/telemetry/request.py` 负责统一解析 `telemetry` 请求参数：

- 支持 `bool | object`
- 归一化为 `TelemetrySelection`
- 校验非法字段，例如 `events`

这样 server、local client、HTTP client 都共享同一套语义。

### 10.3 服务端集成

`openviking/server/telemetry.py` 负责：

- 根据请求创建 collector
- 根据 selection 决定是否附带 `summary`

router 层的职责是：

1. 创建 collector
2. 绑定 operation 上下文
3. 执行实际业务逻辑
4. 按请求返回 `telemetry`

### 10.4 本地与 HTTP client

本地 client 和 HTTP client 都暴露同样的 `telemetry` 参数语义：

```python
await client.find("memory dedup", telemetry=True)
await client.find("memory dedup", telemetry={"summary": True})
```

其中：

- local client 在本地生成 telemetry 并拼回结果
- HTTP client 负责参数校验并透传给服务端

## 11. 异步链路与跨组件聚合

当前 operation telemetry 不只覆盖同步请求栈，也支持部分异步处理链路的数据回流。

典型场景包括：

- 请求线程触发语义队列处理
- 请求线程触发 embedding 处理
- 后台处理线程继续向同一个 operation collector 记录指标

实现方式是：

- collector 生成 `telemetry.id`
- 后续消息携带该 `id`
- 后台组件通过 registry 找回原 collector
- 在新的执行上下文中重新绑定 collector

这样一次操作的最终 summary 可以覆盖：

- 请求入口逻辑
- 检索过程
- embedding 处理
- semantic queue 处理
- memory 提取结果

## 12. 与 OpenTelemetry 的关系

当前方案不是直接把 OpenTelemetry 暴露为业务接口，而是先定义 OpenViking 自己的 telemetry 抽象。

这样做的好处是：

- 对调用方暴露稳定、简单的产品接口
- 不把业务接口和具体观测框架强绑定
- 后续可以新增 OpenTelemetry backend，而不影响现有 SDK / HTTP 语义

可以把 OpenTelemetry 看作未来的一种底层实现或导出方式，而不是当前对外协议本身。

## 13. 未来扩展方向

当前文档描述的是 operation telemetry，但未来需要兼容更广义的 telemetry 数据源。

推荐的扩展方向：

- 服务级 token 消耗聚合
- 存储、向量库、模型服务的接口耗时
- 队列吞吐、失败率、积压长度
- 与 OpenTelemetry exporter 的桥接
- 更长期的指标聚合、采样和导出

这些扩展不要求沿用完全相同的 summary schema，但应复用统一的 telemetry 抽象和运行时。

## 14. 使用示例

### 14.1 返回 telemetry summary

```bash
curl -X POST http://localhost:8080/api/v1/search/find \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "telemetry": true
  }'
```

### 14.2 只返回 summary

```bash
curl -X POST http://localhost:8080/api/v1/search/find \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "telemetry": {
      "summary": true
    }
  }'
```

### 14.4 Python SDK

```python
result = await client.find("memory dedup", telemetry={"summary": True})

print(result.telemetry["summary"]["tokens"]["total"])
```

## 15. 新接口接入规范

新接口如果需要接入 operation telemetry，建议遵循以下规则：

1. 为该操作创建 `OperationTelemetry` collector。
2. 用上下文绑定覆盖整个操作生命周期。
3. 在内部关键阶段记录 counters、gauges 和错误状态。
4. 仅在调用方请求时返回 `telemetry`。
5. summary 只返回本次操作真实产出的分组。

这样可以保持默认低成本，同时为调用方提供稳定、可分析的结构化摘要。
