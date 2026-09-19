# 嵌入模型蓝绿迁移设计文档

## Embedding Model Blue-Green Migration Design

Date: 2026-04-30
Status: 进行中 (In Progress)

> **关联 RFC**: [volcengine/OpenViking#1523](https://github.com/volcengine/OpenViking/issues/1523) — Improve embedder model migration experience

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

### 8.1 正常迁移流程 / Happy Path

```
Operator           CLI          API          Controller    VikingDBMgr    Adapter      Engine      StateFile
  │                 │            │               │             │            │            │            │
  │  POST /start    │            │               │             │            │            │            │
  ├─────────────────→│            │               │             │            │            │            │
  │  (或)            │  ov migration start v2     │             │            │            │            │
  │                 ├───────────→│               │             │            │            │            │
  │                 │            │ start_migration("v2")      │            │            │            │
  │                 │            ├──────────────→│             │            │            │            │
  │                 │            │               │ create target adapter     │            │            │
  │                 │            │               ├─────────────→│            │            │            │
  │                 │            │               │←─────────────┤            │            │            │
  │                 │            │               │ DualWriteAdapter(source, target, "source", dw=true)
  │                 │            │               ├───────────────────────────→│            │            │
  │                 │            │               │ replace_shared_adapter(dw) │            │            │
  │                 │            │               ├─────────────→│             │            │            │
  │                 │            │               │  ← 线上读写已被劫持 ←       │            │            │
  │                 │            │               │  save(state) │             │            │            │
  │                 │            │               ├────────────────────────────────────────────────────→│
  │                 │            │←── 200 {dual_write}         │            │            │            │
  │←── started ────┼────────────┤               │             │            │            │            │
  │                 │            │               │             │            │            │            │
  │  POST /build    │            │               │             │            │            │            │
  ├─────────────────→│            │               │             │            │            │            │
  │  (或)            │  ov migration build        │             │            │            │            │
  │                 ├───────────→│               │             │            │            │            │
  │                 │            │ begin_building │             │            │            │            │
  │                 │            ├──────────────→│             │            │            │            │
  │                 │            │               │ ReindexEngine(source, target_embedder, target)      │
  │                 │            │               ├───────────────────────────────────────→│            │
  │                 │            │               │  create_task(process_queue)            │            │
  │                 │            │               ├───────────────────────────────────────→│            │
  │                 │            │←── 202 {building}           │            │            │            │
  │←── building ───┼────────────┤               │             │            │            │            │
  │                 │            │               │             │            │            │    ...     │
  │  GET /status    │            │               │             │            │            │  (reindex) │
  ├─────────────────→│            │               │             │            │            │            │
  │  (或)            │  ov migration status       │             │            │            │            │
  │                 ├───────────→│               │             │            │            │            │
  │                 │            │ get_status()   │             │            │            │            │
  │                 │            ├──────────────→│             │            │            │            │
  │                 │            │               │ get_progress()            │            │            │
  │                 │            │               ├───────────────────────────────────────→│            │
  │                 │            │←── {progress} │←────────────│            │            │            │
  │                 │            │               │             │            │            │            │
  │                 │            │      ... reindex completes ...             │            │            │
  │                 │            │               │ _on_reindex_done           │            │            │
  │                 │            │               │←────────────────────────────────────────┤            │
  │                 │            │               │ phase=building_complete    │            │            │
  │                 │            │               │             │            │            │            │
  │  POST /switch   │            │               │             │            │            │            │
  ├─────────────────→│            │               │             │            │            │            │
  │                 │            │ confirm_switch │             │            │            │            │
  │                 │            ├──────────────→│             │            │            │            │
  │                 │            │               │ set_active("target") ← 线上查询立即切到 target │      │
  │                 │            │               ├───────────────────────────→│            │            │
  │                 │            │←── 200 {switched}           │            │            │            │
  │                 │            │               │             │            │            │            │
  │  POST /disable-dw            │               │             │            │            │            │
  ├─────────────────→│            │               │             │            │            │            │
  │                 │            │ disable_dual_write()        │            │            │            │
  │                 │            ├──────────────→│             │            │            │            │
  │                 │            │               │ set_dual_write(False) ← source 冻结        │      │
  │                 │            │               ├───────────────────────────→│            │            │
  │                 │            │←── 200 {dual_write_off}     │            │            │            │
  │                 │            │               │             │            │            │            │
  │  POST /finish   │            │               │             │            │            │            │
  ├─────────────────→│            │               │             │            │            │            │
  │                 │            │ finish_migration()          │            │            │            │
  │                 │            ├──────────────→│             │            │            │            │
  │                 │            │               │ update_current_active("v2")              │            │
  │                 │            │               ├──────────────────────────────────────────────────────→│
  │                 │            │               │ update_active_collection("context_v2")    │            │
  │                 │            │               ├──────────────────────────────────────────────────────→│
  │                 │            │               │ replace_shared_adapter(target_adapter)    │            │
  │                 │            │               ├─────────────→│             │            │            │
  │                 │            │               │  ← 剥离 DualWriteAdapter，恢复正常 ←       │            │
  │                 │            │←── 200 {idle} │             │            │            │            │
  │←── completed ──┼────────────┤               │             │            │            │            │
```

### 8.2 崩溃恢复流程 / Crash Recovery

```
Startup        Config          MigrationState   Resilience        VikingDBMgr      Adapter        Engine
  │               │                  │               │                 │              │              │
  │  load ov.conf │                  │               │                 │              │              │
  ├──────────────→│                  │               │                 │              │              │
  │               │ embeddings 非空? │               │                 │              │              │
  │               │ 加载 migration   │               │                 │              │              │
  │               │ state file       │               │                 │              │              │
  │               ├─────────────────→│               │                 │              │              │
  │               │  ← current_active + active_collection ←          │              │              │
  │               │  修补 vectordb.name              │                 │              │              │
  │               │                  │               │                 │              │              │
  │  _init_storage│                  │               │                 │              │              │
  ├──────────────→│                  │               │                 │              │              │
  │               │  state_manager.load()            │                 │              │              │
  │               ├─────────────────→│               │                 │              │              │
  │               │  ← state.phase=building ←         │                 │              │              │
  │               │                  │               │                 │              │              │
  │               │  recover_from_crash(state, config)                │              │              │
  │               ├─────────────────────────────────→│                 │              │              │
  │               │                  │               │ 重建 DualWriteAdapter(source, target)│              │
  │               │                  │               ├─────────────────────────────────────→│              │
  │               │                  │               │  重建 ReindexEngine (building 阶段) │              │
  │               │                  │               ├────────────────────────────────────────────────→│
  │               │                  │               │←── (adapter, engine) ────────────────────────────┤
  │               │←── (adapter, engine) ─────────────┤                 │              │              │
  │               │                  │               │                 │              │              │
  │               │  VikingDBManager(vectordb, shared_adapter=adapter) │                 │              │
  │               ├────────────────────────────────────────────────────→│              │              │
  │               │                  │               │  ← DualWriteAdapter 已注入 ←       │              │
  │               │                  │               │  ← ReindexEngine 自动恢复 ←         │              │
```

### 8.3 线上读写劫持后路径 / Post-Interception Request Path

```
HTTP Request          VikingFS         VikingVectorIndexBackend     _SingleAccountBackend    DualWriteAdapter
  │                      │                       │                         │                       │
  │ POST /api/v1/search/find                   │                         │                       │
  ├──────────────────────→                       │                         │                       │
  │                      │  find()               │                         │                       │
  │                      ├──────────────────────→│                         │                       │
  │                      │                       │ search_in_tenant()      │                       │
  │                      │                       ├────────────────────────→│                       │
  │                      │                       │                         │ query()               │
  │                      │                       │                         ├──────────────────────→│
  │                      │                       │                         │                       │ active 侧查询
  │                      │                       │                         │                       │ = target (switched)
  │                      │                       │                         │←──────────────────────┤
  │                      │                       │←────────────────────────┤                       │
  │                      │←──────────────────────┤                         │                       │
  │←── results from target collection ───────────┤                         │                       │
  │                      │                       │                         │                       │
  │ POST /api/v1/content/upsert                 │                         │                       │
  ├──────────────────────→                       │                         │                       │
  │                      │  _write_to_vector_store()                      │                       │
  │                      ├──────────────────────→│                         │                       │
  │                      │                       │ upsert()                │                       │
  │                      │                       ├────────────────────────→│                       │
  │                      │                       │                         │ upsert()              │
  │                      │                       │                         ├──────────────────────→│
  │                      │                       │                         │                       │ active+standby
  │                      │                       │                         │                       │ (dual_write on)
  │                      │                       │                         │←──────────────────────┤
  │                      │                       │←────────────────────────┤                       │
  │                      │←──────────────────────┤                         │                       │
  │←── 200 OK (written to both source and target)┤                         │                       │
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

## 十一、线上读写劫持 / Online Read-Write Interception

### 11.1 问题

迁移期间 DualWriteAdapter 需要接管线上所有读写请求。当前线上请求链路：

```
HTTP → VikingFS → VikingVectorIndexBackend → _SingleAccountBackend._adapter → CollectionAdapter
                                                                                  ↑
                                                                          必须替换为 DualWriteAdapter
```

直接替换私有字段 `_adapter` / `_shared_adapter` 不安全。使用**构造时注入 + 运行时公共接口**模式。

### 11.2 构造时决策

启动时 `OpenVikingService._init_storage()` 检查 `config.embeddings`：
- `embeddings` 为空 → 传统路径，内部创建 adapter
- `embeddings` 非空，无活跃迁移 → 正常启动
- `embeddings` 非空，有活跃迁移 → 构造 `VikingDBManager` 时注入 DualWriteAdapter

```python
# service/core.py
def _init_storage(self, config, queue_manager):
    shared_adapter = None
    if config.embeddings:
        state = state_manager.load()
        if state and state.phase != MigrationPhase.idle:
            adapter, engine = recover_from_crash(state, config, ...)
            shared_adapter = adapter  # 构造时劫持
    self._vikingdb_manager = VikingDBManager(
        vectordb_config=config.storage.vectordb,
        queue_manager=queue_manager,
        shared_adapter=shared_adapter,  # None → 内部创建
    )
```

### 11.3 运行时替换接口

`VikingVectorIndexBackend` 暴露公共方法供迁移模块调用：

```python
class VikingVectorIndexBackend:
    def replace_shared_adapter(self, new_adapter: CollectionAdapter) -> None:
        """替换所有 account backend 的共享 adapter。
        供迁移模块: POST /start 注入 DualWriteAdapter,
        POST /abort / POST /finish 恢复普通 adapter。
        """
        self._shared_adapter = new_adapter
        for backend in self._account_backends.values():
            backend._adapter = new_adapter
        if self._root_backend:
            self._root_backend._adapter = new_adapter
```

### 11.4 劫持时间线

| 操作 | 调用 | 线上效果 |
|---|---|---|
| 启动（活跃迁移） | 构造时注入 | 线上立即走 DualWriteAdapter |
| `POST /start` | `replace_shared_adapter(dw)` | 写入分流到 source+target |
| `POST /switch` | `dw.set_active("target")` | 查询切到 target |
| `POST /abort` | `replace_shared_adapter(original)` | 恢复正常 |
| `POST /finish` | `replace_shared_adapter(target)` | 切换到纯 target adapter |

### 11.5 集合命名一致性

迁移完成后需确保重启时 adapter 指向正确的集合。在 `embedding_migration_state.json` 中记录 `active_collection`：

```json
{
  "version": 1,
  "current_active": "v2",
  "active_collection": "context_v2",
  "history": [...]
}
```

启动时 `OpenVikingConfig._resolve_embedding_from_embeddings()` 读取 `active_collection` 并修补 `vectordb.name`。集合命名规则：`embeddings["default"]` → `context`（无后缀）；其余 → `context_{embedder_name}`。

---

## 十二、CLI 命令行工具 / CLI Commands

运维人员通过 `ov migration` 子命令完成全部迁移操作，实现与 REST API 对等的端到端可操作性。

### 12.1 命令定义

| 命令 | 对应 API | 功能 |
|---|---|---|
| `ov migration start <name>` | `POST /start` | 启动蓝绿迁移 |
| `ov migration status` | `GET /status` | 查看迁移进度 |
| `ov migration targets` | `GET /targets` | 列出可用目标嵌入模型 |
| `ov migration build` | `POST /build` | 启动后台 reindex |
| `ov migration switch` | `POST /switch` | 确认切换读取侧 |
| `ov migration disable-dual-write` | `POST /disable-dual-write` | 关闭双写 |
| `ov migration finish [--confirm-cleanup]` | `POST /finish` | 完成迁移 |
| `ov migration abort` | `POST /abort` | 终止迁移 |
| `ov migration rollback` | `POST /rollback` | 非破坏性回滚 |

### 12.2 实现位置

`crates/ov_cli/src/commands/migration.rs`（Rust，与现有 `ov` CLI 一致）或 `openviking_cli/commands/migration.py`（Python）。

---

## 十三、风险与缓解 / Risks & Mitigations

| 风险 Risk | 概率 Prob | 影响 Impact | 缓解 Mitigation |
|-----------|----------|------------|-----------------|
| 大集合 reindex 超时 | 中 | 中 | 分页 + 异步后台 + 进度轮询 |
| 嵌入 API 限流 (429) | 中 | 低 | Semaphore 限流 + 指数退避 |
| 双写 standby 延迟 | 中 | 中 | standby 写入短超时 + best-effort |
| 迁移状态文件损坏 | 低 | 高 | 原子写入；损坏时明确拒绝启动 |
| 配置与状态不一致 | 低 | 高 | 启动时校验 current_active ∈ embeddings |

---

## 十四、测试覆盖要求 / Test Coverage Requirements

以下行为必须在测试中覆盖，确保迁移框架在全部阶段和异常路径下的正确性。

### 14.1 状态与配置 / State & Config

| # | 覆盖行为 | 验证点 |
|---|---|---|
| 1 | MigrationState 序列化/反序列化往返 | to_dict/from_dict 一致性 |
| 2 | MigrationStateManager 原子持久化 | tempfile + rename + FileLock；并发写入不损坏 |
| 3 | MigrationStateFile 读写 migration history | current_active 更新、history 追加、原子性 |
| 4 | embeddings 字段解析（含空/多配置/维度不匹配） | 向后兼容、活跃配置解析、C-9 硬阻断 |

### 14.2 双写适配器 / DualWriteAdapter

| # | 覆盖行为 | 验证点 |
|---|---|---|
| 1 | 正常双写 | upsert/delete 数据同时出现在 source/target |
| 2 | 活跃侧切换 | 切换后查询从正确集合返回 |
| 3 | Standby 写入失败容错 | 不阻塞 active 侧；degraded_write_failures 递增 |
| 4 | Standby 重试 | 短暂重试成功后计数器不递增 |
| 5 | drop_collection 安全约束 | 双写时拒绝；非活跃集合可删除 |
| 6 | 构造函数 None 校验 | 任一 adapter 为 None 时抛异常 |

### 14.3 重嵌入引擎 / ReindexEngine

| # | 覆盖行为 | 验证点 |
|---|---|---|
| 1 | 分页扫描 | 100K+ URI 场景内存可控（验证分页调用） |
| 2 | 跳过已有嵌入 | target 中已存在 URI 被跳过；skipped 计数正确 |
| 3 | 增量 reindex | 已有部分数据时仅处理缺失 URI |
| 4 | URI 去重 | 重复 URI 只处理一次 |
| 5 | Semaphore 限流 | 并发嵌入请求数不超过配置 |
| 6 | 优雅取消 | cancel 后不再处理新 URI，已完成的不中断 |
| 7 | 错误隔离 | 单个 URI 嵌入失败不影响其他 |
| 8 | 至少一次语义 | 处理成功 ack；失败消息保留在队列 |

### 14.4 状态机编排 / Controller

| # | 覆盖行为 | 验证点 |
|---|---|---|
| 1 | 每个正向转换 | 前置条件满足时成功 |
| 2 | 非法转换 | 不满足时抛 InvalidTransitionError |
| 3 | start_migration 持久化嵌入器标识 | source_embedder_name / target_embedder_name 写入 |
| 4 | begin_building 创建正确目标嵌入器 | 使用 state.target_embedder_name，非当前活跃 |
| 5 | 从 building_complete re-build | 增量处理，跳过已有嵌入 |
| 6 | confirm_switch 前置校验 | 目标嵌入器可达性 + reindex 完成后才切换 |
| 7 | finish_migration 更新 state file | current_active / active_collection / history 正确 |
| 8 | abort 各阶段清理 | building 阶段取消 reindex + 删除 target + 清 queue |
| 9 | rollback 非破坏性 | switched → dual_write，读切回 source；dual_write_off 拒绝 rollback |
| 10 | get_status 完整快照 | 返回 phase + progress + 计数 |

### 14.5 崩溃恢复 / Crash Recovery

| # | 覆盖行为 | 验证点 |
|---|---|---|
| 1 | 各阶段崩溃恢复 | 重启后状态正确（dual_write/building/switched/...） |
| 2 | building 恢复使用正确嵌入器 | config.get_target_embedder(state.target_embedder_name) |
| 3 | ReindexEngine 断点续传 | AGFS RecoverStale → 未 ack 消息自动恢复 |
| 4 | 构造时注入 | 活跃迁移重启 → VikingDBManager 构造时即含 DualWriteAdapter |

### 14.6 API 集成 / API Integration

| # | 覆盖行为 | 验证点 |
|---|---|---|
| 1 | 完整 happy path (HTTP) | start→build→轮询→switch→disable-dw→finish |
| 2 | 重启后活跃配置正确 | config.embedding 指向 current_active |
| 3 | 增量 re-build | 再次 build 跳过已有嵌入 |
| 4 | 嵌入器正确性 | reindex 后 target 集合向量维度与目标模型匹配 |
| 5 | 各阶段 abort | building 阶段 abort 验证 reindex 取消 + target 清理 |
| 6 | 各阶段 rollback | switched rollback 验证读回 source；dual_write_off 拒绝 |
| 7 | Auth 鉴权 | mutation 端点要求 admin/root；read 端点允许任意认证用户 |
| 8 | 并发安全 | 多线程 upsert + set_active 不崩溃 |
