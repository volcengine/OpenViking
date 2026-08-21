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

- 云：保持 **OpenViking Service (VolcEngine Cloud)**，粘贴 API Key
- 自托管：填 URL（默认 `http://127.0.0.1:1933`）和 API Key；本地免鉴权可留空
- 向导若发现已有 `ovcli.conf`，直接复用即可

## 验证

```bash
hermes memory status
```

## 参见

- [集成能力参考](./16-capability-reference.md)
- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 完整配置指南
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
