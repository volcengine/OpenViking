## 步骤1：安装

1. 安装 OpenViking 插件：

   ```bash
   openclaw plugins install clawhub:@openviking/openclaw-plugin
   ```

2. 将 OpenClaw 连接至火山引擎托管的 OpenViking 服务：

   ```bash
   openclaw openviking setup --base-url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <$OPENVIKING_API_KEY>
   ```

3. 配置 `peer_role`：`peer_role` 用于标识对话参与者的类型，并非权限角色。其中，`assistant` 表示不同的 Agent、工具或模型，`person` 表示不同的人类参与者。完成上述配置后，`peer_role` 默认为 `none`。如需调整 `peer_role`，可执行以下命令：

   ```bash
   openclaw openviking setup --reconfigure
   ```

4. 重启 Gateway 使配置生效：

   ```bash
   openclaw gateway restart
   ```

## 步骤2：验证

1. 在终端执行如下命令检查接入状态：

   ```bash
   openclaw openviking status
   ```

2. 返回如下结果即表示接入成功：

   ```text
   🦣 OpenViking Plugin Status

     Status: Configured
     mode:      remote
     baseUrl:   `https://api.vikingdb.cn-beijing.volces.com/openviking`
     apiKey:    set
     peer_role: none
     accountId: not set
     userId:    not set
     slot:      active

     ✓ Server reachable (version: v0.x.xx.x)
   ```

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件未生效 | 重跑安装，再执行 `openclaw gateway restart` |
| 401 / 403 | 检查鉴权凭据 |

## 参考

- 手动配置文档：[OpenClaw](https://docs.openviking.net/zh/agent-integrations/03-openclaw)
- 源码：[examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)
