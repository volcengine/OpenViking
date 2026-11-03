# TUI

预留命令输入、执行状态与结果展示的视图和交互。
执行状态与结果由上层传入；提交、取消和导航等意图通过事件回调上报，组件不执行命令或维护执行连接。

`tui.ts` 对应 `@openviking/react/components/tui`，当前为空模块，尚未提供组件或类型。后续组件使用 `Tui` 命名。

开发与构建入口见 [Frontend workspace README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。

独立样式入口为 `@openviking/react/components/tui/style.css`，迁入实现前为空样式。
