# 上下文类型

基于对人类认知模式的简化映射与工程化思考，OpenViking 将上下文抽象为 **资源、记忆、能力三种**基本类型，每种类型在 Agent 中有不同的用途。

## 概览

| 类型 | 用途 | 生命周期 | 主动性 |
|------|------|----------|--------|
| **Resource** | 知识和规则 | 长期，相对静态 | 用户添加 |
| **Memory** | Agent 的认知 | 长期，动态更新 | Agent 记录 |
| **Skill** | 可调用的能力 | 长期，静态 | Agent 调用 |

## Resource（资源）

资源是 Agent 可以引用的外部知识。

### 特点

- **用户主动**：由用户主动添加的资源类信息，用于补充大模型的知识，比如产品手册、代码仓库
- **静态内容**：添加后内容很少发生变化，通常为用户主动修改
- **结构化存储**：将按照项目或主题以目录层级组织，并提取出多层信息。

### 示例

- API 文档、产品手册
- FAQ 数据库、代码仓库
- 研究论文、技术规范

### 使用

```python
# 添加资源
client.add_resource(
    "https://docs.example.com/api.pdf",
    reason="API 文档"
)

# 搜索资源
results = client.find(
    "认证方法",
    target_uri="viking://resources/"
)
```

## Memory（记忆）

记忆分为用户记忆和Agent记忆，是 Agent 关于用户和世界的学习知识。

### 特点

- **Agent 主动：**由 Agent 主动提取和记录的记忆信息
- **动态更新：**由 Agent 从交互中持续更新
- **个性化：**针对特定用户或 特定 Agent 学习记录

### 6 种分类

| 分类 | 位置 | 说明 | 更新策略 |
|------|------|------|----------|
| **profile** | `user/memories/.overview.md` | 用户基本信息 | ✅ 可追加 |
| **preferences** | `user/memories/preferences/` | 按主题的用户偏好 | ✅ 可追加 |
| **entities** | `user/memories/entities/` | 实体记忆（人物、项目） | ✅ 可追加 |
| **events** | `user/memories/events/` | 事件记录（决策、里程碑） | ❌ 不更新 |
| **cases** | `agent/memories/cases/` | 学习的案例 | ❌ 不更新 |
| **patterns** | `agent/memories/patterns/` | 学习的模式 | ❌ 不更新 |

### 使用

```python
# 记忆从会话中自动提取
session = client.session()
await session.add_message("user", [{"type": "text", "text": "我喜欢深色模式"}])
await session.commit()  # 提取偏好记忆

# 搜索记忆
results = await client.find(
    "用户界面偏好",
    target_uri="viking://user/memories/"
)
```

## Skill（技能）

技能是 Agent 可以调用的能力，比如目前的Skills、MCP等均属于此类。

### 特点

- **定义的能力：**用于完成某项工作的工具定义
- **相对静态：**运行时技能定义不变，但和工具相关的使用记忆会在记忆中更新
- **可调用：**Agent 决定何时使用哪种技能

### 存储位置

```
viking://agent/skills/{skill-name}/
├── .abstract.md          # L0: 简短描述
├── SKILL.md   						# L1: 详细概览
└── scripts           		# L2: 完整定义

```

### 使用

```python
# 添加技能
await client.add_skill({
    "name": "search-web",
    "description": "搜索网络获取信息",
    "content": "# search-web\n..."
})

# 搜索技能
results = await client.find(
    "网络搜索",
    target_uri="viking://agent/skills/"
)
```

## 统一检索

根据Agent的需求需求，支持对三种上下文类型统一搜索，提供全面信息：

```python
# 跨所有上下文类型搜索
results = await client.find("用户认证")

for ctx in results.memories:
    print(f"记忆: {ctx.uri}")
for ctx in results.resources:
    print(f"资源: {ctx.uri}")
for ctx in results.skills:
    print(f"技能: {ctx.uri}")
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [会话管理](./08-session.md) - 记忆提取机制
