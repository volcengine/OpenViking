
[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) 内置 OpenViking 记忆提供方。无需安装插件——把 Hermes 指向你的 OpenViking 服务即可，记忆存储、召回和抽取均原生支持。

## 步骤 1：运行 Hermes 记忆配置向导：

```bash
hermes memory setup
```

## 步骤 2：复制 Base URL 和 API Key
执行 setup 命令后会依次提示输入 Base URL 和 API Key，可复制后粘贴到你的 Hermes：

- Base URL: 复制以下 Base URL 到你的 Hermes：
```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```
- API Key: 复制页面中展示的 API Key 到你的 Hermes 终端
- 租户 account / user / agent ID：多租户部署时使用

配置保存在 Hermes 的 `config.yaml` 和 `.env` 文件中。


## 步骤 3：验证 Hermes 记忆状态

```bash
hermes memory status
```

配置完成后，Hermes 自动使用 OpenViking 作为长期记忆——`viking_remember`、`viking_recall` 等记忆工具即刻可用。

## 参考文档

- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 完整配置指南
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
