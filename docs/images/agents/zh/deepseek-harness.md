## 步骤1：安装

运行安装器：

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器会依次询问 Harness 和 OpenViking 凭据：

1. Harness 选择 **DeepSeek Harness**。插件会装到 `web` profile；要装到其他 profile，把命令末尾的 `bash` 换成 `bash -s -- --dsh-profile <name>`。
2. 连接方式选择 **火山引擎 OpenViking 云服务**，并填入 API Key：

{{OPENVIKING_API_KEY_BLOCK}}

## 步骤2：验证

1. 启动 `dsh --profile web` 并打开新会话，确认会话顶部显示“上下文注入 · openviking-memory”。
2. 确认模型具有 `mcp__openviking__*` 工具，并能够在会话中正常调用。

## 故障排查

| 现象 | 排查方向 |
|---|---|
| 没有上下文注入，也没有 OpenViking 工具 | 执行 `dsh --profile web --dump-config`，确认输出中包含 `openviking-memory`；若缺失，重新运行安装器或执行 `dsh plugin --profile web add @openviking/dsh-memory-plugin` |
| 插件安装到了错误的 profile | 安装器默认使用 `web`；把命令末尾的 `bash` 换成 `bash -s -- --dsh-profile <name>` 重新运行 |
| DSH 提示插件与当前 dsh 版本不兼容 | 插件接受 `0.1.0-rc.6` 起的所有 DSH 0.x 版本；DSH 已是 1.0 或更高时，升级插件或运行 `dsh plugin allow-version` |
| 升级 DSH 后插件启动失败 | 更新的 0.x 版本不经预先验证就会被接受；请把 DSH 固定到已验证的版本（如 `0.2.0-rc.2`），并提 issue |
| 安装时提示包不在 npm registry 中 | pnpm 默认拒绝发布不满 24 小时的版本；可稍后重试，或把精确版本加入 `pnpm-workspace.yaml` 的 `minimumReleaseAgeExclude` |
| 无法召回历史记忆 | 先执行 `curl http://localhost:1933/health` 确认服务端正常；再检查端点配置，并确认 prompt 不少于 3 个字符 |
| OpenViking 返回 401 / 403 | 检查 API Key；可信模式部署还需检查 `OPENVIKING_ACCOUNT` 与 `OPENVIKING_USER` |
| 召回结果混入其他项目的记忆 | 设置 `OPENVIKING_RECALL_PEER_SCOPE=actor`，将 peer 记忆限定为当前 peer；用户级记忆仍会共享 |
| 异常退出后没有 commit | commit 由 token 阈值和会话 teardown 触发；排队的写入会在下次会话开始时重放 |

## 参考

- 完整文档：[DeepSeek Harness](https://docs.openviking.net/zh/agent-integrations/17-dsh)
- 源码：[examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)
