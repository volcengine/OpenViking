# 调整召回延迟

适用于已完成插件接入的用户。首次安装从[集成选型](01-overview.md)开始。

查询扩展和召回压缩都会增加模型调用。优先考虑响应速度时，可以关闭这两项；检索、预算控制和跨轮去重仍然保留：

```bash
export OPENVIKING_RECALL_QUERY_EXPANSION=off
export OPENVIKING_RECALL_COMPRESS=off
```

也可以在 `~/.openviking/ovcli.conf` 中配置：

```json
{
  "plugin": {
    "recallQueryExpansion": "off",
    "recallCompress": "off"
  }
}
```

### 选择压缩方式

| 值 | 行为 |
| --- | --- |
| `off` | 不压缩召回结果 |
| `server` | 请求 OpenViking 服务端压缩 |
| `client` | 只用本地压缩器，适用于 Claude Code 和 Codex |
| `auto` | 有本地压缩器时优先使用，否则请求服务端自动处理 |

Claude Code 和 Codex 默认使用 `auto`，本地压缩器分别是 `claude -p` 和 `codex exec`，见[Agent 集成能力：召回摘要](./16-capability-reference.md#_3-2-5-召回再摘要)。其他支持云端压缩的集成默认保持 `off`，需要显式开启。服务端压缩适用于 Claude Code、Codex、OpenCode、DSH、pi、Cursor、TRAE、TRAE CN、ZCode、OpenClaw 和 Hermes 外部插件，要求服务端支持 context-search rewrite。Hermes 内置 provider 不支持召回摘要。

这些设置控制自动召回。模型主动调用 MCP `search` 时，使用该次调用传入的参数。完整规则与旧服务端回退行为见[共享插件说明](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression)。

共享插件读取 `ovcli.conf` 的 `plugin` 段；`plugin.<harness>` 可覆盖某个客户端的配置。环境变量优先级更高；旧变量 `OPENVIKING_RECALL_REWRITE` 仍可作为 `OPENVIKING_RECALL_COMPRESS` 的别名使用，详见[插件配置](../configuration/02-client.md#插件配置)。修改后重启对应 Agent，使 hook 进程加载新配置。这些都是插件客户端配置，不需要修改服务端的 `ov.conf`。

### 请求超时

查询扩展、检索和 digest 压缩依次执行。查询扩展默认超时为 5 秒（`retrieval.recall_intent_timeout_s`），digest 默认超时为 30 秒（`retrieval.recall_rewrite_timeout_s`）。

`OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS` 或 `plugin.recallContextTimeoutMs` 控制客户端等待整个 context 请求的上限。未设置时沿用插件的普通超时；请求带 session 且未将 query expansion 设为 `off` 时至少 15 秒，请求 digest 时至少 45 秒。自定义值应高于该请求会用到的服务端超时，并低于宿主 hook 的超时；客户端提前结束会丢失整个响应。
