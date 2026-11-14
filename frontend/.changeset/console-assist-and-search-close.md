---
'@openviking/react': patch
---

## en-US

The session history and new-session actions in VikingBot and in the Console command mode now share one size: 28px buttons 8px apart with 14px icons and a 1.25px stroke, so the icons no longer move when switching modes. The command mode actions also gain the same hover background and darker icon as VikingBot. The new-session glyph draws at the same size as the history glyph. FileBrowser's search dialog keeps its current results until the close transition ends, whether it is closed with the close button, the mask or Escape. When CommandConsole stacks the command details above the suggestion list on narrow widths, the panel can grow to 640px and the list gets more of that height.

## zh-CN

VikingBot 与 Console 命令模式中的历史会话、新建会话按钮统一为 28px 按钮、8px 间距、14px 图标和 1.25px 线宽，切换模式时图标不再移位，命令模式按钮的悬停效果也与 VikingBot 一致（浅色背景、图标加深）；新建会话图标的绘制尺寸与历史图标一致。FileBrowser 搜索弹窗无论通过关闭按钮、遮罩还是 Esc 关闭，都会在关闭动画结束后才重置结果。CommandConsole 在窄宽度下将命令详情与建议列表上下堆叠时，浮层最高可到 640px，建议列表分到的高度更多。
