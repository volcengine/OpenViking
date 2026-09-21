# 路线图

本页区分当前 `main` 分支的实现与后续方向，不承诺发布时间或优先级。已发布版本请查阅 [release notes](https://github.com/volcengine/OpenViking/releases)。

## main 已实现

- **上下文与检索：** L0/L1/L2 分层、Viking URI、语义搜索和上下文感知检索。[概念说明](../concepts/03-context-layers.md)
- **资源：** 文档、代码、网页和媒体导入，可重复读取来源的定时更新。音视频文件可保存；内容理解需要启用兼容的 VLM。[资源管理](../api/02-resources.md)
- **更新与历史：** 根据 freshness 刷新父目录摘要，以及基于 Git 的快照提交、历史查询和恢复。父目录刷新可能延后；快照需显式提交，恢复的是文件内容，不含历史 ACL 或向量索引。[上下文分层](../concepts/03-context-layers.md) · [快照指南](../guides/15-snapshot.md)
- **会话与记忆：** 对话追踪、记忆提取和会话归档。[会话说明](../concepts/08-session.md)
- **接入与集成：** HTTP API、SDK、CLI、MCP 和 Agent 插件。[API 概览](../api/01-overview.md) · [MCP 指南](../guides/06-mcp-integration.md)
- **运维：** JSON 配置（`ov.conf`）、多模型供应商、租户隔离、加密、可观测性和本地/S3 存储。[配置指南](../guides/01-configuration.md) · [部署指南](../guides/03-deployment.md)

## 后续方向

- 继续完善分布式存储。
- 接入更多 Agent 框架。

提案与范围讨论见 [GitHub issues](https://github.com/volcengine/OpenViking/issues)，参与开发见[贡献指南](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING_CN.md)。
