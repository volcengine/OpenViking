# Viking URI

Viking URI 是 OpenViking 中所有内容的统一资源标识符。

## 格式

```
viking://{scope}/{path}
```

- **scheme**: 始终为 `viking`
- **scope**: 顶级命名空间（resources、user、agent、session、queue）
- **path**: 作用域内的资源路径

## 作用域

| 作用域 | 说明 | 生命周期 | 可见性 |
|--------|------|----------|--------|
| **resources** | 独立资源 | 长期 | 全局 |
| **user** | 用户级数据 | 长期 | 全局 |
| **agent** | Agent 级数据 | 长期 | 全局 |
| **session** | 会话级数据 | 会话生命周期 | 当前会话 |
| **queue** | 处理队列 | 临时 | 内部 |
| **temp** | 临时文件 | 解析期间 | 内部 |

## 初始目录

摒弃传统的扁平化数据库思维，将所有上下文组织为一套文件系统。Agent 不再仅是通过向量搜索来找数据，而是可以通过确定性的路径和标准文件系统指令来定位和浏览数据。每个上下文或目录分配唯一的 URI 标识字符串，格式为 viking://{scope}/{path}，让系统能精准定位并访问存储在不同位置的资源。

```
viking://
├── session/{session_id}/
│   ├── .abstract.md          # L0: 会话一句话摘要
│   ├── .overview.md          # L1: 会话概览
│   ├── .meta.json            # 会话元数据
│   ├── messages.json         # 结构化消息存储
│   ├── checkpoints/          # 版本快照
│   ├── summaries/            # 压缩摘要历史
│   └── .relations.json       # 关联表
│
├── user/
│   ├── .abstract.md          # L0: 内容摘要
│   ├── .overview.md          # 用户画像
│   └── memories/             # 用户记忆存储
│       ├── .overview.md      # 记忆概览
│       ├── preferences/      # 用户偏好
│       ├── entities/         # 实体记忆
│       └── events/           # 事件记录
│
├── agent/
│   ├── .abstract.md          # L0: 内容摘要
│   ├── .overview.md          # Agent概览
│   ├── memories/             # Agent学习记忆
│   │   ├── .overview.md
│   │   ├── cases/            # 案例
│   │   └── patterns/         # 模式
│   ├── instructions/         # Agent指令
│   └── skills/               # 技能目录
│
└── resources/{project}/      # 资源工作区
```

## URI 示例

### 资源

```
viking://resources/                           # 所有资源
viking://resources/my-project/                # 项目根目录
viking://resources/my-project/docs/           # 文档目录
viking://resources/my-project/docs/api.md     # 具体文件
```

### 用户数据

```
viking://user/                                # 用户根目录
viking://user/memories/                       # 所有用户记忆
viking://user/memories/preferences/           # 用户偏好
viking://user/memories/preferences/coding     # 具体偏好
viking://user/memories/entities/              # 实体记忆
viking://user/memories/events/                # 事件记忆
```

### Agent 数据

```
viking://agent/                               # Agent 根目录
viking://agent/skills/                        # 所有技能
viking://agent/skills/search-web              # 具体技能
viking://agent/memories/                      # Agent 记忆
viking://agent/memories/cases/                # 学习的案例
viking://agent/memories/patterns/             # 学习的模式
viking://agent/instructions/                  # Agent 指令
```

### 会话数据

```
viking://session/{session_id}/                # 会话根目录
viking://session/{session_id}/messages/       # 会话消息
viking://session/{session_id}/tools/          # 工具执行
viking://session/{session_id}/history/        # 归档历史
```

## 目录结构

```
viking://
├── resources/                    # 独立资源
│   └── {project}/
│       ├── .abstract.md          # 摘要
│       ├── .overview.md          # 概述
│       └── {files...}
│
├── user/
│   ├── profile.md              	# 用户基本信息
│   └── memories/
│       ├── preferences/          # 按主题
│       ├── entities/             # 每条独立
│       └── events/               # 每条独立
│
├── agent/
│   ├── skills/                   # 技能定义
│   ├── memories/
│   │   ├── cases/
│   │   └── patterns/
│   └── instructions/
│
└── session/{session_id}/
    ├── messages/
    ├── tools/
    └── history/
```

## URI 操作

### 解析

```python
from openviking_cli.utils.uri import VikingURI

uri = VikingURI("viking://resources/docs/api")
print(uri.scope)      # "resources"
print(uri.full_path)  # "resources/docs/api"
```

### 构建

```python
# 拼接路径
base = "viking://resources/docs/"
full = VikingURI(base).join("api.md").uri  # viking://resources/docs/api.md

# 父目录
uri = "viking://resources/docs/api.md"
parent = VikingURI(uri).parent.uri  # viking://resources/docs
```

## API 使用

### 指定作用域搜索

```python
# 仅在资源中搜索
results = client.find(
    "认证",
    target_uri="viking://resources/"
)

# 仅在用户记忆中搜索
results = client.find(
    "编码偏好",
    target_uri="viking://user/memories/"
)

# 仅在技能中搜索
results = client.find(
    "网络搜索",
    target_uri="viking://agent/skills/"
)
```

### 文件系统操作

```python
# 列出目录
entries = await client.ls("viking://resources/")

# 读取文件
content = await client.read("viking://resources/docs/api.md")

# 获取摘要
abstract = await client.abstract("viking://resources/docs/")

# 获取概览
overview = await client.overview("viking://resources/docs/")
```

## 特殊文件

每个目录可能包含特殊文件：

| 文件 | 用途 |
|------|------|
| `.abstract.md` | L0 摘要（~100 tokens） |
| `.overview.md` | L1 概览（~2k tokens） |
| `.relations.json` | 相关资源 |
| `.meta.json` | 元数据 |

## 最佳实践

### 目录使用尾部斜杠

```python
# 目录
"viking://resources/docs/"

# 文件
"viking://resources/docs/api.md"
```

### 作用域特定操作

```python
# 资源只添加到 resources 作用域
await client.add_resource(url, target="viking://resources/project/")

# 技能添加到 agent 作用域
await client.add_skill(skill)  # 自动到 viking://agent/skills/
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [存储架构](./05-storage.md) - VikingFS 和 AGFS
- [会话管理](./08-session.md) - 会话存储结构
