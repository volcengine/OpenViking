
将下方提示词复制给你的 AI 助手（Claude Code、Codex、Cursor、Trae 等），它会自动完成 OpenViking CLI 安装、配置和用法学习：

```text
请先向用户询问 OpenViking API Key，并记为 OPENVIKING_API_KEY。

请在 ~/.openviking/ovcli.conf 写入以下内容（将 ${OPENVIKING_API_KEY} 替换为用户提供的实际值）：
{
  "url": "https://api.vikingdb.cn-beijing.volces.com/openviking",
  "api_key": "${OPENVIKING_API_KEY}"
}

如发现 ~/.openviking/ovcli.conf 已存在且内容冲突，请先询问用户是否备份原文件，并在得到确认后再覆盖。

请安装 OpenViking CLI：
npm i -g @openviking/cli

安装完成后，请运行：
ov --help

请探索 CLI 用法，并把 OpenViking CLI 的使用方式写入你的长期记忆。
```
