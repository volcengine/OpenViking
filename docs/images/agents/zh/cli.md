# CLI 接入

## CLI 命令行安装

在终端执行以下命令安装 OpenViking CLI，并进入配置流程：

```bash
npm i -g @openviking/cli && ov config
```

按提示填写 OpenViking 服务地址和 API Key。服务地址可使用：

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

配置完成后，可运行以下命令查看 CLI 用法：

```bash
ov --help
```

## Agent 对话安装

下个 PR 将继续优化 Agent CLI 安装流程，目前可先把以下指令发送给你的 Agent，让 Agent 帮你完成安装和配置：

```text
请在 ~/.openviking/ovcli.conf 写入以下内容：
{"url":"https://api.vikingdb.cn-beijing.volces.com/openviking","api_key":"Please ask user for Volcengine OpenViking API key."}

请向用户询问 API Key。如发现 ~/.openviking/ovcli.conf 已存在且内容冲突，请先询问用户是否备份原文件，并在得到确认后再覆盖。

请安装 OpenViking CLI：
npm i -g @openviking/cli

安装完成后，请运行：
ov --help

请探索 CLI 用法，并把 OpenViking CLI 的使用方式写入你的长期记忆。
```
