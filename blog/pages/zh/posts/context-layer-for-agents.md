---
title: 为 Agent 构建上下文层
date: 2026-05-08
updated: 2026-05-08
categories:
  - 工程
tags:
  - 上下文
  - 记忆
  - 检索
cover: /covers/context-provenance.png
excerpt: OpenViking 将上下文视为 Agent 的一等基础层，让记忆、资源和技能可以通过统一模型管理。
description: 一篇关于 Agent 为什么需要专门上下文层的示例工程文章。
---

![Context retrieval provenance view](/covers/context-provenance.png)

多数 Agent 产品最终都会需要比 prompt 和向量检索调用更多的东西。它们需要一种持久的方式来组织记忆、资源、技能，以及跨会话的检索行为。

## 为什么上下文需要一个归属

OpenViking 把这个问题抽象为上下文层。与其把状态分散在临时文件、数据库和插件缓存里，它提供了一套一致的读写模型，让 Agent 可以稳定地管理上下文。

## 这一层负责什么

- **记忆：** 应该跨越单轮对话保留下来的事实和工作笔记。
- **资源：** Agent 可以检索的文件、片段、项目材料和操作上下文。
- **技能：** 在特定领域内指导 Agent 行动的可复用流程。

## 落地形态

这篇 mock 文章用于验证博客列表、文章详情路由、标签、分类、RSS 输出、Markdown 复制，以及 TOS 部署 workflow。
