---
'@openviking/react': patch
---

Show a quick command's description on hover only when the command defines a `summary`. The row already prints `summary ?? description`, so without a `summary` the Tooltip repeated the visible sentence.
