# OpenViking 记忆插件（Kimi Code CLI）

Kimi Code CLI 的薄适配层，复用 `memory-plugin-shared`，不复制记忆逻辑。

这里仅保留 Kimi 专属的 manifest、生命周期接线和 `wire.jsonl` 转录适配；共享运行时由 `sync.mjs` 从 `../memory-plugin-shared/lib/` 生成。Kimi Code 使用自己的原生插件 manifest、hook 生命周期、MCP 声明和会话日志格式。细节见 [DESIGN.md](./DESIGN.md)。

> **需要支持 `viking://~` 主目录别名的 OpenViking 服务。**
>
> **已用 Kimi Code CLI 0.43.1 验证；更早版本尚未验证。**

## 行为

- **SessionStart**：回放离线 pending 队列（观察事件，不能注入上下文）。
- **UserPromptSubmit**：首次注入 profile，并按查询召回记忆；**stdout 纯文本**会被追加进上下文。
- **PreToolUse**（`Read|Glob|Grep`）：拦截直接读 `viking://`，改走 MCP 工具。
- **Stop / PreCompact / SessionEnd**：在分离进程里按 `wire.jsonl` 增量捕获；压缩、会话结束或达到配置阈值时 commit。
- **Interrupt**：同步捕获（用户 Esc 时触发，**不会**再发 Stop）。

## 安装

```bash
bash examples/memory-plugin-shared/install.sh --harness kimicode
```

安装器把原生插件复制到 `$KIMI_CODE_HOME/plugins/managed/openviking-memory`，并写入 `$KIMI_CODE_HOME/plugins/installed.json`。不会修改已有的 `config.toml` 与 `mcp.json` 配置。

也可以在 Kimi Code 里：

```
/plugins install /path/to/examples/kimicode-memory-plugin
```

然后 `/reload` 或 `/new`。

## 测试

```bash
node --test scripts/*.test.mjs
```
