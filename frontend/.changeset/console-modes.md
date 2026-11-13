---
'@openviking/react': minor
---

## en-US

Add Console `modes` to enable VikingBot conversation, TUI commands, or both. Disabled modes are not rendered and do not send requests. Remove the public `modules/viking-bot` hook entry; use Console with `modes={['conversation']}` instead. Chat and session services remain configurable through Console.

## zh-CN

新增 Console `modes`，可启用 VikingBot 对话、TUI 命令或两者。被禁用的模式不渲染，也不发起请求。移除公开的 `modules/viking-bot` Hook 入口，改用 `modes={['conversation']}` 的 Console。对话和会话服务仍可通过 Console 配置。
