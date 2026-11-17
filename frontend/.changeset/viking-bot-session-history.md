---
'@openviking/react': patch
---

## en-US

Keep the VikingBot history popover at a stable height during refresh, and show a centered diamond loader when switching conversations. Add session rename and delete actions when the session service supports them.

Dismiss pending rename and delete confirmations when the session account or service changes. Standalone VikingBot hosts can provide `sessions.scopeKey` to identify their session scope.

## zh-CN

刷新时保持 VikingBot 历史会话弹层高度稳定，切换会话时居中显示菱形加载动画。会话服务支持相应操作时，提供会话改名和删除入口。

切换会话账号或服务时关闭已打开的重命名和删除确认框。独立使用 VikingBot 时，宿主可通过 `sessions.scopeKey` 标识会话作用域。
