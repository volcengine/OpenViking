# 设计草案：基于 Freshness 的上级摘要冒泡策略

状态：讨论稿

关联 RFC：`docs/design/l0-l1-okf-sidecars-rfc.md`

## 摘要

本文继续细化 L0/L1 OKF sidecar RFC 中的 `Freshness-Aware Parent Bubbling`。目标是在不引入复杂调度基础设施、不扩大 freshness 数据模型的前提下，减少没有实际收益的上级摘要生成、模型调用和逐级写放大。

首版策略只做两层判断：

1. 子目录摘要完成后，先判断父目录实际使用的子目录 L0 正文是否发生变化；如果 L0 未变，停止冒泡。
2. 如果 L0 确实变化，则小目录立即刷新；宽目录只累计现有的 `pending_child_changes`，变化比例达到阈值后再刷新。文件的新增、删除和修改在这里统一视为一次直接子项变化。

对于宽目录，采样只是一次摘要生成过程中的有界输入手段，不是一个需要持久化、追踪或参与调度判断的“采样成员集合”。

首版不提供最长陈旧时间兜底，不维护唯一变化子项集合，也不调整现有 freshness 字段的定义。它是一套务实的成本控制策略，而不是严格的实时一致性协议。

## 当前实现与四个具体问题

当前 resource/skill 语义任务成功后，会执行以下流程：

1. 将父目录 sidecar 的 `pending_child_changes` 加一。
2. 立即为父目录入队一次非递归语义刷新。
3. 父目录刷新成功后，对更上一级重复同样的流程。

这个实现简单，但存在四个值得明确的问题。

### 1. 子目录 L0 未变时仍然冒泡

父目录生成摘要时，消费的是直接子目录的 L0 正文，而不是子目录 L1，也不是 sidecar metadata。

如果子目录重新生成后只有 L1 或 metadata 发生变化，而 L0 正文没有变化，那么父目录的实际输入没有改变。此时仍然刷新父目录属于无效冒泡。

这是首版最应优先解决的问题，因为判断依据明确、实现成本低，而且每一级目录都可以独立停止继续向上传播。

### 2. `pending_child_changes` 统计的是事件次数

当前实现对每次观察到的变化执行整数累加。因此，同一个热点子目录连续变化十次，会被计为十次 pending change，而不是一个发生变化的直接子项。

这与“唯一变化直接子项数”的理想语义并不完全一致。不过，OpenViking 中常规文件变化频率并不高；即使偶尔出现热点连续变化，它最多让目录更早达到刷新阈值，不会让摘要比现在更陈旧。

因此，首版有意容忍这个近似：

- 不持久化 changed child key；
- 不对跨事件的同一 URI 去重；
- 不引入集合截断、哈希冲突或额外 metadata；
- 继续将 `pending_child_changes` 当作简单的变化事件计数使用。

一次批量请求内部仍可对重复 URI 做自然去重，但不建立跨请求的唯一子项状态。

### 3. 采样发生在子项摘要工作之后

当前目录 DAG 会先生成或读取所有直接子项的摘要，最后才使用 `overview_sample_limit` 选出进入父目录 overview prompt 的部分输入。

这能够限制 prompt 大小，却没有避免未参与本次聚合的子项摘要工作。对于宽目录，如果我们的目标是降低重新摘要成本，就应当在调度本轮父目录聚合所需的子项摘要之前完成有界采样。

这里的采样是本轮生成过程中的临时决策，不形成持久化的“当前采样成员”概念，也不参与是否立即刷新的判断。

### 4. 仅使用变化比例可能长期不刷新

例如，一个包含 161 个直接子项的目录始终只有 3 个文件发生变化，那么它可能长期达不到 10% 的刷新阈值。

这是比例阈值的已知取舍。首版不为此引入最长陈旧时间、定时扫描、延迟消息或 dirty-directory registry，原因是这些机制会显著扩大实现和运维复杂度，而当前场景中的预期收益有限。

在没有后续变化的情况下，目录可以通过显式刷新、重新导入或其他自然语义任务获得更新。是否需要时间兜底，应在上线观察真实 pending 分布后再决定，先不讨论。

## 设计原则

- 比较父目录真正消费的语义输入，而不是比较包含 metadata 的原始 sidecar 字节。
- 子目录向上冒泡时，只以 L0 正文变化作为语义信号。
- 文件新增、删除和修改对于宽目录阈值统计是等价的。
- 不把“是否属于上次 sample”作为调度条件。
- 不持久化变化子项集合，只累计简单计数。
- 不改变现有 freshness 的四个字段及其基本含义。
- 显式刷新、首次导入和同步等待应保持可预测，不应被自动阈值静默延迟。
- 文件自身的解析、摘要和向量维护，与目录聚合摘要是否延迟分开处理。
- 首版复用现有持久队列、路径锁和 coalesce 机制，不新增时间调度系统。

## 非目标

首版明确不解决以下问题：

- 不保证 pending change 在固定时间内一定被聚合。
- 不精确统计唯一变化的直接子项数。
- 不记录或维护某个目录的持久化采样成员集合。
- 不根据新增、删除、修改设置不同权重。
- 不根据文件大小、类型或摘要差异程度计算加权变化率。

## 保持现有 Freshness 模型

继续使用 RFC 已定义的字段：

```yaml
freshness:
  total_entries: 161
  sampled_entries: 32
  unsampled_entries: 129
  pending_child_changes: 3
```

字段解释保持不变：

- `total_entries`：上一次成功生成目录摘要时观察到的直接子项总数。
- `sampled_entries`：上一次生成实际使用的直接子项数量。
- `unsampled_entries`：上一次生成未采样的直接子项数量。
- `pending_child_changes`：上一次生成完成后，已知发生变化、但尚未反映到当前正文中的直接子项数。

上述字段定义不变。首版只是在实现上使用轻量的事件累加来近似维护 `pending_child_changes`，暂不对跨事件的同一直接子项去重。因此实际计数可能被重复事件保守高估。该近似只用于判断变化规模是否值得触发一次父目录刷新，不应被当作精确审计数据。

新增和删除可能使当前真实直接子项数与 `total_entries` 暂时不一致。首版仍使用上一次成功生成记录的 `total_entries` 作为阈值分母；下一次成功刷新会重新列目录并修正这些计数。

## 语义变化判断

### 子目录变化

子目录语义任务开始前读取旧 L0 body，生成完成后取得新 L0 body，对规范化正文计算 digest：

```text
old_l0_digest = hash(normalize(old_l0_body))
new_l0_digest = hash(normalize(new_l0_body))
```

digest 不包含：

- YAML frontmatter；
- freshness；
- source；
- generated_by；
- 文件修改时间或其他存储属性。

决策规则：

- 新旧 L0 digest 相同：不标记父目录 pending，不为父目录入队，冒泡在当前层终止。
- 新旧 L0 digest 不同：向父目录上报一次普通的 direct-child change。
- 旧 L0 不存在或无法可靠解析：保守地视为发生变化。

L1 是否变化只决定当前目录自己的写回或向量维护，不决定是否继续向祖先冒泡。

### 直接文件变化

对于直接文件，新增、删除和修改都向所在目录贡献变化计数。首版不要求在入队前比较新旧文件摘要，因为这样会把语义生成工作前移到调度判断阶段，抵消低成本策略的收益。

一次批量操作中有多少个不同的 changed URI，就对本轮 pending 增加多少；后续独立事件再次修改同一个 URI，可以再次累加。

## 调度决策

策略输出只有三种：

```text
NOOP
MARK_PENDING
REFRESH_NOW
```

建议按以下顺序判断：

| 条件 | 决策 | 说明 |
| --- | --- | --- |
| 子目录新旧 L0 正文相同 | `NOOP` | 父目录实际输入没有变化 |
| 父目录不存在有效 sidecar | `REFRESH_NOW` | 没有可继续使用的摘要基线 |
| 首次导入或显式语义刷新 | `REFRESH_NOW` | 保持调用者意图 |
| 父目录上次记录的 `total_entries <= overview_sample_limit` | `REFRESH_NOW` | 小目录变化后直接刷新 |
| 宽目录累计变化率低于阈值 | `MARK_PENDING` | 只更新 freshness，不入队 |
| 宽目录累计变化率达到阈值 | `REFRESH_NOW` | 入队一次有界父目录刷新 |

这里没有以下特殊规则：

- sampled child 变化不要求立即刷新；
- 新增导致本轮临时 sample 变化不要求立即刷新；
- 删除导致本轮临时 sample 变化不要求立即刷新；
- 目录变化和文件变化不设置不同阈值。

对于宽目录，所有增删改都只是 `pending_child_changes` 的增量。

## 宽目录阈值

继续用 `semantic.overview_sample_limit` 区分小目录和宽目录，当前默认值为 32。

首版只新增一个比例配置：

```yaml
semantic:
  overview_sample_limit: 32
  freshness_refresh_ratio: 0.10
```

宽目录的判断为：

```text
pending_after = pending_before + current_change_count
change_ratio = pending_after / max(total_entries, 1)

refresh_now = change_ratio >= freshness_refresh_ratio
```

等价的整数阈值为：

```text
refresh_threshold = ceil(freshness_refresh_ratio * total_entries)
```

示例：

| 上次记录的直接子项数 | 10% 阈值 |
| ---: | ---: |
| 33 | 4 |
| 161 | 17 |
| 1000 | 100 |

首版不额外设置最小变化数或最大变化数，以减少配置项并保持“变化比例”含义直观。如果真实运行数据显示超宽目录等待过久或一次刷新积压过多，再考虑增加上限。

由于热点子项可重复累计，阈值可能比真实的唯一文件变化率更早达到。这是有意接受的保守偏差：它可能多触发一次刷新，但不会让目录更久不刷新。

代码实现时注意将公式的上述解释注释到代码。

## 达到阈值后的刷新方式

宽目录达到阈值后，不应只携带“最后一次变化的 URI”执行普通增量刷新。此前被延迟的变化没有持久化 URI 集合，仅保留了计数；只处理最后一次变化会遗漏先前事件。

因此，阈值触发的任务应被定义为一次当前目录的有界完整聚合：

1. 重新列举当前直接子项。
2. 根据 `overview_sample_limit` 选出本轮有界输入。
3. 为本轮选中的输入读取或生成当前摘要。
4. 生成新的 L1，并从 L1 正文提取 L0。
5. 写入最新的 `total_entries`、`sampled_entries` 和 `unsampled_entries`。
6. 消费本轮刷新开始时已经观察到的 pending 计数。

这里的“完整”是指从当前目录状态重新开始一次聚合决策，不是读取宽目录中的全部文件内容。实际进入目录摘要的输入仍受 `overview_sample_limit` 限制。

每次刷新都可以根据当时的目录状态重新进行确定性采样。系统不记录“上次 sample 中有哪些成员”，调度器也不基于 sample membership 做判断。

## 将采样前移

为了让宽目录阈值真正降低成本，应把本轮目录聚合采样前移到昂贵工作之前：

```text
列举直接子项
  -> 确定本轮有界 sample
  -> 只为本轮聚合准备 sample 所需的摘要输入
  -> 生成目录 L1/L0
```

采样算法只需要满足两个条件：

- 对同一个稳定目录重复执行时结果确定；
- 能够覆盖目录中不同位置的子项，而不是永远只取前 32 个。

可以继续使用现有的确定性保序采样。是否改成哈希采样不属于 freshness 冒泡首版的必要条件，因为我们不再赋予 sample membership 调度语义。

必须区分两类工作：

- 文件自身的解析、摘要或向量更新；
- 文件摘要作为输入参与父目录 L1/L0 聚合。

父目录聚合被延迟或采样，不能导致发生变化的文件失去自身应有的向量维护。采样前移只减少为“生成目录摘要”而进行的额外工作。

## Sidecar 写回结果

当前 sidecar 写回只返回成功与否，无法区分正文变化和 metadata 变化。建议返回结构化结果：

```python
AbstractOverviewWriteResult(
    wrote=True,
    overview_body_changed=True,
    abstract_body_changed=False,
)
```

语义如下：

- `wrote`：是否成功完成写回流程；
- `overview_body_changed`：L1 可见正文是否改变；
- `abstract_body_changed`：L0 可见正文是否改变。

metadata 从 `pending_child_changes > 0` 重置或减少，也可能造成 raw sidecar 变化，但这种变化不应被视为 L0/L1 语义变化。

后续行为：

- L0 正文改变：可以向父目录上报一次变化；
- 只有 L1 正文改变：只处理当前目录自己的写回和向量维护；
- L0/L1 正文都未改变：不需要重新向量化正文，也不继续冒泡；
- 只有 freshness 改变：只写 metadata。

### 标记和决策

在 sidecar exact-path lease 下：

1. 读取当前 `pending_child_changes`。
2. 加上本次 `current_change_count`。
3. 写回新计数。
4. 基于写回后的计数和现有 `total_entries` 做阈值判断。
5. 释放 lease 后再执行 enqueue。

这样 metadata 更新和调度判断来自同一个一致快照。并发事件即使同时到达，也不会简单覆盖彼此的计数。

## API 语义

以下操作默认绕过宽目录阈值，立即刷新：

- 首次资源导入；
- reindex 等触发的显式语义刷新；

普通后台文件变化可以被阈值延迟。如果一次操作只更新 freshness 而没有入队目录摘要，返回结果应明确表达：

```json
{
  "semantic_status": "deferred",
  "semantic_root_uri": "viking://resources/example"
}
```

此时文件自身的向量维护状态应单独表达，不能因为目录摘要被延迟就返回整体 `complete` 或错误地声称已经 `queued`。

## 实现边界

保持 `SemanticProcessor` 是 resource、skill 和 memory 语义处理的统一入口，不引入新的顶层 processor。

建议职责划分如下：

| 位置 | 职责 |
| --- | --- |
| `openviking/storage/queuefs/semantic_ops/freshness_policy.py` | 纯粹的 `NOOP` / `MARK_PENDING` / `REFRESH_NOW` 判断 |
| `openviking/storage/semantic_sidecar.py` | 在 lease 下原子累加、读取和消费现有 freshness 计数 |
| `SemanticDagExecutor` | 在宽目录聚合前完成本轮采样，并返回 L0/L1 正文变化结果 |
| `SemanticProcessor` | 根据 L0 是否变化决定是否继续向父目录冒泡 |
| content write、delete、resource 路径 | 将文件增删改统一记录为所在目录的变化计数 |

不新增 maintenance service、定时 sweep、延迟队列或 dirty-directory registry。

### 采样前移

- 宽目录在生成子项聚合输入前完成采样。
- 未进入本轮目录 sample 的子项不会仅为父目录聚合而产生额外摘要调用。
- 发生变化的文件仍然完成自身需要的向量维护。
- 稳定目录重复刷新产生确定性的采样结果。

### API 行为

- 自动延迟返回 `semantic_status: deferred`。
- 实际入队返回 `queued`，同步完成返回 `complete`。
- 目录聚合延迟与文件自身向量状态可以被调用方区分。

## 示例

假设一个目录上次生成时有 161 个直接子项，`overview_sample_limit=32`，刷新比例为 10%。

1. 子目录 `a/` 完成语义生成，但新旧 L0 相同：不修改父目录 freshness，也不继续冒泡。
2. 文件 `x.md`、`y.md` 和 `z.md` 发生修改：`pending_child_changes` 变为 3，低于阈值 17，不刷新目录摘要。
3. `x.md` 后续又被修改十次：pending 可以累计到 13。首版不做跨事件去重。
4. 再发生四次任意直接子项增删改后，pending 达到 17，触发一次父目录刷新。
5. 刷新重新列举当前目录并选择本轮最多 32 个输入，不关心这些输入是否属于某个历史 sample。
6. 刷新开始时捕获 pending snapshot；执行期间新增的变化计数会在写回后保留。
7. 如果刷新后的父目录 L0 仍然未变，则冒泡在父目录停止，不再刷新祖父目录。
8. 如果目录长期停留在 3 个 pending 且没有其他自然刷新，首版允许它继续保持这一 freshness 状态。
