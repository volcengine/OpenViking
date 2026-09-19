# 记忆整理（Memory Consolidation）

除了用 Skill 把来源材料编译成 Wiki、知识图谱、日报等产物，`ov compile` 还有一种特殊模式：**记忆整理**。它不生成新的知识形态，而是把 OpenViking 里**已有的记忆**就地整理干净——去重、合并、拆分、精简，同时严格遵守每种记忆类型原本的 schema。

与普通 compile 不同，记忆整理**不经过 VikingBot**，而是直接在 OpenViking 内部用记忆框架完成。

## 什么时候用

记忆随着一次次会话不断累积，时间久了会出现：

- **重复**：同一个人、同一件事被多次记录，措辞略有不同；
- **同一实体多份**：模型在不同批次里没认出是同一个对象，拆成了多条（比如"阿珍"和"陈静娴"其实是同一个人）；
- **混杂**：一条记忆里塞进了多个不相关的对象；
- **啰嗦**：同义反复、可以更紧凑。

这时对某个记忆目录跑一次记忆整理，就能让这类记忆集合重新变得干净、无冗余，且不丢事实。

## 怎么用

把 `--skill` 设为哨兵值 **`memory`**，`--to` 指向一个**记忆类型目录**即可：

```bash
# 整理 entities（就地去重 / 合并 / 规范化）
ov compile \
  --to viking://user/<user_id>/memories/entities \
  --skill memory \
  --instruction "合并明显重复的实体，但不同实体不要合并；保留每个实体的独立事实"
```

也可以整理其它记忆类型，比如偏好：

```bash
ov compile \
  --to viking://user/<user_id>/memories/preferences \
  --skill memory
```

命令同样立即返回一个 `cmp_...` 任务 ID，用 `ov task status <id>` 查看结果、用 `ov task cancel <id>` 取消。

## 参数

| 参数 | 说明 |
|------|------|
| `--skill memory` | 固定的哨兵值，触发记忆整理模式（不会被当作真实 Skill 解析）。 |
| `--to` | 必填，必须是**某个记忆类型目录**（如 `.../memories/entities`），不能只到 `.../memories` 根。整理就在这个目录里就地进行。 |
| `--from` | 记忆模式下**不接受**——整理不引入外部来源，只处理 `--to` 空间内已有的记忆。 |
| `--instruction` | 可选。作为整理指令（软提示）交给模型。用来点破模型自己判断不出来的合并，比如"阿珍就是陈静娴，请合并"。 |

## 行为要点

- **单一类型**：只加载 `--to` 目录对应的那一种记忆类型的 schema，整理收敛到这一种类型，不会跨类型改动。
- **就地整理**：`--from` 与 `--to` 是同一空间，不引入外部来源，因此不存在跨身份空间的串号问题。整理的空间（当前用户自己的 self 空间，还是某个 `peers/{peer_id}` 空间）由 `--to` 的 URI 决定。
- **保守合并**：默认只合并明显是同一身份的记忆；不同实体即使话题、类别、属性相近也不会被强行合并。模型从内容判断不出来的合并（例如两个不同名字其实是同一个人），必须通过 `--instruction` 明确点破才会执行。
- **不凭空创造**：只重组已有记忆，不会无中生有地新增事实。
- **保留事实**：合并、精简时会保留每一条独立的原子事实，只压缩重复表述。

## 返回结果

记忆模式的任务结果里包含本次整理的**变化文件清单**，字段沿用记忆归档 `memory_diff.json` 的语义：

| 字段 | 含义 |
|------|------|
| `adds` / `total_adds` | 新建的记忆文件（如拆分产生的新实体） |
| `updates` / `total_updates` | 被修改的记忆文件（合并后的目标、原地精简的文件） |
| `deletes` / `total_deletes` | 被删除/被合并掉的记忆文件 |
| `trace_id` | 本次整理的 trace，用于排查 |

清单中只包含文件 URI，不含正文——一次整理可能触及很多文件，正文不随结果返回。

一个典型的合并例子（把"阿珍"合并进"陈静娴"）：

```json
{
  "memory_type": "entities",
  "trace_id": "…",
  "adds": [],
  "updates": ["viking://user/xiaomei/memories/entities/person/陈静娴.md"],
  "deletes": ["viking://user/xiaomei/memories/entities/person/阿珍.md"],
  "total_adds": 0,
  "total_updates": 1,
  "total_deletes": 1,
  "errors": []
}
```

## 前置条件

- 一个正在运行的 OpenViking 服务（记忆整理在服务进程内执行，不需要额外启用 Bot）。默认端点 `http://localhost:1933`；远程使用需要 API Key，参见 [鉴权](../guides/04-authentication.md)。
- `ov` CLI 已配置好连接（`~/.openviking/ovcli.conf` 或 `OPENVIKING_*` 环境变量）。
- `--to` 指向的记忆目录里已经有记忆（一般由 session commit 从会话中抽取产生）。

## 相关文档

- [上下文编译概览](./01-overview.md) — `ov compile` 的整体介绍
- [Agent Runtime API](../api/23-agent-runtime.md) — 创建、查询和取消 Compile 任务的完整参考
