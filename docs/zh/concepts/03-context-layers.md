# 上下文层级（L0/L1/L2）

OpenViking 使用三层信息模型，在检索效率、导航能力和原始内容完整性之间取得平衡。

## 概览

| 层级 | 名称 | 存储形式 | 默认正文上限 | 用途 |
| --- | --- | --- | --- | --- |
| **L0** | 摘要 | 目录内的 `.abstract.md` | 256 字符 | 向量检索、快速过滤 |
| **L1** | 概览 | 目录内的 `.overview.md` | 4000 字符 | Rerank、内容导航 |
| **L2** | 详情 | 原始文件和子目录 | 无统一上限 | 完整内容、按需加载 |

L0/L1 是**目录级语义 sidecar**。它们描述一个目录，不是为每个普通文件创建的同名伴生文件。文件摘要会作为输入，聚合到所在目录的 L1 中。

L0 和 L1 通常成对生成，但系统允许只存在其中一个。例如，`mkdir(description=...)` 会先创建 L0，因此只有 `.abstract.md` 的目录也是合法状态。读取和向量重建只处理实际存在的层级。

正文上限由 `semantic.abstract_max_chars` 和 `semantic.overview_max_chars` 配置；上表是默认值。限制只作用于 Markdown 正文，不会截断 sidecar 元数据。

## L0：摘要

L0 是目录内容的最精简表示，用于向量召回和快速相关性判断。

```markdown
API 认证指南，涵盖 OAuth 2.0、JWT 令牌和 API 密钥的安全访问方式。
```

通过语义 accessor 读取时，只返回可见正文：

```python
abstract = client.abstract("viking://resources/docs/auth")
```

## L1：概览

L1 提供更完整的目录摘要和导航信息，用于 Rerank 和决定是否继续加载 L2。

```markdown
# 认证指南

本目录介绍 API 的主要认证方式。

## 快速导航

- `oauth.md`：OAuth 2.0 流程和代码示例
- `jwt.md`：令牌生成和验证
- `api-keys.md`：API Key 认证
```

```python
overview = client.overview("viking://resources/docs/auth")
```

L0 从 L1 正文中提取：取 H1 标题之后、第一个 `##` 标题之前的 Brief Description 段落。YAML frontmatter 不参与提取。

## L2：详情

L2 是原始文件或解析后的完整内容，只在需要时加载，并保留源格式和结构。

```python
content = client.read("viking://resources/docs/auth/oauth.md")
```

## 目录结构

一个已完成语义处理的目录通常如下：

```text
viking://resources/docs/auth/
├── .abstract.md          # L0，隐藏的目录级 sidecar
├── .overview.md          # L1，隐藏的目录级 sidecar
├── oauth.md              # L2，完整内容
├── jwt.md                # L2，完整内容
└── api-keys.md           # L2，完整内容
```

普通 `ls` 默认隐藏 `.abstract.md` 和 `.overview.md`。它们不一定同时存在；不要依赖“每个目录始终具有两个 sidecar”的假设。

## OKF sidecar 格式

新生成的 L0/L1 使用最小 OKF Markdown：YAML frontmatter 加可见 Markdown 正文。

```markdown
---
directory: viking://resources/docs/auth/
source:
  kind: http
  uri: https://example.com/auth.pdf
generated_by:
  component: SemanticProcessor
  trigger: resource_ingest
freshness:
  total_entries: 3
  sampled_entries: 3
  unsampled_entries: 0
  pending_child_changes: 0
---

API 认证指南，涵盖 OAuth 2.0、JWT 令牌和 API 密钥。
```

初始元数据字段如下：

| 字段 | 含义 |
| --- | --- |
| `directory` | sidecar 所描述的目录 URI |
| `source` | 可选的导入来源；通常只记录在导入根目录 |
| `generated_by` | 生成组件和粗粒度触发原因 |
| `freshness` | 直接子项覆盖率和已知的待刷新变化 |

已知字段会进行 schema 校验。未知顶层字段或已知对象中的未知嵌套字段会被静默丢弃，不会进入 preview、embedding、canonical writeback 或 metadata 写保护比较。没有 frontmatter 的旧 sidecar 继续作为 legacy Markdown 读取；YAML 损坏、缺少必填 `directory` 或已知字段类型错误仍会显式失败。

## 不同读取表面的行为

同一个 sidecar 在不同表面返回不同视图：

| 访问方式 | 返回内容 |
| --- | --- |
| `abstract()` / `overview()` | 仅 Markdown 正文 |
| `find`、search/rerank preview | 仅 Markdown 正文 |
| `ls output=agent`、tree agent 输出 | 仅 Markdown 正文 |
| 直接 `read(".../.abstract.md")` | 原始 frontmatter 和正文 |
| 普通 `ls` | 默认不列出隐藏 sidecar |

语义生成父目录摘要时也只读取子目录 L0 的正文，`source`、`generated_by` 和 `freshness` 不会进入总结 prompt。

## Embedding 元数据白名单

L0/L1 的 embedding 输入由正文和显式白名单元数据组成。当前白名单只有 `directory`：

```markdown
---
directory: viking://resources/docs/auth/
---

API 认证指南，涵盖 OAuth 2.0、JWT 令牌和 API 密钥。
```

`source`、`generated_by`、`freshness` 以及未知字段都不会进入 embedding。正常向量化和 admin `vectors_only` reindex 使用相同策略，避免重建索引后改变检索输入。L1 的 rerank scalar 仍然是纯 L1 正文。

## Freshness 与稳定采样

`freshness` 统计当前目录的**直接子项**，而不是整个递归子树：

- `total_entries`：参与目录语义的直接文件和直接子目录总数。
- `sampled_entries`：本轮实际用于总结的直接子项数。
- `unsampled_entries`：未采样的直接子项数，满足 `sampled + unsampled = total`。
- `pending_child_changes`：已知发生变化、但尚未反映到当前正文中的直接子项数。

当直接子项超过 `semantic.sidecar_sample_size`（默认 32）时，系统使用确定性、保序的稳定采样。相同目录树重复刷新会选择相同样本，避免无意义的正文和 Git diff 抖动。

`pending_child_changes > 0` 表示正文仍然可读，但已知落后于下层变化。父目录刷新成功后，该值会随新的覆盖率元数据重置为 0。

当前 resource/skill 语义任务成功后会逐级安排父目录刷新，并在入队前将父目录标记为 pending，直到 namespace 根边界。

> **TODO：基于 freshness 控制冒泡频率**
>
> 当前实现会在每次成功的 resource/skill 语义任务后尝试向父级冒泡，即使新生成的子目录摘要与旧值相同。这不是最终期望的调度策略。后续应利用 `freshness` 数据实现合并、阈值或时间窗口节流，例如综合 `pending_child_changes`、采样覆盖率、直接子项变化规模和最近刷新状态，降低热点目录的重复刷新与向上写放大，同时保证最终一致性。

## 写保护

L0/L1 正文可以通过公共 `write` / `batch_write` 更新，但 metadata 受保护：

- 目标 sidecar 必须已存在；公共 API 不允许直接创建新的 `.abstract.md` / `.overview.md`。
- 只提交正文时，系统保留现有 metadata 并重新拼回 canonical OKF。
- 提交完整 OKF 时，已知 metadata 必须与现有值一致；修改受保护字段会失败。
- 未知 metadata 字段会静默丢弃。
- `append` 只追加正文，不会把用户内容追加到 frontmatter。
- 正文更新只重建该目录实际存在的 L0/L1 向量，不触发语义重新生成，避免刚写入的正文被覆盖。

## 生成机制

SemanticProcessor 自底向上处理目录：

```text
文件摘要 → 叶子目录 L1 → 叶子目录 L0 → 父目录 → namespace 根边界
```

子目录 L0 被聚合到父目录 L1。Memory 目录也通过统一的 SemanticProcessor 入口处理，但当前父级冒泡逻辑只用于 resource/skill。多模态文件会先生成文本摘要，再作为普通文件摘要参与其所在目录的 L0/L1；不会为每个图片、音频或视频创建 per-file L0/L1 sidecar。

## 最佳实践

| 场景 | 推荐层级 |
| --- | --- |
| 快速相关性检查 | L0 |
| 理解目录内容范围 | L1 |
| 详细信息提取 | L2 |
| 为 LLM 构建初步上下文 | L1，必要时再加载 L2 |
| 检查 sidecar 来源或 freshness | 直接读取 sidecar 原始内容 |

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [上下文提取](./06-extraction.md) - L0/L1 生成流程
- [检索机制](./07-retrieval.md) - 检索流程详解
