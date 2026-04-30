# 嵌入模型蓝绿迁移设计文档

## Embedding Model Blue-Green Migration Design

Date: 2026-04-30
Status: 已实现 (Implemented)

> **摘要 / Abstract**: 在零停机前提下，将 OpenViking 的嵌入模型从当前模型平滑迁移到新模型。通过七阶段状态机、双写适配器、基于 QueueFS 的批量重建引擎和完整的崩溃恢复机制，实现端到端可用的 REST API 驱动迁移流程。
>
> Zero-downtime migration of OpenViking's embedding model. A seven-phase state machine, dual-write adapter, QueueFS-based batch reindex engine, and full crash recovery enable an end-to-end REST API-driven migration workflow.

---

## 一、目标与背景 / Goals & Background

### 1.1 核心要求 / Core Requirements

| 要求 | Requirement |
|------|-------------|
| **零停机** Zero-downtime | 迁移全程读写可用 — reads and writes remain available throughout |
| **数据完整** Data integrity | 所有现有数据用新模型重新嵌入 — all existing data re-embedded with the new model |
| **可回滚** Rollback-safe | 每个阶段均支持安全回退 — every phase supports safe rollback |
| **可恢复** Crash-resilient | 服务崩溃后能从断点继续 — recovery from any crash point |
| **端到端可用** E2E operable | 运维人员通过 REST API 完成全部操作 — fully REST API-driven |

### 1.2 旧实现教训 / Lessons from Prior Implementation

前次实现存在 6 个关键缺陷 The prior implementation had 6 critical flaws：

| 等级 Level | 问题 Issue | 根因 Root Cause |
|------------|-----------|----------------|
| P0 | 无端到端路径将目标嵌入器传入 reindex 引擎 | `MigrationState` 不持久化嵌入器标识 |
| P1 | degraded 模式是单向陷阱 | 无重试、无计数、无可观测性 |
| P1 | `list_all_uris()` 一次加载全部 URI | OOM 风险 |
| P1 | 无限流/无取消机制 | reindex 循环无 rate limiting、无 cancel event |
| P1 | 无 URI 去重 | 无去重逻辑 |
| P1 | `drop_collection` 缺乏安全约束 | 双写开启时可删除备份集合 |

---

## 二、七阶段状态机 / Seven-Phase State Machine

```
idle ──(start)──→ dual_write ──(build)──→ building ──(auto)──→ building_complete
                     │                │           │         ↑                │
                     └──(abort)───────┘           │         └──(build)───────┘
                                                  │
                     building_complete ──(switch)──→ switched ──(disable-dw)──→ dual_write_off
                                                         │                           │
                                                         └──(rollback)──→ dual_write │
                                                                                     │
                     dual_write_off ──(finish)──→ completed ──(auto)──→ idle
```

### 2.1 阶段定义 / Phase Definitions

| 阶段 Phase | 写入行为 Writes | 读取行为 Reads | 语义 Semantics |
|------------|----------------|---------------|----------------|
| `idle` | source | source | 无迁移进行中 — no migration active |
| `dual_write` | source + target | source | 双写启动，target 已建并接收写入 — dual-write started |
| `building` | source + target | source | ReindexEngine 后台运行 — background reindex |
| `building_complete` | source + target | source | Reindex 完成，等待切换确认 — awaiting switch confirmation |
| `switched` | source + target | target | 读取已切换到 target — reads switched to target |
| `dual_write_off` | target only | target | 双写关闭，source 冻结 — source frozen |
| `completed` | target only | target | 迁移完成，状态永久记录 — migration recorded permanently |

### 2.2 正向转换 / Forward Transitions (T1-T7)

| # | 转换 Transition | 触发 Trigger | 前置条件 Precondition |
|---|----------------|-------------|---------------------|
| T1 | `idle → dual_write` | `POST /start` | 无活跃迁移，target_name 有效 |
| T2 | `dual_write → building` | `POST /build` | target 集合存在，无运行中的 reindex |
| T2b | `building_complete → building` | `POST /build` | 支持多次 rebuild，跳过已有嵌入 |
| T3 | `building → building_complete` | 自动（reindex 完成回调） | reindex 处理完毕 |
| T4 | `building_complete → switched` | `POST /switch` | 错误率 ≤ 阈值，目标嵌入器可达 |
| T5 | `switched → dual_write_off` | `POST /disable-dual-write` | active_side=target |
| T6 | `dual_write_off → completed` | `POST /finish` | 更新迁移状态文件 |
| T7 | `completed → idle` | 自动 | 清理运行时状态 |

### 2.3 回滚操作 / Rollback Actions (R1-R4)

| # | 当前状态 From | 目标 To | 触发 Trigger | 动作 Actions |
|---|-------------|---------|-------------|-------------|
| R1 | `dual_write` | `idle` | `POST /abort` | 关闭双写 → 删除 target → 清除 state |
| R2 | `building` | `idle` | `POST /abort` | cancel reindex → 关闭双写 → 删除 target → 清除 queue → 清除 state |
| R3 | `building_complete` | `idle` | `POST /abort` | 关闭双写 → 删除 target → 清除 queue → 清除 state |
| R4 | `switched` | `dual_write` | `POST /rollback` | 读切回 source → 保持双写（非破坏性） |

> **设计原则 / Design Principle**:
> - `abort` → `idle`：破坏性，删除 target 集合 — destructive, drops target collection
> - `rollback` → `dual_write`：非破坏性，保留 target 数据 — non-destructive, preserves target data
> - `dual_write_off` 及之后不可 rollback — source 已停止写入，追赶增量成本高

### 2.4 崩溃恢复 / Crash Recovery (C1-C7)

| # | 崩溃时状态 Phase at Crash | 重启后恢复行为 Recovery Behavior |
|---|--------------------------|--------------------------------|
| C1 | `idle` | 无需恢复 — no recovery needed |
| C2 | `dual_write` | 重建 DualWriteAdapter（active=source, dw=true） |
| C3 | `building` | 重建 adapter + ReindexEngine，使用 `state.target_embedder_name` 创建正确嵌入器 |
| C4 | `building_complete` | 重建 adapter，保持 building_complete |
| C5 | `switched` | 重建 adapter（active=target, dw=true） |
| C6 | `dual_write_off` | 重建 adapter（active=target, dw=false） |
| C7 | `completed` | 清理运行时 state，迁移状态文件永久保留 |

> **关键设计 / Key Design**: C3 恢复使用 `config.get_target_embedder(state.target_embedder_name)`，而非 `config.embedding.get_embedder()`（当前活跃嵌入器可能仍是源模型）。

---

## 三、模块架构 / Module Architecture

```
openviking/storage/migration/
├── __init__.py                # 公共 API 导出 / public API exports
├── state.py                   # MigrationPhase, ActiveSide, MigrationState, 
│                              #   MigrationStateManager, MigrationStateFile
├── blue_green_adapter.py      # DualWriteAdapter — 双写组合适配器
├── reindex_engine.py          # ReindexEngine — 基于 QueueFS 的批量重建
├── controller.py              # MigrationController — 状态机编排器
├── rollback.py                # 各阶段回滚动作 / per-phase rollback helpers
└── resilience.py              # 崩溃恢复 / crash recovery

openviking_cli/utils/config/
└── open_viking_config.py      # OpenVikingConfig: embeddings 字段 + get_target_embedder()
```

### 3.1 职责边界 / Responsibility Boundaries

| 层 Layer | 模块 Module | 职责 Responsibility |
|----------|------------|---------------------|
| 状态层 State | `state.py` | 枚举、数据类、持久化、迁移历史 |
| 适配器层 Adapter | `blue_green_adapter.py` | 双写路由、standby best-effort、集合管理 |
| 引擎层 Engine | `reindex_engine.py` | 分页扫描、批量过滤、队列处理、进度跟踪 |
| 编排层 Orchestration | `controller.py` | 状态机编排、转换验证、回滚协调 |
| 恢复层 Recovery | `resilience.py` | 崩溃后组件重建 |

### 3.2 类依赖关系 / Class Dependencies

```
DualWriteAdapter ──extends──→ CollectionAdapter
DualWriteAdapter ──wraps──→ CollectionAdapter ×2 (source + target)

ReindexEngine ──reads──→ CollectionAdapter (source)
ReindexEngine ──writes──→ CollectionAdapter (target)
ReindexEngine ──uses──→ NamedQueue (QueueFS)

MigrationController ──orchestrates──→ DualWriteAdapter + ReindexEngine
MigrationController ──persists──→ MigrationStateManager
MigrationController ──records──→ MigrationStateFile

CrashRecovery ──rebuilds──→ DualWriteAdapter + ReindexEngine
```

---

## 四、核心设计 / Core Design

### 4.1 状态持久化 / State Persistence

**MigrationState**（运行时，迁移完成后清理 / runtime, cleaned on completion）：

```python
@dataclass
class MigrationState:
    migration_id: str
    phase: MigrationPhase
    source_collection: str
    target_collection: str
    active_side: str              # "source" or "target"
    dual_write_enabled: bool
    source_embedder_name: str      # C-1 fix: identify embedder for recovery
    target_embedder_name: str
    degraded_write_failures: int   # observability counter
    reindex_progress: Optional[ReindexProgress]
    started_at: str
    updated_at: str
```

**MigrationStateFile**（永久保留 / permanent, never deleted）：

```json
{
  "version": 1,
  "current_active": "v2",
  "history": [
    {
      "id": "mig_...",
      "from_name": "v1",
      "to_name": "v2",
      "status": "completed"
    }
  ]
}
```

### 4.2 DualWriteAdapter

```
Active 写入：必须成功，失败则抛异常（与单写行为一致）
         writes: must succeed, exception propagates to caller

Standby 写入：best-effort，最多 3 次尝试（100ms 间隔），失败后递增
          degraded_write_failures 计数器，永不阻塞 active 侧
         writes: best-effort, 3 attempts max, logs + increments counter on failure
```

**安全约束 / Safety Constraints**：
- `drop_collection()` 双写开启时拒绝 — rejects when `dual_write_enabled`
- `drop_collection()` 拒绝删除活跃侧 — rejects dropping the active side
- Thread-safe via `threading.RLock` on state-mutating methods

### 4.3 ReindexEngine

```
               ┌──────────────────────┐
               │  ReindexEngine        │
               │                      │
Source ────────┤  1. scan_source_uris()│──→ offset+limit 分页，yield URI 批次
               │  2. filter_missing() │──→ 分批 $in 查询 target，仅返回缺失 URI
               │  3. enqueue_uris()   │──→ set 去重 + 写入 NamedQueue
               │  4. process_queue()  │──→ Semaphore 限流，embed + upsert + ack
               │  5. cancel()         │──→ asyncio.Event 优雅停止
               └──────┬───────────────┘
                      │
         ┌────────────┼────────────────┐
         ▼            ▼                ▼
  Target Embedder  Target Adapter  Progress Tracker
```

**关键特性 / Key Features**：
- 分页扫描，`output_fields=["uri"]` 避免全量加载 — paginated scan, URI-only output
- 分批差集过滤，不对 target 做全量加载 — batch diff, no full target load
- `asyncio.Semaphore(max_concurrent)` 并发控制 — concurrency control
- 指数退避重试，单 URI 失败不阻塞批次 — exponential backoff, error isolation
- 未 ack 消息保留在队列，由 AGFS RecoverStale 恢复 — at-least-once via unacked messages

### 4.4 双写容错 / Dual-Write Fault Tolerance

设计原则：不做补偿队列。building 阶段的 standby 写入缺失由 reindex 全量覆盖天然修复。

No compensation queue needed. Missing standby writes during the building phase are naturally repaired by the full reindex scan.

```
standby 写入失败
    ├─ 短暂重试（最多 3 次）── retry up to 3 times
    ├─ 仍失败 → log warning, degraded_write_failures += 1
    └─ 继续处理（不阻塞 active 侧）── continue, never block active
```

---

## 五、API 设计 / REST API

| 端点 Endpoint | 方法 | 转换 Transition | 鉴权 Auth |
|--------------|------|----------------|----------|
| `/api/v1/migration/start` | POST | idle → dual_write | admin/root |
| `/api/v1/migration/status` | GET | — | any authenticated |
| `/api/v1/migration/targets` | GET | — | any authenticated |
| `/api/v1/migration/build` | POST | dual_write/building_complete → building | admin/root |
| `/api/v1/migration/switch` | POST | building_complete → switched | admin/root |
| `/api/v1/migration/disable-dual-write` | POST | switched → dual_write_off | admin/root |
| `/api/v1/migration/finish` | POST | dual_write_off → completed → idle | admin/root |
| `/api/v1/migration/abort` | POST | any → idle | admin/root |
| `/api/v1/migration/rollback` | POST | switched → dual_write | admin/root |

**Happy Path**：
```
POST /start → POST /build → GET /status (轮询 poll phase=building_complete)
                                  → POST /switch
                                  → POST /disable-dual-write
                                  → POST /finish
```

---

## 六、约束检查表 / Constraint Checklist

| # | 约束 Constraint | 位置 Location | 描述 Description |
|---|----------------|--------------|------------------|
| C-1 | MigrationState 持久化嵌入器标识 | `state.py` | `source_embedder_name`, `target_embedder_name` |
| C-2 | ReindexEngine 通过构造函数接收嵌入器 | `reindex_engine.py` | `__init__(..., target_embedder)` |
| C-3 | drop_collection 双写时拒绝 | `blue_green_adapter.py` | `RuntimeError` when `dual_write_enabled` |
| C-4 | API 通过 HTTP 层测试 | `test_migration_api.py` | 28 E2E tests via httpx |
| C-5 | 分页扫描 + 分批过滤 | `reindex_engine.py` | offset+limit; batch `$in` query |
| C-6 | Cancel + at-least-once | `reindex_engine.py` | `asyncio.Event`; unacked messages preserved |
| C-7 | Standby 失败不阻塞 active | `blue_green_adapter.py` | best-effort write pattern |
| C-8 | 迁移状态文件永久保留 | `state.py` | `MigrationStateFile` never deleted |
| C-9 | 维度不匹配硬阻断启动 | `open_viking_config.py` | validator rejects on mismatch |

---

## 七、设计决策 / Design Decisions

| 决策 Decision | 选项 Options | 选择 Choice | 理由 Rationale |
|--------------|-------------|------------|----------------|
| 进度持久化 / Progress persistence | A) 自定义 JSON B) QueueFS | B) QueueFS | 复用已有基础设施，获得 ack/recover 能力 |
| Reindex 触发方式 / Trigger mode | A) 同步阻塞 B) 异步后台 | B) 异步后台 | 大集合迁移耗时长，不能阻塞 HTTP |
| Controller 职责 / Controller scope | A) 含嵌入逻辑 B) 仅编排 | B) 仅编排 | 单一职责，便于测试 |
| 多嵌入器配置 / Multi-embedder config | A) EmbeddingConfig.migration B) OpenVikingConfig.embeddings | B) OpenVikingConfig.embeddings | 多配置是全局系统决策 |
| 活跃配置激活 / Activation | A) 热替换 B) 重启激活 | B) 重启激活 | dual_write 确保重启期间数据不丢失 |
| ActiveSide 类型 / Type | A) raw str B) str, Enum | B) `ActiveSide(str, Enum)` | 与 `MigrationPhase` 一致；向后兼容 |

---

## 八、迁移时序图 / Migration Sequence Diagram

```
Operator        API              Controller       Adapter         Engine         State
  │              │                   │               │              │             │
  │ POST /start  │                   │               │              │             │
  ├─────────────→│  start_migration  │               │              │             │
  │              ├──────────────────→│  create target │              │             │
  │              │                   ├──────────────→│              │             │
  │              │                   │←──────────────┤              │             │
  │              │                   │ DualWriteAdapter(source,target, active=source, dw=true)
  │              │                   ├──────────────→│              │             │
  │              │                   │  save(state)  │              │             │
  │              │                   ├───────────────────────────────────────────→│
  │              │←── 200 {dual_write}│               │              │             │
  │←── started ─┤                   │               │              │             │
  │              │                   │               │              │             │
  │ POST /build  │                   │               │              │             │
  ├─────────────→│  begin_building   │               │              │             │
  │              ├──────────────────→│               │              │             │
  │              │                   │ ReindexEngine(source, target_embedder, target)
  │              │                   ├─────────────────────────────→│              │
  │              │                   │  create_task(process_queue)  │              │
  │              │                   ├─────────────────────────────→│              │
  │              │←── 202 {building}  │               │              │             │
  │←── building ┤                   │               │              │             │
  │              │                   │               │              │    ...      │
  │ GET /status  │                   │               │              │  (reindex)  │
  ├─────────────→│  get_status       │               │              │             │
  │              ├──────────────────→│  get_progress │              │             │
  │              │                   ├─────────────────────────────→│             │
  │              │←── {progress}     │←──────────────│              │             │
  │              │                   │               │              │             │
  │              │      ... reindex completes ...    │              │             │
  │              │                   │ _on_reindex_done             │             │
  │              │                   │←─────────────────────────────┤             │
  │              │                   │ phase=building_complete      │             │
  │              │                   │               │              │             │
  │ POST /switch │                   │               │              │             │
  ├─────────────→│  confirm_switch   │               │              │             │
  │              ├──────────────────→│  set_active("target")        │             │
  │              │                   ├──────────────→│              │             │
  │              │←── 200 {switched}  │               │              │             │
  │              │                   │               │              │             │
  │              │         ... (disable-dual-write → finish) ...    │             │
```

---

## 九、关键数据结构 / Key Data Types

### ActiveSide (Enum)
```python
class ActiveSide(str, Enum):
    SOURCE = "source"
    TARGET = "target"
```

### MigrationPhase (Enum)
```python
class MigrationPhase(str, Enum):
    idle = "idle"
    dual_write = "dual_write"
    building = "building"
    building_complete = "building_complete"
    switched = "switched"
    dual_write_off = "dual_write_off"
    completed = "completed"
```

### ReindexProgress
```python
@dataclass
class ReindexProgress:
    processed: int = 0
    total: int = 0
    errors: int = 0
    skipped: int = 0
```

---

## 十、多嵌入器配置 / Multi-Embedder Configuration

```json
{
  "embedding": { "dense": { ... } },
  "embeddings": {
    "v1": {
      "dense": { "provider": "volcengine", "model": "doubao-embedding-v1", "dimension": 1024 },
      "max_concurrent": 10
    },
    "v2": {
      "dense": { "provider": "volcengine", "model": "doubao-embedding-v2", "dimension": 2048 },
      "max_concurrent": 8
    }
  }
}
```

**解析规则 / Resolution Rules**：
- `embeddings` 为空 → 使用 `embedding` 字段（向后兼容 / backward compat）
- `embeddings` 非空 → 加载迁移状态文件，解析当前活跃配置
- 维度不匹配 → 拒绝启动（C-9）

---

## 十一、风险与缓解 / Risks & Mitigations

| 风险 Risk | 概率 Prob | 影响 Impact | 缓解 Mitigation |
|-----------|----------|------------|-----------------|
| 大集合 reindex 超时 | 中 | 中 | 分页 + 异步后台 + 进度轮询 |
| 嵌入 API 限流 (429) | 中 | 低 | Semaphore 限流 + 指数退避 |
| 双写 standby 延迟 | 中 | 中 | standby 写入短超时 + best-effort |
| 迁移状态文件损坏 | 低 | 高 | 原子写入；损坏时明确拒绝启动 |
| 配置与状态不一致 | 低 | 高 | 启动时校验 current_active ∈ embeddings |

---

## 十二、测试覆盖 / Test Coverage

| 测试文件 Test File | 行数 Lines | 覆盖范围 Coverage |
|-------------------|-----------|-------------------|
| `test_state.py` | 376 | 枚举、数据类、序列化、持久化、并发安全 |
| `test_blue_green_adapter.py` | 550 | 双写路由、standby 容错、集合管理 |
| `test_reindex_engine.py` | 1,111 | 分页、去重、限流、取消、错误隔离 |
| `test_controller.py` | 1,454 | 全部转换、非法转换、回滚、终止 |
| `test_rollback.py` | 769 | R1-R4 回滚动作 |
| `test_resilience.py` | 759 | C1-C7 崩溃恢复 |
| `test_concurrency.py` | 353 | 并发 upsert + set_active |
| `test_coverage_gap.py` | 386 | 边界情况覆盖 |
| `test_migration_api.py` | 925 | E2E happy path、abort/rollback、鉴权、恢复 |
