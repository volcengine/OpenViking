## 步骤1：安装

1. 根据所使用的客户端，在终端执行对应的安装命令：

   **Trae 国际版**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos
   ```

   **Trae 中国版**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos
   ```

   **TraeCode CLI 2.0**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cli --dist tos
   ```

2. 安装器会依次询问以下信息：语言（English / 中文）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

**TRAE / TRAE CN**：在「设置 → MCP → 已配置的 MCP Servers」中确认能够看到 `openviking` 条目即表示接入成功。

**TraeCode CLI 2.0**：Hook 要先信任才会运行。启动 `trae-cli`，在信任确认上选 **Trust all and continue**：

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

再执行 `trae-cli plugin list`，确认 `openviking-memory` 已启用。错过这个提示，或当时选了第 3 项，Hook 就不会运行：输入 `/hooks` 补上信任并开启条目，`/plugins` 里确认插件已启用——两个开关相互独立，都要是开着的。插件更新动了 Hook 时会再要求信任一次。

## 故障排查

| 问题 | 处理 |
|---|---|
| 没有自动召回 | 完全退出 TRAE，重启，再建会话 |
| TraeCode CLI 2.0 装了插件但不召回 | 启动时的 Hook 信任被跳过：`/hooks` 里信任并开启，`/plugins` 里确认插件已启用 |
| 连接 / 鉴权失败 | 检查 `~/.openviking/ovcli.conf`，重启客户端 |
| 需要日志 | `~/.openviking/logs/trae-hooks.log`、`trae-cn-hooks.log` 或 `codex-hooks.log`（TraeCode CLI 2.0） |

## 参考

- 手动配置文档：[TRAE](https://docs.openviking.net/zh/agent-integrations/13-trae)
- 源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)（TRAE / TRAE CN）、[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)（TraeCode CLI 2.0）
