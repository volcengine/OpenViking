# OpenViking ZCode 记忆插件

本包为 ZCode 提供 OpenViking 长期记忆的生命周期适配器。复用 `memory-plugin-shared` 共享运行时——不重复任何记忆逻辑，仅新增一个 ZCode 薄适配层。

## 功能

- **SessionStart** — 注入用户画像和偏好/实体到上下文。
- **UserPromptSubmit** — 搜索 OpenViking 相关记忆并注入。
- **PreToolUse**（`Read|Glob|Grep`）— 拦截 `viking://` 虚拟路径的直接访问，引导使用 MCP 工具。
- **Stop** — 捕获增量用户/助手对话并提交 OpenViking 会话。

ZCode 不支持 `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`，因此采用 commit-on-`Stop` 策略（同 TRAE 和 Codex），补偿缺少 compact/会话结束信号的不足。

## 安装

使用共享安装脚本：

```bash
bash examples/memory-plugin-shared/install.sh --harness zcode
```

安装脚本通过 `~/.zcode/` 或 `zcode` 二进制检测 ZCode，将 hooks 和 MCP 配置合并到 `~/.zcode/cli/config.json`，并将 OpenViking 凭据写入 `~/.openviking/ovcli.conf`。

## 架构

插件通过 `sync.mjs` 将共享运行时 vendor 到 `scripts/shared/`。调度器（`zcode-hook.mjs`）按事件名分支；四个薄 shim 脚本设置环境变量后导入调度器。所有记忆逻辑（召回、捕获、提交、去重、待处理队列、凭据解析、MCP 代理）由共享运行时提供。

详见 [DESIGN.md](./DESIGN.md) 了解已验证的 ZCode 扩展面事实和设计决策来源。

## 测试

```bash
node --test scripts/zcode-turns.test.mjs scripts/zcode-hooks.test.mjs
```
