# 会话管理

Session 负责管理对话消息、记录上下文使用、提取长期记忆。

## 概览

**生命周期**：创建 → 交互 → 提交

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
| `commit()` | 提交：归档 + 记忆提取 |

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
#   "status": "committed",
#   "memories_extracted": 5,
#   "active_count_updated": 2,
#   "archived": True
# }
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

commit() 时自动归档：

1. 递增 compression_index
2. 复制当前消息到归档目录
3. 生成结构化摘要（LLM）
4. 清空当前消息列表

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

### 6 种分类

| 分类 | 归属 | 说明 | 可合并 |
|------|------|------|--------|
| **profile** | user | 用户身份/属性 | ✅ |
| **preferences** | user | 用户偏好 | ✅ |
| **entities** | user | 实体（人/项目） | ✅ |
| **events** | user | 事件/决策 | ❌ |
| **cases** | agent | 问题+解决方案 | ❌ |
| **patterns** | agent | 可复用流程 | ✅ |

### 提取流程

```
消息 → LLM 提取 → 候选记忆
         ↓
向量预过滤 → 找相似记忆
         ↓
LLM 去重决策 → CREATE/UPDATE/MERGE/SKIP
         ↓
写入 AGFS → 向量化
```

### 去重决策

| 决策 | 说明 |
|------|------|
| `CREATE` | 新记忆，直接创建 |
| `UPDATE` | 更新现有记忆 |
| `MERGE` | 合并多条记忆 |
| `SKIP` | 完全重复，跳过 |

## 存储结构

```
viking://session/{session_id}/
├── messages.jsonl            # 当前消息
├── .abstract.md              # 当前摘要
├── .overview.md              # 当前概览
├── history/
│   ├── archive_001/
│   │   ├── messages.jsonl
│   │   ├── .abstract.md
│   │   └── .overview.md
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
└── patterns/
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [上下文提取](./06-extraction.md) - 提取流程
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
