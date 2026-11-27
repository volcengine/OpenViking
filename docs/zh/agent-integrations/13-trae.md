# TRAE

为 TRAE、TRAE CN 和 TraeCode CLI 2.0 添加跨项目、跨会话的长期记忆。OpenViking Hook 会加载相关上下文并捕获每轮对话；MCP 用于主动搜索、读取和管理记忆。

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)（TRAE 和 TRAE CN 专属文件在 [hosts/trae](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/trae)）

## 安装

前置条件：macOS 或 Linux、Node.js 18+，以及支持 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`Stop`、`PostCompact` Hook 的 TRAE/TRAE CN 版本。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

服务运行在本机时选择 **自建 / 本地**（`http://127.0.0.1:1933`）。远程自建服务选择**自定义 URL**并填写其地址；已有连接时，此选项显示为**自定义 URL / 保持当前**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器询问要安装的 harness 时，勾选 **TRAE**、**TRAE CN** 或 **TraeCode CLI 2.0**，也可以多选。安装后完全退出并重启对应客户端。TRAE 和 TRAE CN 使用安装的 `hooks.json`，无需审批 hook。

**TraeCode CLI 2.0** 安装的是 Codex 插件。启动 `trae-cli` 时会显示 hook 审阅提示：选 **Trust all and continue**，并在 `/plugins` 中确认 `openviking-memory` 已启用。其行为与 [Codex 页面](./04-codex.md)所述相同，包括 [hook 信任](./04-codex.md#首次启动-信任-hooks)。

安装完成后，新开一个 TRAE 会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 操作。它会检查连接，并带你完成首次使用；参见[一键接入](../getting-started/07-agent-onboarding.md)。

## 验证

1. 重启客户端并新建 Agent 会话。
2. 在客户端的 MCP 设置中确认 `openviking` 已连接。
3. 询问已有的项目或偏好，确认回答用到了已存储的记忆。
4. TraeCode CLI 2.0 运行 `trae-cli plugin list` 确认 `openviking-memory` 已启用，再在会话中运行 `/hooks`，确认 OpenViking 条目已信任并开启。

排查 Hook 时，用 `OPENVIKING_DEBUG=1` 启动客户端并查看 `~/.openviking/logs/trae-hooks.log`（TRAE）、`~/.openviking/logs/trae-cn-hooks.log`（TRAE CN）或 `~/.openviking/logs/codex-hooks.log`（TraeCode CLI 2.0）。

## TRAE 和 TRAE CN 上的不同之处

- **没有会话结束事件，也没有压缩前事件。** 插件不会提交会话的最后一段，也不会在压缩前提交。请在服务端开启 `memory.session_auto_commit.enabled`，由空闲提交兜底。见[退出时会发生什么](./16-capability-reference.md#what-happens-at-exit)。
- **压缩之后**，`PostCompact` 让下一次提问带上一条简短提示，写明该会话的 OpenViking id 和 `session_history` MCP 工具；Agent 把这个 id 作为工具的 `session` 参数传入。
- **捕获读取 Stop 事件**（提示和最后一条助手消息），不解析对话记录文件。
- **`viking://` 防护**：`PreToolUse` 拒绝路径为 `viking://` 的 `Read`、`Glob`、`Grep` 调用并引导使用 OpenViking MCP 工具；携带 `viking://` URI 的 `Bash` 或 `RunCommand` 命令仍会执行，并附带提示。
- **Skill** `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search` 和 `ov-memory-doctor` 安装到 `~/.trae/skills/<name>`（TRAE CN：`~/.trae-cn/skills/<name>`）。你修改过的 skill，或同名的非托管目录，升级时会保留。TraeCode CLI 也读取 `~/.trae/skills`，所以同时安装 TRAE 集成和 TraeCode CLI 2.0 插件时，CLI 会把这些 skill 各列两次。
- **不捕获子 agent。**

其余内容见[能力参考](./16-capability-reference.md)。

## 升级与卸载

重新运行安装命令即可升级。卸载 TRAE CN：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness trae-cn
```

卸载 TRAE 时把 `trae-cn` 换成 `trae`。卸载会移除 OpenViking 管理的 Hook、MCP 配置和 skill；你修改过的 skill 和同名的非托管目录会保留。TraeCode CLI 2.0 使用 `trae-cli plugin uninstall openviking-memory@openviking`。

## 故障排查

| 现象 | 原因和处理 |
|------|------------|
| 自动召回没有运行 | 完全退出并重启客户端，然后新建 Agent 会话。 |
| TraeCode CLI 2.0 装了插件，但没有召回或捕获 | 在 `/hooks` 中信任并启用 OpenViking 条目，并在 `/plugins` 中确认 `openviking-memory` 已启用；两个开关都要打开。更新改动了 hook 时会再次要求信任。 |
| MCP 无法连接 | 检查 `~/.openviking/ovcli.conf` 中的 URL/API Key，然后重启客户端。 |
| 同一内容被重复捕获 | 检查用户级和项目级 Hook 中是否有旧的 `trae-auto-recall.mjs` 或 `trae-auto-capture.mjs` 条目。重新运行安装脚本会移除 OpenViking 管理的旧条目。 |
| TraeCode CLI 2.0 没有列出插件 | 运行 `trae-cli plugin list`；如果没有 `openviking-memory`，重新运行安装脚本并选择 **TraeCode CLI 2.0**。 |

## 参见

- [能力参考](./16-capability-reference.md)
- [认证](../guides/04-authentication.md)
