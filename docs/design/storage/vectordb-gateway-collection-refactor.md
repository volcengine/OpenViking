# OpenViking 向量存储分层重构设计（Gateway / Collection / Filter）

> 日期：2026-02-26  
> 状态：Draft（可直接进入实施）  
> 范围：`openviking/storage`、`openviking/retrieve`、`openviking/eval` 的向量存储接入层

---

## 1. 背景与目标

当前向量存储路径中，`VikingVectorIndexBackend`、`vector_store.driver`、`collection_adapter`、`vectordb.collection` 在职责上有重叠：

- 过滤表达式编译（AST -> DSL）分散在多层。
- 后端差异（URL 适配、索引参数、读写字段规范化）未完全下沉。
- `collection` 语义与“单 collection 绑定”的运行模型不完全一致。

本次重构目标：

1. **通用业务逻辑上收**到 gateway/backend 层。
2. **后端差异下沉**到 collection 层。
3. `compile_filter` **可扩展但简单**（默认实现 + 子类 override）。
4. backend/store **单 collection 绑定**（除 `create_collection(name, ...)` 外，不再传 collection）。
5. 过滤语义统一：`uri`/`parent_uri` 等 path 字段统一使用 `must` 语义。
6. 移除 `Prefix` / `Regex` expr AST 能力。

---

## 2. 已确认决策（来自前序讨论）

### 2.1 分层决策

- `CollectionAdapter` 与旧 `driver` 职责高度重叠，最终应移除。
- `compile_filter` 不放在独立 driver 层，放到 `ICollection`，由 `Collection` 在调用查询接口前触发。
- 新增向量库时，只需在对应 collection 实现重写 `compile_filter`（默认可不重写）。

### 2.2 单 collection 约束

- `VikingVectorIndexBackend`（及其上层管理者）内部只持有一个当前 collection。
- `create_collection(name, ...)` 是唯一需要显式 `name` 的入口。
- 后续 CRUD / search / filter 等都操作绑定 collection，不再重复传 collection 参数。

### 2.3 filter 语义决策

- 输入兼容：`FilterExpr | dict | None`。
- path 字段过滤不使用 prefix op，统一映射到 `must`。
- 不引入 `_path_must` 之类额外包装层。

### 2.4 命名与代码风格决策

- 业务代码中原有 `self.vikingdb` 命名保持，不做无收益替换为 `self.vector_store`。
- gateway 命名应更语义化（见第 8 节迁移建议）。

### 2.5 表达式能力决策

- `Prefix` / `Regex` 两个 expr AST 能力移除。
- 若需要后端特有复杂语法，使用 `RawDSL` 或 backend-specific collection override。

---

## 3. 当前代码现状（实施前基线）

### 3.1 主要组件

- `openviking/storage/viking_vector_index_backend.py`
  - 当前承担大量业务逻辑、collection 管理、filter 编译调用。
- `openviking/storage/vector_store/driver.py` + `drivers/*`
  - 当前仍存在后端差异封装与 `compile_expr`。
- `openviking/storage/collection_adapter.py`
  - 与 driver 层重复（工厂 + backend 分发 + filter 编译 + normalize）。
- `openviking/storage/vectordb/collection/collection.py`
  - `ICollection` / `Collection` 封装，尚未成为 filter 编译单一入口。

### 3.2 现状问题清单

1. **重复抽象**：driver 与 collection_adapter 并存，维护成本高。
2. **职责漂移**：backend 层仍携带后端差异处理逻辑。
3. **扩展成本高**：新增后端需改多层（factory/driver/backend）。
4. **接口噪音**：单 collection 场景下仍出现 collection 参数概念。

---

## 4. 目标架构

```text
[Business Callers]
    |
    v
[Semantic Gateway / VikingDBManager]
    - 租户作用域/业务语义
    - 单 collection 生命周期
    - 通用查询编排
    |
    v
[Collection (wrapper)]
    - 所有含 filters 的调用前统一 compile_filter
    - 统一结果包装
    |
    v
[ICollection implementations]
    - LocalCollection
    - HttpCollection
    - VolcengineCollection
    - VikingDBCollection
    - backend-specific compile_filter override（可选）
```

核心原则：

- **只保留一条“filter 编译路径”**：`ICollection.compile_filter`。
- **后端差异只出现在具体 collection 子类**。
- **gateway 不持有 backend 语法细节**。

---

## 5. `compile_filter` 设计规范

### 5.1 接口定义

在 `ICollection` 增加默认实现：

```python
def compile_filter(self, filter_expr: FilterExpr | dict | None) -> dict:
    ...
```

### 5.2 默认行为

- `None` -> `{}`
- `dict` -> 原样透传
- `RawDSL` -> 透传 payload
- `Eq/In` -> `{"op": "must", "field": ..., "conds": [...]}`
- `And/Or/Range/Contains/TimeRange` -> 按统一 DSL 映射

### 5.3 可扩展机制

- 新后端如语法不同：只在该后端 collection 中重写 `compile_filter`。
- 未重写时自动使用默认实现，保证接入门槛低。

### 5.4 复杂度控制

- 不引入额外 compiler 注册中心。
- 不新增 driver 级 compiler 层。
- 优先“默认实现 + 最小 override”。

---

## 6. FilterExpr 能力边界（重构后）

### 6.1 保留能力

- `And`
- `Or`
- `Eq`
- `In`
- `Range`
- `Contains`
- `TimeRange`
- `RawDSL`

### 6.2 移除能力

- `Prefix`
- `Regex`

### 6.3 path 字段规则

对 `uri` / `parent_uri` / 其他路径字段，统一使用：

```json
{"op":"must","field":"uri","conds":["..."]}
```

不允许在 AST 层保留 prefix 语义入口。

---

## 7. 单 Collection 运行模型

### 7.1 约束

- backend 实例内部仅绑定一个 active collection。
- 除 `create_collection(name, schema)` 外，其余操作基于绑定对象。

### 7.2 操作模型

- 创建流程：
  - `create_collection(name, schema)`
  - 建立 `self._collection` 绑定
  - 更新 meta cache
- 数据流程：
  - `insert/update/upsert/delete/get/search/filter/count/...` 均操作 `self._collection`

### 7.3 错误模型

- 未绑定 collection 时抛 `CollectionNotFoundError` 或统一运行时错误。
- 不再依赖每次调用传 collection_name 做防御。

---

## 8. 命名与接口整理建议

### 8.1 gateway 命名

建议把“语义检索网关”命名统一为更语义化名称（例如 `SemanticGateway` / `SemanticContextGateway`）。

> 兼容策略：保留原类名 alias 一段时间，避免一次性大面积改动。

### 8.2 变量命名一致性

- 业务模块中已存在 `self.vikingdb` 的位置保持不变。
- 不做“仅换名不换义”的全局改名（避免噪音 diff）。

### 8.3 说明

本设计文档不处理 CRUD private 收口；该议题后续单独评估。

---

## 9. 分阶段迁移计划（实施顺序）

### Phase A：FilterExpr 与语义基线

1. 移除 `Prefix` / `Regex` AST 定义与导出。
2. 清理编译分支中对应逻辑。
3. 修复测试中对 `Prefix`/`Regex` 的 AST 断言。

**验收**：无 `Prefix`/`Regex` 类型引用；编译与静态检查通过。

### Phase B：compile_filter 下沉到 Collection

1. `ICollection` 增加默认 `compile_filter`。
2. `Collection` wrapper 在查询前统一调用 `compile_filter`。
3. backend 中重复的 filter 编译逻辑删除。

**验收**：调用方仍可传 AST/dict，行为一致。

### Phase C：去除 driver / adapter 重复层

1. backend 不再依赖 `create_driver` / `VectorStoreDriver`。
2. 删除（或停用）`collection_adapter.py` 与 `vector_store/driver*` 路径。
3. 后端差异迁移到具体 `ICollection` 子类。

**验收**：新增 backend 仅需实现 collection + optional compile_filter override。

### Phase D：收口与稳定

1. 清理遗留 import/export。
2. 更新设计文档与开发文档。
3. 完成回归测试矩阵。

**验收**：无 dead adapter/driver 引用；主链路稳定。

---

## 10. 影响面与兼容性

### 10.1 影响模块

- `openviking/storage/*`
- `openviking/retrieve/*`
- `openviking/session/*`
- `openviking/eval/recorder/*`
- `openviking/eval/ragas/*`

### 10.2 兼容策略

- dict filter 调用保持兼容。
- AST 精简（去 Prefix/Regex）属于显式破坏性变更，依赖方需改为 `In` 或 `RawDSL`。
- eval/recorder 现状可继续使用通用 CRUD，不在本文档范围做 private 收口。

---

## 11. 测试与验收标准

### 11.1 静态与构建

- `ruff check` 通过
- `python -m compileall openviking` 通过

### 11.2 功能测试矩阵

1. **filter 编译**：AST / dict / RawDSL / None
2. **路径过滤**：`uri`/`parent_uri` 使用 `must` 语义
3. **单 collection**：create 后无需传 collection 参数
4. **后端差异**：至少验证一个后端 override `compile_filter` 生效
5. **回归链路**：检索、去重、目录初始化、URI 更新映射

### 11.3 代码检索验收

- 无 `Prefix` / `Regex` expr 定义与引用。
- 无 `collection_adapter` / `VectorStoreDriver` 生产路径引用（完成 Phase C 后）。

---

## 12. 风险与缓解

### 风险 1：迁移期双实现并存导致行为不一致

- **缓解**：以 `ICollection.compile_filter` 为唯一真源，旧分支尽快删除。

### 风险 2：测试桩接口与真实接口漂移

- **缓解**：统一测试 stub 最小接口契约，优先修复 `collection_exists_bound` 等缺失。

### 风险 3：后端特化语法回归

- **缓解**：在对应 collection override 中增加最小单测覆盖。

---

## 13. Out of Scope（本轮明确不做）

1. CRUD public/private 收口策略。
2. eval/recorder 能力边界重定义。
3. 非向量存储模块（FS/Parser/Client）的结构性重构。

---

## 14. 实施完成定义（DoD）

满足以下条件可认为本重构完成：

1. filter 编译链路单一（Collection 入口）。
2. backend 单 collection 绑定模式稳定。
3. driver/adapter 重复层移除。
4. `Prefix`/`Regex` expr 能力移除且无残留调用。
5. 主流程回归通过并补齐设计文档。

