# Docs Agent Guide

适用于 `docs/**`，同时遵循 [Frontend Agent Guide](../AGENTS.md)。

## 交互控件

- docs 页面、可运行示例和自定义插槽中的交互控件一律复用内部组件；示例通过 `#demo-controls` 使用构建后的基础件，不直接编写原生 `button`、`input`、`select`、`option` 或 `textarea`。
- 文档展示的交互示例遵循同一规则，不以示例简单、临时演示或浏览器默认样式为由使用原生控件。
- 缺少所需控件时，先补齐内部基础件再接入示例，不在 docs 中另写一套控件或覆盖其内部元素来模拟外观。
- 库组件内部为实现语义和键盘行为使用的原生元素由组件维护；docs 使用组件接口，布局和正文保留合适的语义 HTML。
