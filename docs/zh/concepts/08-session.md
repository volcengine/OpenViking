# 会话管理

Session 负责管理对话消息、记录上下文使用、提取长期记忆。

## 概览

**生命周期**：创建 → 交互 → 提交

通过 session_id 获取会话时，默认不会自动创建不存在的会话；如果需要自动创建，请显式使用 `client.get_session(..., auto_create=True)`。

```python
session = client.session(session_id="chat_001")
session.add_message("user", [TextPart("...")])
session.commit()
```

## 核心 API

| 方法 | 说明 |
|------|------|
| `add_message(role, parts)` | 添加消息 |
| `used(contexts, skill)` | 记录使用的上下文/技能 |
| `commit()` | 提交：归档（同步） + 摘要生成和记忆提取（异步后台） |
| `get_task(task_id)` | 查询后台任务状态 |

### add_message

```python
session.add_message(
    "user",
    [TextPart("How to configure embedding?")]
)

session.add_message(
    "assistant",
    [
        TextPart("Here's how..."),
        ContextPart(uri="viking://user/memories/profile.md"),
    ]
)
```

### used

```python
# 记录使用的上下文
session.used(contexts=["viking://user/memories/profile.md"])

# 记录使用的技能
session.used(skill={
    "uri": "viking://agent/skills/code-search",
    "input": "search config",
    "output": "found 3 files",
    "success": True
})
```

### commit

```python
result = session.commit()
# {
#   "status": "accepted",
#   "task_id": "uuid-xxx",
#   "archive_uri": "viking://session/.../history/archive_001",
#   "archived": True
# }

# 查询后台任务进度
task = client.get_task(result["task_id"])
# task["status"]: "pending" | "running" | "completed" | "failed"
# sum(task["result"]["memories_extracted"].values()): 3
```

## 消息结构

### Message

```python
@dataclass
class Message:
    id: str              # msg_{UUID}
    role: str            # "user" | "assistant"
    parts: List[Part]    # 消息部分
    created_at: datetime
```

### Part 类型

| 类型 | 说明 |
|------|------|
| `TextPart` | 文本内容 |
| `ContextPart` | 上下文引用（URI + 摘要） |
| `ToolPart` | 工具调用（输入 + 输出） |

## 压缩策略

### 归档流程

commit() 分两阶段执行：

**Phase 1（同步，立即完成）**：
1. 递增 compression_index
2. 写入消息到归档目录（`messages.jsonl`）
3. 清空当前消息列表
4. 返回 `task_id`

**Phase 2（异步后台）**：
5. 生成结构化摘要（LLM）→ 写入 `.abstract.md` 和 `.overview.md`
6. 提取长期记忆
7. 写入 `memory_diff.json`（记忆变更审计日志）到归档目录
8. 更新 active_count
9. 写入 `.done` 完成标记

### 摘要格式

```markdown
# 会话摘要

**一句话概述**: [主题]: [意图] | [结果] | [状态]

## Analysis
关键步骤列表

## Primary Request and Intent
用户的核心目标

## Key Concepts
关键技术概念

## Pending Tasks
未完成的任务
```

## 记忆提取

### 8 种分类

| 分类 | 归属 | 说明 | 可合并 |
|------|------|------|--------|
| **profile** | user | 用户身份/属性 | ✅ |
| **preferences** | user | 用户偏好 | ✅ |
| **entities** | user | 实体（人/项目） | ✅ |
| **events** | user | 事件/决策 | ❌ |
| **cases** | agent | 问题+解决方案 | ❌ |
| **patterns** | agent | 可复用流程 | ✅ |
| **tools** | agent | 工具使用经验与最佳实践 | ✅ |
| **skills** | agent | 技能执行经验与工作流策略 | ✅ |

### 提取流程

```
消息 → LLM 提取 → 候选记忆
         ↓
向量预过滤 → 找相似记忆
         ↓
LLM 去重决策 → candidate(skip/create/none) + item(merge/delete)
         ↓
写入 AGFS → 向量化
```

### 去重决策

| 层级 | 决策 | 说明 |
|------|------|------|
| Candidate | `skip` | 候选记忆重复，直接跳过 |
| Candidate | `create` | 创建候选记忆；必要时先删除冲突旧记忆 |
| Candidate | `none` | 不创建候选记忆，只处理已有记忆 |
| Existing item | `merge` | 将候选内容合并到指定已有记忆 |
| Existing item | `delete` | 删除冲突的已有记忆 |

## 记忆变更记录

每次 `session.commit()` 会在归档目录写入 `memory_diff.json`，记录本次提交的所有记忆变更，便于审计和回溯。

```json
{
  "archive_uri": "viking://session/{session_id}/history/archive_001",
  "extracted_at": "2026-04-21T10:00:00Z",
  "operations": {
    "adds": [
      {
        "uri": "memory/user/xxx/identity.md",
        "memory_type": "identity",
        "after": "新创建的文件内容"
      }
    ],
    "updates": [
      {
        "uri": "memory/user/xxx/context/project.md",
        "memory_type": "context",
        "before": "修改前的文件内容",
        "after": "修改后的文件内容"
      }
    ],
    "deletes": [
      {
        "uri": "memory/user/xxx/context/old.md",
        "memory_type": "context",
        "deleted_content": "被删除的文件内容"
      }
    ]
  },
  "summary": {
    "total_adds": 1,
    "total_updates": 1,
    "total_deletes": 1
  }
}
```

| 字段 | 说明 |
|------|------|
| `archive_uri` | 本次提交的归档目录 URI |
| `extracted_at` | 提取时间的 ISO 8601 格式 |
| `operations.adds` | 新增的记忆（无 `before`） |
| `operations.updates` | 修改的记忆（含 `before` 和 `after`） |
| `operations.deletes` | 删除的记忆（含 `deleted_content`） |
| `summary` | 各操作类型的计数 |

即使没有记忆操作，也会写入空结构的 `memory_diff.json`（所有计数为零）。

## 存储结构

```
viking://session/{session_id}/
├── messages.jsonl            # 当前消息
├── .abstract.md              # 当前摘要
├── .overview.md              # 当前概览
├── history/
│   ├── archive_001/
│   │   ├── messages.jsonl    # Phase 1 写入
│   │   ├── .abstract.md      # Phase 2 写入（后台）
│   │   ├── .overview.md      # Phase 2 写入（后台）
│   │   ├── memory_diff.json  # Phase 2 写入（后台，记忆变更审计）
│   │   └── .done             # Phase 2 完成标记
│   └── archive_NNN/
└── tools/
    └── {tool_id}/tool.json

viking://user/memories/
├── profile.md                # 追加式用户画像
├── preferences/
├── entities/
└── events/

viking://agent/memories/
├── cases/
├── patterns/
├── tools/
└── skills/
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [上下文提取](./06-extraction.md) - 提取流程
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
