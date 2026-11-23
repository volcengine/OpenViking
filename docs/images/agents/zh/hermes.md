# 为 Hermes 配置 OpenViking Service

[OpenViking Service](https://www.volcengine.com/product/openviking-service) 是
火山引擎托管和运营的 OpenViking 云服务，无需安装服务端或配置本地模型。
在 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)
开通服务，然后从**用户管理**的 **API Key** 中创建密钥。

## 安装和配置

在要使用的 Hermes profile 中运行：

```bash
hermes plugins install openviking --enable
hermes memory setup openviking
```

安装时接受依赖安装提示。如果当前 Hermes 版本已内置 OpenViking，请跳过安装命令。

在向导中：

1. 选择 **Personal Agent** 可召回公共记忆和当前发送者的记忆，并保留现有历史设置。
   选择 **Shared Agent** 可在每个群组或话题内共享历史，召回同一 OpenViking 用户
   下所有发送者的记忆。Shared Agent 会要求确认。
2. 若出现配置来源选项，选择 **Create new OpenViking profile**。也可以复用已保存的
   `ovcli.conf`。
3. 选择 **OpenViking Service (VolcEngine Cloud)**。
4. 输入服务 API Key。
5. 选择保存位置。**Keep in Hermes only** 保存到 Hermes 的 `.env`。
   **Mirror to OpenViking store** 保存到本地 `ovcli.conf.<name>` 并关联到 Hermes。
   两种方式都会将凭据保存在当前电脑。
6. 保存 OpenViking profile 时，填写名称，例如 `hermes`。这只是本地配置名称，
   不会创建用户或改变访问权限。

然后启动新的 Hermes 会话：

```bash
hermes
```

外部插件还提供 **Quick Local**，可复用受支持的 Hermes LLM，安装本地服务和
embedding 模型。要求和已知问题见[插件指南](https://hermes-agent.nousresearch.com/docs/plugins/openviking)。

## 查看状态

```bash
hermes memory status
```

确认 provider 为 `openviking`，状态为 `available`。这只表示配置已保存，
不代表服务健康或记忆抽取成功。

## 故障排查

| 问题 | 处理 |
|------|------|
| 插件未安装 | 在该 profile 中运行 `hermes plugins install openviking --enable`。 |
| 使用了其他 provider | 重新运行 `hermes memory setup openviking`。 |
| 状态不是 available | 检查保存的连接设置和关联的 `ovcli.conf`。 |

## 更多信息

- [Hermes 集成](https://docs.openviking.net/zh/agent-integrations/05-hermes)
- [插件指南](https://hermes-agent.nousresearch.com/docs/plugins/openviking)
