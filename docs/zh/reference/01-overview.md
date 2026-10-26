# 选择参考文档

这里用于查接口约定、协议和配置字段。系统行为与设计约束见[概念与原理](../concepts/00-overview.md)。第一次使用请走[快速开始](../getting-started/02-quickstart.md)；还没确定调用哪些接口，请先选[应用开发任务](../workflows/01-overview.md)。

| 要查什么 | 入口 |
| --- | --- |
| SDK 连接、HTTP 认证、响应格式、错误码 | [SDK、HTTP 与 CLI 约定](../api/01-overview.md) |
| 服务端或客户端配置字段 | [服务端配置](../configuration/01-server.md)、[客户端配置](../configuration/02-client.md) |
| MCP 工具、参数与上传协议 | [MCP 工具与协议](../guides/06-mcp-integration.md)；安装客户端看[连接 MCP 客户端](../agent-integrations/06-mcp-clients.md) |
| 资源、文件和检索接口 | [资源](../api/02-resources.md)、[文件系统](../api/03-filesystem.md)、[内容](../api/12-content.md)、[检索](../api/06-retrieval.md) |
| 会话、记忆与技能接口 | [会话](../api/05-sessions.md)、[记忆](../api/16-memory.md)、[技能](../api/04-skills.md) |
| 管理、权限与后台状态 | [Admin](../api/08-admin.md)、[ACL](../api/12-acl.md)、[后台任务](../api/17-tasks.md)、[Observer](../api/18-observer.md) |
| 数据一致性、隔离和处理完成的含义 | [系统架构](../concepts/01-architecture.md)、[事务与恢复](../concepts/09-transaction.md)、[多租户](../concepts/11-multi-tenant.md)、[任务状态](../concepts/16-queue-lifecycle.md) |

侧边栏按接口所属对象排列；同一主题的操作指南保留在“开发应用”或“部署运维”，通过文内链接互相连接。配置步骤见[配置模型与服务](../guides/01-configuration.md)，集成之间的差异见[集成能力对照](../agent-integrations/16-capability-reference.md)。
