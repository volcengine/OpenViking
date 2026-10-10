# 概念与原理

这里按学习顺序组织 OpenViking 的上下文模型、处理机制、架构与治理。尚未运行过 OpenViking，可以先[导入并检索第一份文档](../getting-started/02-quickstart.md)，再回到这里理解结果如何产生。

## 先理解上下文模型

按顺序阅读前三篇，回答“存什么、在哪里、读多少”：

1. [上下文类型](02-context-types.md)：资源、记忆与技能各自承担什么职责。
2. [Viking URI](04-viking-uri.md)：如何定位上下文，如何区分用户、Peer 与共享空间。
3. [上下文层级（L0/L1/L2）](03-context-layers.md)：摘要、概览与原文的关系，以及按层读取的边界。

## 再看处理与记忆

沿着资料导入和会话积累两条路径阅读：

- [上下文提取](06-extraction.md)：原始资料如何经过解析、组织与语义处理。
- [检索机制](07-retrieval.md)：如何查找相关上下文，`find` 与 `search` 有什么区别。
- [会话管理](08-session.md)：消息、会话提交与记忆提取如何衔接。

准备写应用时，回到[应用开发路径](../workflows/01-overview.md)选择操作流程；参数和响应查[参考文档](../reference/01-overview.md)。

## 按问题深入架构与治理

| 想理解的问题 | 阅读路径 |
| --- | --- |
| 模块如何协作，数据存在哪里？ | [架构概述](01-architecture.md) → [存储架构](05-storage.md) |
| 处理何时结束，失败后如何恢复？ | [任务状态](16-queue-lifecycle.md) → [路径锁与崩溃恢复](09-transaction.md) |
| 主备写入、读取和观测有什么语义？ | [主备存储](14-multi-write-storage.md) → [指标与 Metrics](12-metrics.md) |
| 谁能看到什么，权限如何继承？ | [多租户](11-multi-tenant.md) → [资源访问控制（ACL）](15-acl.md) |
| 数据如何加密，Skill 隐私如何处理？ | [数据加密](10-encryption.md) → [隐私配置与 Skill 隐私提取/加载](13-privacy.md) |

配置和部署步骤集中在[部署运维](../guides/00-overview.md)。原理页说明行为与约束，操作指南说明如何设置和验证，两者通过链接连接。

## 看一个应用示例

[VikingBot](15-vikingbot.md)展示 Agent、上下文与多渠道交互如何组合。需要运行它时，继续阅读[VikingBot 安装与配置](../guides/17-vikingbot.md)；接入其他现成工具时，先[选择 Agent 接入方式](../agent-integrations/01-overview.md)。
