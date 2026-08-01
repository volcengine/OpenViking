# ZCode 记忆集成

为 ZCode 添加跨项目、跨会话的长期记忆。安装后，OpenViking Hook 会自动在会话启动时注入相关上下文、在每次输入时召回记忆、并在对话结束时捕获内容。MCP 工具用于主动搜索、读取和管理记忆。

## 安装

前置条件：Node.js 18+、本地或远程运行的 OpenViking 服务、以及已安装的 ZCode。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness zcode
```

GitHub 访问受限时使用 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness zcode --dist tos
```

安装脚本通过 `~/.zcode/` 或 `zcode` 二进制检测 ZCode，将 hooks 和 MCP 配置合并到 `~/.zcode/cli/config.json`，并将 OpenViking 凭据写入 `~/.openviking/ovcli.conf`。

## 验证

安装后重启 ZCode，然后：

- 检查 `~/.zcode/cli/config.json` 中 `hooks.events` 是否包含 `openviking-memory` 条目，以及 `mcp.servers.openviking` 是否存在。
- 设置 `OPENVIKING_DEBUG=1`（ZCode 进程环境变量）或 `~/.openviking/ov.conf` 中的 `claude_code.debug: true`，查看 `~/.openviking/logs/zcode-hooks.log`。

## 工作原理

插件挂载到 ZCode 生命周期的四个节点：

- **SessionStart** — 注入用户画像和偏好/实体到上下文。
- **UserPromptSubmit** — 搜索 OpenViking 相关记忆并注入 `<openviking-context>`。
- **PreToolUse**（`Read|Glob|Grep`）— 拦截 `viking://` 虚拟路径的直接访问，引导使用 OpenViking MCP 工具。
- **Stop** — 捕获增量用户/助手对话并提交 OpenViking 会话。

ZCode 不支持 `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`，因此采用 commit-on-`Stop` 策略补偿缺少 compact/会话结束信号的不足。

所有数据写入均为异步执行，不会阻塞当前对话。

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| Hook 未触发 | config.json 中 `hooks.enabled` 未设置 | 重跑安装脚本，或手动设置 `"hooks": { "enabled": true }` |
| 召回返回空 | OpenViking 服务未运行或记忆尚未提取 | 检查 `curl http://127.0.0.1:1933/health`；等待记忆提取器处理捕获的对话 |
| MCP 工具未出现 | MCP 服务启动失败 | 检查 `~/.zcode/cli/config.json` → `mcp.servers.openviking` 的 `mcp-proxy.mjs` 绝对路径是否正确 |
| 重复捕获 | 未卸载就重装 | 先运行 `install.sh --harness zcode --uninstall`，再重新安装 |

## 参见

- [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin)
- [DESIGN.md](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin/DESIGN.md) — 已验证的 ZCode 扩展面事实
- [MCP 客户端](./06-mcp-clients.md)
- [部署指南 → CLI](../guides/03-deployment.md#cli)
