## 步骤1：安装

复制 API Key：

{{OPENVIKING_API_KEY_BLOCK}}

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url {{OPENVIKING_BASE_URL}} --api-key <API-Key>
openclaw gateway restart
```

## 步骤2：验证

```bash
openclaw openviking status
```

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件未生效 | 重跑安装，再执行 `openclaw gateway restart` |
| 401 / 403 | 重新粘贴 API Key |

## 参考

- 手动配置文档：[OpenClaw](https://docs.openviking.net/zh/agent-integrations/03-openclaw)
- 源码：[examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)
