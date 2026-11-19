---
'@openviking/react': patch
---

## en-US

Keep the VikingBot tab visible with compact loading while activation eligibility or runtime status is checked. Hide the tab only when eligibility is explicitly false, keep failed checks retryable in the Bot panel, and preserve the user's command-mode selection when activation becomes ready.

Use compact loading for every eligibility/status read and the enabling indicator only while `start` is pending. Show error text once in a notification, keep Retry on the card, and announce lifecycle changes through a persistent live status without repeating errors.

Preserve a failure notification's dismissed state across Bot/CLI switches until a new failure. Transfer focus from a focused Enable or Retry button to the stable Bot panel before the operation, leaving it there on failure. When eligibility removes Bot controls, transfer focus only from the removed mode switch or Bot panel to the built-in CLI editor, with a panel fallback and no change to external focus. Add optional `CommandConsole.inputRef` for its actual textarea, keeping focus on Console's own editor even with complete Consoles nested in slots.

Mount the live status region empty and announce only while visible; showing a host-hidden Console repeats its current state without a new request. Visibility updates cover ancestor attributes (including `data-state`), box-size changes when `ResizeObserver` is available, and window or visual viewport resizing. Hosts should also update explicit state such as `hidden` for independent CSSOM edits or media preference changes that produce none of these signals.

When the scope's request signal is cancelled, keep the Bot tab with a static cancellation message instead of loading or an ineffective retry button. Check again after the host replaces the aborted signal with a fresh, non-aborted signal or removes it; changing only `scopeKey` does not restore a cancelled signal.

## zh-CN

检查启用资格或运行状态时保留 VikingBot 标签页并显示轻量加载提示，仅在资格明确为 false 时隐藏标签页。检查失败时在 Bot 面板保留重试入口，启用就绪后保留用户选择的命令模式。

所有资格和状态读取都显示轻量加载提示，仅在 `start` 等待返回时显示启用中。错误原文仅在通知中显示一次，卡片保留重试入口，常驻状态播报只报告流程状态变化，不重复播报错误。

同一次失败的通知关闭状态跨 Bot/CLI 切换保留，只有新失败才可重新展示。启用或重试时，若操作按钮持有焦点，先移到稳定的 Bot 面板，失败时留在 Bot。资格否定移除 Bot 控件时，仅当焦点在被移除的模式切换组或 Bot 面板内才交给内置 CLI 编辑器，无法聚焦时回退面板，不抢占外部焦点。新增 `CommandConsole.inputRef` 可选属性，提供真实 textarea 引用，插槽嵌入完整 Console 时仍准确定位当前 Console 自己的编辑器。

状态区域先空挂载、可见时发布公告；宿主隐藏 Console 后再次显示会重播当前状态，不发起新请求。可见性更新覆盖祖先属性变化（含 `data-state`）、`ResizeObserver` 可用时的盒子尺寸变化，以及窗口或可视视口尺寸变化。对于未产生上述信号的独立 CSSOM 修改或媒体偏好变化，宿主应同步 `hidden` 等显式状态。

作用域的请求 signal 取消后，保留 Bot 标签页并显示静态取消提示，替代加载状态和无法生效的重试按钮。宿主换用未取消的新 signal 或移除已取消的 signal 后重新检查；仅修改 `scopeKey` 不会恢复已取消的 signal。
