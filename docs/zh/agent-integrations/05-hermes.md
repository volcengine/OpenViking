# Hermes

> **推荐通过 [OpenViking 网关](../guides/15-gateway.md) 接入。** 关掉下面的插件，把模型的 Base URL 指向网关、API Key 换成网关密钥，网关会为新用户消息召回记忆并保存对话。[上游](../gateway/07-upstreams.md#upstreams)要自带服务商 API Key，订阅登录无法走网关。配置见[把客户端接入 OpenViking 网关](../gateway/03-connect-clients.md#openclaw-and-hermes)，从零开始见[网关快速上手](../gateway/02-quickstart.md)，两种方式的取舍见[网关还是插件](../guides/15-gateway.md#gateway-or-plugin)，官网介绍见 [openviking.ai/gateway](https://openviking.ai/gateway)。

为 [Hermes Agent](https://hermes-agent.nousresearch.com/) 配置 OpenViking 长期记忆。

## 开始使用

在要使用的 Hermes profile 中运行：

```bash
hermes plugins install openviking --enable
hermes memory setup openviking
hermes
```

安装时接受依赖安装提示。如果当前 Hermes 版本仍内置 OpenViking，请跳过安装命令。
该版本会使用内置副本，不提供 Quick Local。

对话历史用于理解当前聊天。长期记忆保存有用的信息，供后续聊天使用。
公共记忆是不属于某个发送者的记忆。

选择 **Personal Agent** 可召回公共记忆和当前发送者的记忆，并保留现有对话历史设置。
选择 **Shared Agent** 可在每个群组或话题内共享对话历史，并召回同一 OpenViking 用户
下所有发送者的记忆。Shared Agent 会要求确认。

然后在 **Hermes 自己的配置菜单**中选择连接方式：

- **OpenViking Managed Service** 提供 **Volcengine** 和 **BytePlus** 两个选项。
  Volcengine 使用固定的服务地址和
  [托管服务](https://www.volcengine.com/product/openviking-service?utm_source=opensource_docs&utm_medium=referral&utm_campaign=agent-integrations_05-hermes)的 API Key。
  BytePlus 需要输入实例的 HTTPS 服务地址和 OpenViking API Key。
  详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。
- **Custom** 使用服务地址和凭据连接 OpenViking，也可以复用已保存的 `ovcli.conf`。
- **Quick Local** 安装本地服务和 embedding 模型，复用受支持的 Hermes LLM 抽取记忆。
  LLM 仍可使用远程 API。

旧版插件连接 Volcengine 时使用 **OpenViking Service (VolcEngine Cloud)**，
连接 BytePlus 时使用 **Custom**。

配置后正常聊天即可。插件默认在待提交内容达到 20,000 tokens、会话结束或切换时
请求提交。OpenViking 完成抽取后，记忆才能被召回。已有服务端数据会保留。

Quick Local 服务在 Hermes 退出后仍会运行。支持的模型、服务控制命令和运行环境
升级说明见[插件指南](https://hermes-agent.nousresearch.com/docs/plugins/openviking)。

## 查看状态

```bash
hermes memory status
```

`available` 表示 provider 已配置，不代表服务健康，也不代表记忆抽取成功。

## 更新

```bash
hermes plugins update openviking
```

更新后重启 Hermes 或 gateway。连接设置和数据会保留。更新 Quick Local 服务时，
重新运行配置向导并选择 Quick Local。正常聊天使用已安装的服务，不会检查更新。

Hermes 更新移除内置副本时，会为已使用 OpenViking 的 profile 尝试安装目录插件。
如果失败，请在该 profile 中运行上面的安装命令。

## 更多信息

- [插件指南](https://hermes-agent.nousresearch.com/docs/plugins/openviking)
- [集成能力参考](./16-capability-reference.md)
- [服务部署](../guides/03-deployment.md)
- [API Key](../guides/04-authentication.md)
