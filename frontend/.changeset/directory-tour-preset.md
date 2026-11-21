---
'@openviking/react': minor
---

## en-US

Add `getOpenVikingDirectoryTourSteps(locale)` with complete English and Chinese directory explanations, and align the built-in memories, sessions and experiences illustrations. Remove copied step text from the demo. The default card no longer shows a close button; set `showCloseButton` and provide `onClose` to restore it. Escape and custom-content close actions still use `onClose`. Consumers should remove old text and illustration overrides to use the updated preset.

## zh-CN

新增 `getOpenVikingDirectoryTourSteps(locale)`，提供完整的中英文目录说明，并更新内置的用户记忆、轨迹记录和 Agent 经验插图。示例改为直接使用预设文案。默认卡片不再显示关闭按钮；如需恢复，请设置 `showCloseButton` 并提供 `onClose`。Escape 和自定义内容的关闭动作仍通过 `onClose` 处理。消费者需移除旧文案和插图覆盖，才能使用更新后的预设。
