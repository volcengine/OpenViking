# 简介

OpenViking 是面向 AI Agent 的开源上下文数据库。它用虚拟文件系统组织资源、记忆和技能，让应用按路径浏览、检索相关上下文，并按需读取详细内容。

当 Agent 需要跨会话复用文档和经验时，可以用它集中组织和检索这些上下文。

## 从你的任务开始

| 我想要…… | 阅读入口 |
| --- | --- |
| 连接服务并检索第一份文档 | [快速开始](./02-quickstart.md) |
| 接入已有 Agent 或编程工具 | [Agent 集成](../agent-integrations/01-overview.md) |
| 在终端使用 OpenViking | [CLI 配置](./05-cli-setup.md) |
| 部署和运维共享服务 | [部署](../guides/03-deployment.md)与[认证](../guides/04-authentication.md) |
| 使用 SDK 或 HTTP API 开发 | [API 参考](../api/01-overview.md) |

## 上下文如何组织

每个文件或目录都有一个 `viking://` URI。已知路径时可直接列目录、读内容；不知道内容在哪里时可先检索。

| 上下文 | 存放内容 | 详细说明 |
| --- | --- | --- |
| 资源 | 文档、代码仓库等参考资料 | [资源](../api/02-resources.md) |
| 记忆 | 从会话提取的用户偏好、实体、事件和经验 | [记忆](../api/16-memory.md) |
| 技能 | 可复用 Agent 工作流的指令和配套文件 | [技能](../api/04-skills.md) |

共享资源位于 `viking://resources/`；用户上下文位于 `viking://user/{user_id}/`，其中 `peers/{peer_id}/` 存放特定 Peer 的上下文。共享技能可放在 `viking://agent/skills/`。作用域和路径规则见 [Viking URI](../concepts/04-viking-uri.md)。

## 按层读取内容

OpenViking 可在语义处理时生成目录摘要：

| 层级 | 内容 | 默认正文上限 |
| --- | --- | --- |
| L0 | 用于快速筛选的摘要 | 256 字符 |
| L1 | 用于导航的概览 | 4,000 字符 |
| L2 | 按需读取的原始内容 | 无统一上限 |

L0 和 L1 是目录级附属文件，不会为每个文件固定生成一对摘要；是否可用取决于处理状态和配置。详见[上下文层级](../concepts/03-context-layers.md)。

[检索](../concepts/07-retrieval.md)结合语义匹配与目录遍历。不需要会话上下文时用 `find`，需要结合会话理解查询时用 `search`。[可观测性](../guides/05-observability.md)介绍如何检查处理和检索行为。

## 从会话生成记忆

应用把消息写入会话，提交后触发异步记忆提取。当前记忆策略决定为用户或 Peer 创建、更新哪些记忆。集成插件可自动执行其中部分步骤，使用前需确认对应集成的支持范围。详见[会话](../concepts/08-session.md)和[记忆配置](../guides/01-configuration.md)。

实现原理见[架构](../concepts/01-architecture.md)，各版本变更见 [GitHub Releases](https://github.com/volcengine/OpenViking/releases)。
