---
'@openviking/react': patch
---

## en-US

Fix Console showing a command-unavailable warning during execution. CommandConsole now accepts `busy` to lock input and submission while pending result cards show progress; `disabled` retains its unavailable message.

## zh-CN

修复 Console 执行命令时误显示不可用提示的问题。CommandConsole 新增 `busy`，在等待结果时锁定输入与提交，由执行中的结果卡片展示进度；`disabled` 保留不可用提示。
