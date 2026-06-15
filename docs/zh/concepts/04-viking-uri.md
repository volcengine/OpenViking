# Viking URI

Viking URI 是 OpenViking 中所有内容的统一资源标识符。

## 格式

```
viking://{scope}/{path}
```

- **scheme**: 始终为 `viking`
- **scope**: 顶级命名空间（`resources`、`user`；`temp`、`queue` 和 `upload` 为内部作用域）
- **path**: 作用域内的资源路径

## 作用域

| 作用域 | 说明 | 生命周期 | 可见性 |
|--------|------|----------|--------|
| **resources** | 独立资源 | 长期 | 全局 |
| **user** | 用户级数据，包括 session | 长期 / 会话生命周期 | 当前用户 |
| **queue** | 处理队列 | 临时 | 内部 |
| **temp** | 临时文件 | 解析期间 | 内部 |
| **upload** | 临时上传文件 | 临时 | 内部 |

公开 API 和 CLI 的文件系统/内容操作接受公开作用域 `resources` 和 `user`，
以及根 URI `viking://`。`session` 保留为 user session 路径的向后兼容别名；
新 session 数据位于 `viking://user/{user_id}/sessions`。`agent` 已废弃，但仍作为
legacy agent 数据的只读兼容入口。
`temp`、`queue` 和 `upload` 是内部实现作用域，不能通过公开 API 的 URI 参数直接访问。

## 初始目录

摒弃传统的扁平化数据库思维，将所有上下文组织为一套文件系统。Agent 不再仅是通过向量搜索来找数据，而是可以通过确定性的路径和标准文件系统指令来定位和浏览数据。每个上下文或目录分配唯一的 URI 标识字符串，格式为 viking://{scope}/{path}，让系统能精准定位并访问存储在不同位置的资源。

```
viking://
├── user/
│   └── {user_id}/
│       ├── profile.md        # 用户画像
│       ├── memories/         # 用户记忆
│       ├── resources/        # 用户私有资源
│       ├── skills/           # 用户技能
│       ├── peers/
│       │   └── {peer_id}/
│       │       ├── memories/  # 关于某个交互对象的记忆
│       │       └── resources/ # 归属于该 peer 的资源
│       └── sessions/         # 用户会话
│           └── {session_id}/
│               ├── .abstract.md
│               ├── .overview.md
│               ├── .meta.json
│               ├── messages.jsonl
│               ├── tools/
│               └── history/
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
viking://user/resources/                      # 当前用户资源
viking://user/resources/docs/                 # 当前用户资源目录
```

### 用户技能和 peer 内容

```
viking://user/skills/                         # 当前用户的技能
viking://user/skills/search-web               # 某个技能
viking://user/memories/                       # 当前用户的记忆
viking://user/memories/cases/                 # 学习的案例
viking://user/memories/patterns/              # 学习的模式
viking://user/{user_id}/peers/{peer_id}/memories/
viking://user/{user_id}/peers/{peer_id}/resources/
```

上面的 `viking://user/...` 短路径会按当前请求身份解析。
OpenViking 会在存储和检索前将它展开为显式命名空间路径，例如
`viking://user/{user_id}/...`。
`{user_id}` 和 `{peer_id}` 等身份路径片段必须是安全的单段标识，例如
`alice` 或 `web-visitor-alice`。

### 会话数据

```
viking://user/{user_id}/sessions/{session_id}/          # 会话根目录
viking://user/{user_id}/sessions/{session_id}/messages  # 会话消息
viking://user/{user_id}/sessions/{session_id}/tools     # 工具执行
viking://user/{user_id}/sessions/{session_id}/history   # 归档历史
viking://user/sessions/{session_id}/                    # 当前用户短路径
```

`viking://session/{session_id}` 会作为当前用户 session 路径的向后兼容别名被接受。
它不是新会话数据的独立存储根。

## 路径变量

Viking URI 支持路径变量用于动态路径生成。这对于按时间序列组织数据（如邮件、日志、日报等）特别有用。

### 变量语法

```
{namespace:key}
```

- **namespace**: 变量提供者命名空间（如 `calendar`、`env`、`user`）
- **key**: 命名空间内的变量名

### 日历变量

`calendar` 命名空间提供日期相关变量：

| 变量 | 说明 | 示例（2026-05-07） |
|------|------|----------------------|
| `{calendar:today}` | 完整日期路径 | `2026/05/07` |
| `{calendar:yesterday}` | 昨天的日期路径 | `2026/05/06` |
| `{calendar:tomorrow}` | 明天的日期路径 | `2026/05/08` |
| `{calendar:year}` | 年份 | `2026` |
| `{calendar:month}` | 月份（带前导零） | `05` |
| `{calendar:day}` | 日期（带前导零） | `07` |
| `{calendar:ym}` | 年/月 | `2026/05` |
| `{calendar:quarter}` | 季度（Q1-Q4） | `Q2` |
| `{calendar:yq}` | 年/季度 | `2026/Q2` |
| `{calendar:week}` | ISO 周数（带前导零） | `18` |
| `{calendar:yw}` | 年/ISO 周 | `2026/w18` |

### 使用示例

```python
# 按日期组织邮件
viking://resources/emails/{calendar:today}/inbox
# 渲染为：viking://resources/emails/2026/05/07/inbox

# 查看昨天的日志
viking://resources/logs/{calendar:yesterday}/app.log
# 渲染为：viking://resources/logs/2026/05/06/app.log

# 预上传明天的任务
viking://resources/tasks/{calendar:tomorrow}/todo.md
# 渲染为：viking://resources/tasks/2026/05/08/todo.md

# 月度日志
viking://resources/logs/{calendar:year}/{calendar:month}/app.log
# 渲染为：viking://resources/logs/2026/05/app.log

# 每日快照
viking://resources/snapshots/{calendar:today}/
# 渲染为：viking://resources/snapshots/2026/05/07/
```

### 解析过程

路径变量在 API 执行时**服务器端**进行解析。CLI/SDK 原样传递 URI 模板，服务器根据当前上下文（时间、认证用户等）渲染为具体路径。

### CLI 使用

```bash
# 添加今天的邮件 --parent-auto-create 可以简写为 -p
ov add-resource --parent-auto-create "viking://resources/emails/{calendar:today}/inbox" ./emails/*.eml

# 读取昨天的日志
ov read "viking://resources/logs/{calendar:yesterday}/app.log"

# 准备明天的任务
ov write --uri "viking://resources/tasks/{calendar:tomorrow}/todo.md" --content "规划一天"

# 上传月度报告 --parent-auto-create 可以简写为 -p
ov add-resource --parent-auto-create "viking://resources/reports/{calendar:ym}" ./report.pdf
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
├── user/{user_id}/
│   ├── profile.md                # 用户基本信息
│   ├── memories/
│   │   ├── preferences/          # 按主题
│   │   ├── entities/             # 每条独立
│   │   └── events/               # 每条独立
│   ├── resources/
│   │   └── {project}/
│   ├── skills/
│   └── peers/{peer_id}/
│       ├── memories/
│       └── resources/
│
└── user/{user_id}/sessions/{session_id}/
    ├── messages.jsonl
    ├── tools/
    └── history/
```

`viking://agent/...` 已废弃，仅保留 legacy agent 数据的只读兼容；新数据应写入
`viking://user/{user_id}/peers/{peer_id}/...`。

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

# 仅在当前用户资源中搜索
results = client.find(
    "私有项目笔记",
    target_uri="viking://user/resources/"
)

# 仅在用户记忆中搜索
results = client.find(
    "编码偏好",
    target_uri="viking://user/memories/"
)

# 仅在技能中搜索
results = client.find(
    "网络搜索",
    target_uri="viking://user/skills/"
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
# 添加到 account 共享资源作用域
await client.add_resource(url, to="viking://resources/project/")

# 添加到当前用户私有资源根
await client.add_resource(path, parent="viking://user/resources/project/")

# 技能始终添加到当前用户技能根
await client.add_skill(skill)  # canonical root: viking://user/{user_id}/skills/
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [存储架构](./05-storage.md) - VikingFS 和 AGFS
- [会话管理](./08-session.md) - 会话存储结构
