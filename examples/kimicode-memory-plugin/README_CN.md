# OpenViking 记忆插件（Kimi Code CLI）

Kimi Code CLI 的薄适配层，复用 `memory-plugin-shared`，不复制记忆逻辑。

布局对齐 ZCode 插件（[PR #3678](https://github.com/volcengine/OpenViking/pull/3678)），但**不是**改名拷贝。Kimi Code 用 TOML `[[hooks]]`、`mcp.json`、`wire.jsonl` 会话日志，并且有 ZCode 没有的 `SessionEnd` / `PreCompact` / `Interrupt`。细节见 [DESIGN.md](./DESIGN.md)。

> **需要支持 `viking://~` 主目录别名的 OpenViking 服务。**

## 行为

- **SessionStart**：回放离线 pending 队列（观察事件，不能注入上下文）。
- **UserPromptSubmit**：首次注入 profile，并按查询召回记忆；**stdout 纯文本**会被追加进上下文。
- **PreToolUse**（`Read|Glob|Grep`）：拦截直接读 `viking://`，改走 MCP 工具。
- **Stop / PreCompact / SessionEnd**：在分离进程里按 `wire.jsonl` 增量捕获并 commit。
- **Interrupt**：同步捕获（用户 Esc 时触发，**不会**再发 Stop）。

## 安装

```bash
bash examples/memory-plugin-shared/install.sh --harness kimicode
```

安装器识别 `kimi` 或 `~/.kimi-code/`，把运行时放到 `~/.openviking/agent-integrations/kimicode/`，把 OpenViking 的 `[[hooks]]` 块合并进 `~/.kimi-code/config.toml`，并写入 `~/.kimi-code/mcp.json` 的 `mcpServers.openviking`。已有 Herdr / Orca hook 不会被删掉。

也可以在 Kimi Code 里：

```
/plugins install /path/to/examples/kimicode-memory-plugin
```

然后 `/reload` 或 `/new`。

## 测试

```bash
node --test scripts/*.test.mjs
```
