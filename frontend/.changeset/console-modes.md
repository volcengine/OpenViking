---
'@openviking/react': minor
---

Add Console `modes` to enable VikingBot conversation, TUI commands, or both. Disabled modes are not rendered and do not send requests. Remove the public `modules/viking-bot` hook entry; use Console with `modes={['conversation']}` instead. Chat and session services remain configurable through Console.
