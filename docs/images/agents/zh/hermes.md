## 步骤1：安装

```bash
hermes memory setup openviking
```

保持 **OpenViking Service (VolcEngine Cloud)**，把 API Key 贴进去：

{{OPENVIKING_API_KEY_BLOCK}}

## 步骤2：验证

```bash
hermes memory status
```

应看到 `Provider: openviking` 且 `Status: available`。然后开一轮新会话。

## 故障排查

| 问题 | 处理 |
|---|---|
| Provider 不是 openviking | 重跑 `hermes memory setup openviking` |
| Status 不是 available | 检查 API Key |

## 参考

- 手动配置文档：[Hermes](https://docs.openviking.net/zh/agent-integrations/05-hermes)
- 原理说明：[OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
