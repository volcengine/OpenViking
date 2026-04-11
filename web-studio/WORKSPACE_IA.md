# Web Studio 入口与功能区规划

本文档描述当前 Web Studio 的一级入口、页面骨架分工，以及与服务端模式相关的展示约束。

## 当前骨架

当前前端骨架采用以下结构：

```text
header
sidebar | main
```

- header：仅保留当前一级入口标题、sidebar 开关、服务端模式标签。
- sidebar：承载一级入口导航，以及底部的“连接与身份”入口。
- main：各一级功能区的占位骨架，当前版本以布局和信息架构为主，具体功能逐步接入。

## 全局入口

### 连接与身份

“连接与身份”不是一级页面，而是全局 modal。

用途：

- 配置服务地址。
- 在显式鉴权模式下填写 Account、User、API Key。
- 在开发模式下保持轻量连接，必要时再展开高级字段。

当前实现约定：

- X-OpenViking-Agent 已经固化在 ov-client 适配层，值为 `web-studio`。
- 前端通过 `GET /health` 做最佳努力的服务端模式判断。
- 当请求返回 401/403 时，连接 modal 会自动弹出。

## 一级入口

### 资源

定位：资源浏览与检索工作区。

后续承载内容：

- 资源树与目录浏览。
- 内容预览、摘要、overview、下载。
- 关系查看。
- 导入导出与重建索引。
- 检索 modal。

设计约束：

- “浏览”和“检索”属于同一条操作流，不拆成两个一级入口。
- 检索以 modal 形式挂在资源工作区中，而不是独立页面。

### 会话

定位：会话、上下文与记忆沉淀工作区。

后续承载内容：

- Session 列表与切换。
- 消息与操作主区。
- 上下文装配与 archive 展示。
- commit、extract、session stats。
- 与记忆相关的沉淀结果。

设计约束：

- 会话页不是看板，也不是只读监控大屏。
- 会话页本体成立，不依赖 bot 才能存在。
- 记忆在当前版本不单列一级入口，收纳在会话页内部。

### 运维

定位：服务状态、后台任务与系统级调试面板。

后续承载内容：

- health / ready。
- observer 系列状态。
- tasks 列表与轮询。
- metrics、debug、质量指标。

设计约束：

- 运维入口只放系统运行态信息。
- 不与资源页、会话页的业务操作面混合。

## 服务端模式提醒

当前前端没有拿到服务端显式返回的 `auth_mode` 能力，因此采用启发式判断。

现状：

- 若 `GET /health` 返回 `user_id`，前端倾向于将其视为开发模式或隐式身份模式。
- 否则按显式鉴权模式处理。

这意味着：

- 文档、页面文案和导航显示都应保留“检查服务端模式”的意识。

## 服务端能力与接口映射

当前 Web Studio 对接的是 OpenViking HTTP Server。按服务端路由注册结果，后端主要暴露以下能力域：

- system
- resources
- filesystem
- content
- search
- relations
- sessions
- stats
- pack
- debug
- observer
- metrics
- tasks
- bot（可选开启）

前端现阶段主要依赖生成 client 中已经稳定暴露出来的接口集合。

### 公共与全局接口

这些接口不直接对应某个一级页面，但会影响全局连接、模式判断和系统状态展示。

- `GET /health`
	- 用途：健康检查。
	- 当前前端用途：最佳努力判断服务端模式；若返回 `user_id`，前端倾向视为开发模式或隐式身份模式。
- `GET /ready`
	- 用途：检查 AGFS、VectorDB、APIKeyManager 的 readiness。
	- 当前前端用途：适合后续放到运维页中展示基础依赖状态。
- `GET /api/v1/system/status`
	- 用途：返回系统初始化状态与当前请求解析出来的 user。
	- 当前前端用途：可作为连接成功后的系统上下文确认接口。
- `POST /api/v1/system/wait`
	- 用途：等待服务端处理队列完成。
	- 当前前端用途：暂未接入 UI，但适合作为运维或调试辅助能力。

### 资源入口对应的服务端能力

资源页后续会承接以下服务端接口域：

- `POST /api/v1/resources/temp-upload`
	- 临时上传资源。
- `POST /api/v1/resources`
	- 创建资源记录或导入资源。
- `GET /api/v1/fs/ls`
	- 列目录。
- `GET /api/v1/fs/tree`
	- 目录树。
- `GET /api/v1/fs/stat`
	- 文件或目录元信息。
- `GET /api/v1/content/read`
	- 读取内容。
- `GET /api/v1/content/abstract`
	- 内容摘要。
- `GET /api/v1/content/overview`
	- 内容总览。
- `GET /api/v1/content/download`
	- 下载内容。
- `POST /api/v1/content/write`
	- 写入内容。
- `POST /api/v1/content/reindex`
	- 重建内容索引。
- `POST /api/v1/search/find`
	- 语义/混合检索。
- `POST /api/v1/search/search`
	- 检索接口。
- `POST /api/v1/search/grep`
	- 文本 grep。
- `POST /api/v1/search/glob`
	- 文件匹配。
- `GET /api/v1/relations`
	- 查询关系。
- `POST /api/v1/relations/link`
	- 新建关系。
- `DELETE /api/v1/relations/link`
	- 删除关系。
- `POST /api/v1/pack/export`
	- 导出 pack。
- `POST /api/v1/pack/import`
	- 导入 pack。

对应关系说明：

- 资源树、目录浏览主要依赖 `fs.*`。
- 预览、摘要、下载、写入主要依赖 `content.*`。
- 检索 modal 主要依赖 `search.*`。
- 关系视图主要依赖 `relations.*`。
- 导入导出能力主要依赖 `resources.*` 与 `pack.*`。

### 会话入口对应的服务端能力

会话页后续会承接以下服务端接口域：

- `GET /api/v1/sessions`
	- 列出会话。
- `POST /api/v1/sessions`
	- 创建会话。
- `GET /api/v1/sessions/{session_id}`
	- 获取会话详情。
- `DELETE /api/v1/sessions/{session_id}`
	- 删除会话。
- `GET /api/v1/sessions/{session_id}/context`
	- 获取会话上下文装配结果。
- `GET /api/v1/sessions/{session_id}/archive/{archive_id}`
	- 获取历史 archive。
- `POST /api/v1/sessions/{session_id}/messages`
	- 写入消息。
- `POST /api/v1/sessions/{session_id}/used`
	- 记录已使用上下文。
- `POST /api/v1/sessions/{session_id}/commit`
	- 触发异步 commit。
- `POST /api/v1/sessions/{session_id}/extract`
	- 触发抽取或记忆提炼。
- `GET /api/v1/stats/session/{session_id}`
	- 会话统计。
- `GET /api/v1/stats/memories`
	- 记忆统计汇总。

对应关系说明：

- Session 列表与切换依赖 `sessions list/get/create/delete`。
- 上下文面板依赖 `context` 与 `archive`。
- 写消息与记录引用依赖 `messages` 和 `used`。
- 记忆沉淀结果依赖 `commit`、`extract`、`stats`。

### 运维入口对应的服务端能力

运维页后续会承接以下服务端接口域：

- `GET /health`
- `GET /ready`
- `GET /api/v1/observer/queue`
- `GET /api/v1/observer/vikingdb`
- `GET /api/v1/observer/models`
- `GET /api/v1/observer/lock`
- `GET /api/v1/observer/retrieval`
- `GET /api/v1/observer/system`
- `GET /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `GET /metrics`

对应关系说明：

- 服务 readiness、系统总览、依赖健康放在运维总览。
- `tasks` 负责后台任务列表和单任务追踪。
- `observer.*` 负责模型、向量库、锁、检索质量等运行态观察。
- `metrics` 适合后续扩展为 Prometheus 或系统指标视图。

### 可选与暂未前置到一级入口的能力

- `bot` 路由是可选开启能力，不是当前一级入口成立的前提。
- `debug` 路由目前更适合作为运维页内部的调试分区，而不是独立一级入口。
- `pack` 路由虽然在概念上可独立，但当前更适合作为资源工作区中的导入导出能力。

### 文档与实现的关系

本节的目标是补充“前端规划背后对应的服务端能力”，而不是把前端一级入口机械映射为后端 router。

因此应保持以下原则：

- 一级入口由用户工作流决定，不由 router 数量决定。
- 一个一级入口可以汇聚多个服务端能力域。
- 一个服务端能力域也可以只作为某个页面的局部能力存在。

## 当前代码映射

- 一级入口壳层：`src/components/app-shell.tsx`
- 连接 modal：`src/components/connection-dialog.tsx`
- 连接状态与 provider：`src/hooks/use-app-connection.tsx`
- 服务端模式探测：`src/hooks/use-server-mode.ts`
- 资源页占位：`src/routes/resources/route.tsx`
- 会话页占位：`src/routes/sessions/route.tsx`
- 运维页占位：`src/routes/operations/route.tsx`

## 后续建议

1. 把导航配置从 `app-shell` 中继续拆成独立常量模块。
2. 在服务端补一个稳定的模式或 capability 探测接口，替代当前 `/health` 启发式判断。
3. 在具体页面实现时，继续保持“资源 / 会话 / 运维”三个一级入口的边界，不让功能再次回流混杂。