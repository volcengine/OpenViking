---
'@openviking/react': patch
---

## en-US

Close command suggestions and help when the user presses outside the console, even when a host stops pointer-event propagation. Escape now closes them after focus leaves the input while preserving the draft.

## zh-CN

点击终端外部时关闭命令建议和帮助，即使宿主阻止了指针事件冒泡也能生效。输入框失焦后按 Escape 也会关闭浮层，并保留当前草稿。
