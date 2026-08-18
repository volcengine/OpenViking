# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) 内置 OpenViking 记忆提供方。无需安装插件——把 Hermes 指向你的 OpenViking 服务即可，记忆存储、召回和抽取均原生支持。

## 隔离 Python 环境

Hermes 通过 HTTP 连接 OpenViking，因此无需把 OpenViking 安装到 Hermes 的
Python 环境中。请在独立的虚拟环境或容器中运行 OpenViking 服务。不要在
已有 Hermes 的环境中使用 `--force-reinstall` 安装或升级 OpenViking：Hermes
版本可能会固定与 OpenViking 已支持、已修复安全问题的版本不同的依赖。如果确实要将
两个应用放在同一环境中，请在同一次依赖求解中安装它们，并在启动任一服务前运行
`python -m pip check`。

## 配置

```bash
hermes memory setup openviking
```

带上 `openviking` 会跳过 provider 选择。只跑 `hermes memory setup` 也可以，然后选 **openviking**。

如果 Hermes 已经发现 `~/.openviking/ovcli.conf`，直接复用那个 profile。否则向导会问：

- **OpenViking connection** — 默认 **OpenViking Service (VolcEngine Cloud)**，或 **Custom**（本地 / VPS / 自托管）
- **云** — 端点已填好，粘贴 API Key
- **Custom** — 服务 URL（默认 `http://127.0.0.1:1933`），然后选用户 API Key、root API Key，或本地免鉴权
- **Hermes peer ID** — 默认 `hermes`

只有 root API Key 才需要填 account / user。火山云用用户 API Key 即可。迁移期的旧 `agent_id` 会映射为请求的 actor peer。

配置保存在 Hermes 的 `config.yaml` 和 `.env`。向导也可以镜像到 `~/.openviking/ovcli.conf.<name>`。

## 验证

```bash
hermes memory status
```

配置完成后，Hermes 会通过 OpenViking memory provider 自动注入上下文、预取相关记忆，并在会话后同步和抽取记忆。可用工具包括 `viking_search`、`viking_read`、`viking_browse`、`viking_remember`、`viking_forget` 和 `viking_add_resource`。

## 参见

- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 完整配置指南
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
