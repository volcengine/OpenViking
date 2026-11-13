---
'@openviking/react': patch
---

## en-US

Give Console command mode its own `command.onOpenUri` override, symmetric with `conversation.onOpenUri`. Both modes fall back to the top-level `onOpenUri`, so a host that routes conversation links and terminal links differently no longer has to intercept the shared callback itself.

## zh-CN

为 Console 命令模式新增独立的 `command.onOpenUri` 覆盖项，与 `conversation.onOpenUri` 对应。两种模式均回退到顶层 `onOpenUri`，宿主可以分别处理对话链接和终端链接。
