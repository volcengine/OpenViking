# 社区插件

社区维护的各运行时集成。各插件在目标平台、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## ZCode 记忆集成

源码：[examples/zcode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin)

ZCode 社区集成通过配置驱动的生命周期 Hook 和 OpenViking MCP 服务提供跨项目、跨会话记忆：

- **SessionStart** 注入用户画像。
- **UserPromptSubmit** 召回相关记忆。
- **PreToolUse** 将直接读取 `viking://` 的操作引导至 MCP 工具。
- **Stop** 在 detached worker 中捕获 rollout 里的未处理回合，并 commit OpenViking session。

ZCode 不提供 `PreCompact`、`SessionEnd` 和 subagent 生命周期 Hook。因此该适配器在 `Stop` 时 commit，以 ZCode rollout 文件作为权威增量对话源，只有 rollout 文件不可用时才回退到 Hook stdin。

### 安装

前置条件：Node.js 18+、正在运行的 OpenViking 服务，以及 ZCode。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness zcode
```

GitHub 不可用的地区可使用 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness zcode --dist tos
```

安装器通过 `~/.zcode/` 或 `zcode` 二进制检测 ZCode，将运行时安装到 `~/.openviking/agent-integrations/zcode/`，并把 Hook 与 MCP 配置合并到 `~/.zcode/cli/config.json`。

重启 ZCode 后，请确认：

- `~/.zcode/cli/config.json` 包含 `hooks.enabled: true`、`hooks.events` 下的 OpenViking 条目，以及 `mcp.servers.openviking`。
- 设置 `OPENVIKING_DEBUG=1` 后，可在 `~/.openviking/logs/zcode-hooks.log` 查看诊断日志。

| 现象 | 原因 | 处理方式 |
|------|------|----------|
| Hook 未执行 | Hook 配置被禁用或已过期 | 重跑安装器并重启 ZCode |
| 召回为空 | OpenViking 不可用或记忆尚未提取 | 检查 `curl http://127.0.0.1:1933/health`，并等待提取完成 |
| MCP 工具未出现 | MCP proxy 启动失败 | 检查 `~/.zcode/cli/config.json` 中 `mcp.servers.openviking` 的绝对路径命令 |
| 重复捕获 | 旧安装留下了重复 Hook 条目 | 先运行 `install.sh --harness zcode --uninstall`，再重新安装 |

实现细节与当前已验证的 ZCode 假设见插件目录中的 [README](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin) 和 [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/zcode-memory-plugin/DESIGN.md)。

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

## Open WebUI tool server

[Open WebUI](https://github.com/open-webui/open-webui) 是一个自托管的 AI 聊天界面。

源码：[examples/openwebui-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openwebui-plugin)

一个独立的 FastAPI server，把 OpenViking 的一组精选端点以 OpenAPI tools 形式暴露，让 Open WebUI 作为原生工具调用。部署与端点说明见 README。

## 更多示例

[examples/](https://github.com/volcengine/OpenViking/tree/main/examples) 目录下还有 Agent 插件之外的部署与集成示例——Grafana 面板、Kubernetes Helm chart、多租户配置、快照流程和 SDK 片段等。

