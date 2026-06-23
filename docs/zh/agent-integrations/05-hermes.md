# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) 内置 OpenViking 记忆提供方。无需安装插件——把 Hermes 指向你的 OpenViking 服务即可，记忆存储、召回和抽取均原生支持。

## 配置

运行 Hermes 记忆配置向导：

```bash
hermes memory setup
```

向导会询问：

- **OpenViking 服务 URL** — 自托管服务器（默认 `http://127.0.0.1:1933`）或 OpenViking Service（火山引擎云）
- **API Key** — 本地开发模式留空
- **租户 account / user / peer ID** — 多租户部署时使用。迁移期的旧 `agent_id` 配置会映射为请求的 actor peer。

配置保存在 Hermes 的 `config.yaml` 和 `.env` 文件中。

## 验证

```bash
hermes memory status
```

配置完成后，Hermes 自动使用 OpenViking 作为长期记忆——`viking_remember`、`viking_recall` 等记忆工具即刻可用。

## 参见

- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 完整配置指南
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
