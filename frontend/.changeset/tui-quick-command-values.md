---
'@openviking/react': minor
---

## en-US

Change Tui and Console quickCommands from command-name strings to { name, value } entries. Keep names and descriptions in the command catalogue, and fill the supplied complete value verbatim on selection without automatically executing it. Migrate each existing string to an object; Console defaults retain their ov command prefixes. Empty-state documentation presets follow the Volcengine examples, including the saved-configuration compilation workflow.

Recognize HTTP and HTTPS URLs as native links in Tui input, command echoes and results so they use the host theme primary color, just like Viking resource URIs.

## zh-CN

Tui 和 Console 的 `quickCommands` 从命令名字符串改为 `{ name, value }` 对象。命令目录继续提供名称和说明，选择快捷命令后原样填入完整的 `value`，不自动执行。原有字符串需逐项迁移为对象，Console 默认值保留 `ov` 命令前缀。空状态示例与火山引擎保持一致，包括使用已保存配置的编译流程。

Tui 输入、命令回显和结果中的 HTTP、HTTPS URL 支持原生链接，与 Viking 资源 URI 一样使用宿主主题主色。
