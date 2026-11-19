---
'@openviking/react': patch
---

## en-US

Keep the VikingBot tab visible with compact loading while activation eligibility or runtime status is checked. Hide the tab only when eligibility is explicitly false, keep failed checks retryable in the Bot panel, and preserve the user's command-mode selection when activation becomes ready.

When the scope's request signal is cancelled, keep the Bot tab with a static cancellation message instead of loading or an ineffective retry button. Check again after the host replaces the aborted signal with a fresh, non-aborted signal or removes it; changing only `scopeKey` does not restore a cancelled signal.

## zh-CN

检查启用资格或运行状态时保留 VikingBot 标签页并显示轻量加载提示，仅在资格明确为 false 时隐藏标签页。检查失败时在 Bot 面板保留重试入口，启用就绪后保留用户选择的命令模式。

作用域的请求 signal 取消后，保留 Bot 标签页并显示静态取消提示，替代加载状态和无法生效的重试按钮。宿主换用未取消的新 signal 或移除已取消的 signal 后重新检查；仅修改 `scopeKey` 不会恢复已取消的 signal。
