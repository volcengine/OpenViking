# 社区插件

社区维护的各运行时集成。各插件在目标平台、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## AstrBot 插件

[AstrBot](https://github.com/AstrBotDevs/AstrBot) 是一个多平台 IM Bot 框架，支持 QQ、Telegram、Discord、飞书等 20+ 平台。

源码：[astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

为 AstrBot 提供群聊/私聊的自动捕获、LLM 请求前的语义召回，以及可配置的 venue 记忆隔离。

**安装**：在 AstrBot WebUI → 插件市场搜索 **OpenViking Memory** 并安装；或从链接安装：`https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

**主要特性**：

- 基于 hooks 的自动召回与捕获，模型不需要主动调用工具
- 三档隔离模式：`venue_user`（群/私聊各自独立）、`venue_user_fanout`（跨群共享）、`global_user`（全局共享）
- 四触发器自动 commit：消息计数、token 阈值、空闲超时、进程退出 flush
- 首次接入群聊时自动拉取平台历史消息入库
