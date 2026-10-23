# Rule Scenario

Rule 按 **Scenario** 分组；Scenario 表示约束可能相关的一类实际情况。
Compiler 在 NEW mutation 中使用 `scenario`，并使用 `scenario_id` 标识 Scenario。
Rule 搜索结果包含 `scenarios`、每条 Rule 的 `scenario_id` 以及
`scenario_selection`。

每个 Scenario 都是用户 Rule 库下的一个直接子目录：

```text
viking://user/<user_id>/memories/rules/
└── file-operations/
    ├── abstract.md
    ├── confirm-delete.md
    └── preserve-backups.md
```

`abstract.md` 保存 Scenario 元数据和简短描述：

```markdown
---
schema: openviking.rule.scenario/v1
scenario_id: file-operations
name: File operations
description: Constraints for creating, modifying, and deleting files.
updated_at: "2026-10-21T00:00:00+00:00"
---

# File operations

Constraints for creating, modifying, and deleting files.
```

其他 Markdown 文件保存完整的 Rule 记录，包括 `atomic_id`、`scenario_id`、
revision、status、content、适用条件、例外和证据。`abstract` 是保留文件名。
存储系统生成的 `.abstract.md` 和 `.overview.md` 是另外两种内部摘要。

Scenario 检索仅搜索 `*/abstract.md`，因此 Rule 文件不会占用 Scenario 候选数量。
选中 Scenario 后，服务会读取其直属 Rule 文件。成员关系由目录决定，无需维护
`rule_uris` 列表。暂停和归档的 Rule 会保留在所属 Scenario 中，直到生命周期清理
移除超过保留期的归档 Rule。Rule 搜索使用 active Rule；编译同时考虑 active 和
paused Rule。

Rule 状态包括 `active`、`paused` 和 `archived`。`PAUSE` 暂停 active Rule，
但不安排清理；`ARCHIVE` 标记 Rule，使其在默认 30 天保留期后被清理，并记录
`archived_at`，重复归档不会重置该时间。`RESTORE` 重新激活 paused 或 archived
Rule，并清除 `archived_at`。更新 paused Rule 会将其重新激活。被替换的 Rule
以及合并后的旧片段会转为 archived。生命周期操作必须由用户明确要求。

Jev 判断 Scenario 是否无关以及 Rule 是否适用。当 Jev 未配置、不可用、调用失败
或返回无效结果时，受影响候选会直接通过，不再进行 VLM 判断。Rule 判断失败时，
所有候选 Rule 以 `score=1.0`、`judge=pass` 和 warning 放行；该分数是放行值，
不是估算概率。Agent 仍需在执行 Rule 前检查适用条件和例外。Rule 编译继续使用
Compiler VLM；VLM `rule_matcher` 配置已移除。
