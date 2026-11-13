---
'@openviking/react': patch
---

## en-US

Show a quick command's description on hover only when the row cannot show it in full: when the command defines a `summary`, or when the description is truncated by the row's single-line ellipsis. The row prints `summary ?? description`, so a command without a `summary` used to repeat the same sentence in the Tooltip, while removing it outright hid the full text of a long description in a narrow panel.

## zh-CN

仅在快捷命令行无法完整展示描述时悬浮显示完整描述：命令配置了 `summary`，或描述被单行省略。命令行显示 `summary ?? description`；未配置 `summary` 时，之前 Tooltip 会重复相同文案，而直接移除 Tooltip 又会让窄面板中被截断的长描述无法完整查看。
