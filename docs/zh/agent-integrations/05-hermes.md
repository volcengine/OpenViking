# Hermes

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

然后选择连接方式：

- **Quick Local** 安装本地服务和 embedding 模型，复用受支持的 Hermes LLM 抽取记忆。
  LLM 仍可使用远程 API。
- **OpenViking Service (VolcEngine Cloud)** 通过服务 API Key 连接火山引擎的
  [OpenViking 托管云服务](https://www.volcengine.com/product/openviking-service?utm_source=opensource_docs&utm_medium=referral&utm_campaign=agent-integrations_05-hermes)，
  无需安装服务端或配置本地模型。
- **Custom** 使用 URL 和凭据连接自己的服务，也可以复用已保存的 `ovcli.conf`。

配置后正常聊天即可。插件默认在待提交内容达到 20,000 tokens、会话结束或切换时
请求提交。OpenViking 完成抽取后，记忆才能被召回。已有服务端数据会保留。

Quick Local 服务在 Hermes 退出后仍会运行。支持的模型、服务控制命令和已知本地
embedding 问题见[插件指南](https://hermes-agent.nousresearch.com/docs/plugins/openviking)。

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
