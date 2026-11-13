---
'@openviking/react': patch
---

Show a quick command's description on hover only when the row cannot show it in full: when the command defines a `summary`, or when the description is truncated by the row's single-line ellipsis. The row prints `summary ?? description`, so a command without a `summary` used to repeat the same sentence in the Tooltip, while removing it outright hid the full text of a long description in a narrow panel.
