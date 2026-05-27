# Memory Data Versioning 方案

**日期：** 2026-05-27  
**状态：** Draft  
**作者：** Codex / ChatGPT  

---

## 目标

为记忆文件（memory files）增加版本化能力，满足以下需求：

1. 记忆文件支持版本历史。
2. 检索时可基于 `data_version` 查看某个历史时刻的记忆状态。
3. 不同版本的 diff 保存在记忆文件内部。
4. 支持按版本还原记忆内容。
5. 向量索引仅维护最新版本，避免多版本向量存储成本。

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
- 删除时保留删除前最后正文，仅通过 metadata 标记 deleted

### 9. checkpoint / compact

一期不做 checkpoint：

- 先使用“最新正文 + reverse diff 链”
- 后续通过 compact 机制压缩历史版本

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
3. `VERSION_HISTORY`（版本历史元数据）

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
  "memory_type": "preferences",
  "updated_at": "2026-05-27T15:10:23.456Z",
  "data_version": 1780000000123,
  "deleted": false,
  "deleted_at": null
}
-->

<!-- VERSION_HISTORY
{
  "head_data_version": 1780000000123,
  "entries": [
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

### 1. `head_data_version`

表示当前文件正文对应的最新版本。

### 2. `entries`

每条 entry 表示：

- 当前这版如何回退到上一版

例如：

- `v3` entry 保存 `v3 -> v2` 的 reverse diff
- `v2` entry 保存 `v2 -> v1` 的 reverse diff

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
  - `MEMORY_FIELDS.data_version = 当前 batch data_version`
  - `deleted = false`
- `VERSION_HISTORY.entries` 追加：
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
5. 更新 `head_data_version`
6. 在 `VERSION_HISTORY.entries` 追加：
   - `data_version = 当前 batch data_version`
   - `op = update`
   - `reverse_diff = ...`

#### 场景 C：逻辑删除

流程：

1. 不删除正文
2. 写入：
   - `deleted = true`
   - `deleted_at = now`
   - `data_version = 当前 batch data_version`
3. 追加版本记录：
   - `op = delete`
   - `reverse_diff = 当前 deleted 状态 -> 删除前状态`

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
- 若当前 `deleted = true`，则默认视为不可见

#### 传入 `data_version = X`

流程：

1. 读取最新文件正文与 metadata
2. 获取 `head_data_version`
3. 若文件不存在任何 `<= X` 的历史版本：
   - 说明该文件在该版本视角下不存在可用状态，返回 not found / not visible
4. 若 `head_data_version <= X`：
   - 直接返回当前版本
5. 否则，从新到旧遍历 `VERSION_HISTORY.entries`
6. 对所有 `entry.data_version > X` 的记录依次应用 `reverse_diff`
7. 得到目标版本完整文本
8. 解析目标版本下的 `MEMORY_FIELDS`
9. 若该版本 `deleted = true`，则该版本不可见；否则返回

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
  - 过滤当前 `deleted = true` 的文件
- 当指定 `data_version`：
  - 如果该历史版本为 deleted，则该文件不可见

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

---

## 建议 metadata 字段

### `MEMORY_FIELDS`

建议增加或统一以下字段：

```json
{
  "data_version": 1780000000123,
  "updated_at": "2026-05-27T15:10:23.456Z",
  "deleted": false,
  "deleted_at": null
}
```

### `VERSION_HISTORY`

建议结构：

```json
{
  "head_data_version": 1780000000123,
  "entries": [
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

### 4. 恢复某历史版本为新 head

```python
restore_memory(uri, target_data_version)
```

恢复语义建议：

- 先 materialize 到旧版本
- 再以一个新的 `data_version` 写成最新 head
- 不直接篡改历史链

---

## 一期方案优缺点

### 优点

- 改动相对集中
- 与当前 memory file 体系兼容
- 不需要多版本向量索引
- 单文件自包含历史
- 支持逻辑删除与历史恢复

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
5. 实现逻辑删除及其历史恢复

### Phase 3：检索接入

6. 支持 `search(..., data_version=...)`
7. 检索后对候选文件做历史还原
8. 过滤 deleted 文件

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

