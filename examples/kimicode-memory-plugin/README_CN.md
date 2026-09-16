# OpenViking 记忆插件（Kimi Code CLI）

Kimi Code CLI 的薄适配层，复用 `memory-plugin-shared`，不复制记忆逻辑。

布局对齐 ZCode 插件（[PR #3678](https://github.com/volcengine/OpenViking/pull/3678)），但**不是**改名拷贝。Kimi Code 使用自己的原生插件 manifest、hook 生命周期、MCP 声明和 `wire.jsonl` 会话日志，并且有 ZCode 没有的 `SessionEnd` / `PreCompact` / `Interrupt`。细节见 [DESIGN.md](./DESIGN.md)。

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

安装器通过 `kimi plugin install` 注册插件目录。之后由 Kimi 读取 `kimi.plugin.json` 并管理 hook 和 MCP 生命周期，不修改已有的 `config.toml` 与 `mcp.json` 配置。

也可以在 Kimi Code 里：

```
/plugins install /path/to/examples/kimicode-memory-plugin
```

然后 `/reload` 或 `/new`。

## 测试

```bash
node --test scripts/*.test.mjs
```
