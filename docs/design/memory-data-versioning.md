# Memory Data Versioning 方案

**日期：** 2026-05-28  
**状态：** Draft  
**作者：** Codex / ChatGPT  

---

## 目标

为记忆文件（memory files）增加版本化能力，满足以下需求：

1. 记忆文件支持版本历史。
2. 检索时可基于 `data_version` 查看某个历史时刻的记忆状态。
3. 不同版本的 diff 保存在记忆文件内部。
4. 支持按版本读取和 materialize 记忆内容。
5. 向量索引仅维护最新版本，避免多版本向量存储成本。

---

## 背景与动机

引入 memory versioning 的核心原因，不只是为了“可回溯”或“可审计”，更重要的是为了支持 **on-policy memory update**。

### 关键术语

为避免歧义，本文统一使用以下术语：

- **on-policy memory update**
  - 指经验（experience / memory）的写入、消费、评估，都应基于同一历史策略视图（policy view）来分析。
- **policy view**
  - 指 Agent 在某个时刻真实可见的一组 memory / experience 状态。
- **experience consumption**
  - 指 Agent 在执行任务时，对 memory / experience 的实际读取和使用。
- **historical memory state**
  - 指 memory 在某个历史 `data_version` 下的状态，而不是当前 latest state。

### 为什么需要 on-policy memory update

在 experience system 中，memory 不是静态知识，而是会随着任务反馈持续更新的策略对象（policy object）。

这意味着：

- Agent 在任务 T1 执行时，依赖的是当时的 `policy view`；
- 任务结束后，系统可能会把新的经验写入 memory；
- 到任务 T2 时，Agent 消费的 memory 已经发生变化；
- 因此，T1 和 T2 实际上处在不同的 `historical memory state` 下。

如果系统只保留 latest state，而不保留 historical memory state，那么 experience consumption 的分析就会被“事后知识”污染，导致经验更新无法做真正的 on-policy 归因。

### 示例：为什么需要 on-policy 视角

假设有一个 game agent，在挑战同一个 Boss，目标是尽快学会稳定通关。

#### 历史过程

- 在 `data_version = v1` 时，memory 中还没有这条经验：
  - **E1：Boss 放红圈大招前，会先抬手 1 秒，这时要立刻闪避，不要贪输出。**
- **Task A**：Agent 第一次打 Boss。
  - 当时的 `policy view = v1`
  - Agent 看到 Boss 抬手，但不知道这是大招前摇，于是继续输出
  - 结果被大招击杀
- 任务结束后，系统抽取并写入新经验 E1
- memory 更新到 `data_version = v2`
- **Task B**：Agent 第二次打 Boss
  - 当时的 `policy view = v2`
  - Agent 看到 Boss 抬手，识别为大招前摇，立即闪避
  - 结果成功存活，并完成通关

#### 如果系统支持 on-policy memory update

系统可以明确区分：

- **Task A** 的 experience consumption 基于 `policy view(v1)`
- **Task B** 的 experience consumption 基于 `policy view(v2)`

这样在分析时就能得到正确结论：

- Task A 失败，是因为当时的 historical memory state 中还没有 E1；
- Task B 表现变好，很可能是因为 v2 中新增的经验 E1 已经进入了新的 policy view，并被实际消费了。

#### 如果系统不支持 on-policy memory update

如果系统只保留 latest state（即只看到 v2），那么回看 Task A 时会产生错误理解：

- 当前 memory 明明已经写着“Boss 抬手后立刻闪避”；
- 系统会误以为 Task A 执行时也拥有这条经验；
- 从而错误地认为：
  - Task A 是“明知道该闪避却还在贪输出”；
  - 或者 Agent 执行不稳定；
  - 而不是“当时的 policy view 中根本没有这条经验”。

这就会带来几个典型问题：

1. **历史决策被未来知识污染**
   - 过去对局中的行为，会被后续才学到的经验反向解释。

2. **经验效果归因错误**
   - 无法判断 Task B 的改善究竟来自：
     - experience update
     - 随机发挥更好
     - Boss 行为恰好更容易处理

3. **经验生成与经验消费错位**
   - 一条在 Task A 之后才生成的经验，会被错误地当成 Task A 执行时已存在的前提。

4. **评估结果失真**
   - 系统无法准确回答：
     - 某条经验是否真的改变了后续对局行为；
     - 某次表现提升是否来自 memory update；
     - 某类经验是否值得保留或强化。

### 图示：遵守 on-policy 与不遵守 on-policy 的区别

下面用 Boss 战例子说明，为什么 experience system 需要遵守 on-policy memory update。

#### 场景

Agent 学到一条新经验：

- **E1：Boss 放红圈大招前，会先抬手 1 秒，这时要立刻闪避，不要贪输出。**

#### 情况 A：遵守 on-policy

```text
时间  ─────────────────────────────────────────────────────────→

data_version        v1                              v2
                    │                               │
                    │                               │
                    │   Task A: 第一次打 Boss        │
                    │   ─────────────────────       │
                    │   policy view = v1            │
                    │   memory 中没有 E1            │
                    │                               │
                    │   Agent 行为：                 │
                    │   - 看到 Boss 抬手             │
                    │   - 不知道这是大招前摇         │
                    │   - 继续输出                   │
                    │   - 被秒杀                     │
                    │                               │
                    └──────────────┐                │
                                   │                │
                                   │ 写入经验 E1    │
                                   │                │
                                   │ E1:            │
                                   │ “Boss 抬手 1 秒后
                                   │  会放红圈大招，
                                   │  应立即闪避”   
                                   ▼                │
                                                    │
                                                    │   Task B: 第二次打 Boss
                                                    │   ─────────────────────
                                                    │   policy view = v2
                                                    │   memory 中已有 E1
                                                    │
                                                    │   Agent 行为：
                                                    │   - 看到 Boss 抬手
                                                    │   - 识别为大招前摇
                                                    │   - 立刻闪避
                                                    │   - 成功存活并通关
                                                    │
结论：
- Task A 基于 v1
- Task B 基于 v2
- 可以正确分析：Task B 的改善，很可能来自 E1 被写入并进入了新的 policy view
```

#### 情况 B：不遵守 on-policy

也就是在事后分析时，只看 latest memory，而不回到当时的 historical memory state。

```text
真实执行时：
- Task A 看的是 v1
- Task B 看的是 v2

错误回看时（只看 latest = v2）：
- 回看 Task A，也拿 v2 解释
- 回看 Task B，也拿 v2 解释
```

```text
错误分析图：

Task A（v1） --------> 写入 E1 --------> Task B（v2）
   │                                         │
   └──────────── 事后统一拿 latest(v2) 回看 ──┘

结果：
- Task A 被错误地当成“明明知道要闪避却还在贪输出”
- Task B 的改善也无法准确归因到 E1
```

---

### 本方案解决的问题

因此，memory versioning 的本质，不只是“保存历史”，而是为 memory / experience 提供一个**时间维度上的 policy view**。

这也是为什么本方案必须支持：

- 按版本 `read`
  - 用于恢复某次任务执行时的 historical memory state
- 按版本 `search`
  - 用于近似重建某次任务当时的 experience consumption 视图
- 检索后 materialize 到目标版本
  - 用于把 latest recall 映射回目标 `policy view`

最终目标是让经验系统能够做到：

- **写入是增量演化的**（memory update）
- **消费是版本感知的**（experience consumption is version-aware）
- **分析是 on-policy 的**（evaluation is based on the correct policy view）

---

## 已确认的设计约束

### 1. 版本参数

统一使用：

- `data_version`

### 2. 版本号形式

使用毫秒级时间戳。

约定：

- 一次 memory apply / extraction batch 开始时生成一个统一的 `data_version`
- 这一批里所有被修改或删除的文件共用同一个 `data_version`

因此，`data_version` 是一批 memory 变更的全局版本号，而不是单文件独立版本号。

### 3. 检索语义

当指定：

```python
search(query, data_version=X)
```

语义为：

- 先按最新向量索引召回候选文件
- 对每个候选文件，取 `<= X` 的最近可用状态进行还原
- 如果某个候选文件不存在 `<= X` 的历史版本，则说明该文件在该版本视角下不存在可用状态，不应被召回

### 4. 向量策略

采用简化方案：

- 向量索引只保留最新版本
- 不为历史版本维护独立 embedding
- 历史检索通过“先召回最新，再按版本还原文件内容”实现

该方案的特点：

- 优点：实现简单、存储成本低
- 缺点：历史召回并非严格基于历史语义，只是基于最新语义的近似召回

### 5. 历史存储位置

- 版本 diff 保存在记忆文件内部

### 6. 正文与版本链关系

- 文件正文始终保存最新版本
- 历史版本通过 reverse diff 链从最新版本逐步回退得到

### 7. diff 粒度

- 采用整文件文本 diff
- diff 覆盖范围包括：
  - 正文内容
  - 普通 `MEMORY_FIELDS`

### 8. 删除策略

- 采用逻辑删除
- 默认检索不返回已删除文件
- 历史版本仍可恢复
- 删除时保留删除前最后正文，仅通过 `VERSION_HISTORY.status = "deleted"` 标记当前状态

### 9. checkpoint / compact

一期不做 checkpoint：

- 先使用“最新正文 + reverse diff 链”
- 后续通过 compact 机制压缩历史版本

### 10. diff 算法

- 使用 `diff-match-patch` 生成和应用整文件 reverse diff
- 不自定义 diff 格式

### 11. 历史读取范围

- 支持按版本 `search` 和按版本 `read`
- 历史版本用于恢复某个时刻的 memory 视图

### 12. 历史版本保留策略

- 每个记忆文件最多保留最近 **100** 个历史版本
- 超过 100 个版本时，直接丢弃最老版本
- 一期不做 checkpoint，不做 compact 合并

### 13. 并发与锁策略

- 写入 `memory_file` 时必须加锁
- 锁粒度为单文件级别（per-memory-file exclusive lock）
- 同一时刻只允许一个写入流程修改同一个 memory file
- 历史读取时不加读锁
- 读取正确性依赖写锁和原子落盘保证

### 14. 历史数据兼容性

- 需要兼容已存在但不包含 `VERSION_HISTORY` 的历史 memory file
- 对于这类文件，系统应将其视为仅包含当前 head 的单版本文件
- 读取当前版本时应正常工作，不要求强制迁移
- 当按 `data_version` 读取或检索时，当前文件版本的判断顺序为：
  1. 优先使用 `VERSION_HISTORY.data_version`
  2. 若缺失，则退化为 `VERSION_HISTORY.updated_at`
  3. 若两者都缺失，则视为版本未知的历史数据单版本文件
- 对于版本未知的历史数据单版本文件：
  - 不传 `data_version` 时可正常返回当前内容
  - 传入 `data_version` 时，不参与历史判断，直接视为不存在可用版本
- 若请求版本大于等于该文件当前可判定版本，则可直接返回当前内容
- 若请求版本早于该文件当前可判定版本，则视为不存在可用版本，不应返回该文件
- 历史文件在后续第一次被新版写入后，可自动补齐 `VERSION_HISTORY`

---

## 总体思路

### 核心原则

每个记忆文件只保留一份最新完整内容，历史通过文件内版本链回溯。

即：

- **当前态**：文件正文
- **历史态**：文件内部保存 reverse diff 列表
- **删除态**：逻辑删除标记

当需要查看历史版本时：

1. 读取最新正文
2. 根据 `data_version` 找出目标版本
3. 逐条应用 reverse diff
4. 得到目标时刻的文件内容

---

## 文件结构设计

建议把记忆文件拆成三部分：

1. 正文（最新版本内容）
2. `MEMORY_FIELDS`（普通业务元数据）
3. `VERSION_HISTORY`（系统版本元数据 + 版本链）

其中：

- `MEMORY_FIELDS` 参与 diff
- `VERSION_HISTORY` 不参与 diff 基线

这样可以避免“历史记录本身不断进入下一次 diff”的套娃问题。

---

## 建议文件格式

```md
用户偏好：
- 喜欢简洁回答
- 喜欢 TypeScript
- 不喜欢过度解释

<!-- MEMORY_FIELDS
{
  "memory_type": "preferences"
}
-->

<!-- VERSION_HISTORY
{
  "data_version": 1780000000123,
  "updated_at": "2026-05-27T15:10:23.456Z",
  "status": "active",
  "versions": [
    {
      "data_version": 1780000000123,
      "op": "update",
      "reverse_diff": "..."
    },
    {
      "data_version": 1779999999000,
      "op": "update",
      "reverse_diff": "..."
    },
    {
      "data_version": 1779999998000,
      "op": "create",
      "reverse_diff": null
    }
  ]
}
-->
```

---

## 版本语义

### 1. `VERSION_HISTORY.data_version`

表示当前文件正文对应的最新版本。

### 2. `VERSION_HISTORY.status`

表示当前正文在当前版本下的状态。

建议值：

- `active`
- `deleted`

### 3. `versions`

每个 version item 表示：

- 当前这版如何回退到上一版

例如：

- `v3` version 保存 `v3 -> v2` 的 reverse diff
- `v2` version 保存 `v2 -> v1` 的 reverse diff

因此，版本链方向是：

- 从新到旧逐步回退

---

## 写入流程设计

建议在现有 memory apply / updater 流程中改造。

### Step 1：生成 batch 级 `data_version`

一次 memory apply 开始时生成统一版本号：

```python
data_version = max(now_ms, last_data_version + 1)
```

目的：

- 保证单调递增
- 保证一次 batch 内所有文件共用同一个全局版本号

### Step 2：处理单文件写入

#### 场景 A：新建文件

- 写入最新正文
- 设置：
  - `VERSION_HISTORY.data_version = 当前 batch data_version`
  - `VERSION_HISTORY.updated_at = 当前写入时间`
  - `VERSION_HISTORY.status = "active"`
- `VERSION_HISTORY.versions` 追加：
  - `op = create`
  - `reverse_diff = null`

#### 场景 B：更新文件

流程：

1. 读取旧完整文件文本（正文 + `MEMORY_FIELDS`，不含 `VERSION_HISTORY`）
2. 生成新完整文件文本（正文 + `MEMORY_FIELDS`，不含 `VERSION_HISTORY`）
3. 计算 reverse diff：

```text
new_full_text -> old_full_text
```

4. 写入新正文与新 `MEMORY_FIELDS`
5. 更新 `VERSION_HISTORY.data_version`、`VERSION_HISTORY.updated_at`、`VERSION_HISTORY.status`
6. 在 `VERSION_HISTORY.versions` 追加：
   - `data_version = 当前 batch data_version`
   - `op = update`
   - `reverse_diff = ...`

#### 场景 C：逻辑删除

流程：

1. 不删除正文
2. 写入：
   - `VERSION_HISTORY.status = "deleted"`
   - `VERSION_HISTORY.updated_at = now`
   - `VERSION_HISTORY.data_version = 当前 batch data_version`
3. 追加版本记录：
   - `op = delete`
   - `reverse_diff = 当前 status=deleted 状态 -> 删除前状态`

这样历史还原时即可恢复删除前内容。

---

## 还原流程设计

提供一个核心能力：

```python
materialize_memory_at_version(file_uri, data_version)
```

### 还原逻辑

#### 不传 `data_version`

- 直接返回当前 head 内容
- 若当前 `VERSION_HISTORY.status = "deleted"`，则默认视为不可见

#### 传入 `data_version = X`

流程：

1. 读取最新文件正文、`MEMORY_FIELDS` 与 `VERSION_HISTORY`
2. 获取 `VERSION_HISTORY.data_version`（当前正文版本）
3. 若文件不存在任何 `<= X` 的历史版本：
   - 说明该文件在该版本视角下不存在可用状态，返回 not found / not visible
   - 对于不包含 `VERSION_HISTORY` 的历史文件，应按“仅存在当前 head 单版本”处理
4. 若 `VERSION_HISTORY.data_version <= X`：
   - 直接返回当前版本
5. 否则，从新到旧遍历 `VERSION_HISTORY.versions`
6. 对所有 `entry.data_version > X` 的记录依次应用 `reverse_diff`
7. 得到目标版本完整文本
8. 解析目标版本下的 `MEMORY_FIELDS` 与 `VERSION_HISTORY`
9. 若该版本 `VERSION_HISTORY.status = "deleted"`，则该版本不可见；否则返回

---

## 检索流程设计

### 接口形式

```python
search(query, data_version=None)
```

### 流程

#### Step 1：最新向量召回

从当前向量索引召回候选 memory file URI。

#### Step 2：历史还原

对每个候选 URI 调用：

```python
materialize_memory_at_version(uri, data_version)
```

#### Step 3：过滤删除态

- 当 `data_version is None`：
  - 过滤当前 `VERSION_HISTORY.status = "deleted"` 的文件
- 当指定 `data_version`：
  - 如果该历史版本的 `status = "deleted"`，则该文件不可见

#### Step 4：返回还原内容

将还原后的文本作为最终检索结果返回，或用于后续 prompt 注入。

---

## 删除可见性规则

### 默认检索

不传 `data_version` 时：

- 已逻辑删除文件不参与返回结果

### 历史检索

传 `data_version` 时：

- 如果目标版本当时未删除，则可见
- 如果目标版本当时已删除，则不可见

---

## 存储实现细节建议

### 1. `VERSION_HISTORY` 不参与 diff 基线

建议参与 diff 的“可还原业务文本”只包括：

- 正文
- `MEMORY_FIELDS`

不包括：

- `VERSION_HISTORY`

否则历史链本身会反复被写入 diff，导致版本膨胀。

### 2. diff 算法建议

推荐使用成熟文本 diff / patch 方案，例如：

- unified diff
- diff-match-patch

要求：

- 支持 reverse patch
- 支持稳定 apply
- 支持 patch 失败检测

### 3. 后续 compact 预留

虽然一期不做 compact，但建议版本历史结构预留压缩能力，例如未来支持：

- 历史条目合并
- 老版本快照化
- 版本链裁剪

### 4. 版本历史上限

一期直接限制：

- 每个文件最多保留最近 100 个历史版本
- 超过上限时，丢弃最老版本 item

这意味着非常老的历史版本可能不可恢复。

### 5. 单文件写锁

写入 memory file 时必须加单文件排他锁：

- 锁对象建议使用 memory file URI
- 锁覆盖范围包括：读取旧版本、生成 reverse diff、更新正文、更新 `MEMORY_FIELDS`、更新 `VERSION_HISTORY`
- 目的是保证版本链一致性，避免并发写入导致 diff 基线错误或历史链损坏

---

## 建议 metadata 字段

### `MEMORY_FIELDS`

只保留业务元数据，例如：

```json
{
  "memory_type": "preferences"
}
```

### `VERSION_HISTORY`

建议结构：

```json
{
  "data_version": 1780000000123,
  "updated_at": "2026-05-27T15:10:23.456Z",
  "status": "active",
  "versions": [
    {
      "data_version": 1780000000123,
      "op": "update",
      "reverse_diff": "..."
    },
    {
      "data_version": 1779999999000,
      "op": "delete",
      "reverse_diff": "..."
    },
    {
      "data_version": 1779999998000,
      "op": "create",
      "reverse_diff": null
    }
  ]
}
```

---

## 建议接口

### 1. 读取当前或历史版本

```python
read_memory(uri, data_version=None)
```

### 2. 检索当前或历史版本

```python
search(query, data_version=None)
```

### 3. 逻辑删除

```python
delete_memory(uri, data_version=batch_data_version)
```

说明：

- 历史版本支持按版本读取与按版本检索

---

## 一期方案优缺点

### 优点

- 改动相对集中
- 与当前 memory file 体系兼容
- 不需要多版本向量索引
- 单文件自包含历史
- 支持逻辑删除与历史视图读取

### 缺点

- 历史召回是近似召回，不是严格历史语义检索
- 老版本越旧，还原成本越高
- diff 链一旦损坏，可能影响更早版本恢复

这些问题可以在后续通过 compact / checkpoint / 辅助召回机制优化。

---

## 分阶段落地建议

### Phase 1：版本链基础能力

1. 定义 `VERSION_HISTORY` 格式
2. 在写入流程中生成 reverse diff
3. 实现 `materialize_memory_at_version`

### Phase 2：读取与删除

4. 支持 `read_memory(..., data_version=...)`
5. 实现逻辑删除及其历史可见性控制

### Phase 3：检索接入

6. 支持 `search(..., data_version=...)`
7. 检索后对候选文件做历史还原
8. 过滤 `status = "deleted"` 的文件

### Phase 4：后续优化

9. compact 压缩历史链
10. checkpoint / 快照机制
11. 历史关键词辅助召回

---

## 结论

本方案采用：

- `data_version` 作为 batch 级全局版本号
- 记忆文件正文保存最新版本
- 历史通过文件内 reverse diff 链保存
- 检索时先用最新向量召回，再按 `data_version` 还原目标状态
- 删除采用逻辑删除

这是一个偏工程实用的一期方案：

- 优先降低实现复杂度
- 保持与现有系统兼容
- 先完成可用版本化能力
- 后续再通过 compact / checkpoint 增强性能与健壮性


---

## 代码改造点清单

以下清单基于当前仓库代码结构，目标是把本设计落到现有 memory 写入、读取、检索链路中。

### 1. `openviking/session/memory/utils/memory_file_utils.py`

**职责：** 扩展 memory file 的解析与写回能力，支持 `VERSION_HISTORY`。

**建议改造：**

- 在现有 `MEMORY_FIELDS` 解析逻辑之外，增加 `VERSION_HISTORY` 注释块的读取与写入。
- 提供“参与 diff 的业务文本”序列化方法：
  - 包含正文
  - 包含普通 `MEMORY_FIELDS`
  - 不包含 `VERSION_HISTORY`
- 提供完整文件读写方法：
  - `read(...)` 继续兼容当前 `MemoryFile`
  - 新增类似 `read_version_history(...)`
  - 新增类似 `write_with_version_history(...)`
- 提供基于 `diff-match-patch` 的 helper：
  - 生成 reverse diff
  - 应用 reverse diff

**建议新增能力：**

- `extract_business_text(raw_content)`
- `parse_version_history(raw_content)`
- `append_version_entry(...)`
- `materialize_text_at_version(...)`

---

### 1.1 `openviking/session/memory/utils/memory_version_utils.py`

**职责：** 承载版本解析、历史版本还原、可见性判断等版本领域工具能力。

**建议新增能力：**

- `materialize_memory_at_version(...)`（建议放在 `memory_version_utils.py`）
- `resolve_version_for_data_version(...)`
- `is_version_visible(...)`
- `trim_versions(...)`

**说明：**

- `memory_file_utils.py` 负责 memory file 编解码与底层 diff/patch 辅助
- `memory_version_utils.py` 负责版本选择、reverse diff 回放、历史数据兼容与可见性判断

---

### 2. `openviking/session/memory/dataclass.py`

**职责：** 扩展 memory file / version history 的结构定义。

**建议改造：**

- 为版本记录新增 dataclass / pydantic model，例如：
  - `VersionHistoryItem`
  - `VersionHistory`
- 为 `MemoryFile` 增加版本相关字段：
  - `version_history`

**注意：**

- `version_history` 不应混入普通 `extra_fields` 的 diff 基线文本。

---

### 3. `openviking/session/memory/memory_updater.py`

**职责：** 在 memory upsert 时生成版本链。

**这是核心改造点。**

**建议改造：**

- 在 `_apply_upsert(...)` 中：
  1. 读取旧文件完整内容
  2. 生成新文件完整内容（正文 + 普通 `MEMORY_FIELDS`）
  3. 计算 `new -> old` 的 reverse diff
  4. 更新 `VERSION_HISTORY`
  5. 写回最新正文 + 新 `MEMORY_FIELDS` + 新 `VERSION_HISTORY`
- 在 delete 路径中支持逻辑删除：
  - 不物理删除文件
  - 改为写 `VERSION_HISTORY.status = "deleted"`
  - 写入 delete 对应的 reverse diff
- 增加单文件写锁，锁住整个“读旧内容 → 生成 diff → 写回”过程。

**建议新增入参或上下文：**

- `data_version`
  - 由 batch 级流程统一生成后传入

**建议处理的边界：**

- 新文件：`create`
- 普通更新：`update`
- 逻辑删除：`delete`
- 超过 100 个历史版本时裁剪最老 version item

---

### 4. `openviking/session/compressor_v2.py`

**职责：** 作为一次 memory apply batch 的组织入口，统一生成 `data_version`。

**建议改造：**

- 在 memory update batch 开始时生成统一的 `data_version`：

```python
data_version = max(now_ms, last_data_version + 1)
```

- 将该 `data_version` 传给：
  - memory upsert
  - memory delete
  - `memory_diff.json` 审计日志
- 在 `memory_diff.json` 中追加本次 batch 的 `data_version`，便于后续排查与审计。

**建议新增：**

- batch 级 `data_version` 生成函数
- 全局最近版本号持久化位置（可后续再定）

---

### 5. `openviking/session/session.py`

**职责：** 如果对外暴露 session 级 search / read，需要支持 `data_version` 透传。

**建议改造：**

- 在相关 `search` / `read` / context retrieval 接口上增加：
  - `data_version: Optional[int] = None`
- 将 `data_version` 传入底层 VikingFS 或 memory materialization 流程。

---

### 6. `openviking/storage/viking_fs.py`

**职责：** 检索入口支持 `data_version` 过滤后的 materialization。

**建议改造：**

- 在 `search(...)` 增加参数：
  - `data_version: Optional[int] = None`
- 当前搜索逻辑保持不变：
  - 仍然基于最新向量索引召回
- 在结果返回前增加一个后处理阶段：
  - 如果结果是 memory file，且指定了 `data_version`
  - 则调用 materialize 逻辑恢复目标版本
  - 若不存在 `<= data_version` 的可用版本，则过滤掉该结果
  - 若目标版本的 `status = "deleted"`，也过滤掉

**建议新增内部 helper：**

- `_materialize_search_result_at_data_version(...)`
  - 内部建议复用 `memory_version_utils.py`

---

### 7. `openviking/session/memory/tools.py`

**职责：** 如果 memory 工具对外提供 `read` / `search`，需要暴露 `data_version`。

**建议改造：**

- memory read tool 增加 `data_version`
- memory search tool 增加 `data_version`
- tool 输出要能明确区分：
  - 当前版本内容
  - 历史版本视图内容

---

### 8. `openviking/storage/content_write.py`

**职责：** 如果存在绕过 `MemoryUpdater` 的 memory 写入路径，需要检查并收口。

**建议改造：**

- 检查所有 memory write 是否都能统一走版本化写入逻辑
- 避免出现：
  - 某些路径写 memory file 但不写 version history
- 若无法统一，至少在 memory 相关写入处补齐：
  - 版本生成
  - 历史链维护
  - 逻辑删除处理

---

### 9. 检索结果结构与注入链路

**涉及位置：**

- `openviking/retrieve/...`
- `openviking/session/...`
- 调用 `search(...)` 后消费结果的代码

**建议改造：**

- 如果检索命中的是历史版本内容，结果结构里最好带上：
  - `data_version`
  - `is_historical` 或类似标记
- 方便上层调试和 observability。

---

### 9.1 历史数据兼容改造

**涉及位置：**

- `openviking/session/memory/utils/memory_file_utils.py`
- `openviking/session/memory/memory_updater.py`
- `openviking/storage/viking_fs.py`

**建议改造：**

- 解析 memory file 时允许 `VERSION_HISTORY` 缺失
- 对缺少 `VERSION_HISTORY` 的旧文件，按单版本 head 文件处理
- 对旧文件当前版本号的判断采用三段式 fallback：`VERSION_HISTORY.data_version` -> `VERSION_HISTORY.updated_at` -> 版本未知的历史数据文件
- 对版本未知的历史数据文件，禁止参与带 `data_version` 的历史视图
- materialize / search 过滤逻辑都要覆盖这种兼容路径
- 新版首次重写旧文件时，自动写入 `VERSION_HISTORY`

---

### 10. 测试建议

**建议新增测试范围：**

#### 单元测试

- `memory_file_utils`：
  - 解析 `VERSION_HISTORY`
  - 生成 reverse diff
  - 应用 reverse diff
- `memory_updater`：
  - create / update / logical delete
  - 超过 100 个版本后的裁剪
  - 单文件加锁后的并发安全性
- `materialize_memory_at_version`：
  - 命中 head
  - 命中历史版本
  - 不存在 `<= data_version` 的版本
  - 历史版本 `status = "deleted"` 的情况

#### 集成测试

- 写入多次后按版本 read
- search 命中后按版本 materialize
- 默认 search 不返回逻辑删除文件
- 指定 `data_version` 后返回删除前历史内容（若该版本可见）

---

### 11. 建议的最小落地顺序

1. 先补 `memory_file_utils` 的版本历史解析与 diff 能力
2. 再新增 `memory_version_utils.py`，实现 `materialize_memory_at_version(...)` 等版本工具
3. 再改 `memory_updater`，让写入真正产生版本链
4. 再把 `data_version` 接到 `read`
5. 最后把 `data_version` 接到 `search`

这样风险最可控。

---

## Implementation Checklist

### Phase 1：基础数据结构

- [ ] 在 `memory_file_utils.py` 中支持解析独立的 `VERSION_HISTORY` 注释块
- [ ] 新增 `memory_version_utils.py`
- [ ] 定义 `VERSION_HISTORY` 结构：`data_version` / `updated_at` / `status` / `versions`
- [ ] 明确 `MEMORY_FIELDS` 仅保留业务元数据
- [ ] 支持缺少 `VERSION_HISTORY` 的历史数据兼容路径

### Phase 2：版本读写能力

- [ ] 接入 `diff-match-patch` 生成 reverse diff
- [ ] 实现 `materialize_memory_at_version(...)`
- [ ] 实现 `resolve_version_for_data_version(...)`
- [ ] 实现 `is_version_visible(...)`
- [ ] 实现历史版本上限裁剪：最多保留 100 个版本

### Phase 3：写入链路改造

- [ ] 在 batch 开始时统一生成 `data_version`
- [ ] 在 `memory_updater.py` 中接入版本链写入
- [ ] create/update/delete 三种写入路径都生成版本记录
- [ ] delete 改为逻辑删除：写 `VERSION_HISTORY.status = "deleted"`
- [ ] 写入同一个 memory file 时加单文件排他锁

### Phase 4：读取与检索接入

- [ ] `read` 接口支持 `data_version`
- [ ] `search` 接口支持 `data_version`
- [ ] search 结果在返回前做历史版本 materialize
- [ ] 若不存在 `<= data_version` 的可用版本，则过滤结果
- [ ] 若目标版本 `status = "deleted"`，则过滤结果

### Phase 5：测试

- [ ] 单元测试：`VERSION_HISTORY` 解析
- [ ] 单元测试：reverse diff 生成与应用
- [ ] 单元测试：历史版本 materialize
- [ ] 单元测试：历史数据兼容路径
- [ ] 单元测试：版本上限裁剪
- [ ] 集成测试：按版本 read
- [ ] 集成测试：按版本 search
- [ ] 集成测试：逻辑删除后的当前/历史可见性

### Phase 6：后续优化（非一期必需）

- [ ] compact 机制
- [ ] checkpoint 机制
- [ ] 更强的历史召回优化
- [ ] observability / debug 输出增强


---

## 开发任务单（细化到文件 / 函数 / 测试）

### Task 1：扩展 memory file 解析格式

**目标：** 支持 `VERSION_HISTORY` 独立注释块，且不破坏现有 `MEMORY_FIELDS` 解析。

**文件：**
- `openviking/session/memory/utils/messages.py`
- `openviking/session/memory/utils/memory_file_utils.py`
- `openviking/session/memory/dataclass.py`
- `tests/unit/session/memory/test_memory_version_utils.py`（新增）

**建议函数改造：**
- `parse_memory_file_with_fields(...)`
  - 保持兼容旧逻辑
  - 可考虑拆成：
    - `parse_memory_fields_comment(...)`
    - `parse_version_history_comment(...)`
- `MemoryFileUtils.read(...)`
  - 支持从原始文件中同时解析 `MEMORY_FIELDS` 和 `VERSION_HISTORY`
- `MemoryFileUtils.write(...)`
  - 支持写回 `VERSION_HISTORY`

**建议新增测试：**
- `test_parse_memory_file_without_version_history()`
- `test_parse_memory_file_with_version_history()`
- `test_write_memory_file_with_version_history()`

---

### Task 2：新增版本工具模块

**目标：** 提供历史版本 materialize 与可见性判断能力。

**文件：**
- `openviking/session/memory/utils/memory_version_utils.py`（新增）
- `tests/unit/session/memory/test_memory_version_utils.py`（新增）

**建议新增函数：**
- `materialize_memory_at_version(raw_content: str, data_version: int | None) -> str | None`
- `resolve_version_for_data_version(version_history: dict, data_version: int) -> dict | None`
- `is_version_visible(version_history: dict) -> bool`
- `trim_versions(version_history: dict, limit: int = 100) -> dict`

**建议新增测试：**
- `test_materialize_returns_head_when_data_version_is_none()`
- `test_materialize_returns_head_when_head_version_lte_target()`
- `test_materialize_replays_reverse_diffs()`
- `test_materialize_returns_none_when_no_version_lte_target()`
- `test_materialize_filters_deleted_version()`
- `test_trim_versions_keeps_latest_100_versions()`

---

### Task 3：扩展版本数据模型

**目标：** 给 memory file 增加版本历史结构定义。

**文件：**
- `openviking/session/memory/dataclass.py`
- `tests/unit/session/memory/test_memory_version_models.py`（新增，可选）

**建议新增模型：**
- `VersionHistoryItem`
- `VersionHistory`

**建议模型字段：**
- `VersionHistory`
  - `data_version`
  - `updated_at`
  - `status`
  - `versions`
- `VersionHistoryItem`
  - `data_version`
  - `op`
  - `reverse_diff`

**建议新增测试：**
- `test_version_history_model_accepts_active_status()`
- `test_version_history_model_accepts_deleted_status()`
- `test_version_history_item_create_with_null_reverse_diff()`

---

### Task 4：改造写入链路，生成版本链

**目标：** 在 memory upsert / delete 时生成 reverse diff 并写入 `VERSION_HISTORY`。

**文件：**
- `openviking/session/memory/memory_updater.py`
- `openviking/session/compressor_v2.py`
- `tests/unit/session/memory/test_memory_updater_versioning.py`（新增）

**重点函数：**
- `MemoryUpdater._apply_upsert(...)`
- `MemoryUpdater._apply_delete(...)`
- `compressor_v2` 中发起 memory update batch 的入口

**具体改造点：**
- 在 batch 开始时生成统一 `data_version`
- upsert 时：
  - 读取旧文件
  - 生成新文件
  - 计算 `new -> old` reverse diff
  - 更新 `VERSION_HISTORY.data_version`
  - 更新 `VERSION_HISTORY.updated_at`
  - 更新 `VERSION_HISTORY.status = "active"`
  - 追加新的 version item
- delete 时：
  - 不物理删除文件
  - 仅设置 `VERSION_HISTORY.status = "deleted"`
  - 追加 delete version item
- 版本数超过 100 时裁剪最老 version item

**建议新增测试：**
- `test_apply_upsert_creates_initial_version_history()`
- `test_apply_upsert_appends_reverse_diff()`
- `test_apply_delete_marks_status_deleted()`
- `test_apply_upsert_trims_old_versions_over_limit()`

---

### Task 5：增加单文件写锁

**目标：** 防止并发写同一个 memory file 时破坏版本链。

**文件：**
- `openviking/session/memory/memory_updater.py`
- 可能涉及：`openviking/storage/transaction/*`
- `tests/unit/session/memory/test_memory_version_locking.py`（新增）

**改造建议：**
- 在 `_apply_upsert(...)` / `_apply_delete(...)` 外围加单文件级排他锁
- 锁范围覆盖：
  - 读旧文件
  - 生成 diff
  - 写回正文
  - 写回 `VERSION_HISTORY`

**建议新增测试：**
- `test_concurrent_writes_same_file_are_serialized()`

---

### Task 6：read 接口支持 data_version

**目标：** 文件读取时支持按版本视图读取。

**文件：**
- `openviking/storage/viking_fs.py`
- `openviking/service/fs_service.py`
- `openviking/async_client.py`
- `openviking/sync_client.py`
- `openviking/client/local.py`
- `tests/unit/session/memory/test_memory_version_read.py`（新增）

**重点函数：**
- `VikingFS.read(...)`
- `VikingFS.read_file(...)`
- 上层 client / service 的 read 接口

**具体改造点：**
- 增加 `data_version: Optional[int] = None`
- 对 memory file 读取：
  - 若未传 `data_version`，读 head
  - 若传入 `data_version`，走 `materialize_memory_at_version(...)`

**建议新增测试：**
- `test_read_returns_head_without_data_version()`
- `test_read_returns_materialized_content_with_data_version()`
- `test_read_returns_not_found_for_unknown_historical_file_when_data_version_specified()`

---

### Task 7：search 接口支持 data_version

**目标：** 检索结果在返回前支持历史版本 materialize。

**文件：**
- `openviking/storage/viking_fs.py`
- `openviking/server/routers/search.py`
- `openviking/async_client.py`
- `openviking/sync_client.py`
- `tests/unit/session/memory/test_memory_version_search.py`（新增）

**重点函数：**
- `VikingFS.search(...)`
- 对外 search router / client

**具体改造点：**
- 增加 `data_version: Optional[int] = None`
- 向量召回逻辑不变
- 在返回结果前增加后处理：
  - materialize 到目标版本
  - 若无 `<= data_version` 的可用版本，过滤
  - 若该版本 `status = "deleted"`，过滤

**建议新增测试：**
- `test_search_materializes_memory_results_to_target_data_version()`
- `test_search_filters_result_when_no_version_lte_target()`
- `test_search_filters_deleted_historical_version()`

---

### Task 8：历史数据兼容路径

**目标：** 兼容缺少 `VERSION_HISTORY` 的旧 memory file。

**文件：**
- `openviking/session/memory/utils/memory_version_utils.py`
- `openviking/storage/viking_fs.py`
- `tests/unit/session/memory/test_memory_version_legacy_compat.py`（新增）

**具体改造点：**
- 旧文件当前版本判断顺序：
  1. `VERSION_HISTORY.data_version`
  2. `VERSION_HISTORY.updated_at`
  3. 否则视为版本未知的历史数据文件
- 对版本未知的历史数据文件：
  - 不传 `data_version` 可返回当前内容
  - 传 `data_version` 时不参与历史视图

**建议新增测试：**
- `test_legacy_file_without_version_history_reads_head()`
- `test_legacy_file_without_version_history_is_filtered_in_historical_view()`
- `test_legacy_file_uses_updated_at_as_fallback_version()`

---

### Task 9：端到端验证

**目标：** 验证版本化写入、读取、检索链路端到端可用。

**文件：**
- `tests/integration/test_memory_data_versioning_e2e.py`（新增）

**建议场景：**
- 同一个 memory file 连续写入 3 次
- 按不同 `data_version` 读取，验证内容随版本变化
- 检索命中该 memory file 后，验证返回的是指定版本内容
- 删除后默认 search 不可见
- 删除前版本在历史视图中仍可见

---

### 建议实现顺序（执行版）

1. 先做 `messages.py` / `memory_file_utils.py` 的格式解析扩展
2. 再做 `memory_version_utils.py`
3. 再补 `dataclass.py` 版本模型
4. 再改 `memory_updater.py` 写入链路
5. 再加单文件锁
6. 再接 `read(data_version=...)`
7. 最后接 `search(data_version=...)`
8. 结尾补单测和 e2e
