## 步骤1：安装

运行安装器：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

若 GitHub 访问受限，可改用火山引擎 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

安装器会依次询问语言、Harness、下载源和 OpenViking 凭据：

1. Harness 选择 **DeepSeek Harness**。插件默认安装到 `web` profile，也可通过 `--dsh-profile <name>` 指定其他 profile。
2. 连接方式选择 **火山引擎 OpenViking 云服务**，并填入 API Key：

{{OPENVIKING_API_KEY_BLOCK}}

## 步骤2：验证

1. 启动 `dsh --profile web` 并打开新会话，确认会话顶部显示“上下文注入 · openviking-memory”。
2. 确认模型具有 `mcp__openviking__*` 工具，并能够在会话中正常调用。

## 故障排查

| 现象 | 排查方向 |
|---|---|
| 没有上下文注入，也没有 OpenViking 工具 | 执行 `dsh --profile web --dump-config`，确认输出中包含 `openviking-memory`；若缺失，重新运行安装器或执行 `dsh plugin --profile web add @openviking/dsh-memory-plugin` |
| 插件安装到了错误的 profile | 安装器默认使用 `web`；通过 `--dsh-profile <name>` 重新运行 |
| 安装时报 `ERESOLVE @deepseek-ai/dsh-*` | 各包预发布 tag 可能不同步，请精确安装 `@deepseek-ai/dsh@0.1.0-rc.6` |
| 安装时提示包不在 npm registry 中 | pnpm 默认拒绝发布不满 24 小时的版本；可稍后重试，或把精确版本加入 `pnpm-workspace.yaml` 的 `minimumReleaseAgeExclude` |
| 无法召回历史记忆 | 先执行 `curl http://localhost:1933/health` 确认服务端正常；再检查端点配置，并确认 prompt 不少于 3 个字符 |
| OpenViking 返回 401 / 403 | 检查 API Key；可信模式部署还需检查 `OPENVIKING_ACCOUNT` 与 `OPENVIKING_USER` |
| 召回结果混入其他项目的记忆 | 设置 `OPENVIKING_RECALL_PEER_SCOPE=actor` |
| 异常退出后没有 commit | commit 由 token 阈值和会话 teardown 触发；排队的写入会在下次会话开始时重放 |

## 参考

- 完整文档：[DeepSeek Harness](https://docs.openviking.net/zh/agent-integrations/17-dsh)
- 源码：[examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)
