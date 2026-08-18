[Hermes Agent](https://hermes-agent.nousresearch.com/) 内置 OpenViking 记忆提供方，不用再装插件。本页只讲怎么接到火山引擎 OpenViking 云服务。自托管请看文档站的 Hermes 指南。

## 步骤 1：打开记忆配置向导

```bash
hermes memory setup openviking
```

## 步骤 2：选火山云，粘贴 API Key

向导自己带了云端地址，你只要把本页的 API Key 贴进去。

1. 如果出现 **OpenViking config source**，选 **Create new OpenViking profile**。本机已经跑过共享安装脚本（Claude Code / Codex / Cursor / TRAE）的话，选 **Use existing OpenViking profile** 即可。
2. **OpenViking connection** 默认就是 **OpenViking Service (VolcEngine Cloud)**，直接回车。
3. 终端会自己打印端点：

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

4. 出现 **OpenViking API key** 时，把本页的 API Key 粘贴进去。
5. Hermes peer ID 默认是 `hermes`，一般不用改。

不用手填 Base URL。用用户 API Key 时也不用填租户 account / user。

## 步骤 3：验证

```bash
hermes memory status
```

看到 `Provider: openviking` 且 `Status: available` 就行。然后再开一轮新的 Hermes 会话。

配置完成后，Hermes 会自动注入上下文、预取相关记忆，并在会话后同步和抽取。可用工具：`viking_search`、`viking_read`、`viking_browse`、`viking_remember`、`viking_forget`、`viking_add_resource`。

## 参考文档

- [Hermes — OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
