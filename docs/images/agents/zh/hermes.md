
[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) 内置 OpenViking 记忆提供方。无需安装插件——把 Hermes 指向你的 OpenViking 服务即可，记忆存储、召回和抽取均原生支持。

## 步骤 1：运行 Hermes 记忆配置向导：

```bash
hermes memory setup
```

向导会询问：

- **OpenViking 服务 URL** — 自托管服务器（默认 `http://127.0.0.1:1933`）或火山引擎 OpenViking Cloud
- **API Key** — 本地开发模式留空
- **租户 account / user / agent ID** — 多租户部署时使用

配置保存在 Hermes 的 `config.yaml` 和 `.env` 文件中。


## 步骤 2：验证 Hermes 记忆状态

```bash
hermes memory status
```

配置完成后，Hermes 自动使用 OpenViking 作为长期记忆——`viking_remember`、`viking_recall` 等记忆工具即刻可用。

## 参考文档

- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 完整配置指南
