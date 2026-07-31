# OpenViking Helper

OpenViking Helper 是面向本地开发 Agent 的桌面控制台。它把原本分散在命令行、配置文件和安装脚本中的 OpenViking 接入流程集中到一个界面里，并提供会话分析、本地记忆和技能管理能力。

Helper 不会替代 Claude Code、Codex、Cursor、TRAE 或 OpenCode 的集成；它使用同一套 OpenViking 配置，帮助你完成安装、检查接入状态，并查看 OpenViking 在实际会话中是否生效。

OpenViking Helper 目前处于 Beta 阶段，支持 macOS 和 Windows x64，应用界面支持中文和 English，可在 **设置 → 通用 → 界面语言** 中随时切换。

## 下载

| 平台 | 架构 | 下载 |
|------|------|------|
| macOS | Apple Silicon（arm64） | [下载 DMG](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-arm64/openviking-helper-0.0.19-arm64.dmg) |
| macOS | Intel（x64） | [下载 DMG](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-x64/openviking-helper-0.0.19-x64.dmg) |
| Windows | x64 | [下载安装程序](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/win32-x64/openviking-helper-0.0.19-x64.exe) |

## 开始使用

1. 下载并启动与你的平台和架构匹配的安装包。
2. 打开 **设置 → 配置**，选择火山引擎托管服务或自建 OpenViking 服务，填写连接信息并执行连接测试。
3. 打开 **设置 → Agent 接入**。Helper 会检测本机的 OpenViking CLI、Claude Code、Codex、Cursor、TRAE 和 OpenCode。
4. 为需要使用的 Agent 安装或配置对应的插件、MCP、Hook 或 CLI 接入，然后按界面提示重启 Agent。
5. 在 **会话**、**记忆** 和 **技能** 页面检查接入效果与本地数据。

## 可视化接入 Agent

Helper 会检测已安装的本地 Agent，并展示 OpenViking 的接入状态。你可以在界面中维护多个 OpenViking 服务配置、切换当前配置、测试连接，并为支持的 Agent 执行接入或重新安装。

![OpenViking Helper 的 Agent 接入页面](../../images/openviking-helper/agent-access.webp)

Agent 的具体能力仍取决于对应集成。例如，Claude Code、Codex、Cursor、TRAE 和 OpenCode 的生命周期 Hook、MCP 工具及自动召回能力，请以各自的集成文档为准。

## 查看会话轨迹

Helper 可以解析 Claude Code、Codex 和 TRAE 的本地会话，并按 Agent 和项目展示时间线。通过会话详情可以检查 OpenViking 的关键动作，例如：

- Prompt 前是否发生记忆召回和上下文注入；
- 本轮是否调用 OpenViking MCP 工具；
- 回复结束后是否捕获新增对话；
- 上下文压缩前是否提交会话；
- 会话启动、结束等生命周期动作是否触发。

![OpenViking Helper 的会话时间线](../../images/openviking-helper/session-timeline.webp)

这些信息适合用来确认接入是否真实生效，以及定位配置、Hook 或 MCP 连接问题。

## 管理记忆与技能

Helper 会按 Agent 和项目展示本地 memory、rule 文件及 `SKILL.md` 技能。你可以查看文件内容、路径、更新时间和同步状态，并将选中的本地内容同步到当前 OpenViking 服务。

![OpenViking Helper 的本地记忆管理页面](../../images/openviking-helper/memory-overview.webp)

同步完成后，可以继续在 Helper 中查看 OpenViking 服务端的记忆分类与内容。不同 Agent 原本分散保存的长期信息，也可以通过 OpenViking 统一检索和复用。

## 本地数据与隐私

为展示接入状态、会话和记忆，Helper 会读取本机对应 Agent 的配置与本地数据。只有执行同步或使用 OpenViking 服务能力时，相关内容才会发送到当前激活的服务配置。同步前请确认服务地址，并检查待同步内容是否包含敏感信息。

## 参见

- [Agent 集成概览](./01-overview.md)
- [Claude Code 记忆插件](./02-claude-code.md)
- [Codex 记忆插件](./04-codex.md)
- [Cursor 记忆集成](./12-cursor.md)
- [TRAE 记忆集成](./13-trae.md)
- [OpenCode 插件](./10-opencode.md)
