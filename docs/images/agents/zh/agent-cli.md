
将下方提示词复制给你的 AI 助手（Claude Code、Codex、Cursor、Trae 等），让它完成 OpenViking CLI 安装、配置和连接验证：

```text
请先向用户询问 OpenViking API Key，并记为 OPENVIKING_API_KEY。

请在 ~/.openviking/ovcli.conf 写入以下内容，将 ${OPENVIKING_API_KEY} 替换为用户提供的实际 key；CLI 不会在此文件中展开环境变量：
{
  "url": "{{OPENVIKING_BASE_URL}}",
  "api_key": "${OPENVIKING_API_KEY}"
}

如发现 ~/.openviking/ovcli.conf 已存在且内容冲突，请先询问用户是否备份原文件，并在得到确认后再覆盖。

请安装 OpenViking CLI：
npm i -g @openviking/cli

安装完成后，请运行：
ov config validate
ov health
ov --help

请查阅 ov --help 和具体命令的 --help，再总结常用操作。把常用操作写入长期记忆，只记录用法，不记录 API Key 或原始配置文件。
```
