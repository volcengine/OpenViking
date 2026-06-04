# Agent CLI 接入

面向 AI Agent 使用。把下面的指令发送给你的 Agent，让它完成 OpenViking CLI 安装、配置和用法学习。

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
