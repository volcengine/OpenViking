# 应用开发路径

先完成[第一次导入与检索](../getting-started/02-quickstart.md)，再选择下面的任务。已有 Agent 的用户从[接入 Agent](../agent-integrations/01-overview.md)开始；自己编写应用时，用 [SDK、HTTP 与 CLI 约定](../api/01-overview.md)选择调用方式。

| 要完成的任务 | 阅读顺序 | 用什么确认结果 |
| --- | --- | --- |
| 让应用检索文档或代码 | [导入资源](../api/02-resources.md) → [检索](../api/06-retrieval.md) → [读取内容](../api/12-content.md) | 用已知答案的问题查询，读取命中 URI，核对原文 |
| 跨会话复用记忆 | [会话与记忆模型](../concepts/08-session.md) → [会话 API](../api/05-sessions.md) → [记忆 API](../api/16-memory.md) | 提交会话并确认后台任务完成，再检查记忆内容和归属 |
| 让 Agent 复用技能 | [技能 API](../api/04-skills.md) → [隐私模型](../concepts/13-privacy.md) | 检查安装后的技能内容、可见范围和调用方式 |
| 从已有资料产出 Wiki、图谱或日报 | [上下文编译](../context-compilation/01-overview.md) → 选择产物教程 | 检查任务状态、产物目录，以及产物与输入来源的对应关系 |
| 同步或迁移已有上下文 | [项目资源同步](../guides/18-openviking-assets.md)、[OVPack](../guides/09-ovpack.md)或[版本恢复](../guides/15-snapshot.md) | 按对应指南核对同步状态、导入结果或恢复后的内容 |

## 先验证检索，再接入应用

使用一份内容已知的小文档，走完导入、等待处理、检索、读取原文的流程。`health` 成功只说明服务可以响应；空结果也不能单独证明导入失败。查看[后台任务](../api/17-tasks.md)与[观测入口](../guides/05-observability.md)，区分处理状态和检索结果。

需要持续更新远程资源时，再接入 [Resource Watch](../api/15-watches.md)。需要调整内容处理方式时，查看 [Prompt 自定义](../guides/10-prompt-guide.md)。

## 再验证跨会话记忆

先确定[用户、Peer 与共享上下文的边界](../concepts/11-multi-tenant.md)，再写入消息并提交会话。记忆提取受配置和策略控制，提交成功不等于一定产生某条记忆。用[会话 API](../api/05-sessions.md)与[记忆 API](../api/16-memory.md)检查实际产物；接入已有工具时，先查[集成能力对照](../agent-integrations/16-capability-reference.md)，确认哪些步骤由插件自动执行。

## 上线前补齐访问与运维

共享服务需要配置[身份认证](../guides/04-authentication.md)和 [ACL](../concepts/15-acl.md)。部署路径、观测与升级入口集中在[部署运维](../guides/00-overview.md)。查参数、响应和错误时，进入[参考文档](../reference/01-overview.md)。
